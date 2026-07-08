# code/analiseInventarios/scripts/load_baseline_snapshot.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd


# ------------------------------------------------------------
# CAMINHOS DO PROJETO
# ------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data_db" / "inventarios.sqlite"

# Fonte do baseline: arquivo de estoque do projeto analiseEstoques
BASELINE_DIR = Path(
    r"X:\Filial São José\Remanufatura\Gestão\xApps\HUB_indicadores\data\analiseEstoques\SAP_IN"
)


# ------------------------------------------------------------
# COLUNAS ESPERADAS NO EXCEL DE ESTOQUE
# ------------------------------------------------------------
REQUIRED_COLUMNS = {
    "Material",
    "Descrição de material",
    "Depósito",
    "Estoque de utilização livre",
    "Estoque em controle de qualidade",
    "Estoque bloqueado",
    "Valor do estoque de utilização livre",
    "Valor do estoque no controle de qualidade",
    "Valor do estoque bloqueado",
}


# ------------------------------------------------------------
# UTILITÁRIOS
# ------------------------------------------------------------
def to_float_ptbr(x) -> Optional[float]:
    """
    Converte valores no padrão pt-BR para float.

    Exemplos:
    - '1.234,56' -> 1234.56
    - '8 PEÇ'    -> 8.0
    - '20,33 BRL' -> 20.33
    - vazio / NaN -> None
    """
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None

    s = str(x).strip()
    if not s or s.lower() == "nan":
        return None

    # Remove textos de unidade/moeda mais comuns
    s = s.replace("PEÇ", "").replace("BRL", "").strip()

    # Normaliza pt-BR:
    # 1.234,56 -> 1234.56
    if "," in s:
        s = s.replace(".", "").replace(",", ".")

    try:
        return float(s)
    except Exception:
        return None


def get_snapshot_info_from_filename(file_path: Path) -> tuple[str, str]:
    """
    Extrai snapshot_date e snapshot_month a partir do nome do arquivo.

    Suporta dois formatos observados:
    1) MateriaisMMYYYY.xlsx
       Ex.: Materiais012026.xlsx -> snapshot_date=2026-01-01 | snapshot_month=2026-01

    2) MateriaisDDMMYYYY.xlsx
       Ex.: Materiais03032026.xlsx -> snapshot_date=2026-03-03 | snapshot_month=2026-03
    """
    nome = file_path.stem.replace("Materiais", "").strip()

    # Formato MMYYYY
    if len(nome) == 6 and nome.isdigit():
        mes = int(nome[0:2])
        ano = int(nome[2:6])
        snapshot_date = f"{ano:04d}-{mes:02d}-01"
        snapshot_month = f"{ano:04d}-{mes:02d}"
        return snapshot_date, snapshot_month

    # Formato DDMMYYYY
    if len(nome) == 8 and nome.isdigit():
        dia = int(nome[0:2])
        mes = int(nome[2:4])
        ano = int(nome[4:8])
        snapshot_date = f"{ano:04d}-{mes:02d}-{dia:02d}"
        snapshot_month = f"{ano:04d}-{mes:02d}"
        return snapshot_date, snapshot_month

    raise ValueError(
        f"Nome de arquivo não reconhecido para extrair snapshot: {file_path.name}"
    )


def list_baseline_files() -> list[Path]:
    """
    Lista os arquivos Materiais*.xlsx disponíveis na pasta de baseline.
    """
    if not BASELINE_DIR.exists():
        raise FileNotFoundError(f"Pasta de baseline não encontrada: {BASELINE_DIR}")

    files = sorted(
        [p for p in BASELINE_DIR.glob("Materiais*.xlsx") if p.is_file() and not p.name.startswith("~$")]
    )

    if not files:
        raise FileNotFoundError(f"Nenhum arquivo Materiais*.xlsx encontrado em {BASELINE_DIR}")

    return files


def choose_first_file_per_month(files: list[Path]) -> dict[str, Path]:
    """
    A partir da lista de arquivos, escolhe o primeiro arquivo de cada mês.

    Regras:
    - a competência do mês vem do nome do arquivo
    - se houver mais de um arquivo no mesmo mês,
      usamos o menor snapshot_date daquele mês
    """
    month_to_file: dict[str, tuple[str, Path]] = {}

    for file_path in files:
        snapshot_date, snapshot_month = get_snapshot_info_from_filename(file_path)

        if snapshot_month not in month_to_file:
            month_to_file[snapshot_month] = (snapshot_date, file_path)
        else:
            current_date, _ = month_to_file[snapshot_month]
            if snapshot_date < current_date:
                month_to_file[snapshot_month] = (snapshot_date, file_path)

    return {month: file_path for month, (_, file_path) in month_to_file.items()}


def get_existing_snapshot_months(conn: sqlite3.Connection) -> set[str]:
    """
    Retorna os meses já carregados na tabela baseline_items.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT snapshot_month
        FROM baseline_items
        ORDER BY snapshot_month
        """
    ).fetchall()

    return {str(r[0]) for r in rows if r[0] is not None}


def validate_columns(df: pd.DataFrame, file_name: str) -> None:
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(
            f"{file_name}: faltam colunas esperadas: {missing}\n"
            f"Colunas encontradas: {list(df.columns)}"
        )


def read_baseline_excel(file_path: Path) -> pd.DataFrame:
    """
    Lê o Excel do baseline e valida as colunas mínimas.
    """
    df = pd.read_excel(file_path)
    validate_columns(df, file_path.name)
    return df


def transform_baseline_df(df: pd.DataFrame, file_path: Path) -> pd.DataFrame:
    """
    Padroniza o DataFrame para o schema da tabela baseline_items.
    """
    snapshot_date, snapshot_month = get_snapshot_info_from_filename(file_path)

    df = df.rename(
        columns={
            "Material": "material",
            "Descrição de material": "material_desc",
            "Depósito": "warehouse_code",
            "Estoque de utilização livre": "qty_unrestricted",
            "Estoque em controle de qualidade": "qty_quality",
            "Estoque bloqueado": "qty_blocked",
            "Valor do estoque de utilização livre": "value_unrestricted",
            "Valor do estoque no controle de qualidade": "value_quality",
            "Valor do estoque bloqueado": "value_blocked",
        }
    ).copy()

    # Conversões numéricas robustas
    for col in ["qty_unrestricted", "qty_quality", "qty_blocked"]:
        df[col] = df[col].apply(to_float_ptbr).fillna(0.0)

    for col in ["value_unrestricted", "value_quality", "value_blocked"]:
        df[col] = df[col].apply(to_float_ptbr).fillna(0.0)

    # Totais
    df["qty_total"] = (
        df["qty_unrestricted"]
        + df["qty_quality"]
        + df["qty_blocked"]
    )

    df["value_total"] = (
        df["value_unrestricted"]
        + df["value_quality"]
        + df["value_blocked"]
    )

    # Metadados
    df["snapshot_date"] = snapshot_date
    df["snapshot_month"] = snapshot_month
    df["source_file"] = file_path.name
    df["loaded_at"] = datetime.now().isoformat(timespec="seconds")

    # Limpeza de texto
    df["material"] = df["material"].astype(str).str.strip()
    df["material_desc"] = df["material_desc"].astype(str).str.strip()
    df["warehouse_code"] = df["warehouse_code"].astype(str).str.strip()

    # Remove linhas sem material ou depósito
    df = df[
        (df["material"] != "")
        & (df["material"].str.lower() != "nan")
        & (df["warehouse_code"] != "")
        & (df["warehouse_code"].str.lower() != "nan")
    ].copy()

    # Mantemos apenas itens que tenham alguma presença no estoque
    # (quantidade ou valor)
    df = df[
        (df["qty_total"] > 0) | (df["value_total"] > 0)
    ].copy()

    cols = [
        "snapshot_date",
        "snapshot_month",
        "warehouse_code",
        "material",
        "material_desc",
        "qty_unrestricted",
        "qty_quality",
        "qty_blocked",
        "qty_total",
        "value_unrestricted",
        "value_quality",
        "value_blocked",
        "value_total",
        "source_file",
        "loaded_at",
    ]

    return df[cols]


def load_month_if_missing(conn: sqlite3.Connection, file_path: Path) -> tuple[str, int]:
    """
    Carrega um arquivo de baseline na tabela baseline_items.
    Retorna:
    - snapshot_month carregado
    - quantidade de linhas inseridas
    """
    df_raw = read_baseline_excel(file_path)
    df = transform_baseline_df(df_raw, file_path)

    snapshot_month = str(df["snapshot_month"].iloc[0])

    df.to_sql(
        "baseline_items",
        conn,
        if_exists="append",
        index=False,
    )

    return snapshot_month, len(df)


def main() -> None:
    files = list_baseline_files()
    first_files_by_month = choose_first_file_per_month(files)

    with sqlite3.connect(DB_PATH) as conn:
        existing_months = get_existing_snapshot_months(conn)

        months_loaded = []
        months_skipped = []

        for snapshot_month in sorted(first_files_by_month.keys()):
            file_path = first_files_by_month[snapshot_month]

            if snapshot_month in existing_months:
                months_skipped.append((snapshot_month, file_path.name))
                print(f"[SKIP] baseline_items | mês já existe | {snapshot_month} | arquivo={file_path.name}")
                continue

            loaded_month, row_count = load_month_if_missing(conn, file_path)
            months_loaded.append((loaded_month, file_path.name, row_count))
            print(f"[OK] baseline_items | mês={loaded_month} | arquivo={file_path.name} | linhas={row_count}")

    print("\n[DONE] Carga de baseline mensal finalizada.")

    if months_loaded:
        print("[RESUMO] Meses carregados:")
        for month, file_name, row_count in months_loaded:
            print(f"  - {month} | {file_name} | linhas={row_count}")

    if months_skipped:
        print("[RESUMO] Meses ignorados (já existentes):")
        for month, file_name in months_skipped:
            print(f"  - {month} | {file_name}")


if __name__ == "__main__":
    main()