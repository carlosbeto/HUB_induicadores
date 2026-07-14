# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd


def _normalizar_texto(valor) -> str | None:
    if valor is None or pd.isna(valor):
        return None

    texto = " ".join(str(valor).strip().split())
    return texto or None


def _normalizar_codigo(valor) -> str | None:
    if valor is None or pd.isna(valor):
        return None

    texto = str(valor).strip()

    if texto.endswith(".0"):
        texto = texto[:-2]

    return texto or None


def ler_segmentos_excel(
    arquivo_excel: str | Path,
    aba: str = "Centro de Custo",
) -> pd.DataFrame:
    """
    Lê a aba 'Centro de Custo' apenas para a carga inicial da dim_segmento.

    Layout real identificado:
    - coluna C: BU
    - coluna D: Diretoria
    - coluna E: Segmento
    - coluna F: Centro de Custo

    A planilha não possui cabeçalho formal nessa região.
    """

    arquivo = Path(arquivo_excel)

    if not arquivo.exists():
        raise FileNotFoundError(
            f"Arquivo Excel não encontrado: {arquivo}"
        )

    df = pd.read_excel(
        arquivo,
        sheet_name=aba,
        header=None,
        usecols="C:F",
        dtype="string",
    )

    df.columns = [
        "bu",
        "diretoria",
        "segmento",
        "centro_custo",
    ]

    # BU e Diretoria aparecem apenas na primeira linha de cada bloco.
    df["bu"] = df["bu"].ffill()
    df["diretoria"] = df["diretoria"].ffill()

    for coluna in ["bu", "diretoria", "segmento"]:
        df[coluna] = df[coluna].apply(_normalizar_texto)

    df["centro_custo"] = df["centro_custo"].apply(
        _normalizar_codigo
    )

    df = df.dropna(
        subset=[
            "bu",
            "diretoria",
            "segmento",
            "centro_custo",
        ]
    ).copy()

    if df.empty:
        raise ValueError(
            "Nenhum registro válido encontrado nas colunas C:F "
            "da aba 'Centro de Custo'."
        )

    conflitos = (
        df.groupby("segmento", dropna=False)
        .agg(
            qtd_bu=("bu", "nunique"),
            qtd_diretoria=("diretoria", "nunique"),
            qtd_centro_custo=("centro_custo", "nunique"),
        )
    )

    conflitos = conflitos[
        (conflitos["qtd_bu"] > 1)
        | (conflitos["qtd_diretoria"] > 1)
        | (conflitos["qtd_centro_custo"] > 1)
    ]

    if not conflitos.empty:
        detalhes = (
            df[df["segmento"].isin(conflitos.index)]
            .sort_values(
                ["segmento", "bu", "diretoria", "centro_custo"]
            )
            .to_dict("records")
        )

        raise ValueError(
            "Foram encontrados conflitos na tabela de segmentos. "
            f"Detalhes: {detalhes}"
        )

    df = (
        df.drop_duplicates(subset=["segmento"], keep="last")
        .sort_values(["bu", "diretoria", "segmento"])
        .reset_index(drop=True)
    )

    return df


def garantir_tabelas(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS dim_segmento (
            segmento TEXT PRIMARY KEY,
            bu TEXT NOT NULL,
            diretoria TEXT NOT NULL,
            centro_custo TEXT NOT NULL,
            ativo INTEGER NOT NULL DEFAULT 1,
            vigencia_inicio TEXT NOT NULL,
            vigencia_fim TEXT,
            source_file TEXT,
            source_sheet TEXT,
            source_last_modified TEXT,
            load_ts TEXT
        );
    """)

    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_dim_segmento_bu
        ON dim_segmento (bu);
    """)

    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_dim_segmento_diretoria
        ON dim_segmento (diretoria);
    """)

    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_dim_segmento_centro_custo
        ON dim_segmento (centro_custo);
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS dim_segmento_historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            segmento TEXT NOT NULL,
            bu_anterior TEXT,
            bu_nova TEXT,
            diretoria_anterior TEXT,
            diretoria_nova TEXT,
            centro_custo_anterior TEXT,
            centro_custo_novo TEXT,
            alterado_em TEXT NOT NULL,
            source_file TEXT
        );
    """)


def carregar_dim_segmento(
    db_path: str | Path,
    arquivo_excel: str | Path,
    aba: str = "Centro de Custo",
) -> None:
    db_path = Path(db_path)
    arquivo_excel = Path(arquivo_excel)

    if not db_path.exists():
        raise FileNotFoundError(
            f"Banco Estoques não encontrado: {db_path}"
        )

    df = ler_segmentos_excel(
        arquivo_excel=arquivo_excel,
        aba=aba,
    )

    agora = datetime.now()
    load_ts = agora.strftime("%Y-%m-%d %H:%M:%S")
    vigencia_inicio = agora.strftime("%Y-%m-%d")
    source_last_modified = datetime.fromtimestamp(
        arquivo_excel.stat().st_mtime
    ).strftime("%Y-%m-%d %H:%M:%S")

    con = sqlite3.connect(str(db_path))

    try:
        garantir_tabelas(con)

        existentes = pd.read_sql_query(
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

        novos = df.merge(
            existentes,
            on="segmento",
            how="left",
            suffixes=("_novo", "_anterior"),
        )

        alterados = novos[
            novos["bu_anterior"].notna()
            & (
                (novos["bu_novo"] != novos["bu_anterior"])
                | (
                    novos["diretoria_novo"]
                    != novos["diretoria_anterior"]
                )
                | (
                    novos["centro_custo_novo"]
                    != novos["centro_custo_anterior"]
                )
            )
        ].copy()

        con.execute("BEGIN IMMEDIATE;")

        for _, row in alterados.iterrows():
            con.execute(
                """
                INSERT INTO dim_segmento_historico (
                    segmento,
                    bu_anterior,
                    bu_nova,
                    diretoria_anterior,
                    diretoria_nova,
                    centro_custo_anterior,
                    centro_custo_novo,
                    alterado_em,
                    source_file
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    row["segmento"],
                    row["bu_anterior"],
                    row["bu_novo"],
                    row["diretoria_anterior"],
                    row["diretoria_novo"],
                    row["centro_custo_anterior"],
                    row["centro_custo_novo"],
                    load_ts,
                    arquivo_excel.name,
                ),
            )

        segmentos_atuais = set(df["segmento"])
        segmentos_existentes = set(existentes["segmento"])

        removidos = segmentos_existentes - segmentos_atuais

        for segmento in removidos:
            con.execute(
                """
                UPDATE dim_segmento
                SET
                    ativo = 0,
                    vigencia_fim = ?,
                    load_ts = ?
                WHERE segmento = ?;
                """,
                (
                    vigencia_inicio,
                    load_ts,
                    segmento,
                ),
            )

        for _, row in df.iterrows():
            con.execute(
                """
                INSERT INTO dim_segmento (
                    segmento,
                    bu,
                    diretoria,
                    centro_custo,
                    ativo,
                    vigencia_inicio,
                    vigencia_fim,
                    source_file,
                    source_sheet,
                    source_last_modified,
                    load_ts
                )
                VALUES (?, ?, ?, ?, 1, ?, NULL, ?, ?, ?, ?)
                ON CONFLICT(segmento) DO UPDATE SET
                    bu = excluded.bu,
                    diretoria = excluded.diretoria,
                    centro_custo = excluded.centro_custo,
                    ativo = 1,
                    vigencia_inicio = CASE
                        WHEN dim_segmento.ativo = 0
                        THEN excluded.vigencia_inicio
                        ELSE dim_segmento.vigencia_inicio
                    END,
                    vigencia_fim = NULL,
                    source_file = excluded.source_file,
                    source_sheet = excluded.source_sheet,
                    source_last_modified = excluded.source_last_modified,
                    load_ts = excluded.load_ts;
                """,
                (
                    row["segmento"],
                    row["bu"],
                    row["diretoria"],
                    row["centro_custo"],
                    vigencia_inicio,
                    arquivo_excel.name,
                    aba,
                    source_last_modified,
                    load_ts,
                ),
            )

        total_ativos = con.execute(
            """
            SELECT COUNT(*)
            FROM dim_segmento
            WHERE ativo = 1;
            """
        ).fetchone()[0]

        if total_ativos != len(df):
            raise RuntimeError(
                "Falha de consistência na carga da dim_segmento. "
                f"DataFrame={len(df)} | Banco={total_ativos}"
            )

        con.commit()

    except Exception:
        con.rollback()
        raise

    finally:
        con.close()

    print("=" * 72)
    print("CARGA DA DIM_SEGMENTO CONCLUÍDA")
    print("=" * 72)
    print(f"Segmentos ativos............... {len(df)}")
    print(f"Alterações registradas......... {len(alterados)}")
    print(f"Segmentos inativados........... {len(removidos)}")
    print(f"BUs distintas.................. {df['bu'].nunique()}")
    print(f"Diretorias distintas........... {df['diretoria'].nunique()}")
    print(
        "Centros de custo distintos.... "
        f"{df['centro_custo'].nunique()}"
    )
    print(f"Banco.......................... {db_path}")
    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--db",
        required=True,
        help="Caminho do Banco Estoques estoque.sqlite",
    )

    parser.add_argument(
        "--centro-custo-file",
        required=True,
        help="Arquivo Excel com a aba Centro de Custo",
    )

    parser.add_argument(
        "--sheet",
        default="Centro de Custo",
        help="Nome da aba de apoio",
    )

    args = parser.parse_args()

    carregar_dim_segmento(
        db_path=args.db,
        arquivo_excel=args.centro_custo_file,
        aba=args.sheet,
    )


if __name__ == "__main__":
    main()
