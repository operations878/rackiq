"""Capability matrix — the single source of truth mapping present data to features.

A *feature* declares the canonical fields it requires (and optional fields that enhance
it). At runtime we inspect the loaded data: a field is "present" if it has at least one
non-null value in its primary table. A feature is *enabled* iff all of its required
fields are present. This is what lets RackIQ's capabilities flex with the data provided.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from . import db, schema


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    description: str
    category: str
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...] = dc_field(default_factory=tuple)


# Category ordering for the UI.
CATEGORIES = ["Demand", "Margin", "Receivables", "Inventory", "Market"]

FEATURES: list[Feature] = [
    # --- Demand (the four that survive a "core" book of only required fields) ---
    Feature("demand_ranking", "Customer Demand Ranking",
            "Rank customers by lifted volume and share of book.",
            "Demand", ("customer_id", "net_gallons"), ("terminal", "product")),
    Feature("lift_cadence", "Lift Cadence & Frequency",
            "Inter-lift intervals and ordering rhythm per customer.",
            "Demand", ("customer_id", "lift_datetime")),
    Feature("archetype_detection", "Customer Archetype Detection",
            "Classify customers (ratable, weather-driven, price-chaser, marine, c-store) from lift patterns.",
            "Demand", ("customer_id", "lift_datetime", "net_gallons")),
    Feature("demand_forecast", "Demand Forecast (seasonal-naive)",
            "Project near-term volume from historical seasonality.",
            "Demand", ("customer_id", "lift_datetime", "net_gallons")),
    Feature("product_mix", "Product Mix",
            "Volume split across products.",
            "Demand", ("net_gallons", "product"), ("terminal",)),
    Feature("terminal_breakdown", "Terminal Breakdown",
            "Volume split across terminals.",
            "Demand", ("net_gallons", "terminal"), ("product",)),
    # --- Margin ---
    Feature("net_vs_gross", "Net vs Gross / VCF",
            "Temperature/volume correction analysis (gross-to-net shrinkage).",
            "Margin", ("net_gallons", "gross_gallons"), ("observed_temp", "api_gravity")),
    Feature("margin_analysis", "Per-Gallon Margin",
            "Unit margin and total margin by customer and product.",
            "Margin", ("unit_price", "unit_cost", "net_gallons"), ("product", "terminal")),
    Feature("revenue", "Revenue",
            "Revenue by customer and product from price x volume.",
            "Margin", ("net_gallons", "unit_price"), ("product",)),
    # --- Receivables ---
    Feature("ar_aging", "AR Aging",
            "Open receivables bucketed by age.",
            "Receivables", ("invoice_date", "due_date", "invoice_amount"), ("paid_date",)),
    Feature("dso", "Days Sales Outstanding",
            "Average collection period per customer.",
            "Receivables", ("invoice_date", "paid_date", "invoice_amount")),
    Feature("credit_risk_late_payers", "Credit Risk & Late Payers",
            "Flag chronically late payers and credit exposure.",
            "Receivables", ("due_date", "paid_date"), ("credit_limit", "invoice_amount")),
    # --- Inventory ---
    Feature("inventory_days_of_supply", "Inventory Days of Supply",
            "Days of cover from book inventory above heel vs recent draw.",
            "Inventory", ("inventory_snapshot", "tank_capacity", "min_heel"), ("receipts",)),
    Feature("gain_loss_reconciliation", "Gain/Loss Reconciliation",
            "Book vs physical inventory variance (gain/loss).",
            "Inventory", ("physical_inventory", "inventory_snapshot"), ("receipts",)),
    Feature("tank_utilization", "Tank Utilization",
            "Fill level vs shell capacity.",
            "Inventory", ("inventory_snapshot", "tank_capacity"), ("tank_id",)),
    # --- Market ---
    Feature("basis_tracking", "Basis Tracking",
            "Benchmark market price vs NYH basis and posted rack.",
            "Market", ("market_price", "nyh_basis"), ("street_rack",)),
    Feature("position_committed", "Committed Position (Long/Short)",
            "Net committed position from buys vs sells.",
            "Market", ("committed_buys", "committed_sells")),
]


def field_presence(con) -> dict[str, dict]:
    """For each canonical field: presence + coverage within its primary table.

    coverage = non-null / (row count of that field's own table), so an empty sibling
    table never dilutes another table's coverage.
    """
    nn_by_table = {t: db.nonnull_counts(con, t) for t in schema.CANONICAL_TABLES}
    rows_by_table = {t: db.row_count(con, t) for t in schema.CANONICAL_TABLES}
    out: dict[str, dict] = {}
    for f in schema.CANONICAL_FIELDS:
        nonnull = nn_by_table.get(f.table, {}).get(f.name, 0)
        applicable = rows_by_table.get(f.table, 0)
        coverage = (nonnull / applicable) if applicable else 0.0
        out[f.name] = {
            "present": nonnull > 0,
            "nonnull": int(nonnull),
            "applicable": int(applicable),
            "coverage": round(coverage, 4),
        }
    return out


def compute_capabilities(con) -> dict:
    """Build the full capability-matrix payload served at /api/capabilities."""
    presence = field_presence(con)

    def is_present(name: str) -> bool:
        return presence.get(name, {}).get("present", False)

    features_out = []
    enabled_count = 0
    for ft in FEATURES:
        missing = [r for r in ft.required_fields if not is_present(r)]
        enabled = not missing
        enhanced = [o for o in ft.optional_fields if is_present(o)]
        if enabled:
            enabled_count += 1
            coverage = min(presence[r]["coverage"] for r in ft.required_fields)
        else:
            coverage = 0.0
        features_out.append({
            "key": ft.key,
            "label": ft.label,
            "description": ft.description,
            "category": ft.category,
            "required_fields": list(ft.required_fields),
            "optional_fields": list(ft.optional_fields),
            "enabled": enabled,
            "missing_fields": missing,
            "enhanced_by": enhanced,
            "coverage": round(coverage, 4),
        })

    return {
        "profile": db.get_meta(con, "profile", "unknown"),
        "categories": CATEGORIES,
        "fields": presence,
        "features": features_out,
        "summary": {"enabled": enabled_count, "total": len(FEATURES)},
    }
