# code/hub/check_update_status.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import sqlite3
from datetime import datetime, time
from pathlib import Path
from typing import Optional

import pandas as pd


# ------------------------------------------------------------
# CAMINHOS DO HUB
# ------------------------------------------------------------
HUB_ROOT = Path(__file__).resolve().parents[2]

# Banco do inventário
INV_DB_PATH = HUB_ROOT / "code" / "analiseInventarios" / "data_db" / "inventarios.sqlite"

# Pasta de entrada compartilhada do Materiais*.xlsx
MATERIAIS_DIR = HUB_ROOT / "data" / "analiseEstoques" / "SAP_IN"

# Log do ETL de Estoques (fonte principal do status diário)
ESTOQUES_LOG_DIR = HUB_ROOT / "data" / "analiseEstoques" / "LOGS"

# Janela operacional esperada do Estoques
ESTOQUES_WINDOW_START = time(7, 30)
ESTOQUES_WINDOW_END = time(8, 0)


# ------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------
def fmt_dt(dt: Optional[datetime]) -> str:
    if dt is None:
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def fmt_date(dt: Optional[datetime]) -> str:
    if dt is None:
        return "-"
    return dt.strftime("%Y-%m-%d")


def get_latest_file(files: list[Path]) -> Optional[Path]:
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def get_latest_log(log_dir: Path) -> Optional[Path]:
    if not log_dir.exists():
        return None

    files = [p for p in log_dir.glob("*.log") if p.is_file()]
    return get_latest_file(files)


def get_latest_materiais_file() -> Optional[Path]:
    if not MATERIAIS_DIR.exists():
        return None

    files = [
        p for p in MATERIAIS_DIR.glob("Materiais*.xlsx")
        if p.is_file() and not p.name.startswith("~$")
    ]
    return get_latest_file(files)


def parse_snapshot_info_from_filename(file_path: Path) -> tuple[str, str]:
    """
    Extrai snapshot_date e snapshot_month do nome do arquivo Materiais*.xlsx.

    Formatos aceitos:
    - MateriaisMMYYYY.xlsx
    - MateriaisDDMMYYYY.xlsx
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

    raise ValueError(f"Nome de arquivo não reconhecido: {file_path.name}")


def get_first_file_of_current_month() -> Optional[Path]:
    """
    Localiza o primeiro arquivo Materiais*.xlsx disponível do mês atual.
    A competência é lida pelo nome do arquivo, não por mtime.
    """
    if not MATERIAIS_DIR.exists():
        return None

    hoje = datetime.now()
    mes_atual = f"{hoje.year:04d}-{hoje.month:02d}"

    files = [
        p for p in MATERIAIS_DIR.glob("Materiais*.xlsx")
        if p.is_file() and not p.name.startswith("~$")
    ]

    candidatos: list[tuple[str, Path]] = []

    for file_path in files:
        try:
            snapshot_date, snapshot_month = parse_snapshot_info_from_filename(file_path)
        except Exception:
            continue

        if snapshot_month == mes_atual:
            candidatos.append((snapshot_date, file_path))

    if not candidatos:
        return None

    candidatos.sort(key=lambda x: x[0])
    return candidatos[0][1]


# ------------------------------------------------------------
# STATUS - ESTOQUES
# ------------------------------------------------------------
def check_estoques_status() -> dict:
    """
    Regras:
    - antes de 07:30 -> AGUARDANDO JANELA
    - depois de 07:30:
        - se log mais recente é de hoje -> OK
        - se não -> PENDENTE
    - se não existe arquivo Materiais*.xlsx -> SEM FONTE
    """
    now = datetime.now()
    latest_log = get_latest_log(ESTOQUES_LOG_DIR)
    latest_materiais = get_latest_materiais_file()

    latest_log_dt = (
        datetime.fromtimestamp(latest_log.stat().st_mtime)
        if latest_log else None
    )

    latest_materiais_dt = (
        datetime.fromtimestamp(latest_materiais.stat().st_mtime)
        if latest_materiais else None
    )

    if latest_materiais is None:
        status = "SEM FONTE"
        motivo = "Nenhum arquivo Materiais*.xlsx encontrado na pasta de entrada."
    else:
        if now.time() < ESTOQUES_WINDOW_START:
            status = "AGUARDANDO JANELA"
            motivo = "Ainda não chegou o horário operacional esperado do ETL de Estoques."
        else:
            if latest_log_dt is not None and latest_log_dt.date() == now.date():
                status = "OK"
                motivo = "A atualização diária de Estoques já foi executada hoje."
            else:
                status = "PENDENTE"
                motivo = "Ainda não há evidência de execução do ETL de Estoques hoje."

    return {
        "app": "ESTOQUES",
        "status": status,
        "motivo": motivo,
        "janela": f"{ESTOQUES_WINDOW_START.strftime('%H:%M')}–{ESTOQUES_WINDOW_END.strftime('%H:%M')}",
        "ultimo_arquivo_materiais": latest_materiais.name if latest_materiais else "-",
        "ultima_data_arquivo_materiais": fmt_dt(latest_materiais_dt),
        "ultimo_log": latest_log.name if latest_log else "-",
        "ultima_execucao": fmt_dt(latest_log_dt),
    }


# ------------------------------------------------------------
# STATUS - INVENTÁRIOS (SEMANAL)
# ------------------------------------------------------------
def check_inventarios_weekly_status() -> dict:
    """
    Regras:
    - pega MAX(loaded_at) de counts
    - compara a semana ISO da última carga com a semana ISO atual
    """
    now = datetime.now()
    current_iso = now.isocalendar()
    current_year_week = f"{current_iso.year}-W{current_iso.week:02d}"

    latest_loaded_at = None

    if INV_DB_PATH.exists():
        with sqlite3.connect(INV_DB_PATH) as conn:
            row = conn.execute(
                """
                SELECT MAX(loaded_at)
                FROM counts
                """
            ).fetchone()

            if row and row[0]:
                latest_loaded_at = pd.to_datetime(row[0], errors="coerce")

    if latest_loaded_at is None or pd.isna(latest_loaded_at):
        status = "PENDENTE"
        motivo = "Não há registro de carga na tabela counts."
        last_year_week = "-"
        latest_loaded_at_fmt = "-"
    else:
        latest_loaded_at = latest_loaded_at.to_pydatetime()
        last_iso = latest_loaded_at.isocalendar()
        last_year_week = f"{last_iso.year}-W{last_iso.week:02d}"
        latest_loaded_at_fmt = fmt_dt(latest_loaded_at)

        if last_iso.year == current_iso.year and last_iso.week == current_iso.week:
            status = "OK"
            motivo = "A atualização semanal de Inventários já ocorreu na semana ISO atual."
        else:
            status = "PENDENTE"
            motivo = "A semana ISO atual ainda não possui atualização de Inventários."

    return {
        "app": "INVENTÁRIOS - SEMANA",
        "status": status,
        "motivo": motivo,
        "semana_atual": current_year_week,
        "ultima_semana_atualizada": last_year_week,
        "ultima_execucao": latest_loaded_at_fmt,
    }


# ------------------------------------------------------------
# STATUS - INVENTÁRIOS (BASELINE MENSAL)
# ------------------------------------------------------------
def check_inventarios_baseline_status() -> dict:
    """
    Regras:
    - mês atual já existe em baseline_items -> OK
    - senão:
        - existe primeiro arquivo do mês atual? -> PENDENTE
        - não existe arquivo do mês atual? -> SEM FONTE
    """
    now = datetime.now()
    current_month = f"{now.year:04d}-{now.month:02d}"

    existing_months: set[str] = set()

    if INV_DB_PATH.exists():
        with sqlite3.connect(INV_DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT snapshot_month
                FROM baseline_items
                ORDER BY snapshot_month
                """
            ).fetchall()

            existing_months = {str(r[0]) for r in rows if r[0] is not None}

    first_file_current_month = get_first_file_of_current_month()

    if current_month in existing_months:
        status = "OK"
        motivo = "O baseline do mês atual já está carregado em baseline_items."
    else:
        if first_file_current_month is not None:
            status = "PENDENTE"
            motivo = "Existe arquivo do mês atual, mas o baseline mensal ainda não foi carregado."
        else:
            status = "SEM FONTE"
            motivo = "Ainda não existe arquivo Materiais*.xlsx do mês atual para formar o baseline."

    return {
        "app": "INVENTÁRIOS - BASELINE",
        "status": status,
        "motivo": motivo,
        "mes_atual": current_month,
        "mes_existe_no_banco": "SIM" if current_month in existing_months else "NÃO",
        "primeiro_arquivo_mes_atual": first_file_current_month.name if first_file_current_month else "-",
    }


# ------------------------------------------------------------
# IMPRESSÃO DO RELATÓRIO
# ------------------------------------------------------------
def print_section(title: str, data: dict) -> None:
    print(f"[{title}]")
    for key, value in data.items():
        if key == "app":
            continue
        label = key.replace("_", " ").capitalize()
        print(f"{label:<28}: {value}")
    print()


def main() -> None:
    print("==================================================")
    print("STATUS OPERACIONAL DO HUB")
    print("==================================================")
    print()

    estoques = check_estoques_status()
    inv_week = check_inventarios_weekly_status()
    inv_base = check_inventarios_baseline_status()

    print_section("ESTOQUES", estoques)
    print_section("INVENTÁRIOS - SEMANA", inv_week)
    print_section("INVENTÁRIOS - BASELINE", inv_base)


if __name__ == "__main__":
    main()