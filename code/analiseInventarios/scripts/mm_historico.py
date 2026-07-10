# code/analiseInventarios/scripts/mm_historico.py
# -*- coding: utf-8 -*-

"""
Preparação do histórico mensal dos depósitos MM.

Este módulo concentra a consulta e a montagem da base mensal utilizada
pelo dashboard MM. A regra de cobertura semestral permanece centralizada
em indicadores_inventario.py.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pandas as pd


def montar_historico_mensal_mm(
    *,
    db_path: Path,
    deposito_sel: str,
    ano: int,
    semestre: int,
) -> pd.DataFrame:
    """
    Monta o histórico mensal de baseline e contagens do depósito MM.

    Parâmetros:
    - db_path: caminho do banco SQLite de Inventários;
    - deposito_sel: depósito MM selecionado, como MAST ou MASR;
    - ano: ano de referência;
    - semestre: 1 para janeiro-junho ou 2 para julho-dezembro.

    Retorna um DataFrame com:
    - Mês
    - Baseline
    - Contados
    - Cobertura (%)
    - Meta acumulada (%)
    - Gap (%)

    Esta função preserva a estrutura já utilizada pelo dashboard.
    A evolução semestral consolidada é calculada posteriormente por
    calcular_cobertura_semestral().
    """

    if semestre not in (1, 2):
        raise ValueError("semestre deve ser 1 ou 2.")

    meses = range(1, 7) if semestre == 1 else range(7, 13)
    linhas_hist: list[dict[str, object]] = []

    with sqlite3.connect(db_path) as conn:
        for idx_mes, mes in enumerate(meses, start=1):
            snapshot_month = f"{int(ano):04d}-{int(mes):02d}"

            row_baseline = conn.execute(
                """
                SELECT COUNT(DISTINCT material) AS skus_baseline
                FROM baseline_items
                WHERE snapshot_month = ?
                  AND warehouse_code = ?
                """,
                (snapshot_month, deposito_sel),
            ).fetchone()

            baseline = int(row_baseline[0] or 0) if row_baseline else 0

            row_contados = conn.execute(
                """
                SELECT COUNT(DISTINCT material) AS skus_contados
                FROM counts
                WHERE source_system = 'MM'
                  AND warehouse_code = ?
                  AND substr(count_date, 1, 7) = ?
                """,
                (deposito_sel, snapshot_month),
            ).fetchone()

            contados = int(row_contados[0] or 0) if row_contados else 0

            cobertura = None
            if baseline > 0:
                cobertura = (contados / baseline) * 100

            meta_acumulada = round((100 / 6) * idx_mes, 2)

            gap = None
            if cobertura is not None:
                gap = round(cobertura - meta_acumulada, 2)

            linhas_hist.append(
                {
                    "Mês": snapshot_month,
                    "Baseline": baseline,
                    "Contados": contados,
                    "Cobertura (%)": cobertura,
                    "Meta acumulada (%)": meta_acumulada,
                    "Gap (%)": gap,
                }
            )

    return pd.DataFrame(linhas_hist)
