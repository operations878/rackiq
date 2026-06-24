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
# saved mappings ("profiles"), import history, the customer master crosswalk, the hygiene
# audit log, and the quarantine queue are not lost when the book is reloaded.
STUDIO_DDL = [
    """CREATE TABLE IF NOT EXISTS import_profiles (
        name VARCHAR PRIMARY KEY,
        target_table VARCHAR,
        mapping VARCHAR,
        source_columns VARCHAR,
        created_at VARCHAR,
        hygiene VARCHAR
    )""",
    """CREATE TABLE IF NOT EXISTS import_log (
        imported_at VARCHAR,
        target_table VARCHAR,
        filename VARCHAR,
        rows INTEGER,
        mode VARCHAR
    )""",
    # Customer Master crosswalk: every observed customer variant resolves to a master id.
    # status: 'confirmed' (apply the merge) | 'rejected' (keep separate, suppress re-proposal).
    """CREATE TABLE IF NOT EXISTS customer_crosswalk (
        variant_key VARCHAR PRIMARY KEY,
        master_id VARCHAR,
        master_name VARCHAR,
        confidence DOUBLE,
        status VARCHAR,
        source VARCHAR,
        updated_at VARCHAR
    )""",
    # Hygiene audit log: one row per transformation applied during a commit.
    """CREATE TABLE IF NOT EXISTS hygiene_audit (
        ts VARCHAR,
        target_table VARCHAR,
        filename VARCHAR,
        step VARCHAR,
        detail VARCHAR,
        rows_affected INTEGER
    )""",
    # Quarantine queue: rows that failed validation, held for review / fix / re-import.
    """CREATE TABLE IF NOT EXISTS quarantine (
        id VARCHAR PRIMARY KEY,
        ts VARCHAR,
        target_table VARCHAR,
        filename VARCHAR,
        reasons VARCHAR,
        payload VARCHAR
    )""",
]

# Lightweight, idempotent migrations for stores created before a column existed.
STUDIO_MIGRATIONS = [
    "ALTER TABLE import_profiles ADD COLUMN IF NOT EXISTS hygiene VARCHAR",
    # bol_number became an optional lifts column (BOL/EDI exports group compartments → lifts).
    "ALTER TABLE lifts ADD COLUMN IF NOT EXISTS bol_number VARCHAR",
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
    for stmt in STUDIO_MIGRATIONS:
        try:
            con.execute(stmt)
        except duckdb.Error:
            pass  # best-effort migration; the column already exists or backend lacks IF NOT EXISTS


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
    # Prefer the human master name from the crosswalk over the bare id, where we have one.
    con.execute("""
        UPDATE customers SET name = cw.master_name
        FROM customer_crosswalk cw
        WHERE cw.master_id = customers.customer_id
          AND cw.status = 'confirmed'
          AND cw.master_name IS NOT NULL
          AND customers.name = customers.customer_id
    """)


# ---- Crosswalk re-application across the whole store (name-map upload) -----------
# Tables that carry a customer key the crosswalk resolves (lifts is the dimension's source).
_CUSTOMER_KEY_TABLES = [schema.LIFTS, schema.INVOICES, schema.QUOTES, schema.BOL]


def reapply_crosswalk(con: duckdb.DuckDBPyConnection) -> dict:
    """Re-resolve EVERY already-loaded row to its confirmed master id, then rebuild the
    customers dimension with the resolved coded names.

    The crosswalk normally resolves on *future* imports; this regroups + renames data that was
    loaded BEFORE the mapping existed (e.g. after a hand-built name-map upload). All raw names
    mapping to one coded name collapse into a single master customer, so VAR / forecasts / charts
    recompute on the master. Idempotent (a row already at its master id is skipped).
    """
    remapped: dict[str, int] = {}
    for table in _CUSTOMER_KEY_TABLES:
        if row_count(con, table) == 0:
            continue
        moved = con.execute(f"""
            SELECT count(*) FROM {table} t
            JOIN customer_crosswalk cw ON TRIM(CAST(t.customer_id AS VARCHAR)) = cw.variant_key
            WHERE cw.status = 'confirmed' AND cw.master_id IS NOT NULL
              AND cw.master_id <> t.customer_id
        """).fetchone()[0]
        if moved:
            con.execute(f"""
                UPDATE {table} SET customer_id = cw.master_id
                FROM customer_crosswalk cw
                WHERE TRIM(CAST({table}.customer_id AS VARCHAR)) = cw.variant_key
                  AND cw.status = 'confirmed' AND cw.master_id IS NOT NULL
                  AND cw.master_id <> {table}.customer_id
            """)
            remapped[table] = int(moved)

    # Rebuild the customers dimension: drop stale variant rows, add new masters, apply coded names.
    con.execute("""
        DELETE FROM customers
        WHERE customer_id NOT IN (SELECT DISTINCT customer_id FROM lifts WHERE customer_id IS NOT NULL)
    """)
    con.execute("""
        INSERT INTO customers (customer_id, name, archetype, home_terminal)
        SELECT t.customer_id, t.customer_id, 'imported', t.home_terminal
        FROM (SELECT customer_id, mode(terminal) AS home_terminal FROM lifts
              WHERE customer_id IS NOT NULL GROUP BY customer_id) t
        WHERE t.customer_id NOT IN (SELECT customer_id FROM customers)
    """)
    con.execute("""
        UPDATE customers SET name = cw.master_name
        FROM customer_crosswalk cw
        WHERE cw.master_id = customers.customer_id
          AND cw.status = 'confirmed' AND cw.master_name IS NOT NULL
    """)
    return {"remapped": remapped, "total_remapped": sum(remapped.values())}


def unmapped_customers(con: duckdb.DuckDBPyConnection, limit: int = 500) -> list[dict]:
    """Customers still shown by their raw id (name == id) and NOT resolved to a confirmed
    crosswalk master — i.e. raw BOL account names the hand-built name-map doesn't cover yet."""
    rows = con.execute("""
        SELECT c.customer_id, c.name,
               count(l.customer_id)            AS lifts,
               coalesce(sum(l.net_gallons), 0) AS gal,
               max(l.lift_datetime)            AS last_lift
        FROM customers c
        LEFT JOIN lifts l USING (customer_id)
        WHERE c.name = c.customer_id
          AND c.customer_id NOT IN (
              SELECT master_id FROM customer_crosswalk
              WHERE status = 'confirmed' AND master_id IS NOT NULL)
        GROUP BY 1, 2
        ORDER BY gal DESC
        LIMIT ?
    """, [limit]).fetchall()
    return [{"customer_id": r[0], "name": r[1], "lift_count": int(r[2]),
             "total_net_gallons": round(float(r[3]), 1),
             "last_lift": str(r[4].date()) if r[4] else None} for r in rows]


def crosswalk_master_count(con: duckdb.DuckDBPyConnection) -> int:
    row = con.execute(
        "SELECT count(DISTINCT master_id) FROM customer_crosswalk WHERE status = 'confirmed'"
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


# ---- Import profiles (saved column mappings + cleaning options) -----------------
def save_import_profile(con, name: str, target_table: str, mapping_json: str,
                        source_columns_json: str, created_at: str,
                        hygiene_json: str | None = None) -> None:
    con.execute("DELETE FROM import_profiles WHERE name = ?", [name])
    con.execute(
        "INSERT INTO import_profiles (name, target_table, mapping, source_columns, created_at, hygiene) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [name, target_table, mapping_json, source_columns_json, created_at, hygiene_json],
    )


def list_import_profiles(con) -> list[dict]:
    rows = con.execute(
        "SELECT name, target_table, mapping, source_columns, created_at, hygiene "
        "FROM import_profiles ORDER BY created_at DESC"
    ).fetchall()
    return [{"name": r[0], "target_table": r[1], "mapping": r[2],
             "source_columns": r[3], "created_at": r[4], "hygiene": r[5]} for r in rows]


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


# ---- Customer Master crosswalk --------------------------------------------------
def get_crosswalk(con) -> dict[str, dict]:
    """Map every known variant_key -> {master_id, master_name, confidence, status, source}."""
    rows = con.execute(
        "SELECT variant_key, master_id, master_name, confidence, status, source "
        "FROM customer_crosswalk"
    ).fetchall()
    return {r[0]: {"master_id": r[1], "master_name": r[2],
                   "confidence": float(r[3]) if r[3] is not None else None,
                   "status": r[4], "source": r[5]} for r in rows}


def list_crosswalk(con) -> list[dict]:
    rows = con.execute(
        "SELECT variant_key, master_id, master_name, confidence, status, source, updated_at "
        "FROM customer_crosswalk ORDER BY master_id, variant_key"
    ).fetchall()
    return [{"variant_key": r[0], "master_id": r[1], "master_name": r[2],
             "confidence": float(r[3]) if r[3] is not None else None,
             "status": r[4], "source": r[5], "updated_at": r[6]} for r in rows]


def upsert_crosswalk_entries(con, entries: list[dict]) -> int:
    """Insert/replace crosswalk rows. Each entry: variant_key, master_id, master_name,
    confidence, status, source, updated_at."""
    n = 0
    for e in entries:
        con.execute("DELETE FROM customer_crosswalk WHERE variant_key = ?", [e["variant_key"]])
        con.execute(
            "INSERT INTO customer_crosswalk "
            "(variant_key, master_id, master_name, confidence, status, source, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [e["variant_key"], e.get("master_id"), e.get("master_name"),
             e.get("confidence"), e.get("status", "confirmed"),
             e.get("source", "manual"), e.get("updated_at")],
        )
        n += 1
    return n


def delete_crosswalk_entry(con, variant_key: str) -> None:
    con.execute("DELETE FROM customer_crosswalk WHERE variant_key = ?", [variant_key])


def clear_crosswalk(con) -> None:
    con.execute("DELETE FROM customer_crosswalk")


# ---- Hygiene audit log ----------------------------------------------------------
def log_hygiene_audit(con, at: str, target_table: str, filename: str,
                      entries: list[dict]) -> None:
    for e in entries:
        con.execute(
            "INSERT INTO hygiene_audit (ts, target_table, filename, step, detail, rows_affected) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [at, target_table, filename, e.get("step"), e.get("detail"),
             int(e.get("rows_affected", 0) or 0)],
        )


def list_hygiene_audit(con, limit: int = 100) -> list[dict]:
    rows = con.execute(
        "SELECT ts, target_table, filename, step, detail, rows_affected "
        "FROM hygiene_audit ORDER BY ts DESC LIMIT ?", [limit]
    ).fetchall()
    return [{"at": r[0], "target_table": r[1], "filename": r[2], "step": r[3],
             "detail": r[4], "rows_affected": int(r[5]) if r[5] is not None else 0}
            for r in rows]


# ---- Quarantine queue -----------------------------------------------------------
def add_quarantine(con, rows: list[dict]) -> int:
    for r in rows:
        con.execute(
            "INSERT INTO quarantine (id, ts, target_table, filename, reasons, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [r["id"], r["at"], r["target_table"], r.get("filename"),
             r.get("reasons"), r.get("payload")],
        )
    return len(rows)


def list_quarantine(con, table: str | None = None, limit: int = 500) -> list[dict]:
    if table:
        rows = con.execute(
            "SELECT id, ts, target_table, filename, reasons, payload FROM quarantine "
            "WHERE target_table = ? ORDER BY ts DESC LIMIT ?", [table, limit]).fetchall()
    else:
        rows = con.execute(
            "SELECT id, ts, target_table, filename, reasons, payload FROM quarantine "
            "ORDER BY ts DESC LIMIT ?", [limit]).fetchall()
    return [{"id": r[0], "at": r[1], "target_table": r[2], "filename": r[3],
             "reasons": r[4], "payload": r[5]} for r in rows]


def quarantine_counts(con) -> dict[str, int]:
    rows = con.execute(
        "SELECT target_table, count(*) FROM quarantine GROUP BY 1").fetchall()
    return {r[0]: int(r[1]) for r in rows}


def get_quarantine_rows(con, ids: list[str]) -> list[dict]:
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    rows = con.execute(
        f"SELECT id, ts, target_table, filename, reasons, payload FROM quarantine "
        f"WHERE id IN ({placeholders})", ids).fetchall()
    return [{"id": r[0], "at": r[1], "target_table": r[2], "filename": r[3],
             "reasons": r[4], "payload": r[5]} for r in rows]


def delete_quarantine(con, ids: list[str]) -> int:
    if not ids:
        return 0
    placeholders = ", ".join("?" for _ in ids)
    con.execute(f"DELETE FROM quarantine WHERE id IN ({placeholders})", ids)
    return len(ids)


def clear_quarantine(con, table: str | None = None) -> None:
    if table:
        con.execute("DELETE FROM quarantine WHERE target_table = ?", [table])
    else:
        con.execute("DELETE FROM quarantine")
