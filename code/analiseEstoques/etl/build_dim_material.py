# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

from analiseEstoques.etl.load_assist import load_processamento_assist
from analiseEstoques.etl.load_familia_comercial_csv import (
    load_familia_comercial_csv,
)


def _normalizar_material(valor) -> str:
    if valor is None or pd.isna(valor):
        return ""

    texto = str(valor).strip()

    if texto.endswith(".0"):
        texto = texto[:-2]

    return texto


def load_dim_segmento_db(
    db_path: str | Path,
) -> pd.DataFrame:
    """
    Carrega somente os segmentos ativos da tabela dim_segmento.
    """

    banco = Path(db_path)

    if not banco.exists():
        raise FileNotFoundError(
            f"Banco Estoques não encontrado: {banco}"
        )

    con = sqlite3.connect(str(banco))

    try:
        dim_segmento = pd.read_sql_query(
            """
            SELECT
                segmento,
                bu,
                diretoria,
                centro_custo
            FROM dim_segmento
            WHERE ativo = 1;
            """,
            con,
        )
    finally:
        con.close()

    if dim_segmento.empty:
        raise ValueError(
            "A tabela dim_segmento está vazia ou não possui segmentos ativos."
        )

    duplicados = (
        dim_segmento.loc[
            dim_segmento["segmento"].duplicated(keep=False),
            "segmento",
        ]
        .drop_duplicates()
        .sort_values()
        .tolist()
    )

    if duplicados:
        raise ValueError(
            "A dim_segmento possui segmentos duplicados. "
            f"Segmentos: {duplicados}"
        )

    return dim_segmento


def build_dim_material(
    familia_csv: str | Path,
    db_path: str | Path,
) -> pd.DataFrame:
    """
    Constrói a dimensão definitiva de materiais.

    Fontes:
    - CSV do BI:
        material, descrição, segmento, centro de lucro,
        código do centro de lucro e código do centro SAP.
    - dim_segmento:
        BU, Diretoria e Centro de Custo.
    - ASSIST:
        processamento.

    Regras:
    - outer join entre CSV do BI e ASSIST;
    - dim_segmento é fonte oficial de BU, Diretoria e Centro de Custo;
    - materiais somente no ASSIST são preservados;
    - materiais pendentes no CSV continuam na dimensão;
    - segmento classificado e ausente na dim_segmento interrompe a carga.
    """

    familia_df = load_familia_comercial_csv(
        path=familia_csv,
    ).copy()

    assist_df = load_processamento_assist().copy()
    segmento_df = load_dim_segmento_db(db_path).copy()

    familia_df["material"] = familia_df["material"].apply(
        _normalizar_material
    )
    assist_df["material"] = assist_df["material"].apply(
        _normalizar_material
    )

    familia_df["encontrado_base_familia"] = True
    assist_df["encontrado_assist"] = True

    # A hierarquia oficial vem da dim_segmento.
    familia_df = familia_df.rename(
        columns={
            "bu": "bu_csv",
            "diretoria": "diretoria_csv",
        }
    )

    familia_df = familia_df.merge(
        segmento_df,
        on="segmento",
        how="left",
        validate="many_to_one",
    )

    # Validação: todo segmento classificado do CSV deve existir na dim_segmento.
    segmentos_sem_dim = (
        familia_df.loc[
            familia_df["segmento"].notna()
            & familia_df["bu"].isna(),
            "segmento",
        ]
        .drop_duplicates()
        .sort_values()
        .tolist()
    )

    if segmentos_sem_dim:
        raise ValueError(
            "Existem segmentos classificados no CSV do BI sem correspondência "
            "na dim_segmento. "
            f"Segmentos: {segmentos_sem_dim}"
        )

    # Validação adicional: CSV e dim_segmento não podem divergir na hierarquia.
    divergencias = familia_df[
        familia_df["segmento"].notna()
        & (
            (familia_df["bu_csv"] != familia_df["bu"])
            | (
                familia_df["diretoria_csv"]
                != familia_df["diretoria"]
            )
        )
    ][
        [
            "segmento",
            "bu_csv",
            "bu",
            "diretoria_csv",
            "diretoria",
        ]
    ].drop_duplicates()

    if not divergencias.empty:
        raise ValueError(
            "A hierarquia BU/Diretoria do CSV diverge da dim_segmento. "
            f"Divergências: {divergencias.to_dict('records')}"
        )

    dim = familia_df.merge(
        assist_df[
            [
                "material",
                "processamento_origem",
                "processamento",
                "encontrado_assist",
            ]
        ],
        on="material",
        how="outer",
        validate="one_to_one",
    )

    dim["encontrado_base_familia"] = (
        dim["encontrado_base_familia"].eq(True)
    )

    dim["encontrado_assist"] = (
        dim["encontrado_assist"].eq(True)
    )

    dim["origem_registro"] = "SOMENTE_BASE_FAMILIA"

    dim.loc[
        dim["encontrado_assist"]
        & ~dim["encontrado_base_familia"],
        "origem_registro",
    ] = "SOMENTE_ASSIST"

    dim.loc[
        dim["encontrado_assist"]
        & dim["encontrado_base_familia"],
        "origem_registro",
    ] = "AMBAS_FONTES"

    dim["status_processamento"] = "SEM_PROCESSAMENTO_ASSIST"
    dim.loc[
        dim["processamento"].notna(),
        "status_processamento",
    ] = "COM_PROCESSAMENTO_ASSIST"

    dim["status_segmento"] = "SEM_SEGMENTO"
    dim.loc[
        dim["segmento"].notna(),
        "status_segmento",
    ] = "SEGMENTO_VALIDADO"

    dim["status_centro_custo"] = "SEM_CENTRO_CUSTO"
    dim.loc[
        dim["centro_custo"].notna(),
        "status_centro_custo",
    ] = "CENTRO_CUSTO_VALIDADO"

    dim["origem_classificacao_comercial"] = pd.NA
    dim.loc[
        dim["encontrado_base_familia"],
        "origem_classificacao_comercial",
    ] = "CSV_BI_BASE_FAMILIA_COMERCIAL"

    dim["origem_processamento"] = pd.NA
    dim.loc[
        dim["encontrado_assist"],
        "origem_processamento",
    ] = "ASSIST_INTELBRAS"

    dim["origem_hierarquia"] = pd.NA
    dim.loc[
        dim["segmento"].notna(),
        "origem_hierarquia",
    ] = "DIM_SEGMENTO"

    dim.loc[
        ~dim["encontrado_base_familia"],
        "status_classificacao",
    ] = "SEM_REGISTRO_BASE_FAMILIA"

    colunas_finais = [
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
    ]

    for coluna in colunas_finais:
        if coluna not in dim.columns:
            dim[coluna] = pd.NA

    dim = (
        dim[colunas_finais]
        .sort_values("material")
        .reset_index(drop=True)
    )

    total = len(dim)
    ambas = int((dim["origem_registro"] == "AMBAS_FONTES").sum())
    somente_familia = int(
        (dim["origem_registro"] == "SOMENTE_BASE_FAMILIA").sum()
    )
    somente_assist = int(
        (dim["origem_registro"] == "SOMENTE_ASSIST").sum()
    )

    print()
    print("=" * 72)
    print("DIM_MATERIAL - MODELO DEFINITIVO")
    print("=" * 72)
    print(f"Materiais totais............... {total:,}".replace(",", "."))
    print(f"Ambas as fontes................ {ambas:,}".replace(",", "."))
    print(f"Somente Base Família........... {somente_familia:,}".replace(",", "."))
    print(f"Somente ASSIST................. {somente_assist:,}".replace(",", "."))
    print(
        "Segmentos validados............ "
        f"{(dim['status_segmento'] == 'SEGMENTO_VALIDADO').sum():,}"
        .replace(",", ".")
    )
    print(
        "Centros de custo validados..... "
        f"{(dim['status_centro_custo'] == 'CENTRO_CUSTO_VALIDADO').sum():,}"
        .replace(",", ".")
    )
    print(
        "Com processamento ASSIST....... "
        f"{dim['processamento'].notna().sum():,}"
        .replace(",", ".")
    )
    print("=" * 72)

    return dim


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--familia-csv",
        required=True,
        help="CSV oficial da Base Família Comercial",
    )

    parser.add_argument(
        "--db",
        required=True,
        help="Banco Estoques contendo a dim_segmento",
    )

    args = parser.parse_args()

    dim = build_dim_material(
        familia_csv=args.familia_csv,
        db_path=args.db,
    )

    print()
    print("Origem dos registros:")
    print(dim["origem_registro"].value_counts().to_string())

    print()
    print("Status de segmento:")
    print(dim["status_segmento"].value_counts().to_string())

    print()
    print("Status de centro de custo:")
    print(dim["status_centro_custo"].value_counts().to_string())


if __name__ == "__main__":
    main()
