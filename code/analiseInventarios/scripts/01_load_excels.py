from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional, Iterable

import pandas as pd


# ====== Caminhos do projeto atual (HUB) ======
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MM_DIR = PROJECT_ROOT / "MM_IN"
EWM_DIR = PROJECT_ROOT / "WEPV_IN"
DB_PATH = PROJECT_ROOT / "data_db" / "inventarios.sqlite"
SHEET_NAME = "Data"
# =============================================


MM_COLS = {
    "inv_doc": "Documento inventário",
    "inv_item": "Item",
    "material": "Material",
    "warehouse_code": "Depósito",
    "count_date": "Data contagem",
    "qty_recorded": "Qtd.registrada",
    "qty_counted": "Qtd.contada",
    "qty_diff": "Qtd.diferença",
    "status": "Status invent.físico",
    "counted_by": "Contado por",
    "stock_type": "Tipo de estoque",
}

EWM_COLS = {
    "inv_doc": "DocInvFísico",
    "inv_item": "Item",
    "material": "Produto",
    "warehouse_code": "Tipo de depósito",
    "count_date": "Data de lançamento",
    "qty_recorded": "Qtd.registrada",
    "qty_counted": "Qtd.cont.inv.",
    "qty_diff": "Quantidade de diferença",
    "value_diff": "Valor de diferença",
    "status": "Status do inventário físico",
    "counted_by": "Contador",
    "storage_area": "Área armazmto.",
    "bin_location": "Posição no depósito",
    "stock_type": "Tipo de estoque",
    "wh_order": "Ordem de depósito",
}


INSERT_SQL = """
INSERT OR REPLACE INTO counts (
  row_uid, source_system, doc_key, item_key,
  inv_doc, inv_item, material, warehouse_code,
  count_date, qty_recorded, qty_counted, qty_diff, value_diff,
  status, counted_by,
  storage_area, bin_location, stock_type, wh_order,
  year_iso, week_iso, week_start, week_end,
  file_name, loaded_at
) VALUES (
  :row_uid, :source_system, :doc_key, :item_key,
  :inv_doc, :inv_item, :material, :warehouse_code,
  :count_date, :qty_recorded, :qty_counted, :qty_diff, :value_diff,
  :status, :counted_by,
  :storage_area, :bin_location, :stock_type, :wh_order,
  :year_iso, :week_iso, :week_start, :week_end,
  :file_name, :loaded_at
);
"""


def _to_str(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, float) and pd.isna(x):
        return ""
    return str(x).strip()


def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        if isinstance(x, float) and pd.isna(x):
            return None
        return float(x)
    except Exception:
        return None


def _to_date_iso(x: Any) -> Optional[str]:
    if x is None:
        return None

    if isinstance(x, pd.Timestamp):
        if pd.isna(x):
            return None
        return x.date().isoformat()

    if isinstance(x, datetime):
        return x.date().isoformat()

    if isinstance(x, date):
        return x.isoformat()

    s = str(x).strip()
    if not s or s.lower() == "nan":
        return None

    ts = pd.to_datetime(s, dayfirst=True, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.date().isoformat()


def _iso_week_bounds(year_iso: int, week_iso: int) -> tuple[str, str]:
    start = date.fromisocalendar(year_iso, week_iso, 1)  # Monday
    end = date.fromisocalendar(year_iso, week_iso, 7)    # Sunday
    return start.isoformat(), end.isoformat()


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def iter_xlsx(folder: Path) -> list[Path]:
    if not folder.exists():
        return []

    return sorted([
        p for p in folder.glob("*.xlsx")
        if p.is_file()
        and not p.name.startswith("~$")
    ])


def read_excel(path: Path) -> pd.DataFrame:
    # Aba padrão: "Data"
    return pd.read_excel(path, sheet_name=SHEET_NAME)


def _validate_cols(df: pd.DataFrame, mapping: dict[str, str], file_name: str, source: str) -> None:
    missing = [col for col in mapping.values() if col not in df.columns]
    if missing:
        raise ValueError(f"{source} | {file_name}: faltam colunas esperadas: {missing}")


def build_rows(df: pd.DataFrame, source_system: str, file_name: str, loaded_at: str) -> list[dict]:
    cols = MM_COLS if source_system == "MM" else EWM_COLS
    _validate_cols(df, cols, file_name, source_system)

    out: list[dict] = []

    for _, r in df.iterrows():
        inv_doc = _to_str(r.get(cols["inv_doc"]))
        inv_item = _to_str(r.get(cols["inv_item"]))
        material = _to_str(r.get(cols["material"]))
        warehouse_code = _to_str(r.get(cols["warehouse_code"]))

        count_date = _to_date_iso(r.get(cols["count_date"]))

        year_iso = week_iso = None
        week_start = week_end = None
        if count_date:
            y, w, _ = date.fromisoformat(count_date).isocalendar()
            year_iso = int(y)
            week_iso = int(w)
            week_start, week_end = _iso_week_bounds(year_iso, week_iso)

        qty_recorded = _to_float(r.get(cols.get("qty_recorded", "")))
        qty_counted = _to_float(r.get(cols.get("qty_counted", "")))
        qty_diff = _to_float(r.get(cols.get("qty_diff", "")))
        value_diff = _to_float(r.get(cols.get("value_diff", "")))

        status = _to_str(r.get(cols.get("status", "")))
        counted_by = _to_str(r.get(cols.get("counted_by", "")))

        storage_area = _to_str(r.get(cols.get("storage_area", "")))
        bin_location = _to_str(r.get(cols.get("bin_location", "")))
        stock_type = _to_str(r.get(cols.get("stock_type", "")))
        wh_order = _to_str(r.get(cols.get("wh_order", "")))

        # Documento (nível doc)
        doc_key = f"{source_system}|{warehouse_code}|{inv_doc}"

        # Item (nível linha funcional) — EWM inclui granularidade pra evitar colisão
        if source_system == "MM":
            item_key = f"{source_system}|{warehouse_code}|{inv_doc}|{inv_item}|{material}"
            storage_area_db = None
            bin_location_db = None
            wh_order_db = None
        else:
            item_key = (
                f"{source_system}|{warehouse_code}|{inv_doc}|{inv_item}|{material}"
                f"|{storage_area}|{bin_location}|{stock_type}|{wh_order}"
            )
            storage_area_db = storage_area or None
            bin_location_db = bin_location or None
            wh_order_db = wh_order or None

        # row_uid estável: atualiza a mesma linha quando reimportar
        row_uid = _sha1(item_key)

        out.append(
            dict(
                row_uid=row_uid,
                source_system=source_system,
                doc_key=doc_key,
                item_key=item_key,
                inv_doc=inv_doc,
                inv_item=inv_item,
                material=material,
                warehouse_code=warehouse_code,
                count_date=count_date,
                qty_recorded=qty_recorded,
                qty_counted=qty_counted,
                qty_diff=qty_diff,
                value_diff=value_diff,
                status=status or None,
                counted_by=counted_by or None,
                storage_area=storage_area_db,
                bin_location=bin_location_db,
                stock_type=stock_type or None,
                wh_order=wh_order_db,
                year_iso=year_iso,
                week_iso=week_iso,
                week_start=week_start,
                week_end=week_end,
                file_name=file_name,
                loaded_at=loaded_at,
            )
        )

    return out


def main() -> None:
    if not PROJECT_ROOT.exists():
        raise FileNotFoundError(f"Pasta do projeto não encontrada: {PROJECT_ROOT}")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    mm_files = iter_xlsx(MM_DIR)
    ewm_files = iter_xlsx(EWM_DIR)

    if not mm_files and not ewm_files:
        print("[WARN] Nenhum .xlsx encontrado em MM_IN ou WEPV_IN.")
        return

    loaded_at = datetime.now().isoformat(timespec="seconds")

    conn = sqlite3.connect(DB_PATH)
    try:
        total_files = 0
        total_rows = 0

        for source, files in [("MM", mm_files), ("EWM", ewm_files)]:
            for f in files:
                df = read_excel(f)
                rows = build_rows(df, source_system=source, file_name=f.name, loaded_at=loaded_at)

                with conn:
                    conn.executemany(INSERT_SQL, rows)

                total_files += 1
                total_rows += len(rows)
                print(f"[OK] {source} | {f.name} | linhas={len(rows)}")

        print(f"[DONE] arquivos={total_files} | linhas_processadas={total_rows}")
        print(f"[DONE] db={DB_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
