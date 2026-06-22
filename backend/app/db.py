"""DuckDB connection management, schema lifecycle, and helpers.

The generator opens a read/write connection (drop + recreate + bulk insert). The API
opens short-lived read-only connections per request (multiple readers are fine; we never
serve and regenerate at the same time).
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from . import schema

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "rackiq.duckdb"

# Internal metadata table (generation provenance). Not a canonical data table.
META_DDL = "CREATE TABLE IF NOT EXISTS meta (key VARCHAR PRIMARY KEY, value VARCHAR)"


def get_connection(db_path: str | None = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if read_only and not path.exists():
        # DuckDB cannot open a missing file read-only; create + init an empty store first.
        tmp = duckdb.connect(str(path))
        init_db(tmp)
        tmp.close()
    return duckdb.connect(str(path), read_only=read_only)


def init_db(con: duckdb.DuckDBPyConnection) -> None:
    for ddl in schema.all_ddl():
        con.execute(ddl)
    con.execute(META_DDL)


def drop_all(con: duckdb.DuckDBPyConnection) -> None:
    for table in schema.ALL_TABLES:
        con.execute(f"DROP TABLE IF EXISTS {table}")
    con.execute("DROP TABLE IF EXISTS meta")


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
