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


def export_playbook_main() -> None:
    """Generate docs/playbook.md from the archetype plays + regime cheat-sheets (Blueprint G)."""
    from . import playbook
    ap = argparse.ArgumentParser(prog="rackiq-export-playbook",
                                 description="Write the Sales Playbook to docs/playbook.md.")
    repo_root = Path(__file__).resolve().parent.parent.parent
    ap.add_argument("--out", default=str(repo_root / "docs" / "playbook.md"))
    args = ap.parse_args()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(playbook.render_markdown(), encoding="utf-8")
    print(f"Wrote playbook → {out_path}")


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
    "bol_compartments": {
        "query": "SELECT bol_number, bol_datetime, terminal, product, tank_id, meter_id, "
                 "customer_id, compartment_id, compartment_gross_gallons, compartment_net_gallons, "
                 "compartment_temp, compartment_api, compartment_unit_cost FROM bol_compartments "
                 "ORDER BY bol_datetime LIMIT {limit}",
        "headers": {
            "bol_number": "BOL Number", "bol_datetime": "BOL Date", "terminal": "Terminal",
            "product": "Product", "tank_id": "Tank", "meter_id": "Meter", "customer_id": "Customer",
            "compartment_id": "Compartment", "compartment_gross_gallons": "Gross Gallons",
            "compartment_net_gallons": "Net Gallons", "compartment_temp": "Temp (F)",
            "compartment_api": "API Gravity", "compartment_unit_cost": "Unit Cost",
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
            for path, n in _write_wide_bol_sample(con, out_dir, args.limit, args.seed):
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

    # 3) A few negative volumes (legitimate corrections/reversals — kept and flagged, never
    #    quarantined). Negate both net and gross so the sign survives net-60 recomputation.
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


# A spread of EDI/admin columns a real BOL export carries — almost always blank. They exist to
# prove the validator never quarantines a row just because optional/unused columns are empty.
_BOL_ADMIN_COLUMNS = [
    "Carrier SCAC", "Carrier Name", "Trailer", "Tractor", "Seal Number", "PO Number",
    "Release Number", "Freight Terms", "Pay Terms", "Tax Jurisdiction", "Tax Code",
    "Federal Tax", "State Tax", "EDI Trace", "EDI Doc Type", "ISA Control", "GS Control",
    "Origin Code", "Destination Code", "Driver", "Account Of", "Exchange Party",
    "Contract Number", "Rate Code", "Special Instructions", "Hold Code", "Reason Code",
    "Misc 1", "Misc 2", "Misc 3", "Reserved A", "Reserved B",
]


def _write_wide_bol_sample(con, out_dir: Path, limit: int, seed: int):
    """Write a realistic *wide* EDI/BOL export to exercise the BOL → lifts import path.

    ``bol_wide_sample.csv`` / ``.xlsx`` — one row per loaded compartment, several compartments
    sharing a ``BOL Number`` (one lift), dozens of mostly-blank admin columns, Excel **serial**
    ship dates mixed with text dates, **negative** reversal rows, stray whitespace, and a handful
    of EDI **control rows** (BOL 0 / gross 0 / net 0, product ``ZZZ``). Importing it walks the
    capability matrix up while quarantining only the genuine control-row junk.
    """
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed + 2)
    base = con.execute(
        "SELECT l.customer_id AS cust, c.name AS nm, l.lift_datetime AS dt, l.net_gallons AS net, "
        "l.gross_gallons AS gross, l.terminal AS term, l.product AS prod, "
        "l.observed_temp AS temp, l.api_gravity AS api "
        "FROM lifts l JOIN customers c USING (customer_id) "
        f"ORDER BY l.lift_datetime LIMIT {max(120, limit // 3)}"
    ).df()

    # Stable numeric consignee numbers (real exports identify the buyer by a numeric code).
    codes = {cid: f"{1000 + i:04d}" for i, cid in enumerate(sorted(base["cust"].unique()))}

    records: list[dict] = []
    bol_seq = 700000
    for r in base.itertuples(index=False):
        bol_seq += 1
        n_comp = int(rng.integers(1, 4))                       # 1..3 compartments per BOL
        net = float(r.net)
        gross = float(r.gross) if r.gross == r.gross else net  # NaN-safe
        w = rng.dirichlet(np.ones(n_comp))
        ship = pd.Timestamp(r.dt).normalize()
        # ~20% of BOLs carry the ship date as an Excel serial number instead of a text date.
        serial = (bol_seq % 5 == 0)
        ship_val = int((ship - pd.Timestamp("1899-12-30")).days) if serial else ship.strftime("%Y-%m-%d")
        # Stray whitespace on some rows (fixed-width EDI fields pad with spaces) — on MAPPED
        # fields so the trim auto-fix has something to clean; identity must still resolve.
        pad = (bol_seq % 7 == 0)
        consignee = f" {codes[r.cust]} " if pad else codes[r.cust]
        nm = f"  {r.nm} " if pad else r.nm
        terminal = f"{r.term}  " if pad else r.term
        for c in range(n_comp):
            records.append({
                "BOL Number": str(bol_seq),
                "Consignee Number": consignee,
                "Consignee Name": nm,
                "Ship Date": ship_val,
                "Net Amount": round(net * w[c], 1),
                "Gross Amount": round(gross * w[c], 1),
                "Terminal": terminal,
                "Product": r.prod,
                "Compartment": f"{bol_seq}-{c + 1}",
                "Load Temp": round(float(r.temp), 1) if r.temp == r.temp else "",
                "API Gravity": round(float(r.api), 1) if r.api == r.api else "",
            })

    # A handful of negative reversal rows (credits / re-bills) — kept and flagged, not dropped.
    for _ in range(max(3, len(records) // 80)):
        src = records[int(rng.integers(0, len(records)))].copy()
        bol_seq += 1
        src["BOL Number"] = str(bol_seq)
        src["Compartment"] = f"{bol_seq}-1"
        src["Net Amount"] = -abs(float(src["Net Amount"]))
        src["Gross Amount"] = -abs(float(src["Gross Amount"]))
        records.append(src)

    # A few EDI control / heartbeat rows: BOL 0, no volume, placeholder product code — genuine junk.
    for k in range(5):
        records.append({
            "BOL Number": "0", "Consignee Number": "9999", "Consignee Name": "EDI CONTROL",
            "Ship Date": "2024-01-01", "Net Amount": 0, "Gross Amount": 0,
            "Terminal": "", "Product": "ZZZ", "Compartment": "", "Load Temp": "", "API Gravity": "",
        })

    df = pd.DataFrame(records)
    for c in _BOL_ADMIN_COLUMNS:                               # dozens of blank/admin columns
        df[c] = ""
    # Shuffle so compartments of a BOL aren't guaranteed adjacent (grouping must not rely on order).
    df = df.sample(frac=1.0, random_state=seed).reset_index(drop=True)

    csv_path = out_dir / "bol_wide_sample.csv"
    df.to_csv(csv_path, index=False)

    # An Excel copy too, so the .xlsx (typed-cell) path is exercised. Real text dates become true
    # Excel dates; the serial-coded ones stay numeric to mimic an export that leaked serials.
    xlsx = df.copy()
    def _maybe_date(v):
        try:
            return pd.Timestamp(str(v))
        except (ValueError, TypeError):
            return v
    xlsx["Ship Date"] = xlsx["Ship Date"].map(_maybe_date)
    xlsx_path = out_dir / "bol_wide_sample.xlsx"
    xlsx.to_excel(xlsx_path, index=False)

    return [(csv_path, len(df)), (xlsx_path, len(df))]
