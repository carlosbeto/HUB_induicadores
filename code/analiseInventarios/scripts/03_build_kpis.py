from __future__ import annotations

import sqlite3
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data_db" / "inventarios.sqlite"
OUT_DIR = PROJECT_ROOT / "outputs"

# ------------------------------------------------------------
# PARÂMETROS DE NEGÓCIO
# ------------------------------------------------------------
EWM_SOURCE = "EWM"
MM_SOURCE = "MM"
MM_DEPOSITOS = ["MAST", "MASR"]


def q_df(conn: sqlite3.Connection, sql: str, params: dict | None = None) -> pd.DataFrame:
    """
    Helper simples para executar uma query SQL e devolver DataFrame.
    """
    return pd.read_sql_query(sql, conn, params=params or {})


# ------------------------------------------------------------
# EWM - BASELINE REAL DO CICLO
# ------------------------------------------------------------
def get_latest_ewm_baseline(conn: sqlite3.Connection) -> tuple[str | None, int | None]:
    """
    Busca o último baseline disponível do WEPV na tabela baseline_items.

    Retorna:
    - snapshot_month_ewm: ex. '2026-03'
    - skus_baseline_ewm: quantidade distinta de materiais do baseline
    """
    row_month = conn.execute(
        """
        SELECT MAX(snapshot_month)
        FROM baseline_items
        WHERE warehouse_code = 'WEPV'
        """
    ).fetchone()

    if row_month is None or row_month[0] is None:
        return None, None

    snapshot_month_ewm = str(row_month[0])

    row_baseline = conn.execute(
        """
        SELECT COUNT(DISTINCT material) AS skus_baseline
        FROM baseline_items
        WHERE snapshot_month = ?
          AND warehouse_code = 'WEPV'
        """,
        (snapshot_month_ewm,),
    ).fetchone()

    if row_baseline is None or row_baseline[0] is None:
        return snapshot_month_ewm, None

    return snapshot_month_ewm, int(row_baseline[0])


def build_ewm_cobertura(conn: sqlite3.Connection) -> pd.DataFrame:
    """
    Constrói a cobertura acumulada do EWM por semana.

    IMPORTANTE:
    - antes, o baseline era fixo (7000)
    - agora, o baseline vem da tabela baseline_items
    - isso deixa o CSV coerente com os KPIs do Streamlit
    """
    snapshot_month_ewm, skus_baseline_ewm = get_latest_ewm_baseline(conn)

    if skus_baseline_ewm is None or skus_baseline_ewm <= 0:
        raise RuntimeError(
            "Não foi possível calcular o baseline real do WEPV em baseline_items."
        )

    ewm_cov_sql = """
    WITH base AS (
        SELECT DISTINCT
            year_iso,
            week_iso,
            material,
            (year_iso * 100 + week_iso) AS yw
        FROM counts
        WHERE source_system = :src
    ),
    primeira AS (
        SELECT material, MIN(yw) AS primeira_yw
        FROM base
        GROUP BY material
    ),
    semanas AS (
        SELECT DISTINCT year_iso, week_iso, (year_iso * 100 + week_iso) AS yw
        FROM base
    ),
    acumulado AS (
        SELECT
            s.year_iso,
            s.week_iso,
            COUNT(p.material) AS sku_acumulado
        FROM semanas s
        JOIN primeira p
          ON p.primeira_yw <= s.yw
        GROUP BY s.year_iso, s.week_iso
    )
    SELECT
        year_iso,
        week_iso,
        sku_acumulado,
        ROUND((sku_acumulado * 100.0) / :baseline, 2) AS cobertura_percentual
    FROM acumulado
    ORDER BY year_iso, week_iso;
    """

    df = q_df(
        conn,
        ewm_cov_sql,
        {
            "src": EWM_SOURCE,
            "baseline": skus_baseline_ewm,
        },
    )

    # Guardamos no próprio output qual baseline foi usado.
    # Isso ajuda muito em rastreabilidade.
    df["baseline_snapshot_month"] = snapshot_month_ewm
    df["baseline_skus"] = skus_baseline_ewm

    return df


# ------------------------------------------------------------
# MM - SNAPSHOT FINANCEIRO
# ------------------------------------------------------------
def build_mm_risco(conn: sqlite3.Connection, deposito: str, snapshot_date: str) -> pd.DataFrame:
    """
    Calcula risco financeiro do depósito MM a partir da tabela mm_snapshot.

    Mantemos mm_snapshot como fonte aqui porque este bloco é financeiro
    e já está estruturado para isso.
    """
    sql = """
    SELECT
      :snapshot_date AS snapshot_date,
      :dep AS warehouse_code,
      COUNT(DISTINCT s.material) AS skus_atuais,
      SUM(s.value_total) AS valor_total_atual,

      COUNT(DISTINCT CASE WHEN c.material IS NULL THEN s.material END) AS skus_nao_contados,
      SUM(CASE WHEN c.material IS NULL THEN s.value_total ELSE 0 END) AS valor_nao_contado,

      ROUND(
        (SUM(CASE WHEN c.material IS NULL THEN s.value_total ELSE 0 END) * 100.0) /
        NULLIF(SUM(s.value_total), 0), 2
      ) AS pct_valor_nao_contado
    FROM mm_snapshot s
    LEFT JOIN (
        SELECT DISTINCT material
        FROM counts
        WHERE source_system = :mm_src
          AND warehouse_code = :dep
    ) c
      ON s.material = c.material
    WHERE s.snapshot_date = :snapshot_date
      AND s.warehouse_code = :dep;
    """
    return q_df(conn, sql, {"snapshot_date": snapshot_date, "mm_src": MM_SOURCE, "dep": deposito})


def build_mm_top(conn: sqlite3.Connection, deposito: str, snapshot_date: str, topn: int = 200) -> pd.DataFrame:
    """
    Monta ranking dos materiais MM não contados, ordenados por valor exposto.
    """
    sql = f"""
    SELECT
      s.snapshot_date,
      s.warehouse_code,
      s.material,
      s.material_desc,
      s.qty_total,
      s.value_total
    FROM mm_snapshot s
    LEFT JOIN (
        SELECT DISTINCT material
        FROM counts
        WHERE source_system = :mm_src
          AND warehouse_code = :dep
    ) c
      ON s.material = c.material
    WHERE s.snapshot_date = :snapshot_date
      AND s.warehouse_code = :dep
      AND c.material IS NULL
    ORDER BY s.value_total DESC
    LIMIT {topn};
    """
    return q_df(conn, sql, {"snapshot_date": snapshot_date, "mm_src": MM_SOURCE, "dep": deposito})


def build_mm_sim(conn: sqlite3.Connection, deposito: str, snapshot_date: str) -> pd.DataFrame:
    """
    Simula redução de risco financeiro ao contar Top 50 / Top 100 / Top 200.
    """
    sql = """
    WITH nao_contados AS (
      SELECT s.value_total
      FROM mm_snapshot s
      LEFT JOIN (
          SELECT DISTINCT material
          FROM counts
          WHERE source_system = :mm_src
            AND warehouse_code = :dep
      ) c
        ON s.material = c.material
      WHERE s.snapshot_date = :snapshot_date
        AND s.warehouse_code = :dep
        AND c.material IS NULL
    ),
    total AS (
      SELECT SUM(value_total) AS v_total FROM nao_contados
    )
    SELECT
      :snapshot_date AS snapshot_date,
      :dep AS warehouse_code,
      (SELECT v_total FROM total) AS valor_total_nao_contado,
      (SELECT SUM(value_total) FROM (SELECT value_total FROM nao_contados ORDER BY value_total DESC LIMIT 50)) AS valor_top_50,
      (SELECT SUM(value_total) FROM (SELECT value_total FROM nao_contados ORDER BY value_total DESC LIMIT 100)) AS valor_top_100,
      (SELECT SUM(value_total) FROM (SELECT value_total FROM nao_contados ORDER BY value_total DESC LIMIT 200)) AS valor_top_200
    ;
    """
    df = q_df(conn, sql, {"snapshot_date": snapshot_date, "mm_src": MM_SOURCE, "dep": deposito})

    if df.empty:
        return df

    v_total = float(df.loc[0, "valor_total_nao_contado"] or 0.0)

    for col in ["valor_top_50", "valor_top_100", "valor_top_200"]:
        df[f"pct_{col}"] = (
            round((float(df.loc[0, col] or 0.0) / v_total) * 100.0, 2)
            if v_total else 0.0
        )

    return df


def get_snapshot_dates(conn: sqlite3.Connection) -> dict[str, str]:
    """
    Obtém a data mais recente de snapshot MM por depósito.

    Continua usando mm_snapshot, porque o bloco MM financeiro ainda
    depende dessa tabela.
    """
    sql = """
    SELECT warehouse_code, MAX(snapshot_date) AS snapshot_date
    FROM mm_snapshot
    WHERE warehouse_code IN ('MAST', 'MASR')
    GROUP BY warehouse_code
    ORDER BY warehouse_code;
    """
    df = q_df(conn, sql)
    if df.empty:
        return {}
    return {str(r["warehouse_code"]): str(r["snapshot_date"]) for _, r in df.iterrows()}


# ------------------------------------------------------------
# MAIN - GERAÇÃO DOS OUTPUTS
# ------------------------------------------------------------
def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        # ------------------------------------------------------------
        # KPI 1) EWM - cobertura acumulada por semana (ciclo real)
        # ------------------------------------------------------------
        df_ewm = build_ewm_cobertura(conn)
        df_ewm.to_csv(OUT_DIR / "ewm_cobertura_semanal.csv", index=False, encoding="utf-8-sig")

        # ------------------------------------------------------------
        # KPIs MM por depósito (MAST / MASR)
        # ------------------------------------------------------------
        snapshot_dates = get_snapshot_dates(conn)

        if not snapshot_dates:
            raise RuntimeError("Não achei snapshots em mm_snapshot para MAST/MASR.")

        riscos = []
        tops = []
        sims = []

        for dep in MM_DEPOSITOS:
            snapshot_date = snapshot_dates.get(dep)
            if not snapshot_date:
                continue

            df_risco_dep = build_mm_risco(conn, dep, snapshot_date)
            df_top_dep = build_mm_top(conn, dep, snapshot_date, topn=200)
            df_sim_dep = build_mm_sim(conn, dep, snapshot_date)

            if not df_risco_dep.empty:
                riscos.append(df_risco_dep)

            if not df_top_dep.empty:
                tops.append(df_top_dep)

            if not df_sim_dep.empty:
                sims.append(df_sim_dep)

        df_risco = pd.concat(riscos, ignore_index=True) if riscos else pd.DataFrame()
        df_top = pd.concat(tops, ignore_index=True) if tops else pd.DataFrame()
        df_sim = pd.concat(sims, ignore_index=True) if sims else pd.DataFrame()

        df_risco.to_csv(OUT_DIR / "mm_risco_snapshot.csv", index=False, encoding="utf-8-sig")
        df_top.to_csv(OUT_DIR / "mm_top200_valor_nao_inventariado.csv", index=False, encoding="utf-8-sig")
        df_sim.to_csv(OUT_DIR / "mm_simulacao_topN.csv", index=False, encoding="utf-8-sig")

    print("[DONE] KPIs gerados em:", str(OUT_DIR))

    # ------------------------------------------------------------
    # RELATÓRIO ÚNICO EM EXCEL (1 arquivo, várias abas)
    # ------------------------------------------------------------
    report_path = OUT_DIR / "relatorio_inventarios_mm.xlsx"

    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        df_ewm.to_excel(writer, sheet_name="EWM_Cobertura", index=False)
        df_risco.to_excel(writer, sheet_name="MM_Risco", index=False)
        df_top.to_excel(writer, sheet_name="MM_Top200", index=False)
        df_sim.to_excel(writer, sheet_name="MM_Simulacao", index=False)

        if not df_top.empty:
            for dep in MM_DEPOSITOS:
                df_top_dep = df_top[df_top["warehouse_code"] == dep].head(100).copy()
                if not df_top_dep.empty:
                    df_top_dep.to_excel(writer, sheet_name=f"{dep}_Top100", index=False)

    print("[OK] Excel gerado:", str(report_path))


if __name__ == "__main__":
    main()