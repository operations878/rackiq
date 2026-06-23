"""Tests for the three early data feeds: rack benchmark, quote log, receipt detail.

These feeds "start accumulating now" — they are wired through the column-mapping + hygiene
pipeline, surface as *collecting* (never hard-locked) capabilities, and report running
counts on the data-health dashboard.
"""

from __future__ import annotations

import io

import pandas as pd

from app import capabilities, db, generator, schema


def _feature(caps: dict, key: str) -> dict:
    return next(f for f in caps["features"] if f["key"] == key)


# ---- Schema wiring --------------------------------------------------------------
def test_new_tables_are_canonical_and_importable():
    for t in (schema.QUOTES, schema.RECEIPTS):
        assert t in schema.CANONICAL_TABLES
        assert t in schema.IMPORTABLE_TABLES
        assert t in schema.ALL_TABLES
    assert schema.FIELDS_BY_NAME["rack_benchmark"].table == schema.MARKET
    assert schema.customer_key_column(schema.QUOTES) == "customer_id"
    # bl variance is signed → must NOT be a non-negative field
    assert "bl_vs_received_variance" not in schema.NONNEGATIVE_FIELDS


# ---- Capability matrix: feeds collect rather than hard-lock ----------------------
def test_feeds_collecting_on_core_and_enabled_on_full(con):
    generator.generate(generator.GenConfig(seed=1, n_customers=12, months=10, profile="core"), con)
    caps = capabilities.compute_capabilities(con)
    for key in ("pricing_sandbox", "quote_elasticity", "receipt_detail"):
        f = _feature(caps, key)
        assert f["kind"] == "feed"
        assert f["status"] == "collecting"        # never a hard lock
        assert f["missing_fields"] == []
        assert f["collecting"]["count"] == 0

    generator.generate(generator.GenConfig(seed=1, n_customers=12, months=18, profile="full"), con)
    caps = capabilities.compute_capabilities(con)
    assert _feature(caps, "pricing_sandbox")["status"] == "enabled"
    assert _feature(caps, "quote_elasticity")["status"] == "enabled"
    qe = _feature(caps, "quote_elasticity")
    assert qe["collecting"]["rejections"] > 0       # rejections captured — the whole point
    assert caps["feeds"]["quotes_rejected"] > 0


# ---- Quote-log elasticity signal is recoverable ---------------------------------
def test_generated_quotes_have_negative_price_elasticity(con):
    import numpy as np

    generator.generate(generator.GenConfig(seed=5, n_customers=20, months=14, profile="full"), con)
    q = con.execute(
        "SELECT quoted_price, market_price_at_quote, outcome FROM quotes "
        "WHERE quoted_price IS NOT NULL AND market_price_at_quote IS NOT NULL").df()
    q["acc"] = (q["outcome"] == "accept").astype(int)
    q["spread"] = q["quoted_price"] - q["market_price_at_quote"]
    slope = float(np.polyfit(q["spread"], q["acc"], 1)[0])
    assert slope < 0     # quotes priced above the reference are accepted less often


# ---- API: quick-entry forms route through hygiene -------------------------------
def test_rack_benchmark_quick_entry(client):
    client.post("/api/studio/load-demo", json={"profile": "core"})
    r = client.post("/api/studio/rack-benchmark", json={"entries": [
        {"price_date": "2024-05-01", "terminal": "Linden", "product": "ULSD", "rack_benchmark": 2.71},
        {"price_date": "2024-05-02", "terminal": "Linden", "product": "ULSD", "rack_benchmark": 2.73},
    ]})
    assert r.status_code == 200, r.text
    assert r.json()["rows_written"] == 2
    ps = _feature(r.json()["capabilities"], "pricing_sandbox")
    assert ps["status"] == "collecting" and ps["collecting"]["count"] == 2


def test_quote_quick_entry_counts_rejections(client):
    client.post("/api/studio/load-demo", json={"profile": "core"})
    r = client.post("/api/studio/quote", json={"entries": [
        {"customer_id": "C001", "quote_time": "2024-05-01 10:00", "product": "ULSD",
         "quoted_price": 2.70, "market_price_at_quote": 2.68, "outcome": "reject"},
        {"customer_id": "C001", "quote_time": "2024-05-01 11:00", "product": "ULSD",
         "quoted_price": 2.66, "market_price_at_quote": 2.68, "outcome": "accept", "final_gallons": 5000},
    ]})
    assert r.status_code == 200, r.text
    qe = _feature(r.json()["capabilities"], "quote_elasticity")
    assert qe["collecting"]["count"] == 2
    assert qe["collecting"]["rejections"] == 1
    health = client.get("/api/studio/data-health").json()
    assert health["feeds"]["quotes"]["total"] == 2
    assert health["feeds"]["quotes"]["rejected"] == 1


def test_receipts_import_through_wizard(client):
    df = pd.DataFrame({
        "Receipt Date": ["2024-05-01", "2024-05-02"], "Terminal": ["Linden", "Albany"],
        "Product": ["ULSD", "RBOB"], "Source": ["marine", "truck"],
        "Gross Gallons": [100000, 5000], "Net Gallons": [99900, 4990],
        "Measurement Basis": ["ship_meter", "truck_meter"], "BL Variance": [-50, 5],
    })
    ins = client.post("/api/studio/inspect", files={
        "file": ("receipts.csv", io.BytesIO(df.to_csv(index=False).encode()), "text/csv")}).json()
    assert ins["suggested_table"] == "receipts"
    mapping = {c: s["target"] for c, s in ins["suggestions_by_table"]["receipts"].items()}
    assert mapping["Source"] == "receipt_source"
    co = client.post("/api/studio/commit", json={
        "upload_id": ins["upload_id"], "table": "receipts", "mapping": mapping, "mode": "replace"}).json()
    assert co["rows_written"] == 2
    # negative BL variance is allowed (signed) — it must NOT be quarantined
    assert co["quarantined"] == 0
    assert _feature(co["capabilities"], "receipt_detail")["collecting"]["count"] == 2
