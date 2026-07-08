from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

PROJECT_ROOT = Path(r"X:\Filial São José\Remanufatura\Gestão\Indicadores\analiseInventarios")
DB_PATH = PROJECT_ROOT / "data_db" / "inventarios.sqlite"
SNAP_DIR = PROJECT_ROOT / "MM_SNAPSHOT_IN"

# Cabeçalhos esperados do SAP (linha real do header pode estar deslocada)
EXPECTED_HEADERS = {
    "Material",
    "Texto breve material",
    "Centro",
    "Depósito",
    "UMB",
    "Utilização livre",
    "Bloqueado",
    "Val.utiliz.livre",
    "Val.estoque bloq.",
    "Tipo de material",
}


def infer_snapshot_date(file_name: str) -> str:
    """
    Extrai data do nome do arquivo.
    Ex.: 2026_02_20_MAST_MASR_SNAPSHOT.xlsx -> 2026-02-20
    """
    m = re.search(r"(20\d{2})[._-](\d{2})[._-](\d{2})", file_name)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        return f"{y}-{mo}-{d}"
    return datetime.now().date().isoformat()


def read_snapshot_xlsx(path: Path) -> pd.DataFrame:
    """
    Lê o Excel do SAP mesmo quando:
    - coluna A vazia
    - linha 1 vazia
    Localiza a linha do cabeçalho procurando EXPECTED_HEADERS.
    """
    raw = pd.read_excel(path, sheet_name=0, header=None, dtype=object)

    header_row_idx: Optional[int] = None
    for i in range(min(40, len(raw))):
        row_vals = set(str(x).strip() for x in raw.iloc[i].tolist() if pd.notna(x))
        if EXPECTED_HEADERS.issubset(row_vals):
            header_row_idx = i
            break

    if header_row_idx is None:
        # fallback comum SAP: cabeçalho na linha 2 (índice 1)
        header_row_idx = 1

    df = raw.iloc[header_row_idx + 1 :].copy()
    df.columns = [str(x).strip() if pd.notna(x) else "" for x in raw.iloc[header_row_idx].tolist()]

    # remove colunas sem nome (ex: coluna A vazia)
    df = df.loc[:, df.columns != ""]
    # remove linhas totalmente vazias
    df = df.dropna(how="all")
    return df


def to_float_ptbr(x) -> Optional[float]:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = str(x).strip()
    if not s or s.lower() == "nan":
        return None

    # normaliza formato pt-BR para float:
    # 1.234,56 -> 1234.56
    # 123,45 -> 123.45
    if "," in s:
        s = s.replace(".", "").replace(",", ".")

    try:
        return float(s)
    except Exception:
        return None


def main() -> None:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)

    files = sorted([p for p in SNAP_DIR.glob("*.xlsx") if not p.name.startswith("~$")])
    if not files:
        print(f"[WARN] Nenhum .xlsx encontrado em {SNAP_DIR}")
        return

    loaded_at = datetime.now().isoformat(timespec="seconds")

    conn = sqlite3.connect(DB_PATH)
    try:
        for f in files:
            df = read_snapshot_xlsx(f)

            # valida colunas mínimas
            required = ["Material", "Depósito", "Utilização livre", "Bloqueado", "Val.utiliz.livre", "Val.estoque bloq."]
            missing = [c for c in required if c not in df.columns]
            if missing:
                raise ValueError(f"{f.name}: faltam colunas esperadas: {missing}\nColunas lidas: {list(df.columns)}")

            snapshot_date = infer_snapshot_date(f.name)

            rows = []
            for _, r in df.iterrows():
                material = str(r.get("Material", "")).strip()
                dep = str(r.get("Depósito", "")).strip()

                if not material or not dep or dep.lower() == "nan":
                    continue

                qty_u = to_float_ptbr(r.get("Utilização livre")) or 0.0
                qty_b = to_float_ptbr(r.get("Bloqueado")) or 0.0
                qty_total = qty_u + qty_b

                val_u = to_float_ptbr(r.get("Val.utiliz.livre")) or 0.0
                val_b = to_float_ptbr(r.get("Val.estoque bloq.")) or 0.0
                val_total = val_u + val_b

                # mantém apenas itens com saldo (quantidade ou valor)
                if qty_total <= 0 and val_total <= 0:
                    continue

                rows.append(
                    (
                        snapshot_date,
                        str(r.get("Centro", "")).strip() or None,              # plant
                        dep,                                                   # warehouse_code
                        material,
                        str(r.get("Texto breve material", "")).strip() or None,
                        str(r.get("UMB", "")).strip() or None,
                        qty_u,
                        qty_b,
                        qty_total,
                        val_u,
                        val_b,
                        val_total,
                        str(r.get("Tipo de material", "")).strip() or None,    # tmat
                        f.name,
                        loaded_at,
                    )
                )

            with conn:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO mm_snapshot (
                      snapshot_date, plant, warehouse_code, material,
                      material_desc, umb,
                      qty_unrestricted, qty_blocked, qty_total,
                      value_unrestricted, value_blocked, value_total,
                      tmat, file_name, loaded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    rows,
                )

            print(f"[OK] snapshot MM | {f.name} | linhas_inseridas={len(rows)} | snapshot_date={snapshot_date}")

        print("[DONE] snapshot MM carregado.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
    