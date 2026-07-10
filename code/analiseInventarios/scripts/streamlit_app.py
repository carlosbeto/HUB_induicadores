# code/analiseInventarios/scripts/streamlit_app.py
# -*- coding: utf-8 -*-

from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path
import sqlite3

from analiseInventarios.scripts.mm_dashboard import (
    gerar_semanas_continuas,
    semana_legenda,
)

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import math

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "outputs"
DB_PATH = PROJECT_ROOT / "data_db" / "inventarios.sqlite"

EWM_CSV = OUTPUT_DIR / "ewm_cobertura_semanal.csv"
RISK_CSV = OUTPUT_DIR / "mm_risco_snapshot.csv"
SIM_CSV = OUTPUT_DIR / "mm_simulacao_topN.csv"
TOP_CSV = OUTPUT_DIR / "mm_top200_valor_nao_inventariado.csv"
APP_VERSION = "v1.0.0"
APP_STATUS = "Estável"

st.set_page_config(page_title="Análise Inventários", layout="wide")

st.title("Análise Inventários")

vcol1, vcol2 = st.columns([4, 1])
with vcol1:
    st.caption(
        "Cobertura do inventário, risco financeiro do MAST e priorização dos materiais "
        "não inventariados a partir dos outputs gerados pelo ETL."
    )
with vcol2:
    st.caption(f"Versão: {APP_VERSION} | {APP_STATUS}")


def fmt_moeda(x) -> str:
    try:
        return f"R$ {float(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "-"


def fmt_int(x) -> str:
    try:
        return f"{int(float(x)):,}".replace(",", ".")
    except Exception:
        return "-"


def fmt_pct(x) -> str:
    try:
        return f"{float(x):.2f}%".replace(".", ",")
    except Exception:
        return "-"


# ------------------------------------------------------------
# COBERTURA SEMESTRAL MM
# ------------------------------------------------------------
# Regra adotada:
# - Semestre 1 usa baseline de janeiro
# - Semestre 2 usa baseline de julho
#
# Métricas calculadas:
# 1) cobertura oficial do ciclo
# 2) cobertura ajustada do baseline ainda ativo
# 3) itens novos contados no semestre
# 4) itens do baseline que saíram do estoque
# 5) meta acumulada
# 6) gap
# 7) esforço necessário até fechar o semestre
# ------------------------------------------------------------




def read_csv_safe(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)








st.sidebar.header("Arquivos de saída")
st.sidebar.write("Pasta:", str(OUTPUT_DIR))
st.sidebar.write("ewm_cobertura_semanal.csv:", EWM_CSV.exists())
st.sidebar.write("mm_risco_snapshot.csv:", RISK_CSV.exists())
st.sidebar.write("mm_simulacao_topN.csv:", SIM_CSV.exists())
st.sidebar.write("mm_top200_valor_nao_inventariado.csv:", TOP_CSV.exists())

df_ewm = read_csv_safe(EWM_CSV)
df_risk = read_csv_safe(RISK_CSV)
df_sim = read_csv_safe(SIM_CSV)
df_top = read_csv_safe(TOP_CSV)

depositos_mm = []
if not df_risk.empty and "warehouse_code" in df_risk.columns:
    depositos_mm = sorted(df_risk["warehouse_code"].dropna().astype(str).unique().tolist())

deposito_sel = None
if depositos_mm:
    deposito_sel = st.sidebar.selectbox("Depósito MM", options=depositos_mm, index=0)

# ------------------------------------------------------------
# SELETOR DE SEMANA ISO
# ------------------------------------------------------------
conn = sqlite3.connect(DB_PATH)

# ------------------------------------------------------------
# FILTRO DE PERÍODO (FASE 2)
# ------------------------------------------------------------
st.sidebar.markdown("### 📅 Período de análise")

ano_sel = st.sidebar.selectbox(
    "Ano",
    options=[2026, 2025, 2024],
    index=0
)

semestre_sel = st.sidebar.selectbox(
    "Semestre",
    options=[1, 2],
    index=0
)

if semestre_sel == 1:
    meses_periodo = [1, 2, 3, 4, 5, 6]
else:
    meses_periodo = [7, 8, 9, 10, 11, 12]

periodo_labels = [f"{ano_sel}-{m:02d}" for m in meses_periodo]

if semestre_sel == 1:
    meses_semestre_filtro = ("01", "02", "03", "04", "05", "06")
else:
    meses_semestre_filtro = ("07", "08", "09", "10", "11", "12")

df_weeks_raw = pd.read_sql_query(
    """
    SELECT DISTINCT year_iso, week_iso
    FROM counts
    WHERE source_system = 'MM'
      AND warehouse_code = ?
      AND substr(count_date, 1, 4) = ?
      AND substr(count_date, 6, 2) IN (?, ?, ?, ?, ?, ?)
    ORDER BY year_iso, week_iso
    """,
    conn,
    params=(
        deposito_sel,
        str(ano_sel),
        meses_semestre_filtro[0],
        meses_semestre_filtro[1],
        meses_semestre_filtro[2],
        meses_semestre_filtro[3],
        meses_semestre_filtro[4],
        meses_semestre_filtro[5],
    ) if deposito_sel else None,
)

conn.close()

df_weeks = gerar_semanas_continuas(df_weeks_raw)

if not df_weeks.empty:
    df_weeks["label"] = df_weeks.apply(
        lambda r: semana_legenda(int(r["year_iso"]), int(r["week_iso"])),
        axis=1,
    )
else:
    df_weeks["label"] = pd.Series(dtype="object")

semana_opcoes = ["Todas"] + df_weeks["label"].tolist()

semana_anterior = st.session_state.get("semana_sel", "Todas")

if semana_anterior in semana_opcoes:
    index_semana = semana_opcoes.index(semana_anterior)
else:
    index_semana = 0


semana_sel = st.sidebar.selectbox(
    "Semana de contagem",
    semana_opcoes,
    index=index_semana,
    key="semana_sel",
)

st.sidebar.info("O filtro  atua somente na aba 'Contagens da semana'.")

if deposito_sel:
    if not df_risk.empty and "warehouse_code" in df_risk.columns:
        df_risk = df_risk[df_risk["warehouse_code"] == deposito_sel].copy()

    if not df_sim.empty and "warehouse_code" in df_sim.columns:
        df_sim = df_sim[df_sim["warehouse_code"] == deposito_sel].copy()

    if not df_top.empty and "warehouse_code" in df_top.columns:
        df_top = df_top[df_top["warehouse_code"] == deposito_sel].copy()

if df_ewm.empty and df_risk.empty and df_sim.empty and df_top.empty:
    st.error("Nenhum output encontrado em code/analiseInventarios/outputs.")
    st.stop()

# ------------------------------------------------------------
# GUIA DE INTERPRETAÇÃO
# ------------------------------------------------------------


tab1, tab2, tab3, tab4 = st.tabs([
    "Visão executiva",
    "Prioridades MM",
    "Contagens da semana",
    "Cobertura EWM",
])

with tab1:

    with st.expander("Como interpretar este painel"):
        st.markdown(
            """
            **Objetivo do painel**  
            Apresentar uma visão geral do ciclo de inventário, combinando volume de itens, impacto financeiro e priorização.

            **Contexto do ciclo**  
            Mostra a base de referência da análise:
            - Snapshot do estoque (data e depósito)
            - Última semana processada
            - Período atual do ciclo

            **Cobertura física**  
            Indica o avanço da contagem em quantidade de itens:
            - SKUs atuais → total de itens no estoque
            - SKUs já cobertos → itens já contados
            - SKUs não contados → itens ainda pendentes

            **Exposição financeira**  
            Mostra o impacto financeiro do estoque:
            - Valor total → valor total do estoque
            - Valor não contado → risco atual
            - Valor já coberto → parte já validada
            - % não contado → nível de exposição

            **Impacto da priorização**  
            Mostra o quanto o risco pode ser reduzido ao focar nos itens mais relevantes:
            - Top 50 → redução potencial atacando os 50 principais itens
            - Top 100 → redução acumulada
            - Top 200 → eliminação total do risco

            **Como interpretar na prática**  
            - Muitos SKUs não contados + alto valor → risco elevado  
            - Cobertura baixa → necessidade de avanço operacional  
            - Alto ganho nos Top N → priorização bem direcionada  
            """
        )

    st.subheader("Resumo executivo")

    snapshot_date = "-"
    if not df_risk.empty and "snapshot_date" in df_risk.columns:
        snapshot_date = str(df_risk.iloc[0]["snapshot_date"])

    ultima_semana = "-"
    ciclo_mm_ref = "-"

    if deposito_sel:
        conn_ctx = sqlite3.connect(DB_PATH)

        row_ultima_data = conn_ctx.execute(
            """
            SELECT MAX(count_date)
            FROM counts
            WHERE source_system = 'MM'
              AND warehouse_code = ?
            """,
            (deposito_sel,),
        ).fetchone()

        conn_ctx.close()

        if row_ultima_data is not None and row_ultima_data[0] is not None:
            dt = pd.to_datetime(row_ultima_data[0]).date()
            ano, semana, _ = dt.isocalendar()

            ultima_semana = f"{ano}-S{semana:02d}"

            meses = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
                     "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

            mes_num = dt.month
            semestre = 1 if mes_num <= 6 else 2

            ciclo_mm_ref = f"{meses[mes_num - 1]}/{dt.year} | Semestre {semestre}"

    st.markdown("### Contexto do ciclo")

    c1, c2, c3 = st.columns(3)
    c1.metric("Snapshot de estoque", f"{deposito_sel} — {snapshot_date}")
    c2.metric("Última semana carregada", ultima_semana)
    c3.metric("Ciclo MM em andamento", ciclo_mm_ref)

    st.divider()

    if not df_risk.empty:
        row = df_risk.iloc[0]

        skus_atuais = float(row.get("skus_atuais", 0) or 0)
        skus_nao_contados = float(row.get("skus_nao_contados", 0) or 0)
        skus_cobertos = max(0.0, skus_atuais - skus_nao_contados)

        valor_total = float(row.get("valor_total_atual", 0) or 0)
        valor_nao_contado = float(row.get("valor_nao_contado", 0) or 0)
        valor_coberto = max(0.0, valor_total - valor_nao_contado)
        pct_nao_contado = float(row.get("pct_valor_nao_contado", 0) or 0)

        st.markdown("### Cobertura física")

        f1, f2, f3 = st.columns(3)
        f1.metric("SKUs atuais", fmt_int(skus_atuais))
        f2.metric("SKUs já cobertos", fmt_int(skus_cobertos))
        f3.metric("SKUs não contados", fmt_int(skus_nao_contados))
        
        st.divider()

        st.markdown("### Exposição financeira")

        v1, v2, v3, v4 = st.columns(4)
        v1.metric("Valor total atual", fmt_moeda(valor_total))
        v2.metric("Valor não contado", fmt_moeda(valor_nao_contado))
        v3.metric("Valor já coberto", fmt_moeda(valor_coberto))
        v4.metric("% valor não contado", fmt_pct(pct_nao_contado))

        st.divider()

    if not df_sim.empty:
        row = df_sim.iloc[0]

        st.markdown("### Impacto da priorização")

        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Risco total", fmt_moeda(row.get("valor_total_nao_contado")))
        s2.metric("Top 50 reduz", fmt_pct(row.get("pct_valor_top_50")))
        s3.metric("Top 100 reduz", fmt_pct(row.get("pct_valor_top_100")))
        s4.metric("Top 200 reduz", fmt_pct(row.get("pct_valor_top_200")))

        st.warning(
            "Recomendação gerencial: priorizar a contagem dos Top 100 materiais "
            f"reduz {fmt_pct(row.get('pct_valor_top_100'))} do risco financeiro exposto."
        )

        st.markdown("**Redução do risco ao contar os materiais prioritários**")

        df_bar = pd.DataFrame(
            {
                "Faixa": [
                    "Top 50 materiais",
                    "Top 100 materiais",
                    "Top 200 materiais",
                ],
                "Percentual": [
                    float(row.get("pct_valor_top_50", 0)),
                    float(row.get("pct_valor_top_100", 0)),
                    float(row.get("pct_valor_top_200", 0)),
                ],
            }
        )

        fig_bar = px.bar(
            df_bar,
            x="Faixa",
            y="Percentual",
            category_orders={
                "Faixa": [
                    "Top 50 materiais",
                    "Top 100 materiais",
                    "Top 200 materiais",
                ]
            },
            text="Percentual",
        )

        fig_bar.update_traces(texttemplate="%{text:.2f}%", textposition="outside")
        fig_bar.update_layout(xaxis_title=None, yaxis_title=None)

        st.plotly_chart(fig_bar, width="stretch")


with tab2:
    st.subheader(f"Prioridades de contagem — {deposito_sel}")

    if df_top.empty:
        st.warning(f"Arquivo Top 200 não encontrado ou vazio para {deposito_sel}.")
    else:
        top_n = st.number_input(
            "Quantidade de materiais para visualizar",
            min_value=10,
            max_value=200,
            value=50,
            step=10,
            key="topn_mm",
        )

        st.markdown(f"### Pareto do risco financeiro — {deposito_sel}")

        df_pareto = df_top.copy()
        df_pareto = df_pareto.sort_values("value_total", ascending=False).reset_index(drop=True)

        df_pareto["valor_acumulado"] = df_pareto["value_total"].cumsum()
        total_valor = df_pareto["value_total"].sum()
        df_pareto["pct_acumulado"] = (df_pareto["valor_acumulado"] / total_valor) * 100
        df_pareto["rank"] = range(1, len(df_pareto) + 1)

        fig = go.Figure()

        fig.add_trace(
            go.Bar(
                x=df_pareto["rank"],
                y=df_pareto["value_total"],
                name="Valor material",
                yaxis="y1",
            )
        )

        fig.add_trace(
            go.Scatter(
                x=df_pareto["rank"],
                y=df_pareto["pct_acumulado"],
                name="% acumulado",
                yaxis="y2",
                mode="lines+markers",
            )
        )

        marcos = [50, 100, 200]
        for m in marcos:
            if m <= len(df_pareto):
                pct = df_pareto.loc[df_pareto["rank"] == m, "pct_acumulado"].values[0]

                fig.add_vline(
                    x=m,
                    line_width=1,
                    line_dash="dash",
                    line_color="gray",
                )

                fig.add_annotation(
                    x=m,
                    y=pct,
                    text=f"Top {m}<br>{pct:.1f}%",
                    showarrow=True,
                    arrowhead=1,
                    yshift=10,
                )

        fig.update_layout(
            xaxis_title="Materiais priorizados",
            yaxis=dict(title="Valor (R$)"),
            yaxis2=dict(
                title="% acumulado do risco",
                overlaying="y",
                side="right",
                range=[0, 100],
            ),
            height=500,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="center",
                x=0.5,
            ),
        )

        st.plotly_chart(fig, use_container_width=True)

        df_view = df_top.head(int(top_n)).copy()

        cols = [
            "material",
            "material_desc",
            "value_total",
            "qty_total",
            "snapshot_date",
        ]

        cols_exist = [c for c in cols if c in df_view.columns]
        df_view = df_view[cols_exist]

        if "value_total" in df_view.columns:
            df_view["value_total"] = df_view["value_total"].map(fmt_moeda)

        if "qty_total" in df_view.columns:
            df_view["qty_total"] = df_view["qty_total"].map(fmt_int)

        df_view = df_view.rename(
            columns={
                "material": "material",
                "material_desc": "descricao_material",
                "value_total": "valor_exposto",
                "qty_total": "qtd_estoque_snapshot",
                "snapshot_date": "data_snapshot",
            }
        )

        df_view = df_view.reset_index(drop=True)

        st.dataframe(df_view, width="stretch")

        st.caption(
            f"A lista está ordenada por valor do estoque não inventariado em {deposito_sel}. "
            "A coluna 'qtd_estoque_snapshot' representa a quantidade do material no snapshot do estoque, "
            "não a quantidade contada na semana."
        )

with tab3:
    from analiseInventarios.scripts.mm_dashboard import render_mm_dashboard

    render_mm_dashboard(
        db_path=DB_PATH,
        deposito_sel=deposito_sel,
        semana_sel=semana_sel,
        df_weeks=df_weeks,
        ano_sel=int(ano_sel),
        semestre_sel=int(semestre_sel),
        fmt_int=fmt_int,
        fmt_pct=fmt_pct,
    )

with tab4:

    from analiseInventarios.scripts.ewm_dashboard import (
        render_ewm_dashboard,
    )

    render_ewm_dashboard(
        df_ewm=df_ewm,
        db_path=DB_PATH,
        ano_sel=int(ano_sel),
        semestre_sel=int(semestre_sel),
        fmt_int=fmt_int,
        fmt_pct=fmt_pct,
    )
