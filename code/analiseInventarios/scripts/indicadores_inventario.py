# code/analiseInventarios/scripts/indicadores_inventario.py
# -*- coding: utf-8 -*-

"""
Centraliza as regras de negócio dos indicadores de Inventários.

Os dashboards MM e EWM devem consumir os cálculos deste módulo,
evitando fórmulas duplicadas em diferentes telas.
"""

from __future__ import annotations

import pandas as pd


def calcular_cobertura_semestral(df: pd.DataFrame) -> pd.DataFrame:


    """
    Calcula os indicadores semestrais atualmente usados pelo dashboard.

    Nesta etapa, a regra existente é apenas centralizada neste módulo,
    sem alteração do resultado apresentado.

    Colunas geradas:
    - Baseline acumulado
    - Contados acumulado
    - Cobertura semestre (%)
    - Mês índice
    - Meta semestre (%)
    - Gap semestre (%)
    """

    resultado = df.copy()

    resultado["Baseline"] = pd.to_numeric(
        resultado["Baseline"],
        errors="coerce",
    ).fillna(0)

    resultado["Contados"] = pd.to_numeric(
        resultado["Contados"],
        errors="coerce",
    ).fillna(0)

    # Soma dos itens contados no semestre
    resultado["Contados acumulado"] = resultado["Contados"].cumsum()

    # Média do baseline até o mês corrente
    resultado["Baseline médio"] = (
        resultado["Baseline"]
        .expanding()
        .mean()
    )

    resultado["Cobertura semestre (%)"] = 0.0

    mask_baseline = resultado["Baseline médio"] > 0

    resultado.loc[mask_baseline, "Cobertura semestre (%)"] = (
        resultado.loc[mask_baseline, "Contados acumulado"]
        / resultado.loc[mask_baseline, "Baseline médio"]
        * 100
    ).round(2)

    resultado["Mês índice"] = range(1, len(resultado) + 1)

    meta_mensal = 100 / 6

    resultado["Meta semestre (%)"] = (
        resultado["Mês índice"] * meta_mensal
    ).round(2)

    resultado["Gap semestre (%)"] = (
    resultado["Cobertura semestre (%)"]
    - resultado["Meta semestre (%)"]
    ).round(2)

    return resultado