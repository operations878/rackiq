"""Command-line entrypoints: rackiq-generate, rackiq-serve, rackiq-info."""

from __future__ import annotations

import argparse
import json

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
