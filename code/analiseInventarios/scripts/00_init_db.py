from pathlib import Path
import sqlite3

# Caminho do banco baseado na raiz do projeto atual (HUB)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data_db" / "inventarios.sqlite"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS counts (
    row_uid TEXT PRIMARY KEY,

    source_system TEXT NOT NULL,
    doc_key TEXT NOT NULL,
    item_key TEXT NOT NULL,

    inv_doc TEXT NOT NULL,
    inv_item TEXT NOT NULL,
    material TEXT NOT NULL,
    warehouse_code TEXT NOT NULL,

    count_date TEXT,
    qty_recorded REAL,
    qty_counted REAL,
    qty_diff REAL,
    value_diff REAL,

    status TEXT,
    counted_by TEXT,

    storage_area TEXT,
    bin_location TEXT,
    stock_type TEXT,
    wh_order TEXT,

    year_iso INTEGER,
    week_iso INTEGER,
    week_start TEXT,
    week_end TEXT,

    file_name TEXT NOT NULL,
    loaded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_counts_week
ON counts (year_iso, week_iso, warehouse_code, source_system);

CREATE INDEX IF NOT EXISTS ix_counts_doc
ON counts (source_system, inv_doc);

CREATE INDEX IF NOT EXISTS ix_counts_material
ON counts (material, warehouse_code, source_system);

DROP VIEW IF EXISTS v_docs;

CREATE VIEW v_docs AS
SELECT
    source_system,
    warehouse_code,
    inv_doc,
    doc_key,
    year_iso,
    week_iso,
    COUNT(*) AS item_lines,
    COUNT(DISTINCT material) AS materials_distinct,
    SUM(COALESCE(qty_diff, 0)) AS diff_total
FROM counts
GROUP BY
    source_system,
    warehouse_code,
    inv_doc,
    doc_key,
    year_iso,
    week_iso;
"""

def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()

    print(f"[OK] Banco criado em: {DB_PATH}")

if __name__ == "__main__":
    main()
