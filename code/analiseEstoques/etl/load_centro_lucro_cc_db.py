from pathlib import Path
import sqlite3

import pandas as pd


def load_centro_lucro_cc_db(
    db_path: str | Path,
) -> pd.DataFrame:
    """
    Carrega o mapeamento ativo entre Centro SAP, Centro de Lucro
    e Centro de Custo.

    Fonte:
    - map_centro_lucro_centro_custo

    Chave de negócio:
    - codigo_centro_sap
    - codigo_centro_lucro

    Regras:
    - somente registros ativos são considerados;
    - Centro SAP e Centro de Lucro são obrigatórios;
    - Centro de Custo pode estar vazio para mapeamentos pendentes;
    - não pode existir mais de um registro ativo para a mesma
      combinação Centro SAP + Centro de Lucro.
    """

    banco = Path(db_path)

    if not banco.exists():
        raise FileNotFoundError(
            f"Banco Estoques não encontrado: {banco}"
        )

    with sqlite3.connect(str(banco)) as con:
        tabela_existe = con.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'map_centro_lucro_centro_custo';
            """
        ).fetchone()

        if not tabela_existe:
            raise ValueError(
                "Tabela map_centro_lucro_centro_custo não encontrada "
                f"no banco: {banco}"
            )

        mapa = pd.read_sql_query(
            """
            SELECT
                codigo_centro_sap,
                codigo_centro_lucro,
                centro_custo,
                status_mapeamento
            FROM map_centro_lucro_centro_custo
            WHERE ativo = 1;
            """,
            con,
        )

    if mapa.empty:
        raise ValueError(
            "A tabela map_centro_lucro_centro_custo está vazia "
            "ou não possui registros ativos."
        )

    # Normalização das chaves.
    for coluna in [
        "codigo_centro_sap",
        "codigo_centro_lucro",
        "centro_custo",
        "status_mapeamento",
    ]:
        mapa[coluna] = (
            mapa[coluna]
            .astype("string")
            .str.strip()
            .replace("", pd.NA)
        )

    # As duas partes da chave são obrigatórias.
    chave_invalida = (
        mapa["codigo_centro_sap"].isna()
        | mapa["codigo_centro_lucro"].isna()
    )

    if chave_invalida.any():
        qtd = int(chave_invalida.sum())

        raise ValueError(
            "map_centro_lucro_centro_custo possui registros ativos "
            "sem codigo_centro_sap ou codigo_centro_lucro. "
            f"Quantidade: {qtd}"
        )

    # A chave Centro SAP + Centro de Lucro precisa ser única.
    duplicados = (
        mapa.loc[
            mapa.duplicated(
                subset=[
                    "codigo_centro_sap",
                    "codigo_centro_lucro",
                ],
                keep=False,
            ),
            [
                "codigo_centro_sap",
                "codigo_centro_lucro",
            ],
        ]
        .drop_duplicates()
        .sort_values(
            [
                "codigo_centro_sap",
                "codigo_centro_lucro",
            ]
        )
    )

    if not duplicados.empty:
        raise ValueError(
            "map_centro_lucro_centro_custo possui chaves ativas "
            "duplicadas. "
            f"Combinações: {duplicados.to_dict('records')}"
        )

    return (
        mapa[
            [
                "codigo_centro_sap",
                "codigo_centro_lucro",
                "centro_custo",
                "status_mapeamento",
            ]
        ]
        .sort_values(
            [
                "codigo_centro_sap",
                "codigo_centro_lucro",
            ]
        )
        .reset_index(drop=True)
    )