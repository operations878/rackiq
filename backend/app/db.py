"""DuckDB connection management, schema lifecycle, and helpers.

The generator opens a read/write connection (drop + recreate + bulk insert). The API
opens short-lived read-only connections per request (multiple readers are fine; we never
serve and regenerate at the same time).
"""

from __future__ import annotations

import threading
from pathlib import Path

import duckdb
import pandas as pd

from . import schema

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "rackiq.duckdb"

# Internal metadata table (generation provenance). Not a canonical data table.
META_DDL = "CREATE TABLE IF NOT EXISTS meta (key VARCHAR PRIMARY KEY, value VARCHAR)"

# Data Studio persistence. These survive a demo regeneration / data reset on purpose, so
# saved mappings ("profiles") and import history are not lost when the book is reloaded.
STUDIO_DDL = [
    """CREATE TABLE IF NOT EXISTS import_profiles (
        name VARCHAR PRIMARY KEY,
        target_table VARCHAR,
        mapping VARCHAR,
        source_columns VARCHAR,
        created_at VARCHAR
    )""",
    """CREATE TABLE IF NOT EXISTS import_log (
        imported_at VARCHAR,
        target_table VARCHAR,
        filename VARCHAR,
        rows INTEGER,
        mode VARCHAR
    )""",
]


def get_connection(db_path: str | None = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if read_only and not path.exists():
        # DuckDB cannot open a missing file read-only; create + init an empty store first.
        tmp = duckdb.connect(str(path))
        init_db(tmp)
        tmp.close()
    return duckdb.connect(str(path), read_only=read_only)


# ---- Shared read/write connection (used by the live API) ------------------------
# Data Studio writes (imports, demo loads, resets) happen inside the running server, so
# the API holds ONE long-lived read/write connection and serializes access with a lock.
# DuckDB is single-writer per process; a global lock keeps concurrent requests safe.
_shared: dict[str, duckdb.DuckDBPyConnection] = {}
_lock = threading.RLock()


def get_shared_connection(db_path: str | None = None) -> duckdb.DuckDBPyConnection:
    key = str(Path(db_path) if db_path else DEFAULT_DB_PATH)
    with _lock:
        con = _shared.get(key)
        if con is None:
            path = Path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            con = duckdb.connect(str(path), read_only=False)
            init_db(con)
            _shared[key] = con
        return con


def lock() -> threading.RLock:
    """Process-wide lock guarding the shared connection (reentrant)."""
    return _lock


def init_db(con: duckdb.DuckDBPyConnection) -> None:
    for ddl in schema.all_ddl():
        con.execute(ddl)
    con.execute(META_DDL)
    for ddl in STUDIO_DDL:
        con.execute(ddl)


def drop_all(con: duckdb.DuckDBPyConnection) -> None:
    """Drop canonical tables + meta. Data Studio tables (profiles/log) are preserved."""
    for table in schema.ALL_TABLES:
        con.execute(f"DROP TABLE IF EXISTS {table}")
    con.execute("DROP TABLE IF EXISTS meta")


def reset_data(con: duckdb.DuckDBPyConnection) -> None:
    """Empty all canonical tables (and meta) back to a fresh, unfed store."""
    drop_all(con)
    init_db(con)
    set_meta(con, "profile", "empty")


def row_count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    return int(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def nonnull_counts(con: duckdb.DuckDBPyConnection, table: str) -> dict[str, int]:
    """Per-column count of non-null values (DuckDB count(col) ignores NULLs)."""
    cols = schema.column_names(table)
    if not cols:
        return {}
    exprs = ", ".join(f'count("{c}") AS "{c}"' for c in cols)
    row = con.execute(f"SELECT {exprs} FROM {table}").fetchone()
    return {c: int(v) for c, v in zip(cols, row)}


def table_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return {t: row_count(con, t) for t in schema.ALL_TABLES}


def insert_df(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame | None) -> int:
    """Bulk-insert a DataFrame, casting each column to its declared schema type.

    Only columns that exist in both the DataFrame and the table layout are inserted;
    omitted columns are left NULL (this is how data profiles drop optional fields).
    """
    if df is None or len(df) == 0:
        return 0
    types = schema.column_types(table)
    cols = [c for c in schema.column_names(table) if c in df.columns]
    if not cols:
        return 0
    con.register("_ins_df", df)
    try:
        select_exprs = []
        for c in cols:
            dt = types.get(c)
            select_exprs.append(f'CAST("{c}" AS {dt.value}) AS "{c}"' if dt else f'"{c}"')
        col_sql = ", ".join(f'"{c}"' for c in cols)
        con.execute(f"INSERT INTO {table} ({col_sql}) SELECT {', '.join(select_exprs)} FROM _ins_df")
    finally:
        con.unregister("_ins_df")
    return len(df)


def set_meta(con: duckdb.DuckDBPyConnection, key: str, value) -> None:
    con.execute("DELETE FROM meta WHERE key = ?", [key])
    con.execute("INSERT INTO meta (key, value) VALUES (?, ?)", [key, str(value)])


def get_meta(con: duckdb.DuckDBPyConnection, key: str, default=None):
    try:
        row = con.execute("SELECT value FROM meta WHERE key = ?", [key]).fetchone()
    except duckdb.CatalogException:
        return default
    return row[0] if row else default


# ---- Data Studio write helpers --------------------------------------------------
def truncate(con: duckdb.DuckDBPyConnection, table: str) -> None:
    con.execute(f"DELETE FROM {table}")


def rebuild_customers_from_lifts(con: duckdb.DuckDBPyConnection, replace: bool) -> None:
    """Derive the customers dimension from imported lifts.

    Imports rarely carry a customers dimension, but the dashboard joins on it. We
    synthesize one row per distinct customer_id (name = id, archetype = 'imported',
    home_terminal = its most-frequent terminal). On ``replace`` the dimension is rebuilt
    from scratch; otherwise only unseen customers are added.
    """
    if replace:
        con.execute("DELETE FROM customers")
    con.execute("""
        INSERT INTO customers (customer_id, name, archetype, home_terminal)
        SELECT t.customer_id, t.customer_id, 'imported', t.home_terminal
        FROM (
            SELECT customer_id,
                   mode(terminal) AS home_terminal
            FROM lifts
            WHERE customer_id IS NOT NULL
            GROUP BY customer_id
        ) t
        WHERE t.customer_id NOT IN (SELECT customer_id FROM customers)
    """)


# ---- Import profiles (saved column mappings) ------------------------------------
def save_import_profile(con, name: str, target_table: str, mapping_json: str,
                        source_columns_json: str, created_at: str) -> None:
    con.execute("DELETE FROM import_profiles WHERE name = ?", [name])
    con.execute(
        "INSERT INTO import_profiles (name, target_table, mapping, source_columns, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [name, target_table, mapping_json, source_columns_json, created_at],
    )


def list_import_profiles(con) -> list[dict]:
    rows = con.execute(
        "SELECT name, target_table, mapping, source_columns, created_at "
        "FROM import_profiles ORDER BY created_at DESC"
    ).fetchall()
    return [{"name": r[0], "target_table": r[1], "mapping": r[2],
             "source_columns": r[3], "created_at": r[4]} for r in rows]


def delete_import_profile(con, name: str) -> None:
    con.execute("DELETE FROM import_profiles WHERE name = ?", [name])


def log_import(con, imported_at: str, target_table: str, filename: str,
               rows: int, mode: str) -> None:
    con.execute(
        "INSERT INTO import_log (imported_at, target_table, filename, rows, mode) "
        "VALUES (?, ?, ?, ?, ?)",
        [imported_at, target_table, filename, int(rows), mode],
    )


def list_import_log(con, limit: int = 20) -> list[dict]:
    rows = con.execute(
        "SELECT imported_at, target_table, filename, rows, mode "
        "FROM import_log ORDER BY imported_at DESC LIMIT ?", [limit]
    ).fetchall()
    return [{"imported_at": r[0], "target_table": r[1], "filename": r[2],
             "rows": int(r[3]) if r[3] is not None else 0, "mode": r[4]} for r in rows]
