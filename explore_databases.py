"""Explore Snowflake databases for ARA 10ppm / Platts assessment data."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dash_irr_database"))
from _snowflake import SnowFlake  # noqa: E402

CANDIDATE_DBS = [
    "SPGICI_MARKETDATA_SHARE",
    "SPGICI_EWINDOW_MARKETDATA",
    "COMMOD",
    "COMMOD_UAT",
    "TRADING",
    "TRADING_UAT",
    "ICE",
    "BLOOMBERG",
    "SPGICI_ENERGYPRICE_FORECAST",
]

KEYWORDS = ("%ULSD%", "%10PPM%", "%ARA%", "%MOPL%", "%GASOIL%", "%EWINDOW%", "%PLATTS%", "%BARGE%")


def search_tables(db: str) -> list[tuple]:
    conn = SnowFlake(database=db, schema="PUBLIC")
    cur = conn._conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT table_schema, table_name
            FROM {db}.information_schema.tables
            WHERE table_type = 'BASE TABLE'
              AND (
                UPPER(table_name) LIKE '%EWINDOW%'
                OR UPPER(table_name) LIKE '%ULSD%'
                OR UPPER(table_name) LIKE '%GASOIL%'
                OR UPPER(table_name) LIKE '%ARA%'
                OR UPPER(table_name) LIKE '%MOPL%'
                OR UPPER(table_name) LIKE '%ASSESS%'
                OR UPPER(table_name) LIKE '%PRICE%'
                OR UPPER(table_name) LIKE '%CURVE%'
                OR UPPER(table_name) LIKE '%PLATTS%'
                OR UPPER(table_schema) LIKE '%EWMD%'
                OR UPPER(table_schema) LIKE '%MDV%'
                OR UPPER(table_schema) LIKE '%FC%'
              )
            ORDER BY 1, 2
            LIMIT 80
            """
        )
        rows = cur.fetchall()
    except Exception as exc:
        print(f"\n=== {db}: search failed — {exc}")
        conn._conn.close()
        return []

    print(f"\n=== {db}: {len(rows)} matching tables ===")
    for schema, table in rows[:40]:
        print(f"  {schema}.{table}")
    if len(rows) > 40:
        print(f"  ... and {len(rows) - 40} more")

    conn._conn.close()
    return rows


def search_columns(db: str) -> None:
    conn = SnowFlake(database=db, schema="PUBLIC")
    cur = conn._conn.cursor()
    try:
        conditions = " OR ".join(f"UPPER(column_name) LIKE '{kw}'" for kw in KEYWORDS)
        cur.execute(
            f"""
            SELECT table_schema, table_name, column_name, data_type
            FROM {db}.information_schema.columns
            WHERE ({conditions})
            ORDER BY 1, 2, 3
            LIMIT 60
            """
        )
        rows = cur.fetchall()
    except Exception as exc:
        print(f"  column search failed: {exc}")
        conn._conn.close()
        return

    if rows:
        print(f"  column hits ({len(rows)}):")
        for row in rows[:25]:
            print(f"    {row[0]}.{row[1]}.{row[2]} ({row[3]})")
    conn._conn.close()


def list_schemas(db: str) -> None:
    conn = SnowFlake(database=db, schema="PUBLIC")
    cur = conn._conn.cursor()
    try:
        cur.execute(f"SHOW SCHEMAS IN DATABASE {db}")
        schemas = [r[1] for r in cur.fetchall() if r[1] != "INFORMATION_SCHEMA"]
        print(f"\n=== {db} schemas ({len(schemas)}) ===")
        for s in schemas[:20]:
            print(f"  {s}")
        if len(schemas) > 20:
            print(f"  ... +{len(schemas)-20} more")
    except Exception as exc:
        print(f"\n=== {db}: {exc}")
    conn._conn.close()


def describe_promising(db: str, schema: str, table: str) -> None:
    conn = SnowFlake(database=db, schema=schema)
    cur = conn._conn.cursor()
    full = f"{db}.{schema}.{table}"
    try:
        cur.execute(f"DESCRIBE TABLE {full}")
        cols = [c[0] for c in cur.fetchall()]
        print(f"\n--- {full} ({len(cols)} cols) ---")
        print(", ".join(cols[:25]))
        cur.execute(f"SELECT * FROM {full} LIMIT 3")
        sample = cur.fetchall()
        if sample:
            print("sample row:", sample[0][:8], "...")
    except Exception as exc:
        print(f"  describe {full} failed: {exc}")
    conn._conn.close()


if __name__ == "__main__":
    for db in CANDIDATE_DBS:
        list_schemas(db)
        search_tables(db)
        search_columns(db)

    # Deep-dive known/likely locations
    for target in [
        ("SPGICI_MARKETDATA_SHARE", "MDV2", None),
        ("SPGICI_MARKETDATA_SHARE", "FC", None),
        ("COMMOD", "PUBLIC", None),
        ("ICE", "PUBLIC", None),
    ]:
        db, schema, _ = target
        conn = SnowFlake(database=db, schema=schema)
        cur = conn._conn.cursor()
        try:
            cur.execute(f"SHOW TABLES IN {db}.{schema}")
            tables = [r[1] for r in cur.fetchall()]
            if tables:
                print(f"\n=== ALL tables in {db}.{schema} ({len(tables)}) ===")
                for t in tables[:30]:
                    print(f"  {t}")
        except Exception as exc:
            print(f"  {db}.{schema}: {exc}")
        conn._conn.close()
