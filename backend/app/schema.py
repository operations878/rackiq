"""Canonical schema — the single source of truth for RackIQ.

Everything (DDL, the synthetic generator, capability detection, and the API) derives
from the declarations in this module. A *canonical field* is a unit of customer/terminal
data RackIQ knows how to ingest. There are three REQUIRED core fields and a set of
OPTIONAL fields; the presence (or absence) of the optional fields is what makes
capabilities "flex with the data provided."

Each canonical field is assigned a single *primary table* — the table whose presence of
that field gates the related capability. Some field names also appear physically in other
tables as dimensional/foreign keys (e.g. ``terminal``/``product`` on inventory & market,
``customer_id`` on invoices); those copies are declared as STRUCTURAL columns and are not
re-counted as canonical fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DType(str, Enum):
    VARCHAR = "VARCHAR"
    DOUBLE = "DOUBLE"
    INTEGER = "INTEGER"
    TIMESTAMP = "TIMESTAMP"
    DATE = "DATE"


@dataclass(frozen=True)
class Field:
    """A canonical data field RackIQ can ingest."""

    name: str
    table: str
    dtype: DType
    required: bool
    description: str


# ---- Table names ----------------------------------------------------------------
CUSTOMERS = "customers"
LIFTS = "lifts"
INVENTORY = "inventory_snapshots"
INVOICES = "invoices"
MARKET = "market_prices"

# Canonical data tables whose contents drive capability detection.
CANONICAL_TABLES = [LIFTS, INVENTORY, INVOICES, MARKET]
# All physical tables (customers is a supporting dimension).
ALL_TABLES = [CUSTOMERS, LIFTS, INVENTORY, INVOICES, MARKET]


# ---- Canonical field registry (3 required + 23 optional) ------------------------
CANONICAL_FIELDS: list[Field] = [
    # --- lifts: required core ---
    Field("customer_id", LIFTS, DType.VARCHAR, True, "Customer identifier (core)."),
    Field("lift_datetime", LIFTS, DType.TIMESTAMP, True, "Timestamp of the lift / load event (core)."),
    Field("net_gallons", LIFTS, DType.DOUBLE, True, "Temperature-corrected (net) gallons lifted (core)."),
    # --- lifts: optional ---
    Field("terminal", LIFTS, DType.VARCHAR, False, "Terminal where the lift occurred."),
    Field("product", LIFTS, DType.VARCHAR, False, "Product lifted (RBOB / ULSD / ULSHO)."),
    Field("gross_gallons", LIFTS, DType.DOUBLE, False, "Gross (observed) gallons before temperature correction."),
    Field("observed_temp", LIFTS, DType.DOUBLE, False, "Observed product temperature (deg F)."),
    Field("api_gravity", LIFTS, DType.DOUBLE, False, "API gravity of the product."),
    Field("unit_price", LIFTS, DType.DOUBLE, False, "Price charged to the customer ($/gal)."),
    Field("unit_cost", LIFTS, DType.DOUBLE, False, "Terminal cost of goods ($/gal)."),
    # --- inventory_snapshots: optional ---
    Field("tank_id", INVENTORY, DType.VARCHAR, False, "Tank identifier."),
    Field("tank_capacity", INVENTORY, DType.DOUBLE, False, "Tank shell capacity (gallons)."),
    Field("min_heel", INVENTORY, DType.DOUBLE, False, "Minimum operational heel (gallons)."),
    Field("inventory_snapshot", INVENTORY, DType.DOUBLE, False, "Book inventory at the snapshot (gallons)."),
    Field("physical_inventory", INVENTORY, DType.DOUBLE, False, "Physically gauged inventory (gallons)."),
    Field("receipts", INVENTORY, DType.DOUBLE, False, "Receipts (barge / pipeline) since prior snapshot (gallons)."),
    # --- invoices (AR): optional ---
    Field("invoice_date", INVOICES, DType.DATE, False, "Invoice issue date."),
    Field("due_date", INVOICES, DType.DATE, False, "Payment due date (per terms)."),
    Field("paid_date", INVOICES, DType.DATE, False, "Date paid (NULL = still open)."),
    Field("invoice_amount", INVOICES, DType.DOUBLE, False, "Invoice amount ($)."),
    Field("credit_limit", INVOICES, DType.DOUBLE, False, "Customer credit limit ($)."),
    # --- market_prices: optional ---
    Field("market_price", MARKET, DType.DOUBLE, False, "Benchmark market price ($/gal)."),
    Field("nyh_basis", MARKET, DType.DOUBLE, False, "NY Harbor basis differential ($/gal)."),
    Field("street_rack", MARKET, DType.DOUBLE, False, "Posted street rack price ($/gal)."),
    Field("committed_buys", MARKET, DType.DOUBLE, False, "Committed buy volume / long position (gallons)."),
    Field("committed_sells", MARKET, DType.DOUBLE, False, "Committed sell volume / short position (gallons)."),
]

FIELDS_BY_NAME: dict[str, Field] = {f.name: f for f in CANONICAL_FIELDS}


# ---- Structural / dimensional columns (NOT canonical I/O fields) -----------------
# (name, dtype) pairs that are physical keys or dimension attributes. They precede the
# canonical columns in each table's physical layout.
STRUCTURAL_COLUMNS: dict[str, list[tuple[str, DType]]] = {
    CUSTOMERS: [
        ("customer_id", DType.VARCHAR),
        ("name", DType.VARCHAR),
        ("archetype", DType.VARCHAR),
        ("home_terminal", DType.VARCHAR),
    ],
    LIFTS: [],
    INVENTORY: [
        ("snapshot_datetime", DType.TIMESTAMP),
        ("terminal", DType.VARCHAR),
        ("product", DType.VARCHAR),
    ],
    INVOICES: [
        ("customer_id", DType.VARCHAR),
    ],
    MARKET: [
        ("price_date", DType.DATE),
        ("product", DType.VARCHAR),
        ("terminal", DType.VARCHAR),
    ],
}


# ---- Derived physical layout ----------------------------------------------------
def physical_columns(table: str) -> list[tuple[str, DType, bool]]:
    """Ordered (name, dtype, required) for a table's physical layout.

    Structural columns first (in declared order), then canonical fields whose primary
    table is ``table`` (in registry order). This ordering is authoritative for DDL.
    """
    cols: list[tuple[str, DType, bool]] = []
    for name, dt in STRUCTURAL_COLUMNS.get(table, []):
        cols.append((name, dt, False))
    for f in CANONICAL_FIELDS:
        if f.table == table:
            cols.append((f.name, f.dtype, f.required))
    return cols


def column_names(table: str) -> list[str]:
    return [name for name, _, _ in physical_columns(table)]


def column_types(table: str) -> dict[str, DType]:
    return {name: dt for name, dt, _ in physical_columns(table)}


def ddl_for_table(table: str) -> str:
    lines = []
    for name, dt, required in physical_columns(table):
        suffix = " NOT NULL" if required else ""
        lines.append(f"    {name} {dt.value}{suffix}")
    body = ",\n".join(lines)
    return f"CREATE TABLE IF NOT EXISTS {table} (\n{body}\n)"


def all_ddl() -> list[str]:
    return [ddl_for_table(t) for t in ALL_TABLES]


# ---- Convenience accessors ------------------------------------------------------
def required_field_names() -> list[str]:
    return [f.name for f in CANONICAL_FIELDS if f.required]


def optional_field_names() -> list[str]:
    return [f.name for f in CANONICAL_FIELDS if not f.required]


def optional_fields_for_table(table: str) -> list[str]:
    return [f.name for f in CANONICAL_FIELDS if f.table == table and not f.required]


# ---- Import targets (Data Studio column-mapping) --------------------------------
# A file imported through Data Studio targets exactly one canonical table. Its columns
# map to that table's *import targets*: the structural keys (grain/foreign keys) plus the
# canonical fields whose primary table is this one. A subset of those targets are
# REQUIRED before the file can be committed (e.g. lifts needs the 3 core fields; an AR
# file needs at least customer_id; an inventory file needs its grain keys).

# Human-facing labels + the row grain each table represents (shown in the wizard).
TABLE_LABELS: dict[str, str] = {
    LIFTS: "Lifts / Sales Book",
    INVOICES: "Accounts Receivable (Invoices)",
    INVENTORY: "Inventory Snapshots",
    MARKET: "Market Prices",
}

# Descriptions for the structural (non-canonical) columns so the mapping UI can explain
# every dropdown option, not just canonical fields.
STRUCTURAL_DESCRIPTIONS: dict[str, str] = {
    "snapshot_datetime": "Timestamp of the inventory reading (grain key).",
    "price_date": "Date of the market quote (grain key).",
    "terminal": "Terminal (dimensional key on this table).",
    "product": "Product (dimensional key on this table).",
    "customer_id": "Customer identifier (foreign key on this table).",
}

# Columns that must be mapped before a file can be committed into each table.
REQUIRED_IMPORT_KEYS: dict[str, list[str]] = {
    LIFTS: ["customer_id", "lift_datetime", "net_gallons"],
    INVOICES: ["customer_id"],
    INVENTORY: ["snapshot_datetime", "terminal", "product"],
    MARKET: ["price_date", "product"],
}

# The single datetime/date column that defines a table's time axis (for date-range stats).
PRIMARY_TIME_COLUMN: dict[str, str] = {
    LIFTS: "lift_datetime",
    INVOICES: "invoice_date",
    INVENTORY: "snapshot_datetime",
    MARKET: "price_date",
}

# Tables a user can import into (customers is derived from lifts, never imported directly).
IMPORTABLE_TABLES = [LIFTS, INVOICES, INVENTORY, MARKET]


def import_targets(table: str) -> list[dict]:
    """Ordered mappable columns for an import into ``table``.

    Each target carries enough metadata for the wizard's dropdowns: whether it is a
    canonical field (vs a structural key), whether it is required to commit, its declared
    type, and a human description.
    """
    required = set(REQUIRED_IMPORT_KEYS.get(table, []))
    out: list[dict] = []
    for name, dt, field_required in physical_columns(table):
        f = FIELDS_BY_NAME.get(name)
        is_canonical = f is not None and f.table == table
        desc = f.description if is_canonical else STRUCTURAL_DESCRIPTIONS.get(name, "")
        out.append({
            "name": name,
            "dtype": dt.value,
            "canonical": is_canonical,
            "required": (name in required) or field_required,
            "description": desc,
        })
    return out


def required_import_keys(table: str) -> list[str]:
    return list(REQUIRED_IMPORT_KEYS.get(table, []))


# ---- Data-quality / Hygiene Studio metadata -------------------------------------
# The customer key column on each table that carries one (drives the Customer Master
# crosswalk / de-duplication). Tables without one are skipped by entity resolution.
CUSTOMER_KEY_COLUMN: dict[str, str] = {
    LIFTS: "customer_id",
    INVOICES: "customer_id",
}

# Canonical fields that represent a volume in gallons. These are the columns eligible
# for unit standardization (barrels -> gallons) and the non-negative / sane-bound checks.
VOLUME_FIELDS: set[str] = {
    "net_gallons", "gross_gallons", "tank_capacity", "min_heel",
    "inventory_snapshot", "physical_inventory", "receipts",
    "committed_buys", "committed_sells",
}

# Canonical fields that represent a per-gallon price or a dollar amount.
PRICE_FIELDS: set[str] = {"unit_price", "unit_cost", "market_price", "street_rack", "nyh_basis"}

# Fields that must never be negative (a negative value is a hard data error).
NONNEGATIVE_FIELDS: set[str] = VOLUME_FIELDS | {
    "unit_price", "unit_cost", "market_price", "street_rack",
    "invoice_amount", "credit_limit", "observed_temp",  # observed_temp in deg F, >0 in practice
}

# Inclusive (lo, hi) sane bounds per canonical field for the "within sane bounds" rule.
# Values outside these are flagged (often a unit-mismatch or fat-finger), not auto-dropped.
FIELD_BOUNDS: dict[str, tuple[float, float]] = {
    "net_gallons": (0.0, 2_000_000.0),
    "gross_gallons": (0.0, 2_000_000.0),
    "observed_temp": (-20.0, 160.0),
    "api_gravity": (0.0, 100.0),
    "unit_price": (0.0, 50.0),
    "unit_cost": (0.0, 50.0),
    "market_price": (0.0, 50.0),
    "street_rack": (0.0, 50.0),
    "nyh_basis": (-5.0, 5.0),
    "invoice_amount": (0.0, 100_000_000.0),
    "credit_limit": (0.0, 1_000_000_000.0),
    "tank_capacity": (0.0, 200_000_000.0),
    "min_heel": (0.0, 50_000_000.0),
    "inventory_snapshot": (0.0, 200_000_000.0),
    "physical_inventory": (0.0, 200_000_000.0),
    "receipts": (0.0, 200_000_000.0),
    "committed_buys": (0.0, 1_000_000_000.0),
    "committed_sells": (0.0, 1_000_000_000.0),
}

# Fields a user may fill from a default when missing (low-cardinality dimensional values).
DEFAULTABLE_FIELDS: dict[str, list[str]] = {
    LIFTS: ["terminal", "product"],
    INVENTORY: ["terminal", "product"],
    MARKET: ["terminal", "product"],
}

# Reasonable absolute date window for the "dates in range" rule (catches typo'd years).
MIN_REASONABLE_YEAR = 1990
MAX_REASONABLE_YEAR_OFFSET = 1   # now + this many years is the latest sane date

# One US barrel = 42 US gallons (the only unit standardization we perform).
GALLONS_PER_BARREL = 42.0


def customer_key_column(table: str) -> str | None:
    return CUSTOMER_KEY_COLUMN.get(table)


def volume_fields_for_table(table: str) -> list[str]:
    return [f.name for f in CANONICAL_FIELDS if f.table == table and f.name in VOLUME_FIELDS]
