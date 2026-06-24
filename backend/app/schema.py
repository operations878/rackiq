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
QUOTES = "quotes"              # early feed: the elasticity training set (accept/reject log)
RECEIPTS = "receipts"          # early feed: receipt detail (source / measurement / BL variance)
BOL = "bol_compartments"       # raw compartment rows of a bill-of-lading (rack/truck loadings)

# Canonical data tables whose contents drive capability detection.
CANONICAL_TABLES = [LIFTS, INVENTORY, INVOICES, MARKET, QUOTES, RECEIPTS, BOL]
# All physical tables (customers is a supporting dimension).
ALL_TABLES = [CUSTOMERS, LIFTS, INVENTORY, INVOICES, MARKET, QUOTES, RECEIPTS, BOL]


# ---- Canonical field registry (3 required + 42 optional) ------------------------
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
    Field("rack_benchmark", MARKET, DType.DOUBLE, False, "Street/OPIS posted rack benchmark ($/gal) — the daily pricing reference."),
    # --- quotes (early feed: elasticity training set): optional ---
    Field("quoted_price", QUOTES, DType.DOUBLE, False, "Price we quoted the customer ($/gal)."),
    Field("market_price_at_quote", QUOTES, DType.DOUBLE, False, "Reference market/rack price at quote time ($/gal)."),
    Field("inventory_state", QUOTES, DType.VARCHAR, False, "Our inventory posture at quote (e.g. long / balanced / short)."),
    Field("capacity_state", QUOTES, DType.VARCHAR, False, "Our capacity / logistics posture at quote (e.g. open / tight)."),
    Field("competitor_context", QUOTES, DType.VARCHAR, False, "Competitive context note at quote time."),
    Field("outcome", QUOTES, DType.VARCHAR, False, "Quote outcome: accept / reject / no_response (REJECTIONS are the point)."),
    Field("time_to_decision", QUOTES, DType.DOUBLE, False, "Minutes from quote to the customer's decision."),
    Field("final_gallons", QUOTES, DType.DOUBLE, False, "Gallons actually lifted on an accepted quote (NULL on reject/no_response)."),
    # --- receipts (early feed: receipt detail, capability-gated for P8): optional ---
    Field("receipt_source", RECEIPTS, DType.VARCHAR, False, "Receipt source: marine / pipeline / truck."),
    Field("receipt_gross_gallons", RECEIPTS, DType.DOUBLE, False, "Gross (observed) gallons received."),
    Field("receipt_net_gallons", RECEIPTS, DType.DOUBLE, False, "Net (temperature-corrected) gallons received."),
    Field("measurement_basis", RECEIPTS, DType.VARCHAR, False, "Measurement basis: shore_tank / ship_meter / pipeline_meter / truck_meter."),
    Field("bl_vs_received_variance", RECEIPTS, DType.DOUBLE, False, "Bill-of-lading vs received variance (gallons; signed)."),
    # --- bol_compartments (raw compartment rows; reconciliation groups by bol_number): optional ---
    Field("compartment_gross_gallons", BOL, DType.DOUBLE, False, "Gross (observed) gallons in this compartment."),
    Field("compartment_net_gallons", BOL, DType.DOUBLE, False, "Billed/stated net gallons on the BOL meter ticket for this compartment."),
    Field("compartment_temp", BOL, DType.DOUBLE, False, "Observed loading temperature for this compartment (deg F)."),
    Field("compartment_api", BOL, DType.DOUBLE, False, "API gravity at loading for this compartment."),
    Field("compartment_unit_cost", BOL, DType.DOUBLE, False, "Cost of goods for this compartment ($/gal) — used to dollarize loss."),
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
    LIFTS: [
        # A lift may arrive as a wide BOL/EDI export where several compartment rows share one
        # bill-of-lading number (one disbursement). bol_number is an OPTIONAL grouping key: when
        # present it lets ingestion collapse those compartment rows into a single lift (summing
        # gross/net). It is a structural key — NOT a canonical analytic field — so it never counts
        # toward the capability matrix or the canonical field total.
        ("bol_number", DType.VARCHAR),
    ],
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
    QUOTES: [
        ("customer_id", DType.VARCHAR),
        ("quote_time", DType.TIMESTAMP),
        ("product", DType.VARCHAR),
    ],
    RECEIPTS: [
        ("receipt_datetime", DType.TIMESTAMP),
        ("terminal", DType.VARCHAR),
        ("product", DType.VARCHAR),
    ],
    BOL: [
        ("bol_number", DType.VARCHAR),       # groups compartments into one disbursement event
        ("bol_datetime", DType.TIMESTAMP),
        ("terminal", DType.VARCHAR),
        ("product", DType.VARCHAR),
        ("tank_id", DType.VARCHAR),          # the tank this compartment drew from
        ("meter_id", DType.VARCHAR),         # the loading rack/meter/lane (drift + lane divergence)
        ("customer_id", DType.VARCHAR),      # who lifted (FK; resolved via the crosswalk)
        ("compartment_id", DType.VARCHAR),   # compartment identifier within the BOL
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
    QUOTES: "Quote Log (accept/reject)",
    RECEIPTS: "Receipt Detail",
    BOL: "BOL Compartments (rack loadings)",
}

# Descriptions for the structural (non-canonical) columns so the mapping UI can explain
# every dropdown option, not just canonical fields.
STRUCTURAL_DESCRIPTIONS: dict[str, str] = {
    "snapshot_datetime": "Timestamp of the inventory reading (grain key).",
    "price_date": "Date of the market quote (grain key).",
    "terminal": "Terminal (dimensional key on this table).",
    "product": "Product (dimensional key on this table).",
    "customer_id": "Customer identifier (foreign key on this table).",
    "quote_time": "Timestamp the quote was given (grain key).",
    "receipt_datetime": "Timestamp the receipt landed (grain key).",
    "bol_number": "Bill-of-lading number — groups compartments into one disbursement (grain key).",
    "bol_datetime": "Timestamp the load left the rack (grain key).",
    "tank_id": "Tank the compartment drew from (dimensional key).",
    "meter_id": "Loading rack / meter / lane (dimensional key — drives meter-drift detection).",
    "compartment_id": "Compartment identifier within the BOL.",
}

# Columns that must be mapped before a file can be committed into each table.
REQUIRED_IMPORT_KEYS: dict[str, list[str]] = {
    LIFTS: ["customer_id", "lift_datetime", "net_gallons"],
    INVOICES: ["customer_id"],
    INVENTORY: ["snapshot_datetime", "terminal", "product"],
    MARKET: ["price_date", "product"],
    QUOTES: ["customer_id", "quote_time", "product", "quoted_price", "outcome"],
    RECEIPTS: ["receipt_datetime", "terminal", "product", "receipt_source"],
    # A meaningful compartment row needs only its disbursement identity (bol_number), when it
    # left the rack (bol_datetime), and the billed volume (compartment_net_gallons). terminal /
    # product / tank_id are dimensional keys that sharpen reconciliation but are optional and
    # defaultable — so a partial BOL feed is stored and used, not quarantined wholesale.
    BOL: ["bol_number", "bol_datetime", "compartment_net_gallons"],
}

# The single datetime/date column that defines a table's time axis (for date-range stats).
PRIMARY_TIME_COLUMN: dict[str, str] = {
    LIFTS: "lift_datetime",
    INVOICES: "invoice_date",
    INVENTORY: "snapshot_datetime",
    MARKET: "price_date",
    QUOTES: "quote_time",
    RECEIPTS: "receipt_datetime",
    BOL: "bol_datetime",
}

# Tables a user can import into (customers is derived from lifts, never imported directly).
IMPORTABLE_TABLES = [LIFTS, INVOICES, INVENTORY, MARKET, QUOTES, RECEIPTS, BOL]


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
    QUOTES: "customer_id",
    BOL: "customer_id",
}

# Canonical fields that represent a volume in gallons. These are the columns eligible
# for unit standardization (barrels -> gallons) and the non-negative / sane-bound checks.
VOLUME_FIELDS: set[str] = {
    "net_gallons", "gross_gallons", "tank_capacity", "min_heel",
    "inventory_snapshot", "physical_inventory", "receipts",
    "committed_buys", "committed_sells",
    "final_gallons", "receipt_gross_gallons", "receipt_net_gallons",
    "compartment_gross_gallons", "compartment_net_gallons",
}

# Canonical fields that represent a per-gallon price or a dollar amount.
PRICE_FIELDS: set[str] = {"unit_price", "unit_cost", "market_price", "street_rack", "nyh_basis",
                          "rack_benchmark", "quoted_price", "market_price_at_quote",
                          "compartment_unit_cost"}

# Fields that must never be negative (a negative value is a hard data error).
# Note: bl_vs_received_variance is intentionally excluded — a receipt variance may be signed.
NONNEGATIVE_FIELDS: set[str] = VOLUME_FIELDS | {
    "unit_price", "unit_cost", "market_price", "street_rack",
    "invoice_amount", "credit_limit", "observed_temp",  # observed_temp in deg F, >0 in practice
    "rack_benchmark", "quoted_price", "market_price_at_quote", "time_to_decision",
    "compartment_unit_cost",  # compartment_temp omitted: cold loadings can be sub-freezing (signed)
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
    "rack_benchmark": (0.0, 50.0),
    "quoted_price": (0.0, 50.0),
    "market_price_at_quote": (0.0, 50.0),
    "time_to_decision": (0.0, 1_000_000.0),
    "final_gallons": (0.0, 2_000_000.0),
    "receipt_gross_gallons": (0.0, 200_000_000.0),
    "receipt_net_gallons": (0.0, 200_000_000.0),
    "bl_vs_received_variance": (-50_000_000.0, 50_000_000.0),
    "compartment_gross_gallons": (0.0, 200_000.0),
    "compartment_net_gallons": (0.0, 200_000.0),
    "compartment_temp": (-40.0, 160.0),
    "compartment_api": (0.0, 100.0),
    "compartment_unit_cost": (0.0, 50.0),
}

# Fields a user may fill from a default when missing (low-cardinality dimensional values).
DEFAULTABLE_FIELDS: dict[str, list[str]] = {
    LIFTS: ["terminal", "product"],
    INVENTORY: ["terminal", "product"],
    MARKET: ["terminal", "product"],
    QUOTES: ["product"],
    RECEIPTS: ["terminal", "product"],
    BOL: ["terminal", "product"],
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
