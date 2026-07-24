# scripts/streamlit_app.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
import sqlite3
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px

import os
import json
import io
from datetime import datetime
import unicodedata
import re


# =============================================================================
# Helpers de "runtime": evita warning e evita cache quando rodando via "python -c"
# =============================================================================

def _is_streamlit_runtime() -> bool:
    """
    True quando o script está rodando com 'streamlit run'.
    Em import/bare mode (python -c / testes), retorna False.
    """
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except Exception:
        return False


def _cache_data(**kwargs):
    """
    Decorator compatível:
      - em runtime Streamlit: usa st.cache_data(...)
      - fora do runtime (import/bare): não faz cache e não gera warnings
    """
    if _is_streamlit_runtime():
        return st.cache_data(**kwargs)

    def deco(fn):
        return fn

    return deco


# =============================================================================
# Config HUB / Paths
# =============================================================================

def hub_config_get(path: str, default=None):
    """
    Lê HUB_CONFIG (JSON) e pega valor por caminho tipo 'estoques.db_path'.
    """
    cfg_path = os.getenv("HUB_CONFIG", "")
    if not cfg_path:
        return default
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        cur = cfg
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur
    except Exception:
        return default


# ----------------------------
# Nomes amigáveis (exibição / exportação)
# ----------------------------
COLS_PT = {
    "material": "Material",
    "descricao": "Descrição",
    "quadrante": "Quadrante",

    "delta_val": "Variação de Valor (R$)",
    "delta_qtd": "Variação de Quantidade",
    "qtd_atual": "Saldo atual (qtd)",
    "val_atual": "Saldo atual (R$)",
    "area_negocio": "BU",

    "share_delta_val": "% do Impacto em Valor",
    "cum_share_val": "% Acumulado do Impacto em Valor",

    "share_delta_qtd": "% do Impacto em Quantidade",
    "cum_share_qtd": "% Acumulado do Impacto em Quantidade",

    "familia": "Família",
    "subfamilia": "Subfamília",
    "grupo": "Grupo",
    "processamento": "Processamento",

    "linha_unid_negocio": "BU (Unidade de Negócios)",
    "custo_unit": "Custo Unitário (R$)",

}

# Atenção: sem st.* aqui em cima (pra não executar ao importar)
HUB_ROOT = Path(__file__).resolve().parents[3]  # .../HUB_indicadores

db_rel = hub_config_get("estoques.db_path", None) or hub_config_get(
    "db_path",
    "data/analiseEstoques/DB/estoque.sqlite",
)

DEFAULT_DB = (HUB_ROOT / db_rel).resolve()


# =============================================================================
# Formatação / normalização
# =============================================================================

def _fmt_ptbr_num(x, nd=2):
    """Formata número no padrão pt-BR: 12.345,67 (sem depender de locale do SO)."""
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return ""
        s = f"{float(x):,.{nd}f}"                 # 12,345.67
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return ""


def _fmt_ptbr_int(x):
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return ""
        return f"{int(round(float(x))):,}".replace(",", ".")
    except Exception:
        return ""


def _fmt_pct(x, nd=2):
    """0.1028 -> 10,28%"""
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return ""
        return _fmt_ptbr_num(float(x) * 100.0, nd) + "%"
    except Exception:
        return ""


def df_pt(df: pd.DataFrame) -> pd.DataFrame:
    """
    Renomeia colunas apenas para EXIBIÇÃO/EXPORT.
    Não altera o dataframe original usado em filtros/queries.
    """
    if df is None or df.empty:
        return df
    return df.rename(columns={k: v for k, v in COLS_PT.items() if k in df.columns})


def _norm_txt(x: object) -> str:
    # trata None e NaN (evita "nan" virar opção de filtro)
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""

    s = str(x)

    # normaliza unicode (resolve variações de composição / largura / etc.)
    s = unicodedata.normalize("NFKC", s)

    # remove NBSP e caracteres invisíveis comuns
    s = s.replace("\u00A0", " ")          # NBSP
    s = s.replace("\u200B", "")           # zero width space
    s = s.replace("\u200C", "")           # zero width non-joiner
    s = s.replace("\u200D", "")           # zero width joiner
    s = s.replace("\uFEFF", "")           # BOM / zero width no-break space

    # colapsa whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _norm_bu(x: object) -> str:
    """
    Normalização específica de BU para evitar duplicação visual.
    Padroniza hífens/dashes e espaçamento em volta de '-'.
    """
    s = _norm_txt(x)
    if not s:
        return ""

    # padroniza vários tipos de "hífen/dash" para hífen simples
    s = (s.replace("\u2010", "-")
           .replace("\u2011", "-")
           .replace("\u2012", "-")
           .replace("\u2013", "-")
           .replace("\u2014", "-")
           .replace("\u2212", "-"))

    # padroniza espaços ao redor do hífen
    s = re.sub(r"\s*-\s*", " - ", s)
    s = re.sub(r"\s+", " ", s).strip()

    return s


# =============================================================================
# SQLite read-only (somente leitura)
# =============================================================================

def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """
    Conexão SQLite simples (UNC no Windows OK) + PRAGMA query_only.
    Não usa URI (evita invalid uri authority em caminhos tipo \\SJO-FILE-03\\...).
    """
    con = sqlite3.connect(
        str(db_path),
        timeout=30,
        check_same_thread=False,
    )
    con.execute("PRAGMA query_only = ON;")
    con.execute("PRAGMA foreign_keys = ON;")
    return con


def month_bounds(mes_ref: str) -> tuple[str, str]:
    """('YYYY-MM-01', 'YYYY-MM-01' do mês seguinte)"""
    start = f"{mes_ref}-01"
    next_month = (pd.to_datetime(start) + pd.offsets.MonthBegin(1)).strftime("%Y-%m-%d")
    return start, next_month


def build_excel_export(
    df_pareto_det: pd.DataFrame,
    df_resumo: pd.DataFrame,
    params: dict,
) -> bytes:
    """Gera XLSX em memória para st.download_button."""
    output = io.BytesIO()

    det = df_pareto_det.copy()
    res = df_resumo.copy()

    # nomes amigáveis (português) só para exportação
    det = df_pt(det)
    res = df_pt(res)

    # aba de parâmetros
    df_params = pd.DataFrame([{"chave": k, "valor": v} for k, v in params.items()])

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        det.to_excel(writer, sheet_name="PARETO_DETALHE", index=False)
        res.to_excel(writer, sheet_name="RESUMO_QUADRANTES", index=False)
        df_params.to_excel(writer, sheet_name="PARAMETROS", index=False)

        for sheet in ["PARETO_DETALHE", "RESUMO_QUADRANTES", "PARAMETROS"]:
            ws = writer.book[sheet]
            ws.freeze_panes = "A2"

    return output.getvalue()


# =============================================================================
# Queries (cache só em runtime)
# =============================================================================

@_cache_data(ttl=120)
def load_kpi_mensal_mast(
    db_path_str: str,
    mes_ref: str,
    meta_giro: float,
    desconsiderar_mov_343_344: bool = False,
) -> pd.DataFrame:
    db_path = Path(db_path_str)
    start, next_month = month_bounds(mes_ref)

    filtro_movimento = ""

    if desconsiderar_mov_343_344:
        filtro_movimento = """
        AND TRIM(COALESCE(tipo_movimento,'')) NOT IN ('343','344')
        """

    filtro_movimento = ""

    if desconsiderar_mov_343_344:
        filtro_movimento = """
          AND TRIM(COALESCE(tipo_movimento, '')) NOT IN ('343', '344')
        """

    with connect_readonly(db_path) as con:
        b = con.execute("""
            SELECT baseline_date, qtd_base_total, val_base_total
            FROM baseline_mensal
            WHERE mes_ref=? AND deposito='MAST';
        """, (mes_ref,)).fetchone()

        if not b:
            return pd.DataFrame()

        baseline_date, qtd_base, val_base = b

        last_snap = con.execute("""
            SELECT MAX(snapshot_date)
            FROM fact_estoque_snapshot
            WHERE deposito='MAST'
              AND snapshot_date >= ?
              AND snapshot_date < ?;
        """, (start, next_month)).fetchone()[0]

        if not last_snap:
            return pd.DataFrame()

        qtd_atual, val_atual = con.execute("""
            SELECT
              COALESCE(SUM(qtd_total), 0),
              COALESCE(SUM(val_total), 0)
            FROM fact_estoque_snapshot
            WHERE deposito='MAST'
              AND snapshot_date=?;
        """, (last_snap,)).fetchone()

        qtd_atual = float(qtd_atual or 0)
        val_atual = float(val_atual or 0)

        mb = con.execute(f"""
            SELECT
              COALESCE(SUM(CASE
                    WHEN deb_cred='S'
                     AND TRIM(COALESCE(ordem, ''))=''
                    THEN quantidade
                    ELSE 0
              END), 0) AS entradas_qtd,

              COALESCE(SUM(CASE
                    WHEN deb_cred='S'
                     AND TRIM(COALESCE(ordem, ''))=''
                    THEN valor_estimado
                    ELSE 0
              END), 0) AS entradas_val,

              COALESCE(SUM(CASE
                    WHEN deb_cred='H'
                    THEN quantidade
                    ELSE 0
              END), 0) AS saidas_qtd,

              COALESCE(SUM(CASE
                    WHEN deb_cred='H'
                    THEN valor_estimado
                    ELSE 0
              END), 0) AS saidas_val

            FROM fact_mb51_mov
            WHERE deposito='MAST'
              {filtro_movimento}
              AND data_lancamento >= ?
              AND data_lancamento < ?;
        """, (start, next_month)).fetchone()

        entradas_qtd, entradas_val, saidas_qtd, saidas_val = [
            float(x or 0) for x in mb
        ]

        giro_operacional_val = (
            saidas_val / val_base
            if val_base not in (None, 0)
            else None
        )

        giro_operacional_qtd = (
            saidas_qtd / qtd_base
            if qtd_base not in (None, 0)
            else None
        )

        meta_saldo_val = (
            val_base / meta_giro
            if val_base not in (None, 0)
            and meta_giro not in (None, 0)
            else None
        )

        meta_saldo_qtd = (
            qtd_base / meta_giro
            if qtd_base not in (None, 0)
            and meta_giro not in (None, 0)
            else None
        )

        falta_baixar_val = (
            val_atual - meta_saldo_val
            if meta_saldo_val is not None
            else None
        )

        falta_baixar_qtd = (
            qtd_atual - meta_saldo_qtd
            if meta_saldo_qtd is not None
            else None
        )

        ating_meta_giro_val = (
            giro_operacional_val / meta_giro
            if giro_operacional_val is not None
            and meta_giro not in (None, 0)
            else None
        )

        ating_meta_giro_qtd = (
            giro_operacional_qtd / meta_giro
            if giro_operacional_qtd is not None
            and meta_giro not in (None, 0)
            else None
        )

        giro_bi_val = (
            entradas_val / val_atual
            if val_atual not in (None, 0)
            else None
        )

        giro_bi_qtd = (
            entradas_qtd / qtd_atual
            if qtd_atual not in (None, 0)
            else None
        )

        meta_saldo_bi_val = (
            entradas_val / meta_giro
            if entradas_val not in (None, 0)
            and meta_giro not in (None, 0)
            else None
        )

        meta_saldo_bi_qtd = (
            entradas_qtd / meta_giro
            if entradas_qtd not in (None, 0)
            and meta_giro not in (None, 0)
            else None
        )

        saldo_a_baixar_bi_val = (
            val_atual - meta_saldo_bi_val
            if meta_saldo_bi_val is not None
            else None
        )

        saldo_a_baixar_bi_qtd = (
            qtd_atual - meta_saldo_bi_qtd
            if meta_saldo_bi_qtd is not None
            else None
        )

        consumo_liq_qtd = saidas_qtd - entradas_qtd
        consumo_liq_val = saidas_val - entradas_val

        eficiencia_val = (
            saidas_val / entradas_val
            if entradas_val not in (None, 0)
            else None
        )

        eficiencia_qtd = (
            saidas_qtd / entradas_qtd
            if entradas_qtd not in (None, 0)
            else None
        )

    return pd.DataFrame([{
        "mes_ref": mes_ref,
        "meta_giro": float(meta_giro),
        "baseline_date": str(baseline_date),
        "val_base_total": float(val_base or 0),
        "qtd_base_total": float(qtd_base or 0),
        "last_snapshot_date": str(last_snap),
        "val_atual_total": round(val_atual, 2),

        "giro_bi_val": (
            None
            if giro_bi_val is None
            else round(float(giro_bi_val), 4)
        ),
        "giro_bi_qtd": (
            None
            if giro_bi_qtd is None
            else round(float(giro_bi_qtd), 4)
        ),
        "meta_saldo_bi_val": (
            None
            if meta_saldo_bi_val is None
            else round(float(meta_saldo_bi_val), 2)
        ),
        "meta_saldo_bi_qtd": (
            None
            if meta_saldo_bi_qtd is None
            else round(float(meta_saldo_bi_qtd), 3)
        ),
        "saldo_a_baixar_bi_val": (
            None
            if saldo_a_baixar_bi_val is None
            else round(float(saldo_a_baixar_bi_val), 2)
        ),
        "saldo_a_baixar_bi_qtd": (
            None
            if saldo_a_baixar_bi_qtd is None
            else round(float(saldo_a_baixar_bi_qtd), 3)
        ),

        "qtd_atual_total": round(qtd_atual, 3),
        "giro_operacional_val": (
            None
            if giro_operacional_val is None
            else round(float(giro_operacional_val), 4)
        ),
        "giro_operacional_qtd": (
            None
            if giro_operacional_qtd is None
            else round(float(giro_operacional_qtd), 4)
        ),
        "ating_meta_giro_val": (
            None
            if ating_meta_giro_val is None
            else round(float(ating_meta_giro_val), 4)
        ),
        "ating_meta_giro_qtd": (
            None
            if ating_meta_giro_qtd is None
            else round(float(ating_meta_giro_qtd), 4)
        ),
        "meta_saldo_val": (
            None
            if meta_saldo_val is None
            else round(float(meta_saldo_val), 2)
        ),
        "meta_saldo_qtd": (
            None
            if meta_saldo_qtd is None
            else round(float(meta_saldo_qtd), 3)
        ),
        "falta_baixar_val": (
            None
            if falta_baixar_val is None
            else round(float(falta_baixar_val), 2)
        ),
        "falta_baixar_qtd": (
            None
            if falta_baixar_qtd is None
            else round(float(falta_baixar_qtd), 3)
        ),

        "entradas_reais_qtd_mes": round(entradas_qtd, 3),
        "entradas_reais_val_mes": round(entradas_val, 2),
        "saidas_reais_qtd_mes": round(saidas_qtd, 3),
        "saidas_reais_val_mes": round(saidas_val, 2),
        "consumo_liq_qtd_mes": round(consumo_liq_qtd, 3),
        "consumo_liq_val_mes": round(consumo_liq_val, 2),
        "eficiencia_val_ratio": (
            None
            if eficiencia_val is None
            else round(float(eficiencia_val), 4)
        ),
        "eficiencia_qtd_ratio": (
            None
            if eficiencia_qtd is None
            else round(float(eficiencia_qtd), 4)
        ),
    }])

@_cache_data(ttl=120)
def _load_eficiencia_hierarquia_mast(
    db_path_str: str,
    mes_ref: str,
    group_fields: list[str],
) -> pd.DataFrame:
    """
    Calcula eficiência financeira por uma hierarquia comercial.

    Exemplos de agrupamento:
    - ["bu"]
    - ["bu", "diretoria"]
    - ["bu", "diretoria", "segmento"]
    """

    campos_validos = {
        "bu",
        "diretoria",
        "segmento",
        "centro_lucro",
        "centro_custo",
    }

    if not group_fields:
        raise ValueError("Informe pelo menos um campo de agrupamento.")

    campos_invalidos = set(group_fields) - campos_validos

    if campos_invalidos:
        raise ValueError(
            f"Campos de agrupamento inválidos: {sorted(campos_invalidos)}"
        )

    db_path = Path(db_path_str)
    start, next_month = month_bounds(mes_ref)

    select_hierarquia = ",\n                ".join(
        [
            (
                f"COALESCE(NULLIF(TRIM(dm.{campo}), ''), "
                f"'SEM_{campo.upper()}') AS {campo}"
            )
            for campo in group_fields
        ]
    )

    with connect_readonly(db_path) as con:
        row_base = con.execute(
            """
            SELECT baseline_date
            FROM baseline_mensal
            WHERE mes_ref = ?
              AND deposito = 'MAST'
            """,
            (mes_ref,),
        ).fetchone()

        if not row_base:
            return pd.DataFrame()

        baseline_date = row_base[0]

        last_snap = con.execute(
            """
            SELECT MAX(snapshot_date)
            FROM fact_estoque_snapshot
            WHERE deposito = 'MAST'
              AND snapshot_date >= ?
              AND snapshot_date < ?
            """,
            (start, next_month),
        ).fetchone()[0]

        if not last_snap:
            return pd.DataFrame()

        sql_snap = f"""
            SELECT
                fes.snapshot_date,
                {select_hierarquia},
                fes.material,
                COALESCE(fes.val_total, 0) AS val_total
            FROM fact_estoque_snapshot fes
            LEFT JOIN dim_material dm
                ON fes.material = dm.material
            WHERE fes.deposito = 'MAST'
              AND fes.snapshot_date >= ?
              AND fes.snapshot_date <= ?
        """

        df_snap = pd.read_sql_query(
            sql_snap,
            con,
            params=[baseline_date, last_snap],
        )

    if df_snap.empty:
        return pd.DataFrame()

    df_snap["val_total"] = pd.to_numeric(
        df_snap["val_total"],
        errors="coerce",
    ).fillna(0.0)

    datas = sorted(
        df_snap["snapshot_date"]
        .dropna()
        .unique()
        .tolist()
    )

    if baseline_date not in datas:
        datas.insert(0, baseline_date)

    if last_snap not in datas:
        datas.append(last_snap)

    index_fields = [*group_fields, "material"]

    pivot = (
        df_snap.pivot_table(
            index=index_fields,
            columns="snapshot_date",
            values="val_total",
            aggfunc="sum",
            fill_value=0.0,
        )
        .reindex(columns=datas, fill_value=0.0)
        .sort_index(axis=1)
    )

    baseline_por_item = pivot[baseline_date]
    saldo_atual_por_item = pivot[last_snap]

    deltas = pivot.diff(axis=1).iloc[:, 1:]

    entradas_por_item = deltas.clip(lower=0).sum(axis=1)
    consumo_por_item = (-deltas.clip(upper=0)).sum(axis=1)

    df_calc = pd.DataFrame(
        {
            "baseline_val": baseline_por_item,
            "entradas_val": entradas_por_item,
            "consumo_real_sap_val": consumo_por_item,
            "saldo_atual_sap_val": saldo_atual_por_item,
        }
    ).reset_index()

    df = (
        df_calc.groupby(group_fields, as_index=False)
        .agg(
            baseline_val=("baseline_val", "sum"),
            entradas_val=("entradas_val", "sum"),
            consumo_real_sap_val=("consumo_real_sap_val", "sum"),
            saldo_atual_sap_val=("saldo_atual_sap_val", "sum"),
        )
    )

    df["base_disponivel_val"] = (
        df["baseline_val"] + df["entradas_val"]
    )

    df["eficiencia_disponibilidade_pct"] = np.where(
        df["base_disponivel_val"] > 0,
        df["consumo_real_sap_val"] / df["base_disponivel_val"],
        0,
    )

    df["saidas_val"] = df["consumo_real_sap_val"]
    df["eficiencia_pct"] = df["eficiencia_disponibilidade_pct"]
    df["baseline_restante"] = df["saldo_atual_sap_val"]

    total_consumo = float(df["consumo_real_sap_val"].sum())

    df["participacao_saida_pct"] = np.where(
        total_consumo > 0,
        df["consumo_real_sap_val"] / total_consumo,
        0,
    )

    total_baseline = float(df["baseline_val"].sum())

    df["participacao_baseline_pct"] = np.where(
        total_baseline > 0,
        df["baseline_val"] / total_baseline,
        0,
    )

    df["classe_relevancia"] = np.where(
        df["participacao_baseline_pct"] >= 0.03,
        "Relevante",
        "Baixo impacto",
    )

    df["baseline_date"] = baseline_date
    df["last_snapshot_date"] = last_snap

    df = df.sort_values(
        "consumo_real_sap_val",
        ascending=False,
    ).reset_index(drop=True)

    return df


@_cache_data(ttl=120)
def load_eficiencia_bu_mast(
    db_path_str: str,
    mes_ref: str,
) -> pd.DataFrame:
    df = _load_eficiencia_hierarquia_mast(
        db_path_str=db_path_str,
        mes_ref=mes_ref,
        group_fields=["bu"],
    )

    if not df.empty:
        df["area_negocio"] = df["bu"]

    return df


@_cache_data(ttl=120)
def load_eficiencia_segmento_mast(
    db_path_str: str,
    mes_ref: str,
) -> pd.DataFrame:
    return _load_eficiencia_hierarquia_mast(
        db_path_str=db_path_str,
        mes_ref=mes_ref,
        group_fields=[
            "bu",
            "diretoria",
            "segmento",
        ],
    )

@_cache_data(ttl=120)
def load_snapshot_saldo_mes(db_path_str: str, mes_ref: str) -> pd.DataFrame:
    db_path = Path(db_path_str)
    start, next_month = month_bounds(mes_ref)
    with connect_readonly(db_path) as con:
        return pd.read_sql_query("""
            SELECT
              snapshot_date AS dia,
              COALESCE(SUM(val_total),0) AS saldo_val,
              COALESCE(SUM(qtd_total),0) AS saldo_qtd
            FROM fact_estoque_snapshot
            WHERE deposito='MAST'
              AND snapshot_date >= ?
              AND snapshot_date <  ?
            GROUP BY snapshot_date
            ORDER BY snapshot_date;
        """, con, params=[start, next_month])

@_cache_data(ttl=120)
def load_mb51_diario_real_mes(
    db_path_str: str,
    mes_ref: str,
    desconsiderar_mov_343_344: bool = False,
) -> pd.DataFrame:
    """
    MB51 REAL diário (MAST) no mês.
    Entradas reais: S e ordem vazia.
    Saídas reais: H, todas, independentemente de ordem.
    """
    db_path = Path(db_path_str)
    start, next_month = month_bounds(mes_ref)

    filtro_movimento = ""

    if desconsiderar_mov_343_344:
        filtro_movimento = """
          AND TRIM(COALESCE(tipo_movimento,'')) NOT IN ('343','344')
        """

    with connect_readonly(db_path) as con:
        df = pd.read_sql_query(
            f"""
            SELECT
              data_lancamento AS dia,
              SUM(
                  CASE
                      WHEN deb_cred='S'
                       AND TRIM(COALESCE(ordem,''))=''
                      THEN quantidade
                      ELSE 0
                  END
              ) AS entradas_qtd,
              SUM(
                  CASE
                      WHEN deb_cred='S'
                       AND TRIM(COALESCE(ordem,''))=''
                      THEN valor_estimado
                      ELSE 0
                  END
              ) AS entradas_val,
              SUM(
                  CASE
                      WHEN deb_cred='H'
                      THEN quantidade
                      ELSE 0
                  END
              ) AS saidas_qtd,
              SUM(
                  CASE
                      WHEN deb_cred='H'
                      THEN valor_estimado
                      ELSE 0
                  END
              ) AS saidas_val
            FROM fact_mb51_mov
            WHERE deposito='MAST'
              AND data_lancamento >= ?
              AND data_lancamento < ?
              {filtro_movimento}
            GROUP BY data_lancamento
            ORDER BY data_lancamento;
            """,
            con,
            params=[start, next_month],
        )

    if df.empty:
        return df

    df["consumo_liq_qtd"] = df["saidas_qtd"] - df["entradas_qtd"]
    df["consumo_liq_val"] = df["saidas_val"] - df["entradas_val"]

    return df


@_cache_data(ttl=120)
def load_mb51_resumo_material_mes(
    db_path_str: str,
    mes_ref: str,
    material: str,
    desconsiderar_mov_343_344: bool = False,
) -> dict:
    db_path = Path(db_path_str)
    start, next_month = month_bounds(mes_ref)

    filtro_movimento = ""

    if desconsiderar_mov_343_344:
        filtro_movimento = """
          AND TRIM(COALESCE(tipo_movimento,'')) NOT IN ('343','344')
        """

    with connect_readonly(db_path) as con:
        row = con.execute(
            f"""
            SELECT
              COALESCE(SUM(CASE
                    WHEN deb_cred='S' AND TRIM(COALESCE(ordem,''))=''
                    THEN quantidade ELSE 0 END), 0) AS entradas_qtd,

              COALESCE(SUM(CASE
                    WHEN deb_cred='S' AND TRIM(COALESCE(ordem,''))=''
                    THEN valor_estimado ELSE 0 END), 0) AS entradas_val,

              COALESCE(SUM(CASE
                    WHEN deb_cred='H'
                    THEN quantidade ELSE 0 END), 0) AS saidas_qtd,

              COALESCE(SUM(CASE
                    WHEN deb_cred='H'
                    THEN valor_estimado ELSE 0 END), 0) AS saidas_val

            FROM fact_mb51_mov

            WHERE deposito='MAST'
              AND material = ?
              AND data_lancamento >= ?
              AND data_lancamento < ?
              {filtro_movimento};
            """,
            (str(material), start, next_month),
        ).fetchone()

    entradas_qtd, entradas_val, saidas_qtd, saidas_val = [
        float(x or 0) for x in row
    ]

    return {
        "entradas_qtd": entradas_qtd,
        "entradas_val": entradas_val,
        "saidas_qtd": saidas_qtd,
        "saidas_val": saidas_val,
        "saldo_liq_qtd": entradas_qtd - saidas_qtd,
        "saldo_liq_val": entradas_val - saidas_val,
    }


@_cache_data(ttl=120)
def load_snapshot_material_mes(db_path_str: str, mes_ref: str, material: str) -> dict:
    db_path = Path(db_path_str)
    start, next_month = month_bounds(mes_ref)

    with connect_readonly(db_path) as con:
        baseline_date = con.execute("""
            SELECT baseline_date
            FROM baseline_mensal
            WHERE mes_ref=? AND deposito='MAST';
        """, (mes_ref,)).fetchone()

        if not baseline_date:
            return {"ok": False, "motivo": "Sem baseline_mensal para o mês (MAST)."}

        baseline_date = baseline_date[0]

        last_snap = con.execute("""
            SELECT MAX(snapshot_date)
            FROM fact_estoque_snapshot
            WHERE deposito='MAST'
              AND snapshot_date >= ?
              AND snapshot_date <  ?;
        """, (start, next_month)).fetchone()[0]

        if not last_snap:
            return {"ok": False, "motivo": "Sem snapshot no mês para MAST."}

        qb, vb = con.execute("""
            SELECT COALESCE(qtd_total,0), COALESCE(val_total,0)
            FROM fact_estoque_snapshot
            WHERE deposito='MAST' AND snapshot_date=? AND material=?;
        """, (baseline_date, str(material))).fetchone() or (0, 0)

        qa, va = con.execute("""
            SELECT COALESCE(qtd_total,0), COALESCE(val_total,0)
            FROM fact_estoque_snapshot
            WHERE deposito='MAST' AND snapshot_date=? AND material=?;
        """, (last_snap, str(material))).fetchone() or (0, 0)

    qb = float(qb or 0); vb = float(vb or 0)
    qa = float(qa or 0); va = float(va or 0)

    return {
        "ok": True,
        "baseline_date": str(baseline_date),
        "last_snap": str(last_snap),
        "qtd_base": qb,
        "qtd_atual": qa,
        "delta_qtd": qa - qb,
        "val_base": vb,
        "val_atual": va,
        "delta_val": va - vb,
    }


@_cache_data(ttl=120)
def desconsiderar_mov_343_344(
    db_path_str: str,
    mes_ref: str,
    desconsiderar_mov_343_344: bool = False,
) -> pd.DataFrame:
    db_path = Path(db_path_str)
    start, next_month = month_bounds(mes_ref)

    filtro_movimento = ""

    if desconsiderar_mov_343_344:
        filtro_movimento = """
          AND TRIM(COALESCE(tipo_movimento,'')) NOT IN ('343','344')
        """

    with connect_readonly(db_path) as con:
        df = pd.read_sql_query(
            f"""
            SELECT
              data_lancamento AS dia,

              COALESCE(SUM(CASE
                    WHEN deb_cred='S'
                     AND TRIM(COALESCE(ordem,''))=''
                    THEN quantidade
                    ELSE 0
              END), 0) AS entradas_qtd,

              COALESCE(SUM(CASE
                    WHEN deb_cred='S'
                     AND TRIM(COALESCE(ordem,''))=''
                    THEN valor_estimado
                    ELSE 0
              END), 0) AS entradas_val,

              COALESCE(SUM(CASE
                    WHEN deb_cred='H'
                    THEN quantidade
                    ELSE 0
              END), 0) AS saidas_qtd,

              COALESCE(SUM(CASE
                    WHEN deb_cred='H'
                    THEN valor_estimado
                    ELSE 0
              END), 0) AS saidas_val

            FROM fact_mb51_mov

            WHERE deposito='MAST'
              AND data_lancamento >= ?
              AND data_lancamento < ?
              {filtro_movimento}

            GROUP BY data_lancamento
            ORDER BY data_lancamento;
            """,
            con,
            params=[start, next_month],
        )

    df["consumo_liq_qtd"] = df["saidas_qtd"] - df["entradas_qtd"]
    df["consumo_liq_val"] = df["saidas_val"] - df["entradas_val"]

    return df

@_cache_data(ttl=120)
def load_ataque_quadrantes_mes(db_path_str: str, mes_ref: str, p: float = 0.70) -> tuple[pd.DataFrame, pd.DataFrame, float, float]:
    db_path = Path(db_path_str)
    start, next_month = month_bounds(mes_ref)

    with connect_readonly(db_path) as con:
        b = con.execute("""
            SELECT baseline_date
            FROM baseline_mensal
            WHERE mes_ref=? AND deposito='MAST';
        """, (mes_ref,)).fetchone()
        if not b:
            return pd.DataFrame(), pd.DataFrame(), 0.0, 0.0

        baseline_date = b[0]

        last_snap = con.execute("""
            SELECT MAX(snapshot_date)
            FROM fact_estoque_snapshot
            WHERE deposito='MAST'
              AND snapshot_date >= ?
              AND snapshot_date <  ?;
        """, (start, next_month)).fetchone()[0]

        if not last_snap:
            return pd.DataFrame(), pd.DataFrame(), 0.0, 0.0

        df_base = pd.read_sql_query("""
            SELECT material,
                   COALESCE(descricao,'') AS descricao,
                   COALESCE(qtd_total,0) AS qtd_base,
                   COALESCE(val_total,0) AS val_base
            FROM fact_estoque_snapshot
            WHERE deposito='MAST' AND snapshot_date=?;
        """, con, params=[baseline_date])

        df_atual = pd.read_sql_query("""
            SELECT material,
                   COALESCE(descricao,'') AS descricao,
                   COALESCE(qtd_total,0) AS qtd_atual,
                   COALESCE(val_total,0) AS val_atual
            FROM fact_estoque_snapshot
            WHERE deposito='MAST' AND snapshot_date=?;
        """, con, params=[last_snap])

        d_custo = pd.read_sql_query("""
            SELECT material, custo_unit
            FROM dim_material_custo;
        """, con)

        d_grupo = pd.read_sql_query("""
            SELECT
                material,
                grupo,
                familia,
                subfamilia,
                area_negocio
            FROM dim_material_grupo;
        """, con)

        d_proc = pd.read_sql_query("""
            SELECT material, descricao_curta, linha_unid_negocio, garantia_meses,
                   processamento, saida_manufatura, codigo_remanufaturado, observacao
            FROM dim_material_proc;
        """, con)

    df = df_base.merge(df_atual, on="material", how="outer", suffixes=("_base", "_atual"))

    df["descricao"] = df["descricao_atual"].fillna("").astype(str)
    mask_desc_vazia = df["descricao"].str.strip().eq("")
    df.loc[mask_desc_vazia, "descricao"] = df.loc[mask_desc_vazia, "descricao_base"].fillna("").astype(str)

    for col in ["qtd_base", "val_base", "qtd_atual", "val_atual"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["delta_qtd"] = df["qtd_atual"] - df["qtd_base"]
    df["delta_val"] = df["val_atual"] - df["val_base"]

    pos_val = df.loc[df["delta_val"] > 0, "delta_val"]
    pos_qtd = df.loc[df["delta_qtd"] > 0, "delta_qtd"]

    cut_val = float(pos_val.quantile(p)) if not pos_val.empty else 0.0
    cut_qtd = float(pos_qtd.quantile(p)) if not pos_qtd.empty else 0.0

    alto_val = (df["delta_val"] > 0) & (df["delta_val"] >= cut_val)
    alta_qtd = (df["delta_qtd"] > 0) & (df["delta_qtd"] >= cut_qtd)

    def _quad(av, aq):
        if av and aq:
            return "Q1_ALTO_VALOR_ALTA_QTD"
        if av and (not aq):
            return "Q2_ALTO_VALOR"
        if (not av) and aq:
            return "Q3_ALTA_QTD"
        return "Q4_ESTAVEL_OU_REDUCAO"

    df["quadrante"] = [_quad(av, aq) for av, aq in zip(alto_val.tolist(), alta_qtd.tolist())]

    df = df.merge(d_custo, on="material", how="left")
    df = df.merge(d_grupo, on="material", how="left")
    df = df.merge(d_proc,  on="material", how="left")

    df["mes_ref"] = mes_ref

    df_det = df.sort_values(["quadrante", "delta_val", "delta_qtd"], ascending=[True, False, False]).reset_index(drop=True)

    df_resumo = (
        df_det.groupby(["mes_ref", "quadrante"], as_index=False)
              .agg(
                  qt_materiais=("material", "count"),
                  soma_delta_val=("delta_val", "sum"),
                  soma_delta_qtd=("delta_qtd", "sum"),
              )
              .sort_values("quadrante")
              .reset_index(drop=True)
    )

    return df_resumo, df_det, cut_val, cut_qtd


@_cache_data(ttl=120)
def list_meses_disponiveis(db_path_str: str) -> list[str]:
    db_path = Path(db_path_str)
    with connect_readonly(db_path) as con:
        rows = con.execute("""
            SELECT DISTINCT mes_ref
            FROM baseline_mensal
            WHERE deposito='MAST'
            ORDER BY mes_ref;
        """).fetchall()
    return [r[0] for r in rows]


# =============================================================================
# APP (tudo que usa st.* fica aqui dentro)
# =============================================================================

def run():
    # ----------------------------
    # Config do App (somente aqui dentro)
    # ----------------------------
    st.set_page_config(page_title="Analise Estoques - MAST", layout="wide")
    st.title("Analise Estoques - MAST (SQLite)")
    st.caption("Fonte: SQLite (somente leitura). KPI mensal = snapshot (saldo/meta/giro) + MB51 REAL (eficiência).")

    # ----------------------------
    # Sidebar
    # ----------------------------
    st.sidebar.header("Parâmetros")

    db_path = st.sidebar.text_input(
        "DB (SQLite)",
        value=str(DEFAULT_DB),
    )

    meta_giro = st.sidebar.number_input(
        "Meta de giro (mês)",
        min_value=0.1,
        max_value=10.0,
        value=1.2,
        step=0.1,
    )

    # ------------------------------------------------------------------
    # Filtro opcional para análises operacionais
    #
    # Quando marcado, os movimentos SAP 343 e 344 (bloqueio/desbloqueio)
    # serão desconsiderados nas consultas MB51.
    #
    # Nesta primeira etapa o checkbox apenas cria a variável.
    # Nenhuma consulta utiliza este valor ainda.
    # ------------------------------------------------------------------
    desconsiderar_mov_343_344 = st.sidebar.checkbox(
        "Desconsiderar movimentos 343 e 344",
        value=True,
        help=(
            "Remove das análises MB51 os movimentos de bloqueio/desbloqueio "
            "(343 e 344). O comportamento padrão permanece inalterado."
        ),
    )

    meses = list_meses_disponiveis(db_path)

    if not meses:
        st.error(
            "Não encontrei meses em baseline_mensal para MAST. "
            "Rode o ETL primeiro."
        )
        st.stop()

    mes_ref = st.sidebar.selectbox(
        "Mês de referência",
        options=meses,
        index=len(meses) - 1,
    )

    # ----------------------------
    # Tabs
    # ----------------------------
    tab1, tab2, tab3 = st.tabs(
        [
            "Mensal (KPI)",
            "Diário (séries)",
            "Ataque (quadrantes)",
        ]
    )

    with tab1:
        kpi = load_kpi_mensal_mast(
        db_path,
        mes_ref,
        float(meta_giro),
        desconsiderar_mov_343_344,
    )
        if kpi.empty:
            st.error(f"Sem dados suficientes para montar o KPI mensal de {mes_ref}.")
            st.stop()

        row = kpi.iloc[0].to_dict()

        st.markdown("### BI (replicado)")

        entradas_bi = row.get("entradas_reais_val_mes", 0.0)
        saldo_bi    = row.get("val_atual_total", None)

        meta_saldo_bi = row.get("meta_saldo_bi_val", None)
        saldo_a_baixar_bi = row.get("saldo_a_baixar_bi_val", None)
        giro_bi = row.get("giro_bi_val", None)

        b1, b2, b3, b4, b5 = st.columns(5)
        b1.metric("Entradas MAST (R$)", f"{entradas_bi:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        b2.metric("Saldo MAST (R$)", f"{saldo_bi:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if saldo_bi is not None else "NA")
        b3.metric("Meta de saldo (R$)", f"{meta_saldo_bi:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if meta_saldo_bi is not None else "NA")
        b4.metric("Saldo a baixar (R$)", f"{saldo_a_baixar_bi:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if saldo_a_baixar_bi is not None else "NA")
        b5.metric("Giro atual (mês)", f"{giro_bi:.2f}" if giro_bi is not None else "NA")

        st.info(
            "📌 **BI (replicado):** Entradas do mês (S + ORDEM vazia) comparadas com o **saldo atual (snapshot)** "
            "para obter Giro Atual, Meta de Saldo e Saldo a Baixar (mesma lógica do BI corporativo)."
        )

        st.divider()

        st.markdown("### Eficiência (Consumo do Baseline)")

        baseline_val = row.get("val_base_total", None)
        saidas_val_mes = row.get("saidas_reais_val_mes", None)

        if baseline_val not in (None, 0) and saidas_val_mes is not None:
            eficiencia_baseline = float(saidas_val_mes) / float(baseline_val)
            baseline_restante = max(0.0, float(baseline_val) - float(saidas_val_mes))
            eficiencia_extra = max(0.0, float(saidas_val_mes) - float(baseline_val))
        else:
            eficiencia_baseline = None
            baseline_restante = None
            eficiencia_extra = None

        e1, e2, e3, e4, e5 = st.columns(5)
        e1.metric("Baseline inicial (R$)", f"{baseline_val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if baseline_val is not None else "NA")
        e2.metric("Saídas no mês (R$)", f"{saidas_val_mes:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if saidas_val_mes is not None else "NA")
        e3.metric("Baseline restante (R$)", f"{baseline_restante:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if baseline_restante is not None else "NA")
        e4.metric("Eficiência vs baseline", f"{(eficiencia_baseline*100):.1f}%" if eficiencia_baseline is not None else "NA")
        e5.metric("Eficiência extra (R$)", f"{eficiencia_extra:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if eficiencia_extra is not None else "0,00")

        st.info(
            "📌 **Eficiência (baseline):** mede o quanto do saldo inicial do mês já foi consumido via saídas. "
            "Se as saídas ultrapassarem o baseline, o excedente é mostrado como **eficiência extra** (consumo do que entrou no mês)."
        )

        st.markdown("### Eficiência por BU")

        df_bu = load_eficiencia_bu_mast(db_path, mes_ref)

        if df_bu.empty:
            st.info("Sem dados suficientes para calcular eficiência por BU.")
        else:
            df_bu_view = df_bu.copy()

            df_bu_view["Baseline inicial (R$)"] = df_bu_view["baseline_val"].map(lambda x: _fmt_ptbr_num(x, 2))
            df_bu_view["Saídas MB51 (R$)"] = df_bu_view["saidas_val"].map(lambda x: _fmt_ptbr_num(x, 2))
            df_bu_view["Consumo real SAP (R$)"] = df_bu_view["consumo_real_sap_val"].map(
                lambda x: _fmt_ptbr_num(x, 2)
            )
            df_bu_view["Baseline inicial (R$)"] = df_bu_view["baseline_val"].map(lambda x: _fmt_ptbr_num(x, 2))
            df_bu_view["Entradas SAP (R$)"] = df_bu_view["entradas_val"].map(lambda x: _fmt_ptbr_num(x, 2))
            df_bu_view["Base disponível (R$)"] = df_bu_view["base_disponivel_val"].map(lambda x: _fmt_ptbr_num(x, 2))
            df_bu_view["Consumo SAP (R$)"] = df_bu_view["consumo_real_sap_val"].map(lambda x: _fmt_ptbr_num(x, 2))
            df_bu_view["Saldo atual (SAP) (R$)"] = df_bu_view["saldo_atual_sap_val"].map(lambda x: _fmt_ptbr_num(x, 2))
            df_bu_view["Eficiência"] = df_bu_view["eficiencia_pct"].map(lambda x: _fmt_pct(x, 1))
            df_bu_view["% part. consumo"] = df_bu_view["participacao_saida_pct"].map(lambda x: _fmt_pct(x, 1))
            df_bu_view["% part. baseline"] = df_bu_view["participacao_baseline_pct"].map(lambda x: _fmt_pct(x, 1))
            
            df_bu_view = df_bu_view.rename(columns={
                "area_negocio": "BU"
            })

            st.dataframe(
                df_bu_view[
            [
                "BU",
                "Baseline inicial (R$)",
                "Entradas SAP (R$)",
                "Base disponível (R$)",
                "Consumo SAP (R$)",
                "Saldo atual (SAP) (R$)",
                "Eficiência",
                "% part. consumo",
                "% part. baseline",
            ]
                ],
                width="stretch",
                hide_index=True,
            )

            
            st.markdown("### Detalhamento por Diretoria e Segmento")

            df_segmento = load_eficiencia_segmento_mast(
                db_path,
                mes_ref,
            )

            if df_segmento.empty:
                st.info(
                    "Sem dados suficientes para detalhar a eficiência "
                    "por Diretoria e Segmento."
                )
            else:
                df_segmento_filtrado = df_segmento.copy()

                # ---------------------------------------------------------
                # Filtro de BU
                # ---------------------------------------------------------
                lista_bu = sorted(
                    df_segmento_filtrado["bu"]
                    .dropna()
                    .astype(str)
                    .unique()
                    .tolist()
                )

                opcoes_bu = ["Todas"] + lista_bu

                filtro_bu = st.selectbox(
                    "BU",
                    options=opcoes_bu,
                    index=0,
                    key=f"filtro_eficiencia_bu_segmento_{mes_ref}",
                )

                if filtro_bu != "Todas":
                    df_segmento_filtrado = df_segmento_filtrado[
                        df_segmento_filtrado["bu"] == filtro_bu
                    ].copy()

                # ---------------------------------------------------------
                # Filtro de Diretoria
                # As opções dependem da BU selecionada.
                # ---------------------------------------------------------
                lista_diretoria = sorted(
                    df_segmento_filtrado["diretoria"]
                    .dropna()
                    .astype(str)
                    .unique()
                    .tolist()
                )

                opcoes_diretoria = ["Todas"] + lista_diretoria

                filtro_diretoria = st.selectbox(
                    "Diretoria",
                    options=opcoes_diretoria,
                    index=0,
                    key=f"filtro_eficiencia_diretoria_segmento_{mes_ref}",
                )

                if filtro_diretoria != "Todas":
                    df_segmento_filtrado = df_segmento_filtrado[
                        df_segmento_filtrado["diretoria"]
                        == filtro_diretoria
                    ].copy()

                if df_segmento_filtrado.empty:
                    st.info(
                        "Nenhum segmento encontrado para os filtros selecionados."
                    )
                else:
                    # -----------------------------------------------------
                    # Recalcula as participações dentro do recorte filtrado.
                    # Isso evita mostrar percentuais referentes ao total geral
                    # quando o usuário seleciona uma BU ou Diretoria.
                    # -----------------------------------------------------
                    total_consumo_filtrado = float(
                        df_segmento_filtrado[
                            "consumo_real_sap_val"
                        ].sum()
                    )

                    df_segmento_filtrado[
                        "participacao_consumo_filtrado_pct"
                    ] = np.where(
                        total_consumo_filtrado > 0,
                        (
                            df_segmento_filtrado[
                                "consumo_real_sap_val"
                            ]
                            / total_consumo_filtrado
                        ),
                        0,
                    )

                    total_baseline_filtrado = float(
                        df_segmento_filtrado["baseline_val"].sum()
                    )

                    df_segmento_filtrado[
                        "participacao_baseline_filtrado_pct"
                    ] = np.where(
                        total_baseline_filtrado > 0,
                        (
                            df_segmento_filtrado["baseline_val"]
                            / total_baseline_filtrado
                        ),
                        0,
                    )

                    # -----------------------------------------------------
                    # Montagem da tabela visual
                    # -----------------------------------------------------
                    df_segmento_view = df_segmento_filtrado.copy()

                    df_segmento_view["Baseline inicial (R$)"] = (
                        df_segmento_view["baseline_val"].map(
                            lambda x: _fmt_ptbr_num(x, 2)
                        )
                    )

                    df_segmento_view["Entradas SAP (R$)"] = (
                        df_segmento_view["entradas_val"].map(
                            lambda x: _fmt_ptbr_num(x, 2)
                        )
                    )

                    df_segmento_view["Base disponível (R$)"] = (
                        df_segmento_view["base_disponivel_val"].map(
                            lambda x: _fmt_ptbr_num(x, 2)
                        )
                    )

                    df_segmento_view["Consumo SAP (R$)"] = (
                        df_segmento_view[
                            "consumo_real_sap_val"
                        ].map(
                            lambda x: _fmt_ptbr_num(x, 2)
                        )
                    )

                    df_segmento_view["Saldo atual SAP (R$)"] = (
                        df_segmento_view[
                            "saldo_atual_sap_val"
                        ].map(
                            lambda x: _fmt_ptbr_num(x, 2)
                        )
                    )

                    df_segmento_view["Eficiência"] = (
                        df_segmento_view["eficiencia_pct"].map(
                            lambda x: _fmt_pct(x, 1)
                        )
                    )

                    df_segmento_view["% part. consumo"] = (
                        df_segmento_view[
                            "participacao_consumo_filtrado_pct"
                        ].map(
                            lambda x: _fmt_pct(x, 1)
                        )
                    )

                    df_segmento_view["% part. baseline"] = (
                        df_segmento_view[
                            "participacao_baseline_filtrado_pct"
                        ].map(
                            lambda x: _fmt_pct(x, 1)
                        )
                    )

                    df_segmento_view = df_segmento_view.rename(
                        columns={
                            "bu": "BU",
                            "diretoria": "Diretoria",
                            "segmento": "Segmento",
                        }
                    )

                    df_segmento_view = df_segmento_view.sort_values(
                        [
                            "BU",
                            "Diretoria",
                            "consumo_real_sap_val",
                        ],
                        ascending=[
                            True,
                            True,
                            False,
                        ],
                    )

                    st.dataframe(
                        df_segmento_view[
                            [
                                "BU",
                                "Diretoria",
                                "Segmento",
                                "Baseline inicial (R$)",
                                "Entradas SAP (R$)",
                                "Base disponível (R$)",
                                "Consumo SAP (R$)",
                                "Saldo atual SAP (R$)",
                                "Eficiência",
                                "% part. consumo",
                                "% part. baseline",
                            ]
                        ],
                        width="stretch",
                        hide_index=True,
                    )                        

            st.markdown("### Participação das BUs nas consumos do mês")

            df_plot_saida = df_bu.copy()

            df_plot_saida = df_plot_saida.sort_values(
                "saidas_val",
                ascending=True
            )

            fig_saida = px.bar(
                df_plot_saida,
                x="participacao_saida_pct",
                y="area_negocio",
                orientation="h",
                text="participacao_saida_pct",
                color="classe_relevancia",
                custom_data=["saidas_val", "baseline_val", "participacao_baseline_pct"],
                labels={
                    "participacao_saida_pct": "% do consumo",
                    "area_negocio": "BU"
                },
                color_discrete_map={
                    "Relevante": "#2F6BBA",
                    "Baixo impacto": "#B0B0B0",
                },
                title=None
            )

            fig_saida.update_traces(
                texttemplate="%{text:.1%}",
                textposition="outside",
                hovertemplate=(
                    "<b>%{y}</b><br>"
                    "Participação no consumo: %{x:.1%}<br>"
                    "Consumo SAP: R$ %{customdata[0]:,.2f}<br>"
                    "Baseline: R$ %{customdata[1]:,.2f}<br>"
                    "% do baseline: %{customdata[2]:.1%}"
                    "<extra></extra>"
                )
            )

            fig_saida.update_layout(
                xaxis_tickformat=".0%",
                yaxis_title=None,
                xaxis_title=None,
                height=max(350, len(df_plot_saida) * 55),
            )

            st.plotly_chart(
                fig_saida,
                width="stretch",
                key=f"grafico_bu_saida_{mes_ref}"
            )

            st.markdown("### Eficiência proporcional por BU")

            df_plot_eff = df_bu.copy()

            # Ordena pelo peso financeiro do baseline
            df_plot_eff = df_plot_eff.sort_values(
                "baseline_val",
                ascending=True
            )

            fig_eff = px.bar(
                df_plot_eff,
                x="eficiencia_pct",
                y="area_negocio",
                orientation="h",
                text="eficiencia_pct",
                color="classe_relevancia",
                custom_data=[
                    "baseline_val",
                    "saidas_val",
                    "baseline_restante",
                    "participacao_baseline_pct"
                ],
                color_discrete_map={
                    "Relevante": "#2F6BBA",
                    "Baixo impacto": "#B0B0B0",
                },
                labels={
                    "eficiencia_pct": "Eficiência",
                    "area_negocio": "BU",
                    "classe_relevancia": "Impacto financeiro",
                },
            )

            fig_eff.update_traces(
                texttemplate="%{text:.1%}",
                textposition="outside",
                hovertemplate=(
                    "<b>%{y}</b><br>"
                    "Eficiência: %{x:.1%}<br>"
                    "Baseline: R$ %{customdata[0]:,.2f}<br>"
                    "Consumo SAP: R$ %{customdata[1]:,.2f}<br>"
                     "Saldo atual SAP: R$ %{customdata[2]:,.2f}<br>"
                    "% do baseline: %{customdata[3]:.1%}"
                    "<extra></extra>"
                )
            )

            fig_eff.update_layout(
                xaxis_tickformat=".0%",
                yaxis_title=None,
                xaxis_title=None,
                height=max(350, len(df_plot_eff) * 55),
            )

            st.plotly_chart(
                fig_eff,
                width="stretch",
                key=f"grafico_bu_eficiencia_{mes_ref}"
            )

        # KPI — Ritmo necessário
        try:
            ano, mes = map(int, mes_ref.split("-"))
            hoje = pd.Timestamp.today().normalize()
            fim_mes = pd.Timestamp(ano, mes, 1) + pd.offsets.MonthEnd(0)

            dias_uteis_restantes = np.busday_count(
                hoje.date(),
                (fim_mes + pd.Timedelta(days=1)).date()
            )
        except Exception:
            dias_uteis_restantes = None

        if baseline_restante not in (None, 0) and dias_uteis_restantes not in (None, 0):
            ritmo_necessario = baseline_restante / dias_uteis_restantes
        else:
            ritmo_necessario = None

        st.divider()

        # Ritmo do mês (projeção)
        try:
            inicio_mes = pd.Timestamp(ano, mes, 1)
            dias_uteis_decorridos = np.busday_count(
                inicio_mes.date(),
                (hoje + pd.Timedelta(days=1)).date()
            )
        except Exception:
            dias_uteis_decorridos = None

        if saidas_val_mes not in (None, 0) and dias_uteis_decorridos not in (None, 0):
            ritmo_atual = float(saidas_val_mes) / float(dias_uteis_decorridos)
        else:
            ritmo_atual = None

        if baseline_restante not in (None, 0) and ritmo_atual not in (None, 0) and dias_uteis_restantes not in (None, 0):
            baseline_restante_proj_fechamento = max(
                0.0,
                float(baseline_restante) - float(ritmo_atual) * float(dias_uteis_restantes)
            )
        else:
            baseline_restante_proj_fechamento = None

        with st.expander("Ritmo do mês (projeção)", expanded=False):
            p1, p2, p3, p4, p5 = st.columns(5)

            p1.metric(
                "Dias úteis decorridos",
                f"{dias_uteis_decorridos}"
                if dias_uteis_decorridos is not None else "NA"
            )

            p2.metric(
                "Dias úteis restantes",
                f"{dias_uteis_restantes}"
                if dias_uteis_restantes is not None else "NA"
            )

            p3.metric(
                "Ritmo atual (R$/dia útil)",
                f"{ritmo_atual:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                if ritmo_atual is not None else "NA"
            )

            p4.metric(
                "Ritmo necessário p/ zerar baseline (R$/dia)",
                f"{ritmo_necessario:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                if ritmo_necessario is not None else "NA"
            )

            p5.metric(
                "Baseline restante proj. no fechamento (R$)",
                f"{baseline_restante_proj_fechamento:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                if baseline_restante_proj_fechamento is not None else "NA"
            )

            df_mb_d = load_mb51_diario_real_mes(db_path, mes_ref)

            if df_mb_d is not None and not df_mb_d.empty and "dia" in df_mb_d.columns and "saidas_val" in df_mb_d.columns:
                df_plot = df_mb_d[["dia", "saidas_val"]].copy()
                df_plot["dia"] = pd.to_datetime(df_plot["dia"], errors="coerce")
                df_plot["saidas_val"] = pd.to_numeric(df_plot["saidas_val"], errors="coerce").fillna(0.0)

                fig = px.bar(
                    df_plot,
                    x="dia",
                    y="saidas_val",
                    labels={"dia": "Dia", "saidas_val": "Saídas (R$)"},
                    title="Consumo diário (Saídas) — comparação com ritmo",
                )

                fig.update_traces(
                    text=df_plot["saidas_val"],
                    texttemplate="R$ %{text:,.0f}",
                    textposition="inside",
                    textangle=-90,
                    insidetextanchor="middle",
                )

                fig.update_layout(
                    uniformtext_minsize=9,
                    uniformtext_mode="hide",
                    yaxis_title=None,
                    xaxis_title=None,
                )

                if ritmo_necessario is not None:
                    fig.add_hline(
                        y=float(ritmo_necessario),
                        line_width=2,
                        line_color="red",
                        annotation_font_color="red",
                        annotation_text="Ritmo necessário",
                        annotation_position="top left",
                    )

                if ritmo_atual is not None:
                    fig.add_hline(
                        y=float(ritmo_atual),
                        line_width=2,
                        line_dash="dot",
                        line_color="orange",
                        annotation_font_color="orange",
                        annotation_text="Ritmo atual (média)",
                        annotation_position="top left",
                    )

                st.plotly_chart(fig, width="stretch")
            else:
                st.info("Sem série diária disponível para plotar o consumo (Saídas) no mês.")

        # Auditoria MB51
        st.markdown("### Auditoria MB51 (mês)")
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Entradas reais (R$)", f"{row['entradas_reais_val_mes']:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        a2.metric("Saídas reais (R$)", f"{row['saidas_reais_val_mes']:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        a3.metric("Eficiência (%)", f"{row['eficiencia_val_ratio']:.2%}".replace(".", ",") if row.get("eficiencia_val_ratio") is not None else "NA")
        a4.metric("Consumo líquido (R$)", f"{row['consumo_liq_val_mes']:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

        st.markdown("### 📸 Foto do baseline e do saldo")
        df_foto = pd.DataFrame([{
            "Mês ref": row.get("mes_ref", ""),
            "Data baseline (foto inicial)": row.get("baseline_date", ""),
            "Baseline (R$)": float(row.get("val_base_total", 0.0) or 0.0),
            "Data saldo atual (snapshot)": row.get("last_snapshot_date", ""),
            "Saldo atual (R$)": float(row.get("val_atual_total", 0.0) or 0.0),
        }])

        df_foto["Baseline (R$)"] = df_foto["Baseline (R$)"].map(lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        df_foto["Saldo atual (R$)"] = df_foto["Saldo atual (R$)"].map(lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

        st.dataframe(df_foto, width="stretch")

        st.markdown("### 📊 Entradas x Saídas (mês)")
        entradas_val = float(row.get("entradas_reais_val_mes", 0.0) or 0.0)
        saidas_val   = float(row.get("saidas_reais_val_mes", 0.0) or 0.0)

        falta_consumir_entradas = max(0.0, entradas_val - saidas_val)
        excedente_vs_entradas   = max(0.0, saidas_val - entradas_val)

        df_barras = pd.DataFrame({
            "Item": ["Entradas (R$)", "Saídas (R$)", "Falta p/ consumir entradas (R$)"],
            "Valor": [entradas_val, saidas_val, falta_consumir_entradas],
        })

        fig = px.bar(df_barras, x="Item", y="Valor", text="Valor", title=None)
        fig.update_traces(texttemplate="%{text:,.0f}", textposition="outside")
        fig.update_layout(yaxis_title=None, xaxis_title=None)
        st.plotly_chart(fig, width="stretch")

        if excedente_vs_entradas > 0:
            st.success(
                f"Saídas já superaram as entradas do mês em "
                f"{excedente_vs_entradas:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            )
        else:
            st.warning(
                f"Ainda faltam "
                f"{falta_consumir_entradas:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                + " em saídas para consumir tudo que entrou no mês."
            )

        st.divider()

        df_snap = load_snapshot_saldo_mes(db_path, mes_ref)
        if not df_snap.empty:
            fig_line = px.line(df_snap, x="dia", y="saldo_val", title=f"Saldo diário (snapshot) — {mes_ref}")

            meta_saldo = row.get("meta_saldo_bi_val", None)
            if meta_saldo not in (None, 0):
                fig_line.add_hline(
                    y=float(meta_saldo),
                    line_dash="dash",
                    annotation_text="Meta de saldo (BI)",
                    annotation_position="top left",
                )

            st.plotly_chart(fig_line, width="stretch", key=f"line_saldo_{mes_ref}")
            st.divider()

    with tab2:
        st.subheader(f"Séries diárias — {mes_ref}")

        df_mb_d = load_mb51_diario_real_mes(
            db_path,
            mes_ref,
            desconsiderar_mov_343_344,
)
        df_snap = load_snapshot_saldo_mes(db_path, mes_ref)

        c1, c2 = st.columns(2)

        with c1:
            st.markdown("**MB51 REAL por dia**")

            if df_mb_d is not None and not df_mb_d.empty:
                for col in ["entradas_val", "saidas_val", "consumo_liq_val",
                            "entradas_qtd", "saidas_qtd", "consumo_liq_qtd"]:
                    if col in df_mb_d.columns:
                        df_mb_d[col] = pd.to_numeric(df_mb_d[col], errors="coerce").fillna(0)

                entradas_val_mes = float(df_mb_d["entradas_val"].sum())
                saidas_val_mes   = float(df_mb_d["saidas_val"].sum())
                consumo_val_mes  = float(df_mb_d["consumo_liq_val"].sum())

                entradas_qtd_mes = float(df_mb_d["entradas_qtd"].sum())
                saidas_qtd_mes   = float(df_mb_d["saidas_qtd"].sum())
                consumo_qtd_mes  = float(df_mb_d["consumo_liq_qtd"].sum())
            else:
                entradas_val_mes = saidas_val_mes = consumo_val_mes = 0.0
                entradas_qtd_mes = saidas_qtd_mes = consumo_qtd_mes = 0.0

            a, b, c = st.columns(3)
            a.metric("Entradas mês (valor)", f"{entradas_val_mes:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
            b.metric("Saídas mês (valor)",   f"{saidas_val_mes:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
            c.metric("Consumo mês (valor)",  f"{consumo_val_mes:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

            d, e, f = st.columns(3)
            d.metric("Entradas mês (qtd)", f"{entradas_qtd_mes:,.3f}".replace(",", "X").replace(".", ",").replace("X", "."))
            e.metric("Saídas mês (qtd)",   f"{saidas_qtd_mes:,.3f}".replace(",", "X").replace(".", ",").replace("X", "."))
            f.metric("Consumo mês (qtd)",  f"{consumo_qtd_mes:,.3f}".replace(",", "X").replace(".", ",").replace("X", "."))

        with c2:
            st.markdown("**Saldo (snapshot) por dia**")

        st.divider()

        st.markdown("### KPI Diário REAL (MB51) — tabela")

        if df_mb_d is None or df_mb_d.empty:
            st.dataframe(df_mb_d, width="stretch")
        else:
            df_mb_view = df_mb_d.copy()

            for col in ["entradas_qtd", "saidas_qtd", "consumo_liq_qtd",
                        "entradas_val", "saidas_val", "consumo_liq_val"]:
                if col in df_mb_view.columns:
                    df_mb_view[col] = pd.to_numeric(df_mb_view[col], errors="coerce")

            fmt = {}
            for col in ["entradas_qtd", "saidas_qtd", "consumo_liq_qtd"]:
                if col in df_mb_view.columns:
                    fmt[col] = lambda x: _fmt_ptbr_num(x, 0)

            for col in ["entradas_val", "saidas_val", "consumo_liq_val"]:
                if col in df_mb_view.columns:
                    fmt[col] = lambda x: _fmt_ptbr_num(x, 2)

            styled = df_mb_view.style.format(fmt, na_rep="")
            st.dataframe(styled, width="stretch")

        with st.expander("Ver tabela de saldo (snapshot) por dia (auditoria)", expanded=False):
            if df_snap is None or df_snap.empty:
                st.dataframe(df_snap, width="stretch")
            else:
                df_snap_view = df_snap.copy()
                for col in ["saldo_val", "saldo_qtd"]:
                    if col in df_snap_view.columns:
                        df_snap_view[col] = pd.to_numeric(df_snap_view[col], errors="coerce")

                fmt2 = {}
                if "saldo_val" in df_snap_view.columns:
                    fmt2["saldo_val"] = lambda x: _fmt_ptbr_num(x, 2)
                if "saldo_qtd" in df_snap_view.columns:
                    fmt2["saldo_qtd"] = lambda x: _fmt_ptbr_num(x, 0)

                styled2 = df_snap_view.style.format(fmt2, na_rep="")
                st.dataframe(styled2, width="stretch")

        if df_mb_d is not None and not df_mb_d.empty:
            df_val = df_mb_d[["dia", "consumo_liq_val"]].copy()
            fig_d1 = px.line(df_val, x="dia", y="consumo_liq_val",
                             title=f"MB51 REAL — consumo líquido (valor) por dia ({mes_ref})")
            st.plotly_chart(fig_d1, width="stretch", key=f"d1_mb51_cons_val_{mes_ref}")

            df_qtd = df_mb_d[["dia", "consumo_liq_qtd"]].copy()
            fig_d2 = px.line(df_qtd, x="dia", y="consumo_liq_qtd",
                             title=f"MB51 REAL — consumo líquido (qtd) por dia ({mes_ref})")
            st.plotly_chart(fig_d2, width="stretch", key=f"d2_mb51_cons_qtd_{mes_ref}")

        if df_snap is not None and not df_snap.empty:
            fig_d3 = px.line(df_snap, x="dia", y="saldo_val", title=f"Saldo diário (snapshot) — {mes_ref}")
            st.plotly_chart(fig_d3, width="stretch", key=f"d3_saldo_val_{mes_ref}")

    with tab3:
        st.subheader("Ataque por Quadrantes — MAST (valor e quantidade)")

        cA, cB, cC = st.columns([1, 1, 1])
        with cA:
            p = st.slider("Percentil de corte (P)", min_value=0.50, max_value=0.95, value=0.70, step=0.05)
        with cB:
            top_n = st.number_input("Top N (detalhe)", min_value=10, max_value=2000, value=200, step=10)
        with cC:
            somente_delta_positivo = st.checkbox("Somente materiais com delta positivo", value=True)

        df_res, df_det, cut_val, cut_qtd = load_ataque_quadrantes_mes(db_path, mes_ref, p=float(p))

        if df_res.empty or df_det.empty:
            st.warning("Sem dados suficientes para montar quadrantes (baseline/último snapshot/dimensões).")
            st.stop()

        st.caption(
            f"Cortes (P{int(p*100)}): "
            f"cut_val={cut_val:,.2f} | cut_qtd={cut_qtd:,.3f}"
            .replace(",", "X").replace(".", ",").replace("X", ".")
        )

        st.markdown("**Resumo por quadrante**")
        df_res_view = df_res.copy()

        for col in ["qt_materiais", "soma_delta_val", "soma_delta_qtd"]:
            if col in df_res_view.columns:
                df_res_view[col] = pd.to_numeric(df_res_view[col], errors="coerce")

        fmt = {}
        if "qt_materiais" in df_res_view.columns:
            fmt["qt_materiais"] = lambda x: _fmt_ptbr_num(x, 0)
        if "soma_delta_val" in df_res_view.columns:
            fmt["soma_delta_val"] = lambda x: _fmt_ptbr_num(x, 2)
        if "soma_delta_qtd" in df_res_view.columns:
            fmt["soma_delta_qtd"] = lambda x: _fmt_ptbr_num(x, 0)

        styled_res = df_res_view.style.format(fmt, na_rep="")
        st.dataframe(styled_res, width="stretch")

        quad_sel = st.selectbox("Escolha o quadrante para detalhar", options=df_res["quadrante"].tolist())
        st.markdown(f"**Detalhe — {quad_sel}**")

        df_f = df_det[df_det["quadrante"] == quad_sel].copy()

        if somente_delta_positivo:
            df_f = df_f[(df_f["delta_val"] > 0) | (df_f["delta_qtd"] > 0)].copy()

        filtros = st.expander("Filtros (dimensões)", expanded=True)
        with filtros:
            f1, f2, f3, f4 = st.columns(4)

            if "familia" in df_f.columns:
                opts = sorted([x for x in df_f["familia"].dropna().unique().tolist() if str(x).strip() != ""])
                fam_sel = f1.multiselect("Família", options=opts, default=[], key="f_fam")
            else:
                fam_sel = []

            if "subfamilia" in df_f.columns:
                opts = sorted([x for x in df_f["subfamilia"].dropna().unique().tolist() if str(x).strip() != ""])
                sub_sel = f2.multiselect("Subfamília", options=opts, default=[], key="f_sub")
            else:
                sub_sel = []

            if "processamento" in df_f.columns:
                opts = sorted([x for x in df_f["processamento"].dropna().unique().tolist() if str(x).strip() != ""])
                proc_sel = f3.multiselect("Processamento", options=opts, default=[], key="f_proc")
            else:
                proc_sel = []

            # ---------------------------------------------------------
            # BU oficial vinda da Base Família Comercial
            # (dim_material_grupo.area_negocio)
            # ---------------------------------------------------------
            if "area_negocio" in df_f.columns:

                bu_norm = (
                    df_f["area_negocio"]
                    .fillna("SEM_BU")
                    .astype(str)
                    .str.strip()
                )

                opts = sorted([
                    x for x in bu_norm.unique().tolist()
                    if x != ""
                ])

                bu_sel = f4.multiselect(
                    "BU (Unidade de Negócios)",
                    options=opts,
                    default=[],
                    key="f_bu"
                )

            else:
                bu_sel = []

        if fam_sel and "familia" in df_f.columns:
            df_f = df_f[df_f["familia"].isin(fam_sel)].copy()
        if sub_sel and "subfamilia" in df_f.columns:
            df_f = df_f[df_f["subfamilia"].isin(sub_sel)].copy()
        if proc_sel and "processamento" in df_f.columns:
            df_f = df_f[df_f["processamento"].isin(proc_sel)].copy()
        if bu_sel and "area_negocio" in df_f.columns:
            bu_norm = (
                df_f["area_negocio"]
                .fillna("SEM_BU")
                .astype(str)
                .str.strip()
            )
            df_f = df_f[bu_norm.isin(bu_sel)].copy()

        if df_f.empty:
            st.warning("Sem dados para os filtros selecionados.")
            st.stop()

        df_f = (
            df_f.sort_values(["delta_val", "delta_qtd"], ascending=[False, False])
                .head(int(top_n))
                .copy()
        )

        df_p = df_f.copy()
        df_p["delta_val_pos"] = pd.to_numeric(df_p.get("delta_val", 0), errors="coerce").fillna(0).clip(lower=0)
        df_p["delta_qtd_pos"] = pd.to_numeric(df_p.get("delta_qtd", 0), errors="coerce").fillna(0).clip(lower=0)

        df_p = (
            df_p.sort_values(["delta_val_pos", "delta_qtd_pos"], ascending=[False, False])
                .reset_index(drop=True)
        )

        total_val = float(df_p["delta_val_pos"].sum())
        total_qtd = float(df_p["delta_qtd_pos"].sum())

        if total_val > 0:
            df_p["share_delta_val"] = df_p["delta_val_pos"] / total_val
            df_p["cum_share_val"] = df_p["share_delta_val"].cumsum()
        else:
            df_p["share_delta_val"] = 0.0
            df_p["cum_share_val"] = 0.0

        if total_qtd > 0:
            df_p["share_delta_qtd"] = df_p["delta_qtd_pos"] / total_qtd
            df_p["cum_share_qtd"] = df_p["share_delta_qtd"].cumsum()
        else:
            df_p["share_delta_qtd"] = 0.0
            df_p["cum_share_qtd"] = 0.0

        pareto_cut = st.slider("Pareto (acumulado alvo)", min_value=0.50, max_value=0.95, value=0.80, step=0.05)

        if total_val > 0:
            n_pareto_val = int((df_p["cum_share_val"] <= pareto_cut).sum())
            n_pareto_val = max(n_pareto_val, 1)
        else:
            n_pareto_val = 0

        if total_qtd > 0:
            n_pareto_qtd = int((df_p["cum_share_qtd"] <= pareto_cut).sum())
            n_pareto_qtd = max(n_pareto_qtd, 1)
        else:
            n_pareto_qtd = 0

        st.caption(
            f"Pareto (delta_val): {n_pareto_val} materiais explicam ~{int(pareto_cut*100)}% do aumento de valor | "
            f"Pareto (delta_qtd): {n_pareto_qtd} materiais explicam ~{int(pareto_cut*100)}% do aumento de quantidade"
        )

        cols_show = [
            "material", "descricao", "quadrante",
            "delta_val", "delta_qtd",

            # Saldo atual SAP — ajuda o responsável pelo estoque
            # a entender quanto existe agora para atacar.
            "qtd_atual", "val_atual",

            "share_delta_val", "cum_share_val",
            "share_delta_qtd", "cum_share_qtd",
            "familia", "subfamilia", "processamento", "area_negocio"
        ]
        cols_show = [c for c in cols_show if c in df_p.columns]

        df_view = df_p[cols_show].head(int(top_n)).copy()
        df_view_pt = df_pt(df_view)

        fmtv = {}
        if "Variação de Valor (R$)" in df_view_pt.columns:
            fmtv["Variação de Valor (R$)"] = lambda x: _fmt_ptbr_num(x, 2)
        if "Variação de Quantidade" in df_view_pt.columns:
            fmtv["Variação de Quantidade"] = lambda x: _fmt_ptbr_num(x, 0)

        if "Saldo atual (R$)" in df_view_pt.columns:
            fmtv["Saldo atual (R$)"] = lambda x: _fmt_ptbr_num(x, 2)

        if "Saldo atual (qtd)" in df_view_pt.columns:
            fmtv["Saldo atual (qtd)"] = lambda x: _fmt_ptbr_num(x, 0)

        for col in ["% do Impacto em Valor", "% Acumulado do Impacto em Valor",
                    "% do Impacto em Quantidade", "% Acumulado do Impacto em Quantidade"]:
            if col in df_view_pt.columns:
                fmtv[col] = lambda x: _fmt_pct(x, 2)

        styled = df_view_pt.style.format(fmtv, na_rep="")
        st.dataframe(styled, width="stretch")
        st.divider()

        # --- EXPORTAÇÃO ---
        params_export = {
            "mes_ref": mes_ref,
            "quadrante": quad_sel,
            "percentil_P": float(p),
            "cut_val": float(cut_val),
            "cut_qtd": float(cut_qtd),
            "gerado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        xlsx_bytes = build_excel_export(
            df_pareto_det=df_f,
            df_resumo=df_res,
            params=params_export,
        )

        nome_arquivo = f"MAST_PARETO_{mes_ref}_{quad_sel}_P{int(p*100)}.xlsx"

        st.download_button(
            label="Baixar Excel (Pareto + Resumo + Parâmetros)",
            data=xlsx_bytes,
            file_name=nome_arquivo,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"dl_pareto_{mes_ref}_{quad_sel}_{int(p*100)}",
        )

