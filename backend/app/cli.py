"""Command-line entrypoints: rackiq-generate, rackiq-serve, rackiq-info, rackiq-export-samples."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

from . import capabilities, db, generator


def generate_main() -> None:
    ap = argparse.ArgumentParser(prog="rackiq-generate",
                                 description="(Re)generate the synthetic Soundview book.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n-customers", type=int, default=40)
    ap.add_argument("--months", type=int, default=21)
    ap.add_argument("--terminals", default="Linden,Providence,Albany")
    ap.add_argument("--products", default="RBOB,ULSD,ULSHO")
    ap.add_argument("--profile", choices=["core", "lite", "full"], default="full",
                    help="Which optional field groups to populate (demonstrates capability flex).")
    ap.add_argument("--end-date", default=None, help="YYYY-MM-DD (default: today).")
    ap.add_argument("--db", default=None, help="Path to the DuckDB file (default: backend/data/rackiq.duckdb).")
    args = ap.parse_args()

    cfg = generator.GenConfig(
        seed=args.seed, n_customers=args.n_customers, months=args.months,
        terminals=tuple(t.strip() for t in args.terminals.split(",") if t.strip()),
        products=tuple(p.strip() for p in args.products.split(",") if p.strip()),
        profile=args.profile, end_date=args.end_date,
    )
    con = db.get_connection(args.db, read_only=False)
    try:
        counts = generator.generate(cfg, con)
        caps = capabilities.compute_capabilities(con)
    finally:
        con.close()

    print(f"Generated profile='{args.profile}' seed={args.seed} "
          f"customers={args.n_customers} months={args.months}")
    print(f"  rows: {json.dumps(counts)}")
    print(f"  capabilities enabled: {caps['summary']['enabled']}/{caps['summary']['total']}")
    enabled = [f["key"] for f in caps["features"] if f["enabled"]]
    print(f"  enabled features: {', '.join(enabled)}")


def info_main() -> None:
    con = db.get_connection(read_only=True)
    try:
        counts = db.table_counts(con)
        caps = capabilities.compute_capabilities(con)
    finally:
        con.close()
    print(f"profile={caps['profile']}  rows={json.dumps(counts)}")
    print(f"capabilities enabled: {caps['summary']['enabled']}/{caps['summary']['total']}")


def serve_main() -> None:
    import uvicorn

    from .config import settings
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


# Friendly (non-canonical) headers so the exported samples exercise Data Studio's fuzzy
# column matcher rather than mapping 1:1 by name.
_SAMPLE_EXPORTS = {
    "lifts": {
        "query": "SELECT customer_id, lift_datetime, net_gallons, terminal, product, "
                 "gross_gallons, observed_temp, api_gravity, unit_price, unit_cost "
                 "FROM lifts ORDER BY lift_datetime LIMIT {limit}",
        "headers": {
            "customer_id": "Customer", "lift_datetime": "Lift Date", "net_gallons": "Net Gallons",
            "terminal": "Terminal", "product": "Product", "gross_gallons": "Gross Gallons",
            "observed_temp": "Temp (F)", "api_gravity": "API Gravity", "unit_price": "Sell Price",
            "unit_cost": "Unit Cost",
        },
    },
    "invoices": {
        "query": "SELECT customer_id, invoice_date, due_date, paid_date, invoice_amount, "
                 "credit_limit FROM invoices ORDER BY invoice_date LIMIT {limit}",
        "headers": {
            "customer_id": "Account", "invoice_date": "Invoice Date", "due_date": "Due Date",
            "paid_date": "Paid Date", "invoice_amount": "Amount", "credit_limit": "Credit Limit",
        },
    },
    "market_prices": {
        "query": "SELECT price_date, product, terminal, market_price, nyh_basis, street_rack, "
                 "rack_benchmark, committed_buys, committed_sells FROM market_prices "
                 "ORDER BY price_date LIMIT {limit}",
        "headers": {
            "price_date": "Date", "product": "Product", "terminal": "Terminal",
            "market_price": "Benchmark", "nyh_basis": "Basis", "street_rack": "Posted Rack",
            "rack_benchmark": "OPIS Rack",
            "committed_buys": "Committed Buys", "committed_sells": "Committed Sells",
        },
    },
    "inventory_snapshots": {
        "query": "SELECT snapshot_datetime, terminal, product, tank_id, tank_capacity, min_heel, "
                 "inventory_snapshot, physical_inventory, receipts FROM inventory_snapshots "
                 "ORDER BY snapshot_datetime LIMIT {limit}",
        "headers": {
            "snapshot_datetime": "As Of Date", "terminal": "Terminal", "product": "Product",
            "tank_id": "Tank", "tank_capacity": "Capacity", "min_heel": "Min Heel",
            "inventory_snapshot": "Book Inventory", "physical_inventory": "Physical Inventory",
            "receipts": "Receipts",
        },
    },
    "quotes": {
        "query": "SELECT customer_id, quote_time, product, quoted_price, market_price_at_quote, "
                 "inventory_state, capacity_state, competitor_context, outcome, time_to_decision, "
                 "final_gallons FROM quotes ORDER BY quote_time LIMIT {limit}",
        "headers": {
            "customer_id": "Customer", "quote_time": "Quote Time", "product": "Product",
            "quoted_price": "Quoted Price", "market_price_at_quote": "Market At Quote",
            "inventory_state": "Inventory State", "capacity_state": "Capacity State",
            "competitor_context": "Competitor", "outcome": "Outcome",
            "time_to_decision": "Mins To Decide", "final_gallons": "Final Gallons",
        },
    },
    "receipts": {
        "query": "SELECT receipt_datetime, terminal, product, receipt_source, receipt_gross_gallons, "
                 "receipt_net_gallons, measurement_basis, bl_vs_received_variance FROM receipts "
                 "ORDER BY receipt_datetime LIMIT {limit}",
        "headers": {
            "receipt_datetime": "Receipt Date", "terminal": "Terminal", "product": "Product",
            "receipt_source": "Source", "receipt_gross_gallons": "Gross Gallons",
            "receipt_net_gallons": "Net Gallons", "measurement_basis": "Measurement Basis",
            "bl_vs_received_variance": "BL Variance",
        },
    },
}


def export_samples_main() -> None:
    """Write friendly-headered CSV/Excel samples from a generated book (for Data Studio)."""
    ap = argparse.ArgumentParser(prog="rackiq-export-samples",
                                 description="Export sample CSV/Excel files for Data Studio imports.")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--n-customers", type=int, default=24)
    ap.add_argument("--months", type=int, default=14)
    ap.add_argument("--limit", type=int, default=1200, help="Max rows per sample file.")
    ap.add_argument("--no-dirty", action="store_true",
                    help="Skip the deliberately-dirty Hygiene Studio demo files.")
    repo_root = Path(__file__).resolve().parent.parent.parent
    ap.add_argument("--out", default=str(repo_root / "samples"))
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build a full book in an in-memory DuckDB so we never touch the served file.
    cfg = generator.GenConfig(seed=args.seed, n_customers=args.n_customers,
                              months=args.months, profile="full")
    con = duckdb.connect(":memory:")
    try:
        generator.generate(cfg, con)
        written = []
        for table, spec in _SAMPLE_EXPORTS.items():
            frame = con.execute(spec["query"].format(limit=args.limit)).df()
            frame = frame.rename(columns=spec["headers"])[list(spec["headers"].values())]
            csv_path = out_dir / f"{table}_sample.csv"
            frame.to_csv(csv_path, index=False)
            written.append((csv_path, len(frame)))
            if table == "lifts":  # also emit an Excel sample to exercise the .xlsx path
                xlsx_path = out_dir / f"{table}_sample.xlsx"
                frame.to_excel(xlsx_path, index=False)
                written.append((xlsx_path, len(frame)))

        if not args.no_dirty:
            for path, n in _write_dirty_samples(con, out_dir, args.limit, args.seed):
                written.append((path, n))
    finally:
        con.close()

    print(f"Wrote {len(written)} sample file(s) to {out_dir}:")
    for path, n in written:
        print(f"  {path.name:28s} {n:>6d} rows")


# Spelling/ID variants used to "dirty" customer names so de-duplication has work to do.
def _name_variants(name: str) -> list[str]:
    base = name.strip()
    upper = base.upper()
    return [
        base,
        f"{upper} ",                       # caps + trailing whitespace
        base.replace(" ", "") ,            # de-spaced
        f"{base} Inc",                     # legal-suffix variant
        f"  {base} Dist",                  # leading whitespace + suffix
    ]


def _write_dirty_samples(con, out_dir: Path, limit: int, seed: int):
    """Write deliberately-dirty lifts files to exercise the Data Hygiene Studio.

    ``lifts_dirty.csv``  — customer NAMES (not codes) with spelling/ID variants of a few
    customers (de-duplication), mixed/bad dates, a few negative volumes, exact-duplicate
    rows, and stray whitespace. ``lifts_barrels.csv`` — the same shape with volumes in
    barrels, to demonstrate unit standardization (bbl → gal).
    """
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed + 1)
    df = con.execute(
        "SELECT c.name AS Customer, l.lift_datetime AS \"Lift Date\", l.net_gallons AS \"Net Gallons\", "
        "l.terminal AS Terminal, l.product AS Product, l.gross_gallons AS \"Gross Gallons\", "
        "l.observed_temp AS \"Temp (F)\", l.api_gravity AS \"API Gravity\", "
        "l.unit_price AS \"Sell Price\", l.unit_cost AS \"Unit Cost\" "
        "FROM lifts l JOIN customers c USING (customer_id) "
        f"ORDER BY l.lift_datetime LIMIT {limit}"
    ).df()
    df["Lift Date"] = pd.to_datetime(df["Lift Date"]).dt.strftime("%Y-%m-%d %H:%M:%S")

    # 1) Inject spelling/ID variants for the four busiest customers (de-duplication target).
    busy = df["Customer"].value_counts().head(4).index.tolist()
    for name in busy:
        variants = _name_variants(name)
        idx = df.index[df["Customer"] == name].tolist()
        for i in idx:
            df.at[i, "Customer"] = variants[int(rng.integers(0, len(variants)))]

    # 2) A few mixed-format and bad dates.
    bad_dates = ["13/02/2024", "2024-13-45", "not a date", "07/22/2023"]
    for i in rng.choice(df.index, size=max(3, len(df) // 60), replace=False):
        df.at[i, "Lift Date"] = bad_dates[int(rng.integers(0, len(bad_dates)))]

    # 3) A few negative volumes (will be quarantined, never silently stored). Negate both
    #    net and gross so the anomaly survives net-60 recomputation from gross.
    for i in rng.choice(df.index, size=max(4, len(df) // 100), replace=False):
        df.at[i, "Net Gallons"] = -abs(float(df.at[i, "Net Gallons"]))
        df.at[i, "Gross Gallons"] = -abs(float(df.at[i, "Gross Gallons"]))

    # 4) Some exact-duplicate rows appended (hygiene removes these losslessly).
    dupes = df.loc[rng.choice(df.index, size=max(3, len(df) // 80), replace=False)]
    df = pd.concat([df, dupes], ignore_index=True)

    dirty_path = out_dir / "lifts_dirty.csv"
    df.to_csv(dirty_path, index=False)

    # Barrels variant (volumes ÷ 42) for the unit-standardization demo.
    bbl = df.head(max(60, limit // 4)).copy()
    bbl = bbl[~bbl["Net Gallons"].astype(str).str.startswith("-")]  # keep it tidy
    for col in ("Net Gallons", "Gross Gallons"):
        bbl[col] = (pd.to_numeric(bbl[col], errors="coerce") / 42.0).round(2)
    bbl_path = out_dir / "lifts_barrels.csv"
    bbl.to_csv(bbl_path, index=False)

    return [(dirty_path, len(df)), (bbl_path, len(bbl))]
