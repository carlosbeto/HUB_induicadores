# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from analiseEstoques.etl.build_dim_material import build_dim_material


def _to_sql_value(value):
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(value, bool):
        return int(value)

    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass

    return value


def garantir_schema_dim_material(con: sqlite3.Connection) -> None:
    """
    Garante o schema definitivo da dim_material.

    Como a tabela já existe na DEV, adiciona somente as colunas ausentes.
    Não remove dados nem colunas legadas nesta etapa.
    """

    con.execute("""
        CREATE TABLE IF NOT EXISTS dim_material (
            material TEXT PRIMARY KEY
        );
    """)

    colunas_esperadas = {
        "descricao_material": "TEXT",
        "segmento": "TEXT",
        "bu": "TEXT",
        "diretoria": "TEXT",
        "centro_custo": "TEXT",
        "centro_lucro": "TEXT",
        "codigo_centro_lucro": "TEXT",
        "codigo_centro_sap": "TEXT",
        "status_classificacao": "TEXT",
        "status_segmento": "TEXT",
        "status_centro_custo": "TEXT",
        "processamento_origem": "TEXT",
        "processamento": "TEXT",
        "status_processamento": "TEXT",
        "encontrado_base_familia": "INTEGER NOT NULL DEFAULT 0",
        "encontrado_assist": "INTEGER NOT NULL DEFAULT 0",
        "origem_registro": "TEXT",
        "origem_classificacao_comercial": "TEXT",
        "origem_processamento": "TEXT",
        "origem_hierarquia": "TEXT",
        "source_familia_file": "TEXT",
        "source_familia_last_modified": "TEXT",
        "source_assist_database": "TEXT",
        "load_ts": "TEXT",
    }

    existentes = {
        row[1]
        for row in con.execute(
            "PRAGMA table_info(dim_material);"
        ).fetchall()
    }

    for coluna, tipo in colunas_esperadas.items():
        if coluna not in existentes:
            con.execute(
                f"ALTER TABLE dim_material ADD COLUMN {coluna} {tipo};"
            )

    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_dim_material_segmento
        ON dim_material (segmento);
    """)

    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_dim_material_bu
        ON dim_material (bu);
    """)

    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_dim_material_diretoria
        ON dim_material (diretoria);
    """)

    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_dim_material_centro_custo
        ON dim_material (centro_custo);
    """)

    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_dim_material_processamento
        ON dim_material (processamento);
    """)

    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_dim_material_origem_registro
        ON dim_material (origem_registro);
    """)


def carregar_dim_material(
    db_path: str | Path,
    familia_csv: str | Path,
) -> None:
    banco = Path(db_path)
    csv_bi = Path(familia_csv)

    if not banco.exists():
        raise FileNotFoundError(
            f"Banco Estoques não encontrado: {banco}"
        )

    if not csv_bi.exists():
        raise FileNotFoundError(
            f"CSV da Base Família Comercial não encontrado: {csv_bi}"
        )

    dim = build_dim_material(
        familia_csv=csv_bi,
        db_path=banco,
    ).copy()

    load_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    familia_last_modified = datetime.fromtimestamp(
        csv_bi.stat().st_mtime
    ).strftime("%Y-%m-%d %H:%M:%S")

    dim["source_familia_file"] = csv_bi.name
    dim["source_familia_last_modified"] = familia_last_modified
    dim["source_assist_database"] = "ASSIST_INTELBRAS"
    dim["load_ts"] = load_ts

    colunas = [
        "material",
        "descricao_material",
        "segmento",
        "bu",
        "diretoria",
        "centro_custo",
        "centro_lucro",
        "codigo_centro_lucro",
        "codigo_centro_sap",
        "status_classificacao",
        "status_segmento",
        "status_centro_custo",
        "processamento_origem",
        "processamento",
        "status_processamento",
        "encontrado_base_familia",
        "encontrado_assist",
        "origem_registro",
        "origem_classificacao_comercial",
        "origem_processamento",
        "origem_hierarquia",
        "source_familia_file",
        "source_familia_last_modified",
        "source_assist_database",
        "load_ts",
    ]

    rows = [
        tuple(_to_sql_value(value) for value in row)
        for row in dim[colunas].itertuples(index=False, name=None)
    ]

    con = sqlite3.connect(str(banco))

    try:
        garantir_schema_dim_material(con)
        con.execute("BEGIN IMMEDIATE;")

        # A carga integral ocorre dentro da mesma transação.
        # Em qualquer erro, o rollback preserva a versão anterior.
        con.execute("DELETE FROM dim_material;")

        placeholders = ",".join(["?"] * len(colunas))
        nomes_colunas = ",".join(colunas)

        con.executemany(
            f"""
            INSERT INTO dim_material (
                {nomes_colunas}
            )
            VALUES ({placeholders});
            """,
            rows,
        )

        total_db = con.execute(
            "SELECT COUNT(*) FROM dim_material;"
        ).fetchone()[0]

        if total_db != len(dim):
            raise RuntimeError(
                "Falha de consistência após a carga da dim_material. "
                f"DataFrame={len(dim)} | Banco={total_db}"
            )

        con.commit()

    except Exception:
        con.rollback()
        raise

    finally:
        con.close()

    print()
    print("=" * 72)
    print("CARGA DA DIM_MATERIAL DEFINITIVA CONCLUÍDA")
    print("=" * 72)
    print(f"Banco.......................... {banco}")
    print(f"Registros gravados............. {len(dim):,}".replace(",", "."))
    print(f"Fonte comercial................ {csv_bi.name}")
    print("Fonte processamento............ ASSIST_INTELBRAS")
    print("Fonte hierarquia comercial..... CSV_BI_BASE_FAMILIA_COMERCIAL")
    print("Fonte centro de custo........... map_centro_lucro_centro_custo")
    print(f"Data/hora da carga............. {load_ts}")
    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--db",
        required=True,
        help="Caminho do Banco Estoques estoque.sqlite",
    )

    parser.add_argument(
        "--familia-csv",
        required=True,
        help="CSV oficial da Base Família Comercial",
    )

    args = parser.parse_args()

    carregar_dim_material(
        db_path=args.db,
        familia_csv=args.familia_csv,
    )


if __name__ == "__main__":
    main()
