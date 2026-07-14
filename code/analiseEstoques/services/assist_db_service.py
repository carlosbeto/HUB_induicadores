# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path

import pyodbc
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_PATH = PROJECT_ROOT / ".env"

if ENV_PATH.exists():
    load_dotenv(ENV_PATH)


def conectar_assist() -> pyodbc.Connection:
    """Cria e retorna uma conexão com o banco ASSIST."""

    driver = os.getenv("ASSIST_DRIVER", "SQL Server").strip()
    server = os.getenv("ASSIST_SERVER", "").strip()
    database = os.getenv("ASSIST_DATABASE", "").strip()
    user = os.getenv("ASSIST_USER", "").strip()
    password = os.getenv("ASSIST_PASSWORD", "").strip()

    faltantes: list[str] = []

    if not server:
        faltantes.append("ASSIST_SERVER")
    if not database:
        faltantes.append("ASSIST_DATABASE")
    if not user:
        faltantes.append("ASSIST_USER")
    if not password:
        faltantes.append("ASSIST_PASSWORD")

    if faltantes:
        raise RuntimeError(
            "Configuração ASSIST incompleta no arquivo .env. "
            f"Variáveis faltantes: {', '.join(faltantes)}"
        )

    connection_string = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={user};"
        f"PWD={{{password}}};"
        "TrustServerCertificate=yes;"
    )

    return pyodbc.connect(connection_string, timeout=30)
