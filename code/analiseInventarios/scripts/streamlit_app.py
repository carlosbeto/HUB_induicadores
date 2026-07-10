# code/analiseInventarios/scripts/streamlit_app.py
# -*- coding: utf-8 -*-

from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path
import sqlite3

from analiseInventarios.scripts.indicadores_inventario import (
    calcular_cobertura_semestral,
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


def read_csv_safe(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


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

    if int(semestre_sel) == 1:
        semana_ini, semana_fim = 1, 26
    else:
        semana_ini, semana_fim = 27, 53

    # ------------------------------------------------------------
    # EVOLUÇÃO DAS CONTAGENS POR SEMANA
    # ------------------------------------------------------------
    st.markdown("### Evolução das contagens por semana")

    conn = sqlite3.connect(DB_PATH)

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
        conn = sqlite3.connect(DB_PATH)

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
                    db_path=DB_PATH,
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

                conn_base = sqlite3.connect(DB_PATH)

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
                    db_path=DB_PATH,
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
                        db_path=DB_PATH,
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

                    conn_mes = sqlite3.connect(DB_PATH)

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
            # COBERTURA DO CICLO SEMESTRAL
            # ------------------------------------------------------------
            # Este bloco foi reescrito para resolver três problemas de uma vez:
            # 1) eliminar a dependência de df_hist antes da criação;
            # 2) usar UMA ÚNICA métrica no velocímetro da direita do MM;
            # 3) manter os textos coerentes com a regra realmente aplicada.
            #
            # Regra adotada no velocímetro da direita:
            #   cobertura consolidada do período
            # = soma dos contados dos meses com contagem
            #   ÷
            #   soma dos baselines desses meses
            #
            # Observação:
            # com os números 137 e 604, o resultado matematicamente correto
            # em 2 casas é 22,68%.
            # ------------------------------------------------------------
            if mm_sem is not None:
                # --------------------------------------------------------
                # 1) MONTA A EVOLUÇÃO MENSAL DO SEMESTRE PRIMEIRO
                # --------------------------------------------------------
                # Fazemos isso antes dos velocímetros para que df_hist exista
                # e possa ser reaproveitado com segurança em todo o bloco.
                # --------------------------------------------------------
                ano_ref = int(mm_sem["ano"])
                semestre_ref = int(mm_sem["semestre"])

                if semestre_ref == 1:
                    meses_semestre = [1, 2, 3, 4, 5, 6]
                else:
                    meses_semestre = [7, 8, 9, 10, 11, 12]

                linhas_hist = []

                with sqlite3.connect(DB_PATH) as conn_hist:
                    for idx_mes, mes_hist in enumerate(meses_semestre, start=1):
                        snapshot_month_hist = f"{ano_ref:04d}-{mes_hist:02d}"

                        # Baseline do mês.
                        row_base_hist = conn_hist.execute(
                            """
                            SELECT COUNT(DISTINCT material) AS skus_baseline
                            FROM baseline_items
                            WHERE snapshot_month = ?
                              AND warehouse_code = ?
                            """,
                            (snapshot_month_hist, deposito_sel),
                        ).fetchone()

                        baseline_hist = 0
                        if row_base_hist is not None and row_base_hist[0] is not None:
                            baseline_hist = int(row_base_hist[0])

                        # Contados do mês.
                        row_cont_hist = conn_hist.execute(
                            """
                            SELECT COUNT(DISTINCT material) AS skus_contados
                            FROM counts
                            WHERE source_system = 'MM'
                              AND warehouse_code = ?
                              AND substr(count_date, 1, 7) = ?
                            """,
                            (deposito_sel, snapshot_month_hist),
                        ).fetchone()

                        contados_hist = 0
                        if row_cont_hist is not None and row_cont_hist[0] is not None:
                            contados_hist = int(row_cont_hist[0])

                        # Cobertura mensal do mês.
                        cobertura_hist = None
                        if baseline_hist > 0:
                            cobertura_hist = (contados_hist / baseline_hist) * 100

                        meta_acumulada_hist = round((100 / 6) * idx_mes, 2)

                        gap_hist = None
                        if cobertura_hist is not None:
                            gap_hist = round(cobertura_hist - meta_acumulada_hist, 2)

                        linhas_hist.append(
                            {
                                "Mês": snapshot_month_hist,
                                "Baseline": baseline_hist,
                                "Contados": contados_hist,
                                "Cobertura (%)": cobertura_hist,
                                "Meta acumulada (%)": meta_acumulada_hist,
                                "Gap (%)": gap_hist,
                            }
                        )

                df_hist = pd.DataFrame(linhas_hist)

                # --------------------------------------------------------
                # 2) PREPARA A BASE QUE ALIMENTA O VELOCÍMETRO DA DIREITA
                # --------------------------------------------------------
                # Mantemos apenas meses que realmente tiveram contagem.
                # Isso evita diluir o indicador com meses zerados/futuros.
                # --------------------------------------------------------
                df_hist_media = df_hist.copy()

                if not df_hist_media.empty:
                    df_hist_media["Baseline"] = pd.to_numeric(
                        df_hist_media["Baseline"], errors="coerce"
                    )
                    df_hist_media["Contados"] = pd.to_numeric(
                        df_hist_media["Contados"], errors="coerce"
                    )
                    df_hist_media["Cobertura (%)"] = pd.to_numeric(
                        df_hist_media["Cobertura (%)"], errors="coerce"
                    )

                    df_hist_media = df_hist_media[
                        df_hist_media["Contados"].fillna(0) > 0
                    ].copy()

                # --------------------------------------------------------
                # 3) CONSOLIDA OS TOTAIS DO PERÍODO
                # --------------------------------------------------------
                # baseline_tabela = soma dos baselines dos meses com contagem
                # contados_tabela = soma dos contados dos meses com contagem
                # soma_coberturas = soma dos percentuais mensais
                #
                # O velocímetro da direita usa APENAS a cobertura consolidada:
                #   (contados_tabela / baseline_tabela) * 100
                # --------------------------------------------------------
                baseline_tabela = 0.0
                contados_tabela = 0.0
                soma_coberturas = 0.0
                valor_cobertura_tabela = 0.0

                if not df_hist_media.empty:
                    baseline_tabela = float(df_hist_media["Baseline"].sum())
                    contados_tabela = float(df_hist_media["Contados"].sum())
                    soma_coberturas = float(df_hist_media["Cobertura (%)"].sum())

                    if baseline_tabela > 0:
                        valor_cobertura_tabela = round(
                            (contados_tabela / baseline_tabela) * 100,
                            2
                        )

                with st.container(border=True):
                    st.markdown("**Cobertura do ciclo semestral**")

                    s1, s2, s3, s4, s5, s6 = st.columns(6)

                    s1.metric(
                        "Ciclo semestral",
                        f"{mm_sem['semestre']}º semestre de {mm_sem['ano']}"
                    )
                    s2.metric(
                        "Meta acumulada",
                        fmt_pct(mm_sem["meta_acumulada_pct"])
                        if mm_sem["meta_acumulada_pct"] is not None else "-"
                    )
                    s3.metric(
                        "Cobertura oficial",
                        fmt_pct(mm_sem["cobertura_oficial_pct"])
                        if mm_sem["cobertura_oficial_pct"] is not None else "-"
                    )
                    s4.metric(
                        "Gap vs meta",
                        fmt_pct(mm_sem["gap_oficial_pct"])
                        if mm_sem["gap_oficial_pct"] is not None else "-"
                    )

                    if mm_sem["status_delta_pct"] is not None:
                        s5.metric(
                            "Status do semestre",
                            mm_sem["status_semestre"],
                            delta=fmt_pct(mm_sem["status_delta_pct"])
                        )
                    else:
                        s5.metric("Status do semestre", "-")

                    s6.metric(
                        "Esforço necessário / mês",
                        fmt_pct(mm_sem["esforco_necessario_pct"])
                        if mm_sem["esforco_necessario_pct"] is not None else "-"
                    )

                    st.caption(
                        f"Baseline oficial do semestre: {mm_sem['baseline_semestre_month']} | "
                        f"Meta mensal adotada: {fmt_pct(mm_sem['meta_mensal_pct'])}"
                    )

                    # Valores do velocímetro da esquerda:
                    # cobertura oficial do semestre vs meta acumulada.
                    valor_cobertura = float(mm_sem["cobertura_oficial_pct"] or 0)
                    valor_meta = float(mm_sem["meta_acumulada_pct"] or 0)

                    fig_gauge = go.Figure(
                        go.Indicator(
                            mode="gauge+number+delta",
                            value=valor_cobertura,
                            number={
                                "suffix": "%",
                                "valueformat": ".2f",
                            },
                            delta={
                                "reference": valor_meta,
                                "relative": False,
                                "valueformat": ".2f",
                            },
                            title={"text": "Cobertura oficial do semestre (baseline)"},
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
                                "bar": {"thickness": 0.35},
                            },
                        )
                    )

                    fig_gauge.update_layout(
                        height=360,
                        margin=dict(l=20, r=20, t=60, b=20),
                    )

                    fig_gauge_tabela = go.Figure(
                        go.Indicator(
                            mode="gauge+number+delta",
                            value=valor_cobertura_tabela,
                            number={
                                "suffix": "%",
                                "valueformat": ".2f",
                            },
                            delta={
                                "reference": valor_meta,
                                "relative": False,
                                "valueformat": ".2f",
                            },
                            title={"text": "Cobertura acumulada da tabela mensal"},
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
                                "bar": {"thickness": 0.35},
                            },
                        )
                    )

                    fig_gauge_tabela.update_layout(
                        height=360,
                        margin=dict(l=20, r=20, t=60, b=20),
                    )

                    col_g1, col_g2 = st.columns(2)

                    with col_g1:
                        st.plotly_chart(fig_gauge, width="stretch")

                    with col_g2:
                        st.plotly_chart(fig_gauge_tabela, width="stretch")

                    comp_col1, comp_col2 = st.columns(2)

                    with comp_col1:
                        with st.container(border=True):
                            st.markdown("**Composição do indicador (baseline do ciclo)**")

                            b1, b2, b3 = st.columns(3)

                            b1.metric(
                                "Itens do baseline já contados",
                                fmt_int(mm_sem["counted_baseline_sem"])
                            )

                            b2.metric(
                                "Baseline do semestre",
                                fmt_int(mm_sem["baseline_sem_total"])
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
                                "Meses com contagem",
                                fmt_int(len(df_hist_media))
                            )

                            t2.metric(
                                "Soma dos baselines",
                                fmt_int(baseline_tabela) if baseline_tabela > 0 else "-"
                            )

                            t3.metric(
                                "Cobertura consolidada do período",
                                fmt_pct(valor_cobertura_tabela)
                            )

                    st.caption(
                        f"O velocímetro da esquerda mostra a cobertura oficial do semestre "
                        f"comparada com a meta acumulada ({fmt_pct(mm_sem['meta_acumulada_pct'])}). "
                        f"O velocímetro da direita mostra a cobertura consolidada do período, "
                        f"calculada por soma dos contados ÷ soma dos baselines dos meses com contagem."
                    )

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
                st.markdown("## Evolução mensal — MM")

                # --------------------------------------------------------
                # 4) EXIBE A EVOLUÇÃO MENSAL DO SEMESTRE
                # --------------------------------------------------------
                if not df_hist.empty:
                        META_MENSAL_MM = 100 / 6

                        df_hist_mm = df_hist.copy()

                        df_hist_mm = calcular_cobertura_semestral(df_hist_mm)

                        df_hist_mm["Baseline"] = pd.to_numeric(
                            df_hist_mm["Baseline"], errors="coerce"
                        ).fillna(0)

                        df_hist_mm["Contados"] = pd.to_numeric(
                            df_hist_mm["Contados"], errors="coerce"
                        ).fillna(0)

                        df_hist_mm["Cobertura (%)"] = pd.to_numeric(
                            df_hist_mm["Cobertura (%)"], errors="coerce"
                        ).fillna(0)

                        df_hist_mm["Gap mês (%)"] = (
                            df_hist_mm["Cobertura (%)"] - META_MENSAL_MM
                        ).round(2)

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

                        def status_com_icone_mm(valor):
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
                        df_hist_view["Status mês"] = df_hist_view["Gap mês (%)"].apply(status_com_icone_mm)
                        df_hist_view["Status semestre"] = df_hist_view["Gap semestre (%)"].apply(
                            status_com_icone_mm
                        )

                        def cor_gap_mm(valor):
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


                        def cor_status_mm(valor):
                            if "Em linha" in str(valor):
                                return "background-color: #d9ead3; color: #274e13;"
                            elif "Atenção" in str(valor):
                                return "background-color: #fff2cc; color: #7f6000;"
                            elif "Atrasado" in str(valor):
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
                            .map(cor_gap_mm, subset=["Gap mês (%)", "Gap semestre (%)"])
                            .map(cor_status_mm, subset=["Status mês", "Status semestre"])
                        )

                        st.dataframe(
                            df_hist_style,
                            width="stretch",
                            hide_index=True
                        )

                        df_validos_mm = df_hist_mm[
                            df_hist_mm["Contados"] > 0
                        ].copy()

                        if not df_validos_mm.empty:
                            baseline_medio_mm = df_validos_mm["Baseline"].mean()
                            contados_medio_mm = df_validos_mm["Contados"].mean()
                            cobertura_media_mm = df_validos_mm["Cobertura (%)"].mean()
                            gap_medio_mm = cobertura_media_mm - META_MENSAL_MM

                            with st.container(border=True):
                                st.markdown("**Resumo do período (médias)**")

                                r1, r2, r3, r4 = st.columns(4)

                                r1.metric("Baseline médio", fmt_int(baseline_medio_mm))
                                r2.metric("Contados médio", fmt_int(contados_medio_mm))
                                r3.metric("Cobertura média", fmt_pct(cobertura_media_mm))
                                r4.metric("GAP médio", fmt_pct(gap_medio_mm))

                        st.caption(
                            "Esta tabela apresenta duas leituras complementares: "
                            "GAP mês compara a cobertura isolada do mês com a meta mensal de 16,67%; "
                            "GAP semestre compara a evolução acumulada do semestre, calculada pela soma "
                            "dos itens contados dividida pela média dos baselines, com a meta acumulada."
                        )
                else:
                    st.info("Sem dados suficientes para montar a evolução mensal do semestre.")


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

with tab4:

    from analiseInventarios.scripts.ewm import render_ewm_tab

    render_ewm_tab(
        df_ewm=df_ewm,
        db_path=DB_PATH,
        ano_sel=int(ano_sel),
        semestre_sel=int(semestre_sel),
        fmt_int=fmt_int,
        fmt_pct=fmt_pct,
    )
