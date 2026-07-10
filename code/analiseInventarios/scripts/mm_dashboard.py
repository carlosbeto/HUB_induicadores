# code/analiseInventarios/scripts/mm_dashboard.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import sqlite3

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from analiseInventarios.scripts.indicadores_inventario import (
    calcular_cobertura_semestral,
)

from analiseInventarios.scripts.mm_historico import (
    montar_historico_mensal_mm,
)


def get_semester_info_from_month(snapshot_month_ref: str) -> dict:
    """
    Recebe mês no formato YYYY-MM e devolve:
    - ano
    - mês numérico
    - semestre (1 ou 2)
    - mês inicial do semestre
    - posição do mês dentro do semestre
    - meses restantes no semestre
    """
    ano = int(snapshot_month_ref[0:4])
    mes = int(snapshot_month_ref[5:7])

    if mes <= 6:
        semestre = 1
        mes_inicio_semestre = 1
        mes_no_semestre = mes
    else:
        semestre = 2
        mes_inicio_semestre = 7
        mes_no_semestre = mes - 6

    meses_restantes = 6 - mes_no_semestre

    return {
        "ano": ano,
        "mes": mes,
        "semestre": semestre,
        "mes_inicio_semestre": mes_inicio_semestre,
        "mes_no_semestre": mes_no_semestre,
        "meses_restantes": meses_restantes,
        "baseline_semestre_month": f"{ano:04d}-{mes_inicio_semestre:02d}",
    }

def load_mm_semester_metrics(
    db_path: Path,
    deposito_sel: str,
    snapshot_month_ref: str,
    data_fim_ref: str,
) -> dict:
    """
    Calcula as métricas semestrais do depósito MM selecionado.

    Lógica:
    - baseline oficial do semestre = baseline do primeiro mês do semestre
    - mês atual = snapshot_month_ref
    - contagens consideradas = do início do semestre até o fim do mês atual
    """
    info_sem = get_semester_info_from_month(snapshot_month_ref)

    ano = info_sem["ano"]
    semestre = info_sem["semestre"]
    mes = info_sem["mes"]
    mes_no_semestre = info_sem["mes_no_semestre"]
    meses_restantes = info_sem["meses_restantes"]
    baseline_semestre_month = info_sem["baseline_semestre_month"]

    # Faixa do semestre
    if semestre == 1:
        data_inicio_semestre = f"{ano:04d}-01-01"
    else:
        data_inicio_semestre = f"{ano:04d}-07-01"

    meta_mensal_pct = 100.0 / 6.0
    meta_acumulada_pct = round(meta_mensal_pct * mes_no_semestre, 2)

    with sqlite3.connect(db_path) as conn:
        # 1) BASELINE OFICIAL DO SEMESTRE
        row_baseline_sem = conn.execute(
            """
            SELECT COUNT(DISTINCT material) AS qtd
            FROM baseline_items
            WHERE snapshot_month = ?
              AND warehouse_code = ?
            """,
            (baseline_semestre_month, deposito_sel),
        ).fetchone()

        baseline_sem_total = int(row_baseline_sem[0] or 0)

        # 2) BASELINE DO MÊS ATUAL
        row_baseline_mes = conn.execute(
            """
            SELECT COUNT(DISTINCT material) AS qtd
            FROM baseline_items
            WHERE snapshot_month = ?
              AND warehouse_code = ?
            """,
            (snapshot_month_ref, deposito_sel),
        ).fetchone()

        baseline_mes_total = int(row_baseline_mes[0] or 0)

        # 3) SKUs DO BASELINE DO SEMESTRE JÁ CONTADOS NO SEMESTRE
        row_counted_baseline_sem = conn.execute(
            """
            SELECT COUNT(DISTINCT c.material) AS qtd
            FROM counts c
            INNER JOIN baseline_items b
                ON b.material = c.material
               AND b.snapshot_month = ?
               AND b.warehouse_code = ?
            WHERE c.source_system = 'MM'
              AND c.warehouse_code = ?
              AND c.count_date >= ?
              AND c.count_date <= ?
            """,
            (
                baseline_semestre_month,
                deposito_sel,
                deposito_sel,
                data_inicio_semestre,
                data_fim_ref,
            ),
        ).fetchone()

        counted_baseline_sem = int(row_counted_baseline_sem[0] or 0)

        # 4) BASELINE ORIGINAL AINDA ATIVO NO MÊS ATUAL
        row_baseline_ativo = conn.execute(
            """
            SELECT COUNT(DISTINCT b1.material) AS qtd
            FROM baseline_items b1
            INNER JOIN baseline_items b2
                ON b2.material = b1.material
               AND b2.snapshot_month = ?
               AND b2.warehouse_code = ?
            WHERE b1.snapshot_month = ?
              AND b1.warehouse_code = ?
            """,
            (
                snapshot_month_ref,
                deposito_sel,
                baseline_semestre_month,
                deposito_sel,
            ),
        ).fetchone()

        baseline_ativo_total = int(row_baseline_ativo[0] or 0)

        # 5) QUANTOS DO BASELINE ATIVO JÁ FORAM CONTADOS
        row_counted_baseline_ativo = conn.execute(
            """
            SELECT COUNT(DISTINCT c.material) AS qtd
            FROM counts c
            INNER JOIN baseline_items b_sem
                ON b_sem.material = c.material
               AND b_sem.snapshot_month = ?
               AND b_sem.warehouse_code = ?
            INNER JOIN baseline_items b_mes
                ON b_mes.material = c.material
               AND b_mes.snapshot_month = ?
               AND b_mes.warehouse_code = ?
            WHERE c.source_system = 'MM'
              AND c.warehouse_code = ?
              AND c.count_date >= ?
              AND c.count_date <= ?
            """,
            (
                baseline_semestre_month,
                deposito_sel,
                snapshot_month_ref,
                deposito_sel,
                deposito_sel,
                data_inicio_semestre,
                data_fim_ref,
            ),
        ).fetchone()

        counted_baseline_ativo = int(row_counted_baseline_ativo[0] or 0)

        # 6) TOTAL DE SKUs DISTINTOS CONTADOS NO SEMESTRE
        row_counted_total_sem = conn.execute(
            """
            SELECT COUNT(DISTINCT material) AS qtd
            FROM counts
            WHERE source_system = 'MM'
              AND warehouse_code = ?
              AND count_date >= ?
              AND count_date <= ?
            """,
            (
                deposito_sel,
                data_inicio_semestre,
                data_fim_ref,
            ),
        ).fetchone()

        counted_total_sem = int(row_counted_total_sem[0] or 0)

    itens_novos_contados = max(counted_total_sem - counted_baseline_sem, 0)
    itens_baseline_saidos = max(baseline_sem_total - baseline_ativo_total, 0)

    cobertura_oficial_pct = None
    if baseline_sem_total > 0:
        cobertura_oficial_pct = round(
            (counted_baseline_sem / baseline_sem_total) * 100.0,
            2,
        )

    cobertura_ajustada_pct = None
    if baseline_ativo_total > 0:
        cobertura_ajustada_pct = round(
            (counted_baseline_ativo / baseline_ativo_total) * 100.0,
            2,
        )

    gap_oficial_pct = None
    if cobertura_oficial_pct is not None:
        gap_oficial_pct = round(cobertura_oficial_pct - meta_acumulada_pct, 2)

    esforco_necessario_pct = None
    if cobertura_oficial_pct is not None and meses_restantes > 0:
        restante_pct = max(100.0 - cobertura_oficial_pct, 0.0)
        esforco_necessario_pct = round(restante_pct / meses_restantes, 2)

    media_mensal_realizada_pct = None
    if cobertura_oficial_pct is not None and mes_no_semestre > 0:
        media_mensal_realizada_pct = round(cobertura_oficial_pct / mes_no_semestre, 2)

    status_semestre = "Sem leitura"
    status_delta_pct = None

    if gap_oficial_pct is not None:
        status_delta_pct = gap_oficial_pct
        if gap_oficial_pct >= 2.0:
            status_semestre = "Adiantado"
        elif gap_oficial_pct <= -2.0:
            status_semestre = "Atrasado"
        else:
            status_semestre = "Em linha"

    return {
        "ano": ano,
        "semestre": semestre,
        "mes": mes,
        "mes_no_semestre": mes_no_semestre,
        "meses_restantes": meses_restantes,
        "baseline_semestre_month": baseline_semestre_month,
        "snapshot_month_ref": snapshot_month_ref,
        "meta_mensal_pct": round(meta_mensal_pct, 2),
        "meta_acumulada_pct": meta_acumulada_pct,
        "baseline_sem_total": baseline_sem_total,
        "baseline_mes_total": baseline_mes_total,
        "baseline_ativo_total": baseline_ativo_total,
        "counted_baseline_sem": counted_baseline_sem,
        "counted_baseline_ativo": counted_baseline_ativo,
        "counted_total_sem": counted_total_sem,
        "itens_novos_contados": itens_novos_contados,
        "itens_baseline_saidos": itens_baseline_saidos,
        "cobertura_oficial_pct": cobertura_oficial_pct,
        "cobertura_ajustada_pct": cobertura_ajustada_pct,
        "gap_oficial_pct": gap_oficial_pct,
        "esforco_necessario_pct": esforco_necessario_pct,
        "media_mensal_realizada_pct": media_mensal_realizada_pct,
        "status_semestre": status_semestre,
        "status_delta_pct": status_delta_pct,
    }

def semana_legenda(year: int, week: int) -> str:
    inicio = date.fromisocalendar(year, week, 1)
    fim = inicio + timedelta(days=6)

    meses = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
             "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

    mes_ini = meses[inicio.month - 1]
    mes_fim = meses[fim.month - 1]

    if mes_ini == mes_fim:
        mes_label = mes_ini
    else:
        mes_label = f"{mes_ini}/{mes_fim}"

    return f"S{week:02d} — {inicio.strftime('%d')}→{fim.strftime('%d')} ({mes_label})"

def gerar_semanas_continuas(df_base: pd.DataFrame) -> pd.DataFrame:
    """
    Recebe um DataFrame com colunas year_iso e week_iso e devolve
    uma série contínua de semanas ISO entre a menor e a maior semana.
    """
    if df_base.empty:
        return pd.DataFrame(columns=["year_iso", "week_iso"])

    df_aux = df_base.copy()
    df_aux["year_iso"] = df_aux["year_iso"].astype(int)
    df_aux["week_iso"] = df_aux["week_iso"].astype(int)

    datas_inicio = df_aux.apply(
        lambda r: date.fromisocalendar(int(r["year_iso"]), int(r["week_iso"]), 1),
        axis=1,
    )

    data_min = datas_inicio.min()
    data_max = datas_inicio.max()

    semanas = []
    atual = data_min

    while atual <= data_max:
        y, w, _ = atual.isocalendar()
        semanas.append({"year_iso": int(y), "week_iso": int(w)})
        atual += timedelta(days=7)

    return pd.DataFrame(semanas).drop_duplicates().reset_index(drop=True)

def semanas_calendario_do_mes(ano: int, mes: int) -> list[tuple[int, int]]:
    """
    Retorna a lista de semanas ISO que possuem pelo menos 1 dia dentro do mês.
    Exemplo:
    [(2026, 9), (2026, 10), (2026, 11)]
    """
    inicio_mes = date(ano, mes, 1)

    if mes == 12:
        prox_mes = date(ano + 1, 1, 1)
    else:
        prox_mes = date(ano, mes + 1, 1)

    fim_mes = prox_mes - timedelta(days=1)

    semanas = set()
    dia_atual = inicio_mes

    while dia_atual <= fim_mes:
        y, w, _ = dia_atual.isocalendar()
        semanas.add((int(y), int(w)))
        dia_atual += timedelta(days=1)

    return sorted(semanas)

def _preparar_historico_mm(
    *,
    db_path: Path,
    deposito_sel: str,
    ano_sel: int,
    semestre_sel: int,
) -> pd.DataFrame:
    """Monta e prepara o histórico mensal utilizado por indicadores e tabela."""

    df_hist_mm = montar_historico_mensal_mm(
        db_path=db_path,
        deposito_sel=deposito_sel,
        ano=int(ano_sel),
        semestre=int(semestre_sel),
    )

    if df_hist_mm.empty:
        return df_hist_mm

    df_hist_mm["Baseline"] = pd.to_numeric(
        df_hist_mm["Baseline"], errors="coerce"
    ).fillna(0)

    df_hist_mm["Contados"] = pd.to_numeric(
        df_hist_mm["Contados"], errors="coerce"
    ).fillna(0)

    df_hist_mm["Cobertura (%)"] = pd.to_numeric(
        df_hist_mm["Cobertura (%)"], errors="coerce"
    ).fillna(0)

    df_hist_mm = calcular_cobertura_semestral(df_hist_mm)

    meta_mensal_mm = 100.0 / 6.0

    df_hist_mm["Gap mês (%)"] = (
        df_hist_mm["Cobertura (%)"] - meta_mensal_mm
    ).round(2)

    return df_hist_mm


def _render_indicadores_principais_mm(
    *,
    df_hist_mm: pd.DataFrame,
    deposito_sel: str,
    ano_sel: int,
    semestre_sel: int,
    fmt_int,
    fmt_pct,
) -> None:
    """Renderiza os dois velocímetros principais do MM."""

    with st.container(border=True):
        st.markdown(f"**Indicadores principais — {deposito_sel}**")

        df_validos = df_hist_mm[
            pd.to_numeric(
                df_hist_mm["Contados"],
                errors="coerce",
            ).fillna(0) > 0
        ].copy()

        if df_validos.empty:
            st.info("Não há meses com contagem para calcular os indicadores.")
            return

        row_atual = df_validos.iloc[-1]
        mes_referencia = str(row_atual["Mês"])

        cobertura_mes = (
            float(row_atual["Cobertura (%)"])
            if pd.notna(row_atual["Cobertura (%)"])
            else 0.0
        )

        meta_mensal = 100.0 / 6.0
        gap_mes = round(cobertura_mes - meta_mensal, 2)

        cobertura_semestre = (
            float(row_atual["Cobertura semestre (%)"])
            if pd.notna(row_atual["Cobertura semestre (%)"])
            else 0.0
        )

        meta_semestre = (
            float(row_atual["Meta semestre (%)"])
            if pd.notna(row_atual["Meta semestre (%)"])
            else 0.0
        )

        gap_semestre = round(cobertura_semestre - meta_semestre, 2)

        baseline_mes = (
            float(row_atual["Baseline"])
            if pd.notna(row_atual["Baseline"])
            else 0.0
        )

        contados_mes = (
            float(row_atual["Contados"])
            if pd.notna(row_atual["Contados"])
            else 0.0
        )

        baseline_medio = float(
            pd.to_numeric(
                df_validos["Baseline"],
                errors="coerce",
            ).fillna(0).mean()
        )

        contados_acumulados = float(
            pd.to_numeric(
                df_validos["Contados"],
                errors="coerce",
            ).fillna(0).sum()
        )

        range_max_mes = max(
            25,
            int(max(cobertura_mes, meta_mensal) * 1.35) + 1,
        )

        range_max_semestre = max(
            110,
            int(max(cobertura_semestre, meta_semestre, 100.0) * 1.20) + 1,
        )

        fig_gauge_mes = go.Figure(
            go.Indicator(
                mode="gauge+number+delta",
                value=cobertura_mes,
                number={
                    "suffix": "%",
                    "valueformat": ".2f",
                },
                delta={
                    "reference": meta_mensal,
                    "relative": False,
                    "valueformat": ".2f",
                },
                title={"text": f"Cobertura do mês — {mes_referencia}"},
                gauge={
                    "axis": {"range": [0, range_max_mes]},
                    "threshold": {
                        "line": {"width": 4},
                        "thickness": 0.9,
                        "value": meta_mensal,
                    },
                    "steps": [
                        {"range": [0, meta_mensal], "color": "#f5f5f5"},
                        {"range": [meta_mensal, range_max_mes], "color": "#eaeaea"},
                    ],
                    "bar": {"thickness": 0.35},
                },
            )
        )

        fig_gauge_mes.update_layout(
            height=360,
            margin=dict(l=20, r=20, t=60, b=20),
        )

        fig_gauge_semestre = go.Figure(
            go.Indicator(
                mode="gauge+number+delta",
                value=cobertura_semestre,
                number={
                    "suffix": "%",
                    "valueformat": ".2f",
                },
                delta={
                    "reference": meta_semestre,
                    "relative": False,
                    "valueformat": ".2f",
                },
                title={"text": "Evolução do semestre"},
                gauge={
                    "axis": {"range": [0, range_max_semestre]},
                    "threshold": {
                        "line": {"width": 4},
                        "thickness": 0.9,
                        "value": meta_semestre,
                    },
                    "steps": [
                        {"range": [0, meta_semestre], "color": "#f5f5f5"},
                        {"range": [meta_semestre, range_max_semestre], "color": "#eaeaea"},
                    ],
                    "bar": {"thickness": 0.35},
                },
            )
        )

        fig_gauge_semestre.update_layout(
            height=360,
            margin=dict(l=20, r=20, t=60, b=20),
        )

        col_g1, col_g2 = st.columns(2)

        with col_g1:
            st.plotly_chart(
                fig_gauge_mes,
                width="stretch",
                key=f"gauge_mes_mm_{deposito_sel}_{ano_sel}_{semestre_sel}",
            )

        with col_g2:
            st.plotly_chart(
                fig_gauge_semestre,
                width="stretch",
                key=f"gauge_semestre_mm_{deposito_sel}_{ano_sel}_{semestre_sel}",
            )

        comp_col1, comp_col2 = st.columns(2)

        with comp_col1:
            with st.container(border=True):
                st.markdown("**Composição da cobertura do mês**")

                m1, m2, m3 = st.columns(3)

                m1.metric("Contados no mês", fmt_int(contados_mes))
                m2.metric("Baseline do mês", fmt_int(baseline_mes))
                m3.metric("Gap vs meta mensal", fmt_pct(gap_mes))

        with comp_col2:
            with st.container(border=True):
                st.markdown("**Composição da evolução do semestre**")

                s1, s2, s3 = st.columns(3)

                s1.metric("Contados acumulados", fmt_int(contados_acumulados))
                s2.metric("Baseline médio", fmt_int(baseline_medio))
                s3.metric("Gap vs meta acumulada", fmt_pct(gap_semestre))

        st.caption(
            f"Mês de referência: {mes_referencia}. "
            f"A cobertura mensal compara os itens contados no mês com o baseline "
            f"do mesmo mês e utiliza meta mensal de {fmt_pct(meta_mensal)}. "
            f"A evolução semestral utiliza a soma dos itens contados dividida "
            f"pela média dos baselines dos meses com contagem e é comparada "
            f"com a meta acumulada de {fmt_pct(meta_semestre)}."
        )


def _render_tabela_evolucao_mm(
    *,
    df_hist_mm: pd.DataFrame,
    deposito_sel: str,
    fmt_int,
    fmt_pct,
) -> None:
    """Renderiza a tabela de evolução mensal imediatamente abaixo dos gauges."""

    with st.container(border=True):
        st.markdown(f"**Evolução mensal do semestre — {deposito_sel}**")

        if df_hist_mm.empty:
            st.info("Sem dados suficientes para montar a evolução mensal do semestre.")
            return

        df_hist_view = df_hist_mm[
            [
                "Mês",
                "Baseline",
                "Contados",
                "Cobertura (%)",
                "Gap mês (%)",
                "Meta semestre (%)",
                "Cobertura semestre (%)",
                "Gap semestre (%)",
            ]
        ].copy()

        def status_com_icone(valor):
            try:
                valor = float(valor)
            except Exception:
                return "⚪ Sem leitura"

            if valor >= 0:
                return "🟢 Em linha"
            if valor >= -5:
                return "🟡 Atenção"
            return "🔴 Atrasado"

        df_hist_view["Status mês"] = df_hist_view["Gap mês (%)"].apply(
            status_com_icone
        )
        df_hist_view["Status semestre"] = df_hist_view[
            "Gap semestre (%)"
        ].apply(status_com_icone)

        def cor_gap(valor):
            try:
                valor = float(valor)
            except Exception:
                return ""

            if valor >= 0:
                return "background-color: #d9ead3; color: #274e13;"
            if valor >= -5:
                return "background-color: #fff2cc; color: #7f6000;"
            return "background-color: #f4cccc; color: #990000;"

        def cor_status(valor):
            if "Em linha" in str(valor):
                return "background-color: #d9ead3; color: #274e13;"
            if "Atenção" in str(valor):
                return "background-color: #fff2cc; color: #7f6000;"
            if "Atrasado" in str(valor):
                return "background-color: #f4cccc; color: #990000;"
            return ""

        df_hist_style = (
            df_hist_view.style
            .format(
                {
                    "Baseline": lambda x: fmt_int(x),
                    "Contados": lambda x: fmt_int(x),
                    "Cobertura (%)": lambda x: fmt_pct(x),
                    "Gap mês (%)": lambda x: fmt_pct(x),
                    "Meta semestre (%)": lambda x: fmt_pct(x),
                    "Cobertura semestre (%)": lambda x: fmt_pct(x),
                    "Gap semestre (%)": lambda x: fmt_pct(x),
                }
            )
            .map(cor_gap, subset=["Gap mês (%)", "Gap semestre (%)"])
            .map(cor_status, subset=["Status mês", "Status semestre"])
        )

        st.dataframe(
            df_hist_style,
            width="stretch",
            hide_index=True,
        )

        df_validos = df_hist_mm[
            df_hist_mm["Contados"] > 0
        ].copy()

        if not df_validos.empty:
            baseline_medio = df_validos["Baseline"].mean()
            contados_medio = df_validos["Contados"].mean()
            cobertura_media = df_validos["Cobertura (%)"].mean()
            gap_medio = cobertura_media - (100.0 / 6.0)

            with st.container(border=True):
                st.markdown("**Resumo do período (médias)**")

                r1, r2, r3, r4 = st.columns(4)

                r1.metric("Baseline médio", fmt_int(baseline_medio))
                r2.metric("Contados médio", fmt_int(contados_medio))
                r3.metric("Cobertura média", fmt_pct(cobertura_media))
                r4.metric("GAP médio", fmt_pct(gap_medio))

        st.caption(
            "GAP mês compara a cobertura isolada do mês com a meta mensal "
            "de 16,67%. GAP semestre compara a evolução acumulada, calculada "
            "pela soma dos itens contados dividida pela média dos baselines, "
            "com a meta acumulada."
        )


def render_mm_dashboard(
    *,
    db_path: Path,
    deposito_sel: str | None,
    semana_sel: str,
    df_weeks: pd.DataFrame,
    ano_sel: int,
    semestre_sel: int,
    fmt_int,
    fmt_pct,
) -> None:
    """Renderiza a aba Contagens da semana para os depósitos MM."""

    with st.expander("Como interpretar este painel"):
        st.markdown(
            """
            **Objetivo do painel**  
            Mostrar o ritmo das contagens ao longo das semanas, como o esforço da equipe está distribuído no período selecionado e como evolui a cobertura do ciclo semestral.

            **Evolução das contagens por semana**  
            Mostra quantas linhas foram contadas em cada semana do período.  
            Ajuda a identificar semanas com maior ou menor volume de execução.

            **Resumo da semana e do mês**  
            Consolida o resultado do período selecionado:
            - Linhas contadas → volume total de contagens realizadas
            - SKUs distintos → quantidade de materiais diferentes contados
            - Operadores envolvidos → quantas pessoas atuaram na contagem
            - Depósitos envolvidos → abrangência da execução

            **Produtividade dos operadores**  
            Mostra como o volume de contagens ficou distribuído entre os colaboradores.  
            Ajuda a identificar concentração, equilíbrio da carga e liderança operacional.

            **Cobertura do ciclo semestral**  
            O painel apresenta duas leituras complementares:

            - **Cobertura oficial do semestre (velocímetro da esquerda)**  
              Representa a cobertura baseada no baseline oficial do mês de referência.  
              É a leitura formal do semestre e compara o resultado com a meta acumulada.

            - **Cobertura consolidada do período (velocímetro da direita)**  
              Representa a cobertura real do período já percorrido no semestre.  
              O cálculo considera:

              **soma dos itens contados ÷ soma dos baselines dos meses com contagem**

              Essa leitura é a principal visão operacional do painel, pois mostra o desempenho acumulado real do período.

            **Composição da cobertura consolidada**  
            - Meses com contagem → quantos meses do semestre tiveram inventário
            - Soma dos baselines → total de itens esperados nos meses considerados
            - Cobertura consolidada do período → percentual efetivo contado sobre o total esperado

            **Leitura prática**  
            - Mais linhas contadas → maior volume de execução  
            - Mais SKUs distintos → maior cobertura de itens diferentes  
            - Poucos operadores com muito volume → concentração do esforço  
            - Oscilações fortes entre semanas → variação operacional do processo  
            - Cobertura consolidada próxima ou acima da meta → operação em linha com o esperado  
            - Cobertura consolidada abaixo da meta → atraso no ciclo de inventário
            """
        )

    ctop1, ctop2, ctop3 = st.columns(3)
    ctop1.metric("Depósito selecionado", deposito_sel if deposito_sel else "-")
    ctop2.metric("Semana selecionada", semana_sel)

    inicio_semana = None
    fim_semana = None
    year_num = None
    week_num = None

    if semana_sel != "Todas":
        semana_codigo = semana_sel.split("—")[0].strip()
        week_num = int(semana_codigo.replace("S", ""))
        year_num = int(df_weeks.loc[df_weeks["label"] == semana_sel, "year_iso"].iloc[0])

        inicio_semana = date.fromisocalendar(year_num, week_num, 1)
        fim_semana = date.fromisocalendar(year_num, week_num, 7)
        periodo_real = f"{inicio_semana.strftime('%d/%m/%Y')} a {fim_semana.strftime('%d/%m/%Y')}"
    else:
        periodo_real = "-"

    ctop3.metric("Período real", periodo_real)

    # ------------------------------------------------------------
    # INFORMAÇÕES PRINCIPAIS DO MM
    # Ordem executiva:
    # 1) velocímetros;
    # 2) evolução mensal do semestre;
    # 3) demais análises.
    # ------------------------------------------------------------
    if deposito_sel:
        df_hist_mm_topo = _preparar_historico_mm(
            db_path=db_path,
            deposito_sel=deposito_sel,
            ano_sel=int(ano_sel),
            semestre_sel=int(semestre_sel),
        )

        _render_indicadores_principais_mm(
            df_hist_mm=df_hist_mm_topo,
            deposito_sel=deposito_sel,
            ano_sel=int(ano_sel),
            semestre_sel=int(semestre_sel),
            fmt_int=fmt_int,
            fmt_pct=fmt_pct,
        )

        _render_tabela_evolucao_mm(
            df_hist_mm=df_hist_mm_topo,
            deposito_sel=deposito_sel,
            fmt_int=fmt_int,
            fmt_pct=fmt_pct,
        )

        st.divider()


    if int(semestre_sel) == 1:
        semana_ini, semana_fim = 1, 26
    else:
        semana_ini, semana_fim = 27, 53

    # ------------------------------------------------------------
    # EVOLUÇÃO DAS CONTAGENS POR SEMANA
    # ------------------------------------------------------------
    st.markdown("### Evolução das contagens por semana")

    conn = sqlite3.connect(db_path)

    if semana_sel == "Todas":
        df_evol = pd.read_sql_query(
            """
            SELECT
                year_iso,
                week_iso,
                COUNT(*) AS linhas
            FROM counts
            WHERE source_system = 'MM'
              AND warehouse_code = ?
              AND year_iso = ?
              AND week_iso BETWEEN ? AND ?
            GROUP BY year_iso, week_iso
            ORDER BY year_iso, week_iso
            """,
            conn,
            params=(deposito_sel, int(ano_sel), semana_ini, semana_fim),
        )
    else:
        df_evol = pd.read_sql_query(
            """
            SELECT
                year_iso,
                week_iso,
                COUNT(*) AS linhas
            FROM counts
            WHERE source_system = 'MM'
              AND warehouse_code = ?
              AND year_iso = ?
              AND week_iso = ?
            GROUP BY year_iso, week_iso
            ORDER BY year_iso, week_iso
            """,
            conn,
            params=(deposito_sel, int(year_num), int(week_num)),
        )

    conn.close()

    if not df_evol.empty:
        if semana_sel == "Todas":
            df_evol_base = gerar_semanas_continuas(df_evol[["year_iso", "week_iso"]])

            df_evol = df_evol_base.merge(
                df_evol,
                on=["year_iso", "week_iso"],
                how="left"
            )

            df_evol["linhas"] = df_evol["linhas"].fillna(0).astype(int)

        df_evol["semana_label"] = df_evol.apply(
            lambda r: semana_legenda(int(r["year_iso"]), int(r["week_iso"])),
            axis=1,
        )

        fig_evol = px.bar(
            df_evol,
            y="semana_label",
            x="linhas",
            orientation="h",
            text="linhas",
            title=f"Linhas contadas por semana — {deposito_sel}",
        )

        fig_evol.update_traces(textposition="outside")
        fig_evol.update_layout(
            xaxis_title="Linhas contadas",
            yaxis_title="Semana",
        )

        st.plotly_chart(fig_evol, width="stretch", key=f"fig_evol_{deposito_sel}_{semana_sel}")
    else:
        if semana_sel == "Todas":
            st.info(f"Não há contagens MM para o depósito {deposito_sel} no período selecionado.")
        else:
            st.info(f"Não há contagens MM para o depósito {deposito_sel} na semana selecionada.")

    st.divider()

    try:
        conn = sqlite3.connect(db_path)

        if semana_sel == "Todas":
            df_semana = pd.read_sql_query(
                """
                SELECT
                    warehouse_code,
                    counted_by,
                    material,
                    count_date,
                    year_iso,
                    week_iso
                FROM counts
                WHERE source_system = 'MM'
                  AND warehouse_code = ?
                  AND year_iso = ?
                  AND week_iso BETWEEN ? AND ?
                """,
                conn,
                params=(deposito_sel, int(ano_sel), semana_ini, semana_fim),
            )
        else:
            df_semana = pd.read_sql_query(
                """
                SELECT
                    warehouse_code,
                    counted_by,
                    material,
                    count_date,
                    year_iso,
                    week_iso
                FROM counts
                WHERE source_system = 'MM'
                  AND warehouse_code = ?
                  AND year_iso = ?
                  AND week_iso = ?
                """,
                conn,
                params=(deposito_sel, int(year_num), int(week_num)),
            )

        conn.close()

        if df_semana.empty:
            if semana_sel == "Todas":
                st.warning(f"Sem registros para o depósito {deposito_sel}.")
            else:
                st.warning(f"Sem registros para o depósito {deposito_sel} na semana selecionada.")
        else:
            linhas = len(df_semana)
            skus = df_semana["material"].astype(str).nunique()

            # ------------------------------------------------------------
            # REFERÊNCIA SEMESTRAL MM
            # Garante que a tabela do semestre apareça também quando
            # Semana de contagem = "Todas".
            # ------------------------------------------------------------
            mm_sem = None

            df_mm_ref = df_semana.copy()

            df_mm_ref["count_date_dt"] = pd.to_datetime(
                df_mm_ref["count_date"],
                errors="coerce"
            )

            df_mm_ref = df_mm_ref.dropna(subset=["count_date_dt"]).copy()

            if not df_mm_ref.empty:
                data_fim_ref_mm = df_mm_ref["count_date_dt"].max().date()
                snapshot_month_ref_mm = data_fim_ref_mm.strftime("%Y-%m")

                mm_sem = load_mm_semester_metrics(
                    db_path=db_path,
                    deposito_sel=deposito_sel,
                    snapshot_month_ref=snapshot_month_ref_mm,
                    data_fim_ref=data_fim_ref_mm.isoformat(),
                )
            operadores = df_semana["counted_by"].astype(str).nunique()
            depositos = df_semana["warehouse_code"].astype(str).nunique()

            snapshot_month_ref = None
            skus_baseline_mes = None
            skus_acumulados_mes = None
            cobertura_acumulada_mes = None
            skus_faltantes_mes = None
            skus_novos_semana = None

            total_weeks_mes = None
            elapsed_weeks_mes = None
            ritmo_observado = None
            projecao_fechamento_skus = None
            cobertura_projetada_mes = None

            mm_sem = None

            # ------------------------------------------------------------
            # CÁLCULOS MENSAIS E SEMESTRAIS
            # ------------------------------------------------------------
            if semana_sel != "Todas" and inicio_semana is not None:
                snapshot_month_ref = inicio_semana.strftime("%Y-%m")

                conn_base = sqlite3.connect(db_path)

                row_base = conn_base.execute(
                    """
                    SELECT COUNT(DISTINCT material) AS skus_baseline
                    FROM baseline_items
                    WHERE snapshot_month = ?
                      AND warehouse_code = ?
                    """,
                    (snapshot_month_ref, deposito_sel),
                ).fetchone()

                if row_base is not None and row_base[0] is not None:
                    skus_baseline_mes = int(row_base[0])

                row_acum = conn_base.execute(
                    """
                    SELECT COUNT(DISTINCT material) AS skus_acumulados_mes
                    FROM counts
                    WHERE source_system = 'MM'
                      AND warehouse_code = ?
                      AND substr(count_date, 1, 7) = ?
                      AND (
                            year_iso < ?
                            OR (year_iso = ? AND week_iso <= ?)
                          )
                    """,
                    (
                        deposito_sel,
                        snapshot_month_ref,
                        year_num,
                        year_num,
                        week_num,
                    ),
                ).fetchone()

                if row_acum is not None and row_acum[0] is not None:
                    skus_acumulados_mes = int(row_acum[0])

                row_novos_semana = conn_base.execute(
                    """
                    SELECT COUNT(DISTINCT c.material) AS skus_novos_semana
                    FROM counts c
                    WHERE c.source_system = 'MM'
                      AND c.warehouse_code = ?
                      AND c.year_iso = ?
                      AND c.week_iso = ?
                      AND c.material NOT IN (
                            SELECT DISTINCT material
                            FROM counts
                            WHERE source_system = 'MM'
                              AND warehouse_code = ?
                              AND substr(count_date, 1, 7) = ?
                              AND (
                                    year_iso < ?
                                    OR (year_iso = ? AND week_iso < ?)
                                  )
                      )
                    """,
                    (
                        deposito_sel,
                        year_num,
                        week_num,
                        deposito_sel,
                        snapshot_month_ref,
                        year_num,
                        year_num,
                        week_num,
                    ),
                ).fetchone()

                skus_novos_semana = 0
                if row_novos_semana is not None and row_novos_semana[0] is not None:
                    skus_novos_semana = int(row_novos_semana[0])

                ano_mes = int(snapshot_month_ref[:4])
                mes_ref = int(snapshot_month_ref[5:7])

                semanas_mes_cal = semanas_calendario_do_mes(ano_mes, mes_ref)

                total_weeks_mes = len(semanas_mes_cal)
                elapsed_weeks_mes = len(
                    [
                        (y, w)
                        for (y, w) in semanas_mes_cal
                        if (y < year_num) or (y == year_num and w <= week_num)
                    ]
                )

                conn_base.close()

                if (
                    skus_baseline_mes is not None
                    and skus_baseline_mes > 0
                    and skus_acumulados_mes is not None
                ):
                    cobertura_acumulada_mes = (skus_acumulados_mes / skus_baseline_mes) * 100
                    skus_faltantes_mes = max(skus_baseline_mes - skus_acumulados_mes, 0)

                if elapsed_weeks_mes and skus_acumulados_mes is not None:
                    ritmo_observado = skus_acumulados_mes / elapsed_weeks_mes

                if (
                    ritmo_observado is not None
                    and total_weeks_mes
                    and skus_baseline_mes is not None
                    and skus_baseline_mes > 0
                ):
                    projecao_fechamento_skus = ritmo_observado * total_weeks_mes
                    cobertura_projetada_mes = (projecao_fechamento_skus / skus_baseline_mes) * 100

                mm_sem = load_mm_semester_metrics(
                    db_path=db_path,
                    deposito_sel=deposito_sel,
                    snapshot_month_ref=snapshot_month_ref,
                    data_fim_ref=fim_semana.isoformat(),
                )

            # ------------------------------------------------------------
            # QUANDO A SEMANA = "TODAS"
            # Calcula o resumo semestral usando a última data de contagem
            # disponível no período filtrado.
            # ------------------------------------------------------------
            if semana_sel == "Todas":
                df_mm_ref = df_semana.copy()

                df_mm_ref["count_date_dt"] = pd.to_datetime(
                    df_mm_ref["count_date"],
                    errors="coerce"
                )

                df_mm_ref = df_mm_ref.dropna(subset=["count_date_dt"]).copy()

                if not df_mm_ref.empty:
                    data_fim_ref = df_mm_ref["count_date_dt"].max().date()
                    snapshot_month_ref = data_fim_ref.strftime("%Y-%m")

                    mm_sem = load_mm_semester_metrics(
                        db_path=db_path,
                        deposito_sel=deposito_sel,
                        snapshot_month_ref=snapshot_month_ref,
                        data_fim_ref=data_fim_ref.isoformat(),
                    )
            with st.container(border=True):
                st.markdown("**Resumo da semana e do mês**")

                c1, c2, c3, c4, c5, c6, c7 = st.columns(7)

                c1.metric("Linhas contadas", fmt_int(linhas))
                c2.metric("SKUs distintos da semana", fmt_int(skus))
                c3.metric("Operadores envolvidos", fmt_int(operadores))
                c4.metric("Depósitos envolvidos", fmt_int(depositos))
                c5.metric("SKUs baseline mês", fmt_int(skus_baseline_mes) if skus_baseline_mes is not None else "-")
                c6.metric("SKUs acumulados mês", fmt_int(skus_acumulados_mes) if skus_acumulados_mes is not None else "-")
                c7.metric("Cobertura acumulada mês", fmt_pct(cobertura_acumulada_mes) if cobertura_acumulada_mes is not None else "-")

                if snapshot_month_ref is not None:
                    st.caption(
                        f"Baseline utilizado nesta leitura: {snapshot_month_ref} | "
                        f"Depósito: {deposito_sel}"
                    )
                else:
                    st.caption(
                        "Cobertura acumulada do mês fica disponível quando uma semana específica é selecionada."
                    )

            # ------------------------------------------------------------
            # CONTEXTO OPERACIONAL DO MÊS
            # ------------------------------------------------------------
            if semana_sel != "Todas":
                with st.container(border=True):
                    st.markdown("**Contexto operacional do mês**")

                    ctx1, ctx2, ctx3, ctx4 = st.columns(4)

                    ctx1.metric(
                        "SKUs ainda não contados no mês",
                        fmt_int(skus_faltantes_mes) if skus_faltantes_mes is not None else "-"
                    )
                    ctx2.metric("SKUs semana selecionada", fmt_int(skus))
                    ctx3.metric(
                        "Novos SKUs contados",
                        fmt_int(skus_novos_semana) if skus_novos_semana is not None else "-"
                    )
                    ctx4.metric(
                        "Ritmo necessário (SKUs / semana)",
                        fmt_int(skus_baseline_mes / total_weeks_mes) if skus_baseline_mes and total_weeks_mes else "-"
                    )

                    if total_weeks_mes:
                        st.caption(
                            f"O ritmo necessário considera {fmt_int(total_weeks_mes)} semana(s) de calendário no mês {snapshot_month_ref}."
                        )

            # ------------------------------------------------------------
            # PROGRESSO DO CICLO MENSAL
            # ------------------------------------------------------------
            if (
                semana_sel != "Todas"
                and cobertura_acumulada_mes is not None
                and skus_baseline_mes is not None
                and skus_acumulados_mes is not None
            ):
                with st.container(border=True):
                    st.markdown("**Progresso do ciclo mensal**")

                    progresso = max(0.0, min(cobertura_acumulada_mes / 100, 1.0))
                    st.progress(progresso)

                    st.caption(
                        f"{fmt_int(skus_acumulados_mes)} de {fmt_int(skus_baseline_mes)} SKUs "
                        f"já cobertos no mês ({fmt_pct(cobertura_acumulada_mes)})."
                    )

            # ------------------------------------------------------------
            # PROJEÇÃO DE FECHAMENTO DO MÊS
            # ------------------------------------------------------------
            if semana_sel != "Todas":
                with st.container(border=True):
                    st.markdown("**Projeção de fechamento do mês**")

                    p1, p2, p3, p4 = st.columns(4)

                    p1.metric("Semanas do mês", fmt_int(total_weeks_mes) if total_weeks_mes is not None else "-")
                    p2.metric("Semanas decorridas", fmt_int(elapsed_weeks_mes) if elapsed_weeks_mes is not None else "-")
                    p3.metric("Ritmo observado (SKUs / semana)", fmt_int(ritmo_observado) if ritmo_observado is not None else "-")
                    p4.metric("Projeção fechamento mês", fmt_int(projecao_fechamento_skus) if projecao_fechamento_skus is not None else "-")

                    if cobertura_projetada_mes is not None:
                        st.caption(
                            f"Se o ritmo atual for mantido, a projeção é de "
                            f"{fmt_int(projecao_fechamento_skus)} SKUs no mês, "
                            f"equivalente a {fmt_pct(cobertura_projetada_mes)} do baseline."
                        )
                    else:
                        st.caption("Ainda não há base suficiente para projetar o fechamento do mês.")

            # ------------------------------------------------------------
            # EVOLUÇÃO DO MÊS
            # ------------------------------------------------------------
            if semana_sel != "Todas" and snapshot_month_ref is not None:
                with st.container(border=True):
                    st.markdown("**Evolução do mês**")

                    conn_mes = sqlite3.connect(db_path)

                    df_mes_evol = pd.read_sql_query(
                        """
                        SELECT
                            c.year_iso,
                            c.week_iso,
                            COUNT(DISTINCT c.material) AS skus_semana
                        FROM counts c
                        WHERE c.source_system = 'MM'
                          AND c.warehouse_code = ?
                          AND substr(c.count_date, 1, 7) = ?
                          AND c.material NOT IN (
                                SELECT DISTINCT c2.material
                                FROM counts c2
                                WHERE c2.source_system = 'MM'
                                  AND c2.warehouse_code = c.warehouse_code
                                  AND substr(c2.count_date, 1, 7) = ?
                                  AND (
                                        c2.year_iso < c.year_iso
                                        OR (c2.year_iso = c.year_iso AND c2.week_iso < c.week_iso)
                                      )
                          )
                        GROUP BY c.year_iso, c.week_iso
                        ORDER BY c.year_iso, c.week_iso
                        """,
                        conn_mes,
                        params=(deposito_sel, snapshot_month_ref, snapshot_month_ref),
                    )

                    conn_mes.close()

                    if not df_mes_evol.empty:
                        ano_mes = int(snapshot_month_ref[:4])
                        mes_ref = int(snapshot_month_ref[5:7])

                        semanas_mes_cal = semanas_calendario_do_mes(ano_mes, mes_ref)
                        df_cal = pd.DataFrame(semanas_mes_cal, columns=["year_iso", "week_iso"])

                        df_mes_evol = df_cal.merge(
                            df_mes_evol,
                            on=["year_iso", "week_iso"],
                            how="left"
                        )

                        df_mes_evol["skus_semana"] = df_mes_evol["skus_semana"].fillna(0).astype(int)

                        # Limita a evolução até a semana selecionada
                        df_mes_evol = df_mes_evol[
                            (df_mes_evol["year_iso"] < year_num)
                            | (
                                (df_mes_evol["year_iso"] == year_num)
                                & (df_mes_evol["week_iso"] <= week_num)
                            )
                        ].copy()

                        df_mes_evol["skus_acumulados_mes"] = df_mes_evol["skus_semana"].cumsum()

                        df_mes_evol["semana_label"] = df_mes_evol.apply(
                            lambda r: semana_legenda(int(r["year_iso"]), int(r["week_iso"])),
                            axis=1,
                        )

                        fig_mes = go.Figure()

                        fig_mes.add_trace(
                            go.Bar(
                                x=df_mes_evol["semana_label"],
                                y=df_mes_evol["skus_semana"],
                                name="Novos SKUs da semana",
                                text=df_mes_evol["skus_semana"].apply(
                                    lambda x: "" if x == 0 else f"Sem: {int(x)}"
                                ),
                                textposition="outside",
                                opacity=1,
                                textfont=dict(color="black", size=11),
                                marker=dict(color="#2b65c5")
                            )
                        )

                        fig_mes.add_trace(
                            go.Scatter(
                                x=df_mes_evol["semana_label"],
                                y=df_mes_evol["skus_acumulados_mes"],
                                name="Acumulado no mês",
                                mode="lines+markers+text",
                                text=df_mes_evol["skus_acumulados_mes"].apply(
                                    lambda x: "" if x == 0 else f"Acum: {int(x)}"
                                ),
                                textposition="top center",
                                textfont=dict(color="black", size=11),
                                yaxis="y2",
                                line=dict(width=3, dash="dot", color="#d62728"),
                                marker=dict(size=8, color="#d62728"),
                            )
                        )

                        fig_mes.update_layout(
                            xaxis_title="Semana",
                            yaxis=dict(title="Novos SKUs da semana"),
                            yaxis2=dict(
                                title="SKUs acumulados no mês",
                                overlaying="y",
                                side="right",
                            ),
                            legend=dict(
                                orientation="h",
                                yanchor="bottom",
                                y=1.02,
                                xanchor="center",
                                x=0.5,
                            ),
                            height=450,
                            margin=dict(t=80),
                        )

                        st.plotly_chart(
                            fig_mes,
                            width="stretch",
                            key=f"fig_evol_mes_{deposito_sel}_{snapshot_month_ref}_{week_num}"
                        )

                        st.caption(
                            "As barras mostram os novos SKUs contados em cada semana do mês. "
                            "A linha mostra o acumulado mensal até a semana selecionada."
                        )
                    else:
                        st.info("Não há contagens suficientes para montar a evolução do mês.")


            # ------------------------------------------------------------
            # ------------------------------------------------------------
            # LEITURAS OPERACIONAIS COMPLEMENTARES DO SEMESTRE
            # Os indicadores principais e a tabela já foram exibidos no topo.
            # ------------------------------------------------------------
            if mm_sem is not None:
                with st.container(border=True):
                    st.markdown("**Leitura operacional do semestre**")

                    esforco_total_sem = (
                        int(mm_sem["counted_baseline_sem"]) + int(mm_sem["itens_novos_contados"])
                        if mm_sem["counted_baseline_sem"] is not None and mm_sem["itens_novos_contados"] is not None
                        else None
                    )

                    o1, o2, o3, o4, o5 = st.columns(5)

                    o1.metric("Baseline semestre", fmt_int(mm_sem["baseline_sem_total"]))
                    o2.metric("Baseline ainda ativo", fmt_int(mm_sem["baseline_ativo_total"]))
                    o3.metric(
                        "Cobertura ajustada",
                        fmt_pct(mm_sem["cobertura_ajustada_pct"])
                        if mm_sem["cobertura_ajustada_pct"] is not None else "-"
                    )
                    o4.metric(
                        "Esforço total da operação",
                        fmt_int(esforco_total_sem) if esforco_total_sem is not None else "-"
                    )
                    o5.metric(
                        "Esforço necessário / mês",
                        fmt_pct(mm_sem["esforco_necessario_pct"])
                        if mm_sem["esforco_necessario_pct"] is not None else "-"
                    )

                    st.caption(
                        "Cobertura ajustada considera apenas os itens do baseline do semestre "
                        "que ainda permanecem no baseline do mês atual. "
                        "Esforço total da operação soma os itens do baseline já contados com os itens novos contados fora do baseline."
                    )

                    if esforco_total_sem is not None and mm_sem["baseline_sem_total"] is not None:
                        if mm_sem["counted_baseline_sem"] >= mm_sem["baseline_sem_total"]:
                            leitura = "Meta oficial atingida"
                        elif esforco_total_sem >= mm_sem["baseline_sem_total"]:
                            leitura = "Meta operacional atingida (com esforço em itens fora do baseline)"
                        else:
                            leitura = "Abaixo da meta"

                        st.markdown(f"**Leitura:** {leitura}")

                with st.container(border=True):
                    st.markdown("**Dinâmica do estoque no semestre (itens fora do baseline oficial)**")

                    d1, d2, d3, d4 = st.columns(4)

                    d1.metric("Itens novos (fora do baseline)", fmt_int(mm_sem["itens_novos_contados"]))
                    d2.metric("Itens do baseline que saíram", fmt_int(mm_sem["itens_baseline_saidos"]))
                    d3.metric("Itens do baseline já contados", fmt_int(mm_sem["counted_baseline_sem"]))
                    d4.metric(
                        "Média mensal realizada (baseline)",
                        fmt_pct(mm_sem["media_mensal_realizada_pct"])
                        if mm_sem["media_mensal_realizada_pct"] is not None else "-"
                    )

                    st.caption(
                        "Este bloco mostra apenas itens fora do baseline oficial do semestre. "
                        "Eles não impactam a cobertura do velocímetro."
                    )

                st.divider()

            # ------------------------------------------------------------
            # CONTAGENS POR DEPÓSITO
            # ------------------------------------------------------------
            st.markdown("### Contagens por depósito")

            df_dep = (
                df_semana.groupby("warehouse_code")
                .size()
                .reset_index(name="linhas")
            )

            fig_dep = px.bar(
                df_dep,
                x="warehouse_code",
                y="linhas",
                text="linhas",
                title="Quantidade de contagens por depósito",
            )

            fig_dep.update_traces(textposition="outside")
            fig_dep.update_layout(
                xaxis_title="Depósito",
                yaxis_title="Linhas contadas",
            )

            st.plotly_chart(fig_dep, width="stretch", key=f"fig_dep_{deposito_sel}_{semana_sel}")

            st.divider()

            # ------------------------------------------------------------
            # PRODUTIVIDADE / RANKING DE OPERADORES
            # ------------------------------------------------------------
            df_op_base = df_semana.copy()

            df_op_base["counted_by"] = (
                df_op_base["counted_by"]
                .fillna("Não informado")
                .astype(str)
                .str.strip()
            )

            df_op_base.loc[
                df_op_base["counted_by"] == "",
                "counted_by"
            ] = "Não informado"

            df_op = (
                df_op_base.groupby("counted_by", as_index=False)
                .agg(
                    linhas=("material", "count"),
                    skus=("material", "nunique"),
                )
                .sort_values(
                    ["linhas", "skus", "counted_by"],
                    ascending=[False, False, True]
                )
                .reset_index(drop=True)
            )

            total_operadores_ativos = int(len(df_op))
            total_linhas_contadas = int(df_op["linhas"].sum())
            media_linhas_por_operador = (
                total_linhas_contadas / total_operadores_ativos
                if total_operadores_ativos > 0 else 0
            )

            operador_lider = str(df_op.iloc[0]["counted_by"])
            linhas_operador_lider = int(df_op.iloc[0]["linhas"])
            skus_operador_lider = int(df_op.iloc[0]["skus"])

            pct_lider = (
                (linhas_operador_lider / total_linhas_contadas) * 100
                if total_linhas_contadas > 0 else 0
            )

            with st.container(border=True):
                st.markdown("**Produtividade dos operadores**")

                k1, k2, k3, k4 = st.columns(4)

                with k1:
                    st.metric("Operadores ativos", fmt_int(total_operadores_ativos))

                with k2:
                    st.metric(
                        "Média linhas / operador",
                        f"{media_linhas_por_operador:.1f}".replace(".", ",")
                    )

                with k3:
                    st.metric("Operador líder", operador_lider)

                with k4:
                    st.metric("Participação do líder", fmt_pct(pct_lider))

                st.caption(
                    f"Líder do período: {operador_lider} | "
                    f"{fmt_int(linhas_operador_lider)} linhas contadas | "
                    f"{fmt_int(skus_operador_lider)} SKUs distintos."
                )

            df_top3 = df_op.head(3).copy()

            with st.container(border=True):
                st.markdown("**Destaques do período**")

                if len(df_top3) == 1:
                    row1 = df_top3.iloc[0]
                    pct1 = (
                        (float(row1["linhas"]) / total_linhas_contadas) * 100
                        if total_linhas_contadas > 0 else 0
                    )

                    st.metric("Operador líder", str(row1["counted_by"]))
                    st.caption(
                        f"{fmt_int(row1['linhas'])} linhas | "
                        f"{fmt_int(row1['skus'])} SKUs | "
                        f"{fmt_pct(pct1)} do período"
                    )
                    st.caption(
                        "Neste período, a contagem ficou concentrada em um único operador."
                    )

                else:
                    top_col1, top_col2, top_col3 = st.columns(3)

                    if len(df_top3) >= 1:
                        row1 = df_top3.iloc[0]
                        pct1 = (
                            (float(row1["linhas"]) / total_linhas_contadas) * 100
                            if total_linhas_contadas > 0 else 0
                        )

                        with top_col1:
                            st.markdown("**Top 1**")
                            st.metric("Operador", str(row1["counted_by"]))
                            st.caption(
                                f"{fmt_int(row1['linhas'])} linhas | "
                                f"{fmt_int(row1['skus'])} SKUs | "
                                f"{fmt_pct(pct1)} do período"
                            )

                    if len(df_top3) >= 2:
                        row2 = df_top3.iloc[1]
                        pct2 = (
                            (float(row2["linhas"]) / total_linhas_contadas) * 100
                            if total_linhas_contadas > 0 else 0
                        )

                        with top_col2:
                            st.markdown("**Top 2**")
                            st.metric("Operador", str(row2["counted_by"]))
                            st.caption(
                                f"{fmt_int(row2['linhas'])} linhas | "
                                f"{fmt_int(row2['skus'])} SKUs | "
                                f"{fmt_pct(pct2)} do período"
                            )

                    if len(df_top3) >= 3:
                        row3 = df_top3.iloc[2]
                        pct3 = (
                            (float(row3["linhas"]) / total_linhas_contadas) * 100
                            if total_linhas_contadas > 0 else 0
                        )

                        with top_col3:
                            st.markdown("**Top 3**")
                            st.metric("Operador", str(row3["counted_by"]))
                            st.caption(
                                f"{fmt_int(row3['linhas'])} linhas | "
                                f"{fmt_int(row3['skus'])} SKUs | "
                                f"{fmt_pct(pct3)} do período"
                            )

            st.divider()

            df_op_plot = df_op.sort_values("linhas", ascending=True).copy()

            fig_op = px.bar(
                df_op_plot,
                x="linhas",
                y="counted_by",
                orientation="h",
                text="linhas",
                title="Contagens por operador",
            )

            fig_op.update_traces(textposition="outside")
            fig_op.update_layout(
                xaxis_title="Linhas contadas",
                yaxis_title="Operador",
                height=max(320, len(df_op_plot) * 55),
            )

            st.plotly_chart(fig_op, width="stretch", key=f"fig_op_{deposito_sel}_{semana_sel}")

            df_op_view = df_op.copy()
            df_op_view["pct_participacao"] = (
                (df_op_view["linhas"] / total_linhas_contadas) * 100
            ).round(2)

            df_op_view.insert(0, "ranking", range(1, len(df_op_view) + 1))

            df_op_view = df_op_view.rename(
                columns={
                    "counted_by": "operador",
                    "linhas": "linhas_contadas",
                    "skus": "skus_distintos",
                }
            )

            df_op_view["linhas_contadas"] = df_op_view["linhas_contadas"].map(fmt_int)
            df_op_view["skus_distintos"] = df_op_view["skus_distintos"].map(fmt_int)
            df_op_view["pct_participacao"] = df_op_view["pct_participacao"].map(fmt_pct)

            df_op_view = df_op_view[
                [
                    "ranking",
                    "operador",
                    "linhas_contadas",
                    "skus_distintos",
                    "pct_participacao",
                ]
            ]

            st.data_editor(
                df_op_view,
                width="stretch",
                hide_index=True,
                disabled=True,
                column_config={
                    "ranking": st.column_config.NumberColumn(
                        "Rank",
                        help="Posição no ranking de produtividade do período",
                        width="small"
                    ),
                    "operador": st.column_config.TextColumn(
                        "Operador",
                        width="medium"
                    ),
                    "linhas_contadas": st.column_config.NumberColumn(
                        "Linhas contadas",
                        help="Quantidade total de linhas contadas pelo operador",
                        width="medium"
                    ),
                    "skus_distintos": st.column_config.NumberColumn(
                        "SKUs distintos",
                        help="Quantidade de materiais diferentes contados",
                        width="medium"
                    ),
                    "pct_participacao": st.column_config.ProgressColumn(
                        "Participação",
                        help="Participação do operador no total de contagens do período",
                        format="%.2f%%",
                        min_value=0,
                        max_value=100,
                    ),
                }
            )

    except Exception as e:
        st.error(f"Erro ao montar a aba de contagens da semana: {e}")

