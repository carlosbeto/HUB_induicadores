# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd


TABELA_MAPA = "map_centro_lucro_centro_custo"


def _normalizar_codigo(valor) -> str | None:
    if valor is None or pd.isna(valor):
        return None

    texto = str(valor).strip()

    if texto.endswith(".0"):
        texto = texto[:-2]

    return texto or None


def garantir_tabela_mapeamento(
    con: sqlite3.Connection,
) -> None:
    """
    Garante a existência da tabela oficial de mapeamento:

        Centro SAP + Centro de Lucro -> Centro de Custo

    A criação é idempotente e não altera registros existentes.
    """

    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABELA_MAPA} (
            codigo_centro_sap   TEXT NOT NULL,
            codigo_centro_lucro TEXT NOT NULL,
            centro_custo        TEXT,
            status_mapeamento   TEXT NOT NULL,
            origem              TEXT,
            observacao          TEXT,
            ativo               INTEGER NOT NULL DEFAULT 1,
            criado_em           TEXT NOT NULL,
            atualizado_em       TEXT NOT NULL,

            PRIMARY KEY (
                codigo_centro_sap,
                codigo_centro_lucro
            )
        );
        """
    )

    con.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_map_cl_cc_centro_custo
        ON {TABELA_MAPA} (centro_custo);
        """
    )

    con.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_map_cl_cc_status
        ON {TABELA_MAPA} (status_mapeamento);
        """
    )


def validar_dim_material(
    con: sqlite3.Connection,
) -> None:
    """
    Valida se a dim_material pode ser usada como fonte da migração.

    A migração só é aceita quando cada combinação
    Centro SAP + Centro de Lucro possui no máximo um
    Centro de Custo conhecido.
    """

    tabela_existe = con.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'dim_material';
        """
    ).fetchone()

    if not tabela_existe:
        raise ValueError(
            "Tabela dim_material não encontrada no banco. "
            "Não é possível executar a migração."
        )

    conflitos = pd.read_sql_query(
        """
        SELECT
            codigo_centro_sap,
            codigo_centro_lucro,
            COUNT(DISTINCT centro_custo) AS qtd_cc,
            GROUP_CONCAT(
                DISTINCT centro_custo
            ) AS centros_custo
        FROM dim_material
        WHERE codigo_centro_sap IS NOT NULL
          AND TRIM(codigo_centro_sap) <> ''
          AND codigo_centro_lucro IS NOT NULL
          AND TRIM(codigo_centro_lucro) <> ''
          AND centro_custo IS NOT NULL
          AND TRIM(centro_custo) <> ''
        GROUP BY
            codigo_centro_sap,
            codigo_centro_lucro
        HAVING COUNT(DISTINCT centro_custo) > 1
        ORDER BY
            codigo_centro_sap,
            codigo_centro_lucro;
        """,
        con,
    )

    if not conflitos.empty:
        raise ValueError(
            "Existem combinações Centro SAP + Centro de Lucro "
            "associadas a mais de um Centro de Custo. "
            "A migração foi interrompida. "
            f"Conflitos: {conflitos.to_dict('records')}"
        )


def carregar_origem_dim_material(
    con: sqlite3.Connection,
) -> pd.DataFrame:
    """
    Obtém uma linha por Centro SAP + Centro de Lucro.

    Quando já existe CC conhecido na dim_material, ele é usado
    como semente do novo mapa.

    Quando não existe CC, a combinação é criada como PENDENTE.
    """

    df = pd.read_sql_query(
        """
        SELECT
            codigo_centro_sap,
            codigo_centro_lucro,
            MAX(
                CASE
                    WHEN centro_custo IS NOT NULL
                     AND TRIM(centro_custo) <> ''
                    THEN centro_custo
                END
            ) AS centro_custo
        FROM dim_material
        WHERE codigo_centro_sap IS NOT NULL
          AND TRIM(codigo_centro_sap) <> ''
          AND codigo_centro_lucro IS NOT NULL
          AND TRIM(codigo_centro_lucro) <> ''
        GROUP BY
            codigo_centro_sap,
            codigo_centro_lucro
        ORDER BY
            codigo_centro_sap,
            codigo_centro_lucro;
        """,
        con,
    )

    if df.empty:
        raise ValueError(
            "Nenhuma combinação Centro SAP + Centro de Lucro "
            "foi encontrada na dim_material."
        )

    for coluna in [
        "codigo_centro_sap",
        "codigo_centro_lucro",
        "centro_custo",
    ]:
        df[coluna] = df[coluna].apply(_normalizar_codigo)

    return df


def migrar_centro_lucro_cc(
    db_path: str | Path,
) -> None:
    """
    Cria e inicializa o mapeamento oficial CL -> CC.

    Regras:
    - cria a tabela se ainda não existir;
    - valida conflitos na dim_material;
    - inclui combinações ainda inexistentes;
    - preserva integralmente registros já cadastrados;
    - não sobrescreve Centro de Custo existente;
    - novos CLs sem CC entram como PENDENTE;
    - pode ser executado mais de uma vez com segurança.
    """

    banco = Path(db_path)

    if not banco.exists():
        raise FileNotFoundError(
            f"Banco Estoques não encontrado: {banco}"
        )

    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    con = sqlite3.connect(str(banco))

    try:
        validar_dim_material(con)

        con.execute("BEGIN IMMEDIATE;")

        garantir_tabela_mapeamento(con)

        origem = carregar_origem_dim_material(con)

        total_antes = con.execute(
            f"""
            SELECT COUNT(*)
            FROM {TABELA_MAPA};
            """
        ).fetchone()[0]

        inseridos = 0

        for _, row in origem.iterrows():
            codigo_centro_sap = row["codigo_centro_sap"]
            codigo_centro_lucro = row["codigo_centro_lucro"]
            centro_custo = row["centro_custo"]

            if centro_custo:
                status = "MAPEADO"
                observacao = None
            else:
                status = "PENDENTE"
                observacao = (
                    "Centro de Custo ainda não definido"
                )

            cursor = con.execute(
                f"""
                INSERT OR IGNORE INTO {TABELA_MAPA} (
                    codigo_centro_sap,
                    codigo_centro_lucro,
                    centro_custo,
                    status_mapeamento,
                    origem,
                    observacao,
                    ativo,
                    criado_em,
                    atualizado_em
                )
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?);
                """,
                (
                    codigo_centro_sap,
                    codigo_centro_lucro,
                    centro_custo,
                    status,
                    "MIGRACAO_DIM_MATERIAL",
                    observacao,
                    agora,
                    agora,
                ),
            )

            inseridos += cursor.rowcount

        total_depois = con.execute(
            f"""
            SELECT COUNT(*)
            FROM {TABELA_MAPA};
            """
        ).fetchone()[0]

        mapeados = con.execute(
            f"""
            SELECT COUNT(*)
            FROM {TABELA_MAPA}
            WHERE ativo = 1
              AND status_mapeamento = 'MAPEADO';
            """
        ).fetchone()[0]

        pendentes = con.execute(
            f"""
            SELECT COUNT(*)
            FROM {TABELA_MAPA}
            WHERE ativo = 1
              AND status_mapeamento = 'PENDENTE';
            """
        ).fetchone()[0]

        con.commit()

    except Exception:
        con.rollback()
        raise

    finally:
        con.close()

    print()
    print("=" * 72)
    print("MIGRAÇÃO CENTRO DE LUCRO -> CENTRO DE CUSTO")
    print("=" * 72)
    print(f"Banco.......................... {banco}")
    print(f"Registros antes................ {total_antes}")
    print(f"Novos registros inseridos...... {inseridos}")
    print(f"Registros depois............... {total_depois}")
    print(f"Mapeados ativos................ {mapeados}")
    print(f"Pendentes ativos............... {pendentes}")
    print("Registros existentes........... preservados")
    print(f"Data/hora...................... {agora}")
    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Cria e inicializa o mapeamento "
            "Centro SAP + Centro de Lucro -> Centro de Custo."
        )
    )

    parser.add_argument(
        "--db",
        required=True,
        help="Banco SQLite oficial da Análise de Estoques",
    )

    args = parser.parse_args()

    migrar_centro_lucro_cc(
        db_path=args.db,
    )


if __name__ == "__main__":
    main()