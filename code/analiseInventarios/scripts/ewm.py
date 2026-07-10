# code/analiseInventarios/scripts/ewm.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import date
from pathlib import Path
import math
import sqlite3

from analiseInventarios.scripts.indicadores_inventario import (
    calcular_cobertura_semestral,
)

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


def render_ewm_tab(
    *,
    df_ewm: pd.DataFrame,
    db_path: Path,
    ano_sel: int,
    semestre_sel: int,
    fmt_int,
    fmt_pct,
) -> None:
    """Renderiza a aba Cobertura EWM (WEPV).

    Este módulo foi extraído de streamlit_app.py para isolar a lógica EWM
    da lógica MM. Nesta etapa, a refatoração preserva o comportamento atual.
    """

    st.subheader("Cobertura acumulada do inventário EWM")

    if df_ewm.empty:
        st.warning("Arquivo de cobertura EWM não encontrado ou vazio.")
        st.stop()

    df_plot = df_ewm.copy()

    df_plot["year_iso"] = pd.to_numeric(df_plot["year_iso"], errors="coerce")
    df_plot["week_iso"] = pd.to_numeric(df_plot["week_iso"], errors="coerce")
    df_plot["sku_acumulado"] = pd.to_numeric(df_plot["sku_acumulado"], errors="coerce")
    df_plot["cobertura_percentual"] = pd.to_numeric(df_plot["cobertura_percentual"], errors="coerce")
    df_plot["baseline_skus"] = pd.to_numeric(df_plot["baseline_skus"], errors="coerce")

    df_plot = df_plot.dropna(subset=["year_iso", "week_iso", "sku_acumulado", "cobertura_percentual"]).copy()
    df_plot["year_iso"] = df_plot["year_iso"].astype(int)
    df_plot["week_iso"] = df_plot["week_iso"].astype(int)

    if int(semestre_sel) == 1:
        semana_ini, semana_fim = 1, 26
    else:
        semana_ini, semana_fim = 27, 53

    df_plot = df_plot[
        (df_plot["year_iso"] == int(ano_sel)) &
        (df_plot["week_iso"] >= semana_ini) &
        (df_plot["week_iso"] <= semana_fim)
    ].copy()

    df_plot = df_plot.sort_values(["year_iso", "week_iso"]).reset_index(drop=True)

    if df_plot.empty:
        st.info("Não há dados EWM para o período selecionado.")
        st.stop()

    df_plot["semana_label"] = (
        "S" + df_plot["week_iso"].astype(str).str.zfill(2)
    )

    skus_novos_ultima_semana = None
    if len(df_plot) >= 2:
        sku_acumulado_ultima = float(df_plot.iloc[-1]["sku_acumulado"])
        sku_acumulado_semana_anterior = float(df_plot.iloc[-2]["sku_acumulado"])
        skus_novos_ultima_semana = int(sku_acumulado_ultima - sku_acumulado_semana_anterior)
    elif len(df_plot) == 1:
        skus_novos_ultima_semana = int(float(df_plot.iloc[-1]["sku_acumulado"]))

    conn_ewm = sqlite3.connect(db_path)

    row_snapshot_month = conn_ewm.execute(
        """
        SELECT MAX(snapshot_month)
        FROM baseline_items
        WHERE warehouse_code = 'WEPV'
        """
    ).fetchone()

    snapshot_month_ewm = None
    if row_snapshot_month is not None and row_snapshot_month[0] is not None:
        snapshot_month_ewm = str(row_snapshot_month[0])

    skus_baseline_ewm = None
    skus_contados_ewm = None
    skus_contados_total_ewm = None
    cobertura_real_ewm = None
    skus_restantes_ewm = None

    # Novos itens fora do baseline
    itens_novos_ultima_semana_ewm = None
    itens_novos_mes_ewm = None
    itens_novos_semestre_ewm = None

    if snapshot_month_ewm is not None:
        # ------------------------------------------------------------
        # 1) BASELINE OFICIAL DO CICLO
        # ------------------------------------------------------------
        row_baseline = conn_ewm.execute(
            """
            SELECT COUNT(DISTINCT material) AS skus_baseline
            FROM baseline_items
            WHERE snapshot_month = ?
              AND warehouse_code = 'WEPV'
            """,
            (snapshot_month_ewm,),
        ).fetchone()

        if row_baseline is not None and row_baseline[0] is not None:
            skus_baseline_ewm = int(row_baseline[0])

        # ------------------------------------------------------------
        # 2) SKUs DO BASELINE JÁ CONTADOS NO CICLO INTEIRO
        # ------------------------------------------------------------
        row_contados_baseline = conn_ewm.execute(
            """
            SELECT COUNT(DISTINCT c.material) AS skus_contados_baseline
            FROM counts c
            INNER JOIN baseline_items b
                ON b.material = c.material
               AND b.snapshot_month = ?
               AND b.warehouse_code = 'WEPV'
            WHERE c.source_system = 'EWM'
            """,
            (snapshot_month_ewm,),
        ).fetchone()

        if row_contados_baseline is not None and row_contados_baseline[0] is not None:
            skus_contados_ewm = int(row_contados_baseline[0])

        # ------------------------------------------------------------
        # 3) SKUs TOTAIS CONTADOS NO CICLO (inclui fora do baseline)
        # ------------------------------------------------------------
        row_contados_total = conn_ewm.execute(
            """
            SELECT COUNT(DISTINCT material) AS skus_contados_total
            FROM counts
            WHERE source_system = 'EWM'
            """
        ).fetchone()

        if row_contados_total is not None and row_contados_total[0] is not None:
            skus_contados_total_ewm = int(row_contados_total[0])

        # ------------------------------------------------------------
        # 4) COBERTURA REAL DO CICLO
        # ------------------------------------------------------------
        if (
            skus_baseline_ewm is not None
            and skus_baseline_ewm > 0
            and skus_contados_ewm is not None
        ):
            cobertura_real_ewm = round(
                (skus_contados_ewm / skus_baseline_ewm) * 100,
                2
            )
            skus_restantes_ewm = max(skus_baseline_ewm - skus_contados_ewm, 0)

    conn_ewm.close()

    # ------------------------------------------------------------
    # IMPACTO DO ESTOQUE VIVO - EWM
    # Regra correta:
    # item novo = material contado que NÃO pertence ao baseline oficial
    # ------------------------------------------------------------
    if not df_plot.empty:
        conn_live_ewm = sqlite3.connect(db_path)

        # Última semana visível no gráfico/filtro
        semana_atual_ewm = int(df_plot.iloc[-1]["week_iso"])
        ano_atual_ewm = int(df_plot.iloc[-1]["year_iso"])

        # ------------------------------------------------------------
        # 5) ITENS NOVOS DA ÚLTIMA SEMANA
        # ------------------------------------------------------------
        if snapshot_month_ewm is not None:
            row_novos_ultima_semana = conn_live_ewm.execute(
                """
                SELECT COUNT(DISTINCT c.material)
                FROM counts c
                LEFT JOIN baseline_items b
                    ON b.material = c.material
                   AND b.snapshot_month = ?
                   AND b.warehouse_code = 'WEPV'
                WHERE c.source_system = 'EWM'
                  AND c.year_iso = ?
                  AND c.week_iso = ?
                  AND b.material IS NULL
                """,
                (snapshot_month_ewm, ano_atual_ewm, semana_atual_ewm),
            ).fetchone()

            if row_novos_ultima_semana is not None and row_novos_ultima_semana[0] is not None:
                itens_novos_ultima_semana_ewm = int(row_novos_ultima_semana[0])

        # ------------------------------------------------------------
        # 6) ITENS NOVOS DO MÊS DE REFERÊNCIA
        # ------------------------------------------------------------
        mes_ref_ewm = snapshot_month_ewm

        if mes_ref_ewm is None and not df_plot.empty:
            ano_ref_mes = int(df_plot.iloc[-1]["year_iso"])
            semana_ref_mes = int(df_plot.iloc[-1]["week_iso"])
            data_ref_mes = date.fromisocalendar(ano_ref_mes, semana_ref_mes, 1)
            mes_ref_ewm = data_ref_mes.strftime("%Y-%m")

        if snapshot_month_ewm is not None and mes_ref_ewm is not None:
            row_mes_ewm = conn_live_ewm.execute(
                """
                SELECT COUNT(DISTINCT c.material)
                FROM counts c
                LEFT JOIN baseline_items b
                    ON b.material = c.material
                   AND b.snapshot_month = ?
                   AND b.warehouse_code = 'WEPV'
                WHERE c.source_system = 'EWM'
                  AND substr(c.count_date, 1, 7) = ?
                  AND b.material IS NULL
                """,
                (snapshot_month_ewm, mes_ref_ewm),
            ).fetchone()

            if row_mes_ewm is not None and row_mes_ewm[0] is not None:
                itens_novos_mes_ewm = int(row_mes_ewm[0])

        # ------------------------------------------------------------
        # 7) ITENS NOVOS DO SEMESTRE
        # ------------------------------------------------------------
        if int(semestre_sel) == 1:
            meses_semestre_ewm = [1, 2, 3, 4, 5, 6]
        else:
            meses_semestre_ewm = [7, 8, 9, 10, 11, 12]

        meses_semestre_str_ewm = [
            f"{int(ano_sel):04d}-{m:02d}" for m in meses_semestre_ewm
        ]

        placeholders_ewm = ",".join(["?"] * len(meses_semestre_str_ewm))

        if snapshot_month_ewm is not None:
            query_semestre_ewm = f"""
                SELECT COUNT(DISTINCT c.material)
                FROM counts c
                LEFT JOIN baseline_items b
                    ON b.material = c.material
                   AND b.snapshot_month = ?
                   AND b.warehouse_code = 'WEPV'
                WHERE c.source_system = 'EWM'
                  AND substr(c.count_date, 1, 7) IN ({placeholders_ewm})
                  AND b.material IS NULL
            """

            params_semestre_ewm = [snapshot_month_ewm] + meses_semestre_str_ewm

            row_semestre_ewm = conn_live_ewm.execute(
                query_semestre_ewm,
                params_semestre_ewm,
            ).fetchone()

            if row_semestre_ewm is not None and row_semestre_ewm[0] is not None:
                itens_novos_semestre_ewm = int(row_semestre_ewm[0])

        conn_live_ewm.close()

    # ------------------------------------------------------------
    # IMPACTO DO ESTOQUE VIVO NA CONTAGEM — EWM
    # ------------------------------------------------------------
    with st.container(border=True):
        st.markdown("**Impacto do estoque vivo na contagem — EWM**")

        esforco_total_ewm = None
        if skus_contados_ewm is not None and itens_novos_semestre_ewm is not None:
            esforco_total_ewm = skus_contados_ewm + itens_novos_semestre_ewm

        df_impacto_ewm = pd.DataFrame(
            [
                {
                    "Indicador": "Itens novos (última semana)",
                    "Valor": int(itens_novos_ultima_semana_ewm) if itens_novos_ultima_semana_ewm is not None else 0,
                },
                {
                    "Indicador": "Itens novos (mês)",
                    "Valor": int(itens_novos_mes_ewm) if itens_novos_mes_ewm is not None else 0,
                },
                {
                    "Indicador": "Itens novos (semestre)",
                    "Valor": int(itens_novos_semestre_ewm) if itens_novos_semestre_ewm is not None else 0,
                },
                {
                    "Indicador": "Esforço total da operação",
                    "Valor": int(esforco_total_ewm) if esforco_total_ewm is not None else 0,
                },
            ]
        )

        df_impacto_ewm = df_impacto_ewm.sort_values("Valor", ascending=True)

        fig_impacto_ewm = px.bar(
            df_impacto_ewm,
            x="Valor",
            y="Indicador",
            orientation="h",
            text=df_impacto_ewm["Valor"].map(lambda x: f"{x:,.0f}".replace(",", ".")),
            title="Indicadores operacionais do ciclo EWM",
        )

        fig_impacto_ewm.update_traces(textposition="outside")
        fig_impacto_ewm.update_layout(
            xaxis_title="Quantidade",
            yaxis_title="",
            height=360,
        )

        st.plotly_chart(
            fig_impacto_ewm,
            width="stretch",
            key=f"fig_impacto_ewm_{ano_sel}_{semestre_sel}"
        )

        st.caption(
            "Itens novos são materiais contados que não pertenciam ao baseline oficial do ciclo. "
            "Eles consomem esforço operacional, mas não aumentam a cobertura oficial do semestre. "
            "Esforço total da operação = itens do baseline já contados + itens novos contados no semestre."
        )

    if (
        skus_baseline_ewm is not None
        and skus_baseline_ewm > 0
        and skus_contados_ewm is not None
        and cobertura_real_ewm is not None
    ):
        with st.container(border=True):
            st.markdown("**Progresso do ciclo EWM**")

            progresso_ewm = max(0.0, min(cobertura_real_ewm / 100, 1.0))
            st.progress(progresso_ewm)

            st.caption(
                f"{fmt_int(skus_contados_ewm)} de {fmt_int(skus_baseline_ewm)} SKUs "
                f"já cobertos no ciclo ({fmt_pct(cobertura_real_ewm)})."
            )

    if skus_baseline_ewm is not None and skus_baseline_ewm > 0:
        df_plot["cobertura_real_ciclo"] = (
            df_plot["sku_acumulado"] / skus_baseline_ewm * 100
        )
    else:
        df_plot["cobertura_real_ciclo"] = df_plot["cobertura_percentual"]

    fig_ewm = px.line(
        df_plot,
        x="semana_label",
        y="cobertura_real_ciclo",
        markers=True,
        title="Evolução da cobertura do ciclo EWM",
    )

    fig_ewm.add_hline(
        y=100,
        line_dash="dash",
        line_width=1,
        line_color="gray",
        annotation_text="Meta do ciclo: 100%",
        annotation_position="top left",
    )

    if not df_plot.empty:
        ultima_linha = df_plot.iloc[-1]

        fig_ewm.add_annotation(
            x=ultima_linha["semana_label"],
            y=float(ultima_linha["cobertura_real_ciclo"]),
            text=f"{float(ultima_linha['cobertura_real_ciclo']):.2f}%".replace(".", ","),
            showarrow=True,
            arrowhead=1,
            yshift=10,
        )

    fig_ewm.update_layout(
        xaxis_title="Semana",
        yaxis_title="Cobertura do ciclo (%)",
        yaxis=dict(range=[0, 100]),
    )

    st.plotly_chart(fig_ewm, width="stretch", key=f"fig_ewm_{ano_sel}_{semestre_sel}")

    df_plot["skus_novos_semana"] = (
        df_plot["sku_acumulado"].diff().fillna(df_plot["sku_acumulado"])
    )
    df_plot["skus_novos_semana"] = df_plot["skus_novos_semana"].astype(int)

    melhor_semana = None
    maior_ganho = None

    if not df_plot.empty and "skus_novos_semana" in df_plot.columns:
        idx_melhor_semana = df_plot["skus_novos_semana"].idxmax()
        melhor_semana = str(df_plot.loc[idx_melhor_semana, "semana_label"])
        maior_ganho = int(df_plot.loc[idx_melhor_semana, "skus_novos_semana"])

    if melhor_semana is not None:
        st.metric(
            "Melhor semana de avanço",
            melhor_semana,
            delta=f"{fmt_int(maior_ganho)} SKUs"
        )

    # ------------------------------------------------------------
    # VISÃO OPERACIONAL OTIMIZADA DO CICLO EWM
    # ------------------------------------------------------------
    df_chart = df_plot.tail(12).copy()

    df_chart["acumulado"] = df_chart["sku_acumulado"]
    df_chart["novos"] = df_chart["skus_novos_semana"]
    df_chart["semana"] = df_chart["semana_label"]

    if skus_baseline_ewm is not None and skus_baseline_ewm > 0:
        meta_por_semana = skus_baseline_ewm / 26

        df_chart["meta_acumulada"] = [
            meta_por_semana * i for i in range(1, len(df_chart) + 1)
        ]
    else:
        df_chart["meta_acumulada"] = None

    fig_operacional = go.Figure()

    fig_operacional.add_trace(
        go.Bar(
            x=df_chart["semana"],
            y=df_chart["novos"],
            name="SKUs contados na semana",
            text=df_chart["novos"],
            textposition="outside",
        )
    )

    fig_operacional.add_trace(
        go.Scatter(
            x=df_chart["semana"],
            y=df_chart["acumulado"],
            name="SKUs Acumulado real",
            mode="lines+markers",
            yaxis="y2",
        )
    )

    if df_chart["meta_acumulada"].notna().all():
        fig_operacional.add_trace(
            go.Scatter(
                x=df_chart["semana"],
                y=df_chart["meta_acumulada"],
                name="Meta acumulada do cíclo",
                mode="lines",
                line=dict(dash="dash"),
                yaxis="y2",
            )
        )

        ultima_semana_meta = df_chart.iloc[-1]["semana"]
        ultimo_valor_meta = float(df_chart.iloc[-1]["meta_acumulada"])

        fig_operacional.add_annotation(
            x=ultima_semana_meta,
            y=ultimo_valor_meta,
            text=f"Meta: {fmt_int(ultimo_valor_meta)}",
            showarrow=True,
            arrowhead=1,
            yshift=-18,
        )

    fig_operacional.update_layout(
        title="Rítmo de contagem do ciclo EWM (últimas 12 semanas)",
        xaxis_title="Semana",
        yaxis=dict(
            title="SKUs novos na semana",
        ),
        yaxis2=dict(
            title="Acumulado do ciclo",
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
        height=420,
    )

    st.plotly_chart(
        fig_operacional,
        width="stretch",
        key=f"fig_operacional_ewm_{ano_sel}_{semestre_sel}"
    )

    with st.expander("Ver tabela detalhada das semanas"):
        df_ewm_view = df_plot.copy()

        df_ewm_view["Semana"] = (
            "S" + df_ewm_view["week_iso"].astype(str).str.zfill(2)
        )

        df_ewm_view = df_ewm_view[
            [
                "Semana",
                "sku_acumulado",
                "cobertura_percentual",
                "skus_novos_semana",
            ]
        ].copy()

        df_ewm_view = df_ewm_view.rename(
            columns={
                "sku_acumulado": "SKU acumulado",
                "cobertura_percentual": "Cobertura acumulada",
                "skus_novos_semana": "SKUs novos na semana",
            }
        )

        df_ewm_view["SKU acumulado"] = df_ewm_view["SKU acumulado"].map(fmt_int)
        df_ewm_view["Cobertura acumulada"] = df_ewm_view["Cobertura acumulada"].map(fmt_pct)
        df_ewm_view["SKUs novos na semana"] = df_ewm_view["SKUs novos na semana"].map(fmt_int)

        st.dataframe(df_ewm_view, width="stretch", hide_index=True)

    st.divider()

    # ------------------------------------------------------------
    # EVOLUÇÃO MENSAL DO SEMESTRE - EWM / WEPV
    # ------------------------------------------------------------
    with st.container(border=True):
        st.markdown("**Evolução mensal do semestre — WEPV**")

        ano_ref = int(ano_sel)

        if int(semestre_sel) == 1:
            meses_semestre = [1, 2, 3, 4, 5, 6]
        else:
            meses_semestre = [7, 8, 9, 10, 11, 12]

        linhas_hist_ewm = []

        conn_hist_ewm = sqlite3.connect(db_path)

        for idx_mes, mes_hist in enumerate(meses_semestre, start=1):
            snapshot_month_hist = f"{ano_ref:04d}-{mes_hist:02d}"

            row_base_hist = conn_hist_ewm.execute(
                """
                SELECT COUNT(DISTINCT material) AS skus_baseline
                FROM baseline_items
                WHERE snapshot_month = ?
                  AND warehouse_code = 'WEPV'
                """,
                (snapshot_month_hist,),
            ).fetchone()

            baseline_hist = 0
            if row_base_hist is not None and row_base_hist[0] is not None:
                baseline_hist = int(row_base_hist[0])

            row_cont_hist = conn_hist_ewm.execute(
                """
                SELECT COUNT(DISTINCT material) AS skus_contados
                FROM counts
                WHERE warehouse_code NOT LIKE 'M%'
                  AND substr(count_date, 1, 7) = ?
                """,
                (snapshot_month_hist,),
            ).fetchone()

            contados_hist = 0
            if row_cont_hist is not None and row_cont_hist[0] is not None:
                contados_hist = int(row_cont_hist[0])

            cobertura_hist = None
            if baseline_hist > 0:
                cobertura_hist = (contados_hist / baseline_hist) * 100

            meta_acumulada_hist = round((100 / 6) * idx_mes, 2)

            gap_hist = None
            if cobertura_hist is not None:
                gap_hist = round(cobertura_hist - meta_acumulada_hist, 2)

            linhas_hist_ewm.append(
                {
                    "Mês": snapshot_month_hist,
                    "Baseline": baseline_hist,
                    "Contados": contados_hist,
                    "Cobertura (%)": cobertura_hist,
                    "Meta acumulada (%)": meta_acumulada_hist,
                    "Gap (%)": gap_hist,
                }
            )

        conn_hist_ewm.close()

        df_hist_ewm = pd.DataFrame(linhas_hist_ewm)

        if not df_hist_ewm.empty:
            # ------------------------------------------------------------
            # PREPARA A TABELA MENSAL DO WEPV COM DUAS LEITURAS DE GAP
            #
            # GAP mês:
            #   cobertura do mês - meta mensal de 16,66%
            #
            # GAP semestre:
            #   cobertura acumulada até o mês - meta acumulada até o mês
            # ------------------------------------------------------------

            META_MENSAL_EWM = 100 / 6

            df_hist_ewm["Baseline"] = pd.to_numeric(
                df_hist_ewm["Baseline"], errors="coerce"
            ).fillna(0)

            df_hist_ewm["Contados"] = pd.to_numeric(
                df_hist_ewm["Contados"], errors="coerce"
            ).fillna(0)

            df_hist_ewm["Cobertura (%)"] = pd.to_numeric(
                df_hist_ewm["Cobertura (%)"], errors="coerce"
            ).fillna(0)

            df_hist_ewm = calcular_cobertura_semestral(df_hist_ewm)

            # ------------------------------------------------------------
            # GAP DO MÊS
            # Exemplo:
            # janeiro = cobertura de janeiro - 16,66%
            # ------------------------------------------------------------
            df_hist_ewm["Gap mês (%)"] = (
                df_hist_ewm["Cobertura (%)"] - META_MENSAL_EWM
            ).round(2)

            # ------------------------------------------------------------
            # META ACUMULADA DO SEMESTRE
            # Janeiro = 16,66%
            # Fevereiro = 33,33%
            # Março = 50,00%
            # Abril = 66,66%
            # ------------------------------------------------------------
            df_hist_ewm["Mês índice"] = range(1, len(df_hist_ewm) + 1)

            df_hist_ewm["Meta semestre (%)"] = (
                df_hist_ewm["Mês índice"] * META_MENSAL_EWM
            ).round(2)

            # ------------------------------------------------------------
            # GAP DO SEMESTRE
            # Exemplo:
            # abril = cobertura acumulada jan-abr - meta acumulada jan-abr
            # ------------------------------------------------------------
            df_hist_ewm["Gap semestre (%)"] = (
                df_hist_ewm["Cobertura semestre (%)"]
                - df_hist_ewm["Meta semestre (%)"]
            ).round(2)

            # ------------------------------------------------------------
            # MONTA A VISÃO FORMATADA PARA EXIBIR NA TELA
            # COM CORES E LEITURA AUTOMÁTICA
            # ------------------------------------------------------------

            df_hist_ewm_view = df_hist_ewm[
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

            # ------------------------------------------------------------
            # STATUS AUTOMÁTICO DO MÊS E DO SEMESTRE
            # Regra:
            # >= 0      = Em linha / acima da meta
            # entre -5 e 0 = Atenção
            # < -5      = Atrasado
            # ------------------------------------------------------------

            def status_com_icone(valor):
                try:
                    valor = float(valor)
                except Exception:
                    return "⚪ Sem leitura"

                if valor >= 0:
                    return "🟢 Em linha"
                elif valor >= -5:
                    return "🟡 Atenção"
                else:
                    return "🔴 Atrasado"

            df_hist_ewm_view["Status mês"] = df_hist_ewm_view["Gap mês (%)"].apply(status_com_icone)
            df_hist_ewm_view["Status semestre"] = df_hist_ewm_view["Gap semestre (%)"].apply(status_com_icone)

            # ------------------------------------------------------------
            # FUNÇÃO DE COR PARA OS GAPS
            # ------------------------------------------------------------

            def cor_gap(valor):
                try:
                    valor = float(valor)
                except Exception:
                    return ""

                if valor >= 0:
                    return "background-color: #d9ead3; color: #274e13;"
                elif valor >= -5:
                    return "background-color: #fff2cc; color: #7f6000;"
                else:
                    return "background-color: #f4cccc; color: #990000;"

            def cor_status(valor):
                if "Em linha" in str(valor):
                    return "background-color: #d9ead3; color: #274e13;"
                elif "Atenção" in str(valor):
                    return "background-color: #fff2cc; color: #7f6000;"
                elif "Atrasado" in str(valor):
                    return "background-color: #f4cccc; color: #990000;"
                return ""

            # ------------------------------------------------------------
            # ESTILO DA TABELA
            # Mantemos os valores numéricos até o Styler para permitir cor.
            # ------------------------------------------------------------

            df_hist_ewm_style = (
                df_hist_ewm_view.style
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
                df_hist_ewm_style,
                width="stretch",
                hide_index=True
            )

            # ------------------------------------------------------------
            # LINHA DE RESUMO DO PERÍODO (MÉDIAS)
            # ------------------------------------------------------------

            df_validos = df_hist_ewm[
                df_hist_ewm["Contados"] > 0
            ].copy()

            if not df_validos.empty:
                baseline_medio = df_validos["Baseline"].mean()
                contados_medio = df_validos["Contados"].mean()
                cobertura_media = df_validos["Cobertura (%)"].mean()
                gap_medio = cobertura_media - (100 / 6)

                with st.container(border=True):
                    st.markdown("**Resumo executivo do período (médias)**")

                    r1, r2, r3, r4 = st.columns(4)

                    r1.metric("Baseline médio", fmt_int(baseline_medio))
                    r2.metric("Contados médio", fmt_int(contados_medio))
                    r3.metric("Cobertura média", fmt_pct(cobertura_media))
                    r4.metric("GAP médio", fmt_pct(gap_medio))

            # ------------------------------------------------------------
            # LEITURA AUTOMÁTICA DO ÚLTIMO MÊS COM DADOS
            # ------------------------------------------------------------

            df_hist_ewm_leitura = df_hist_ewm_view[
                df_hist_ewm_view["Contados"].fillna(0) > 0
            ].copy()

            if not df_hist_ewm_leitura.empty:
                linha_leitura = df_hist_ewm_leitura.iloc[-1]

                mes_leitura = str(linha_leitura["Mês"])
                gap_mes_leitura = float(linha_leitura["Gap mês (%)"])
                gap_semestre_leitura = float(linha_leitura["Gap semestre (%)"])
                status_mes_leitura = str(linha_leitura["Status mês"])
                status_semestre_leitura = str(linha_leitura["Status semestre"])

                # ------------------------------------------------------------
                # CARD EXECUTIVO DO CICLO EWM
                # ------------------------------------------------------------
                st.markdown("**Status executivo do ciclo EWM**")

                # ------------------------------------------------------------
                # CÁLCULO DO ESFORÇO NECESSÁRIO POR MÊS
                # ------------------------------------------------------------
                mes_num_leitura = int(mes_leitura[5:7])

                if int(semestre_sel) == 1:
                    mes_indice_semestre = mes_num_leitura
                else:
                    mes_indice_semestre = mes_num_leitura - 6

                meses_restantes_semestre = max(6 - mes_indice_semestre, 0)

                cobertura_semestre_leitura = float(linha_leitura["Cobertura semestre (%)"])

                esforco_necessario_mes_ewm = None

                if meses_restantes_semestre > 0:
                    esforco_necessario_mes_ewm = (
                        max(100.0 - cobertura_semestre_leitura, 0.0)
                        / meses_restantes_semestre
                    )
                elif cobertura_semestre_leitura >= 100:
                    esforco_necessario_mes_ewm = 0.0

                # ------------------------------------------------------------
                # CARD EXECUTIVO DO CICLO EWM
                # ------------------------------------------------------------
                card1, card2, card3, card4, card5 = st.columns(5)

                card1.metric(
                    "Mês de referência",
                    mes_leitura
                )

                card2.metric(
                    "Status do semestre",
                    status_semestre_leitura
                )

                card3.metric(
                    "GAP mês",
                    fmt_pct(gap_mes_leitura)
                )

                card4.metric(
                    "GAP semestre",
                    fmt_pct(gap_semestre_leitura)
                )

                if esforco_necessario_mes_ewm is not None:
                    if esforco_necessario_mes_ewm <= (100 / 6):
                        st.success("Projeção: Meta do semestre ainda é atingível.")
                    else:
                        st.error("Projeção: Meta do semestre não será atingida no ritmo atual.")

                card5.metric(
                    "Esforço necessário / mês",
                    fmt_pct(esforco_necessario_mes_ewm)
                    if esforco_necessario_mes_ewm is not None else "-"
                )


                st.caption(
                    f"Leitura do mês: {status_mes_leitura} | "
                    f"GAP mês: {fmt_pct(gap_mes_leitura)}. "
                    f"Leitura do semestre: {status_semestre_leitura} | "
                    f"GAP semestre: {fmt_pct(gap_semestre_leitura)}."
                )

            st.caption(
                "Esta tabela mostra duas leituras de GAP: "
                "GAP mês compara a cobertura do mês contra a meta mensal de 16,66%; "
                "GAP semestre compara a cobertura acumulada até o mês contra a meta acumulada do semestre. "
                "Cores: verde = em linha/acima da meta; amarelo = atenção; vermelho = atrasado."
            )

            # ------------------------------------------------------------
            # VELOCÍMETRO DO SEMESTRE - WEPV
            # ------------------------------------------------------------
            with st.container(border=True):
                st.markdown("**Cobertura do ciclo semestral — WEPV**")
                valor_cobertura_tabela_ewm = 0.0

                linha_atual = df_hist_ewm[df_hist_ewm["Mês"] <= (snapshot_month_ewm or "")].copy()

                if not linha_atual.empty:
                    row_atual = linha_atual.iloc[-1]
                else:
                    row_atual = df_hist_ewm.iloc[-1]

                # --------------------------------------------------------
                # KPI ESQUERDA = COBERTURA OFICIAL DO CICLO (BASELINE)
                # Regra:
                # materiais do baseline do ciclo já contados no semestre
                # ÷
                # baseline oficial do ciclo
                # --------------------------------------------------------
                valor_cobertura = float(cobertura_real_ewm or 0)

                # Meta acumulada segue sendo a do mês de referência atual
                valor_meta = (
                    float(row_atual["Meta acumulada (%)"] or 0)
                    if pd.notna(row_atual["Meta acumulada (%)"])
                    else 0.0
                )

                # ---------------------------------------------
                # Cobertura acumulada pela tabela mensal - WEPV
                # ---------------------------------------------
                baseline_tabela_ewm = pd.to_numeric(
                    df_hist_ewm["Baseline"], errors="coerce"
                ).fillna(0).sum()

                contados_tabela_ewm = pd.to_numeric(
                    df_hist_ewm["Contados"], errors="coerce"
                ).fillna(0).sum()

                valor_cobertura_tabela_ewm = 0.0
                valor_meta_mensal = 100 / 6
                valor_range = max(valor_cobertura, valor_meta_mensal)
                range_max = max(20, math.ceil(valor_range * 1.5))

                if baseline_tabela_ewm > 0:
                    valor_cobertura_tabela_ewm = (contados_tabela_ewm / baseline_tabela_ewm) * 100

                fig_gauge_ewm = go.Figure(
                    go.Indicator(
                        mode="gauge+number+delta",
                        value=valor_cobertura,
                        number={"suffix": "%"},
                        delta={
                            "reference": valor_meta,
                            "relative": False,
                            "valueformat": ".2f",
                        },
                        title={"text": "Cobertura oficial do semestre (Baseline)"},
                        gauge={
                            "axis": {"range": [0, range_max]},
                            "threshold": {
                                "line": {"width": 4},
                                "thickness": 0.9,
                                "value": valor_meta_mensal,
                            },
                            "steps": [
                                {"range": [0, valor_meta_mensal], "color": "#f5f5f5"},
                                {"range": [valor_meta_mensal, range_max], "color": "#eaeaea"},
                            ],
                            "bar": {"thickness": 0.35},
                        }
                    )
                )

                fig_gauge_ewm.update_layout(
                    height=360,
                    margin=dict(l=20, r=20, t=60, b=20),
                )

                df_hist_ewm_media = df_hist_ewm.copy()

                df_hist_ewm_media["Cobertura (%)"] = pd.to_numeric(
                    df_hist_ewm_media["Cobertura (%)"], errors="coerce"
                )

                df_hist_ewm_media["Contados"] = pd.to_numeric(
                    df_hist_ewm_media["Contados"], errors="coerce"
                )

                df_hist_ewm_media = df_hist_ewm_media[
                    df_hist_ewm_media["Contados"].fillna(0) > 0
                ].copy()

                # --------------------------------------------------------
                # COBERTURA CONSOLIDADA DA TABELA MENSAL - WEPV
                # --------------------------------------------------------
                # Regra:
                # soma dos contados ÷ soma dos baselines
                #
                # Isso mantém o velocímetro da direita coerente com o
                # texto explicativo e com os cards de composição.
                # --------------------------------------------------------
                valor_cobertura_tabela_ewm = 0.0
                if baseline_tabela_ewm > 0:
                    valor_cobertura_tabela_ewm = round(
                        (contados_tabela_ewm / baseline_tabela_ewm) * 100,
                        2
                    )

                fig_gauge_ewm_tabela = go.Figure(
                    go.Indicator(
                        mode="gauge+number+delta",
                        value=valor_cobertura_tabela_ewm,
                        number={"suffix": "%"},
                        delta={
                            "reference": valor_meta,
                            "relative": False,
                            "valueformat": ".2f",
                        },
                        title={"text": "Cobertura média operacional do período"},
                        gauge={
                            "axis": {"range": [0, 100]},
                            "threshold": {
                                "line": {"width": 4},
                                "thickness": 0.9,
                                "value": valor_meta,
                            },
                            "steps": [
                                {"range": [0, 50], "color": "#f5f5f5"},
                                {"range": [50, 100], "color": "#eaeaea"},
                            ],
                            "bar": {"color": "green", "thickness": 0.35},
                        },
                    )
                )

                fig_gauge_ewm_tabela.update_layout(
                    height=360,
                    margin=dict(l=20, r=20, t=60, b=20),
                )

                col_g1, col_g2 = st.columns(2)

                with col_g1:
                    st.plotly_chart(
                        fig_gauge_ewm,
                        width="stretch",
                        key=f"gauge_ewm_{ano_sel}_{semestre_sel}"
                    )

                with col_g2:
                    st.plotly_chart(
                        fig_gauge_ewm_tabela,
                        width="stretch",
                        key=f"gauge_ewm_tabela_{ano_sel}_{semestre_sel}"
                    )

                comp_col1, comp_col2 = st.columns(2)

                with comp_col1:
                    with st.container(border=True):
                        st.markdown("**Composição da cobertura oficial do ciclo - (baseline)**")

                        b1, b2, b3 = st.columns(3)

                        b1.metric(
                            "Itens do baseline já contados no ciclo",
                            fmt_int(skus_contados_ewm) if skus_contados_ewm is not None else "-"
                        )

                        b2.metric(
                            "Baseline oficial do ciclo",
                            fmt_int(skus_baseline_ewm) if skus_baseline_ewm is not None else "-"
                        )

                        b3.metric(
                            "Cobertura oficial resultante",
                            fmt_pct(valor_cobertura)
                        )

                with comp_col2:
                    with st.container(border=True):
                        st.markdown("**Composição da cobertura da tabela mensal (Consolidado do semestre)**")

                        t1, t2, t3 = st.columns(3)

                        t1.metric(
                            "Meses considerados",
                            fmt_int(len(df_hist_ewm_media))
                        )

                        # ------------------------------------------------
                        # Exibe a cobertura consolidada com base na soma
                        # dos contados ÷ soma dos baselines.
                        # ------------------------------------------------
                        t2.metric(
                            "Baseline total considerado ",
                            fmt_int(baseline_tabela_ewm) if baseline_tabela_ewm > 0 else "-"
                        )

                        t3.metric(
                            "Cobertura média operacional",
                            fmt_pct(valor_cobertura_tabela_ewm)
                        )  

                st.caption(
                    f"O velocímetro da esquerda mostra a cobertura oficial do semestre "
                    f"comparada com a meta acumulada ({fmt_pct(valor_meta)}). "
                    "O velocímetro da direita mostra a cobertura média operacional do período, "
                    "calculada pela média dos SKUs contados nos meses com contagem "
                    "dividida pela média dos baselines desses mesmos meses, "
                    "seguindo a referência de meta mensal de 16,66%."
                )
        else:
            st.info("Sem dados suficientes para montar a evolução mensal do semestre do WEPV.")

    st.divider()

    # ------------------------------------------------------------
    # PRODUTIVIDADE DOS OPERADORES - EWM
    # ------------------------------------------------------------
    with st.container(border=True):
        st.markdown("**Produtividade dos operadores — contagens EWM**")

        conn_op_ewm = sqlite3.connect(db_path)

        if int(semestre_sel) == 1:
            meses_semestre_sql = [f"{int(ano_sel):04d}-{m:02d}" for m in [1, 2, 3, 4, 5, 6]]
        else:
            meses_semestre_sql = [f"{int(ano_sel):04d}-{m:02d}" for m in [7, 8, 9, 10, 11, 12]]

        placeholders = ",".join(["?"] * len(meses_semestre_sql))

        query_op_ewm = f"""
            SELECT
                counted_by,
                COUNT(*) AS linhas,
                COUNT(DISTINCT material) AS skus
            FROM counts
            WHERE warehouse_code NOT LIKE 'M%'
            AND substr(count_date, 1, 7) IN ({placeholders})
            AND counted_by IS NOT NULL
            AND TRIM(counted_by) <> ''
            AND UPPER(TRIM(counted_by)) GLOB '[A-Z][A-Z][0-9][0-9][0-9][0-9][0-9][0-9]'
            GROUP BY UPPER(TRIM(counted_by))
            ORDER BY linhas DESC, skus DESC, counted_by
        """

        df_op_ewm = pd.read_sql_query(
            query_op_ewm,
            conn_op_ewm,
            params=meses_semestre_sql,
        )

        conn_op_ewm.close()

        if df_op_ewm.empty:
            st.info("Não há contagens EWM no período selecionado.")
        else:
            df_op_ewm["counted_by"] = (
                df_op_ewm["counted_by"]
                .fillna("Não informado")
                .astype(str)
                .str.strip()
            )

            df_op_ewm.loc[df_op_ewm["counted_by"] == "", "counted_by"] = "Não informado"

            total_operadores_ewm = int(len(df_op_ewm))
            total_linhas_ewm = int(df_op_ewm["linhas"].sum())
            media_linhas_ewm = (
                total_linhas_ewm / total_operadores_ewm if total_operadores_ewm > 0 else 0
            )

            operador_lider_ewm = str(df_op_ewm.iloc[0]["counted_by"])
            linhas_lider_ewm = int(df_op_ewm.iloc[0]["linhas"])
            skus_lider_ewm = int(df_op_ewm.iloc[0]["skus"])
            pct_lider_ewm = (
                (linhas_lider_ewm / total_linhas_ewm) * 100 if total_linhas_ewm > 0 else 0
            )

            e1, e2, e3, e4 = st.columns(4)
            e1.metric("Operadores ativos", fmt_int(total_operadores_ewm))
            e2.metric("Média linhas / operador", f"{media_linhas_ewm:.1f}".replace(".", ","))
            e3.metric("Operador líder", operador_lider_ewm)
            e4.metric("Participação do líder", fmt_pct(pct_lider_ewm))

            st.caption(
                f"Líder do período: {operador_lider_ewm} | "
                f"{fmt_int(linhas_lider_ewm)} linhas contadas | "
                f"{fmt_int(skus_lider_ewm)} SKUs distintos."
            )

            st.divider()

            df_op_ewm_plot = df_op_ewm.sort_values("linhas", ascending=True).copy()

            fig_op_ewm = px.bar(
                df_op_ewm_plot,
                x="linhas",
                y="counted_by",
                orientation="h",
                text="linhas",
                title="Contagens EWM por operador",
            )

            fig_op_ewm.update_traces(textposition="outside")
            fig_op_ewm.update_layout(
                xaxis_title="Linhas contadas",
                yaxis_title="Operador",
                height=max(320, len(df_op_ewm_plot) * 55),
            )

            st.plotly_chart(fig_op_ewm, width="stretch", key=f"fig_op_ewm_{ano_sel}_{semestre_sel}")

            df_op_ewm_view = df_op_ewm.copy()
            df_op_ewm_view["pct_participacao"] = (
                (df_op_ewm_view["linhas"] / total_linhas_ewm) * 100
            ).round(2)

            df_op_ewm_view.insert(0, "ranking", range(1, len(df_op_ewm_view) + 1))

            df_op_ewm_view = df_op_ewm_view.rename(
                columns={
                    "counted_by": "operador",
                    "linhas": "linhas_contadas",
                    "skus": "skus_distintos",
                }
            )

            df_op_ewm_view["linhas_contadas"] = df_op_ewm_view["linhas_contadas"].map(fmt_int)
            df_op_ewm_view["skus_distintos"] = df_op_ewm_view["skus_distintos"].map(fmt_int)
            df_op_ewm_view["pct_participacao"] = df_op_ewm_view["pct_participacao"].map(fmt_pct)

            df_op_ewm_view = df_op_ewm_view[
                [
                    "ranking",
                    "operador",
                    "linhas_contadas",
                    "skus_distintos",
                    "pct_participacao",
                ]
            ]

            st.data_editor(
                df_op_ewm_view,
                width="stretch",
                hide_index=True,
                disabled=True,
                column_config={
                    "ranking": st.column_config.NumberColumn("Rank", width="small"),
                    "operador": st.column_config.TextColumn("Operador", width="medium"),
                    "linhas_contadas": st.column_config.TextColumn("Linhas contadas", width="medium"),
                    "skus_distintos": st.column_config.TextColumn("SKUs distintos", width="medium"),
                    "pct_participacao": st.column_config.TextColumn("Participação", width="medium"),
                },
                key=f"grid_op_ewm_{ano_sel}_{semestre_sel}"
            )
