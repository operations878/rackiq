"""Tests for the Reconciliation & Loss-Control module (P8).

Covers the canonical wiring of bol_compartments, the capability gate, and the engine:
book-vs-physical gain/loss, BOL-grouped disbursements, the net-recon cross-check, the
loss-mechanism split, control-chart meter-drift detection, receipt basis, and dollarizing.
The synthetic ``full`` book deliberately seeds small routine losses + a bad-VCF lane + meter-
drift tanks + a high-evaporation tank, so the offenders below are expected to be found.
"""

from __future__ import annotations

import io

import pandas as pd

from app import capabilities, generator, reconciliation, schema


def _feature(caps: dict, key: str) -> dict:
    return next(f for f in caps["features"] if f["key"] == key)


def _full(con, seed=42, n=40, months=21):
    generator.generate(generator.GenConfig(seed=seed, n_customers=n, months=months, profile="full"), con)
    return con


# ---- Schema wiring --------------------------------------------------------------
def test_bol_compartments_is_canonical_and_importable():
    assert schema.BOL in schema.CANONICAL_TABLES
    assert schema.BOL in schema.ALL_TABLES
    assert schema.BOL in schema.IMPORTABLE_TABLES
    assert schema.FIELDS_BY_NAME["compartment_net_gallons"].table == schema.BOL
    assert schema.customer_key_column(schema.BOL) == "customer_id"
    # A meaningful compartment row needs its disbursement id, timestamp, and billed volume.
    for k in ("bol_number", "bol_datetime", "compartment_net_gallons"):
        assert k in schema.required_import_keys(schema.BOL)
    # terminal / product / tank_id sharpen reconciliation but are optional & defaultable, so
    # a partial BOL feed is stored and used rather than quarantined wholesale.
    for k in ("terminal", "product", "tank_id"):
        assert k not in schema.required_import_keys(schema.BOL)
    # compartment_temp must stay signed (cold loadings can be sub-freezing)
    assert "compartment_temp" not in schema.NONNEGATIVE_FIELDS


# ---- Capability gate ------------------------------------------------------------
def test_reconciliation_gated_then_unlocked(con):
    generator.generate(generator.GenConfig(seed=1, n_customers=12, months=10, profile="core"), con)
    f = _feature(capabilities.compute_capabilities(con), "reconciliation")
    assert f["enabled"] is False and f["status"] == "locked"
    assert "physical_inventory" in f["missing_fields"] and "receipt_source" in f["missing_fields"]

    _full(con, seed=1, n=14, months=14)
    f = _feature(capabilities.compute_capabilities(con), "reconciliation")
    assert f["enabled"] is True and f["status"] == "enabled"


def test_engine_locked_payload_on_lite(con):
    generator.generate(generator.GenConfig(seed=2, n_customers=12, months=12, profile="lite"), con)
    r = reconciliation.compute_reconciliation(con)
    assert r["available"] is False
    assert "physical_inventory" in r["missing_fields"]
    assert "Feed me" in r["reason"]


# ---- (1) Book vs physical + BOL grouping ----------------------------------------
def test_disbursements_grouped_by_bol_not_per_compartment(con):
    _full(con)
    r = reconciliation.compute_reconciliation(con, period="month")
    assert r["available"] and r["has_bol"]
    n_compartments = con.execute("SELECT count(*) FROM bol_compartments").fetchone()[0]
    n_bols = con.execute("SELECT count(DISTINCT bol_number) FROM bol_compartments").fetchone()[0]
    assert n_bols < n_compartments                       # compartments really are grouped
    nr = r["net_recon"]
    assert nr["checked_compartments"] == n_compartments  # every compartment recomputed
    assert nr["checked_bols"] == n_bols                  # but the cross-check is per BOL
    assert nr["checked_bols"] < nr["checked_compartments"]


def test_network_and_tanks_present(con):
    _full(con)
    r = reconciliation.compute_reconciliation(con, period="month")
    net = r["network"]
    assert net["throughput_gal"] > 0 and net["n_tanks"] >= 5
    assert 0 < net["loss_pct"] < 2.0                     # realistic terminal loss, not absurd
    assert net["gross_loss_gal"] is not None


# ---- (3) Loss-mechanism decomposition -------------------------------------------
def test_mechanism_split_is_additive(con):
    _full(con)
    r = reconciliation.compute_reconciliation(con, period="month")
    for t in r["tanks"]:
        m = t["mechanism"]
        if m["physical"] is None:
            continue
        # measurement + physical == net loss; + temperature == gross loss
        assert abs((m["measurement"] + m["physical"]) - t["net_loss_gal"]) < 2.0
        assert abs((m["measurement"] + m["physical"] + m["temperature"]) - t["gross_loss_gal"]) < 2.0


def test_seeded_offenders_are_found(con):
    _full(con)
    r = reconciliation.compute_reconciliation(con, period="month")
    tanks = r["tanks"]
    # a measurement-driven offender (the bad-VCF lane / drift tanks) and a physical one (evap)
    meas = [t for t in tanks if t["dominant_mechanism"] == "measurement" and t["control"]["persistent_out"]]
    phys = [t for t in tanks if t["dominant_mechanism"] == "physical" and t["control"]["persistent_out"]]
    assert meas, "expected a measurement-dominated tank out of control (drift / bad-VCF lane)"
    assert phys, "expected a physical-dominated tank out of control (evaporation)"
    # worst measurement offender clears the network average comfortably
    assert max(t["loss_pct"] for t in meas) > r["network"]["loss_pct"]


# ---- (2) Net-recon cross-check --------------------------------------------------
def test_net_recon_flags_systematic_meter_and_preserves_billed(con):
    _full(con)
    before = con.execute("SELECT round(sum(compartment_net_gallons),1) FROM bol_compartments").fetchone()[0]
    r = reconciliation.compute_reconciliation(con, period="month")
    nr = r["net_recon"]
    assert nr["available"]
    systematic = [m for m in nr["by_meter"] if m["systematic"]]
    assert systematic, "expected at least one systematically divergent meter/lane"
    assert any(m["flag_label"] for m in systematic)
    # billed net is reported-against, never overwritten
    after = con.execute("SELECT round(sum(compartment_net_gallons),1) FROM bol_compartments").fetchone()[0]
    assert before == after


# ---- (4) Receipt measurement basis ----------------------------------------------
def test_receipt_basis_surfaces_vef_and_shrink(con):
    _full(con)
    r = reconciliation.compute_reconciliation(con, period="month")
    rb = r["receipts"]
    assert rb["available"]
    by = {s["source"]: s for s in rb["by_source"]}
    # marine vessel-experience-factor and pipeline shrink come back as their own (negative) lines
    if "marine" in by:
        assert by["marine"]["bl_variance_pct"] <= 0
        assert "VEF" in by["marine"]["label"]
    if "pipeline" in by:
        assert by["pipeline"]["bl_variance_pct"] <= 0


# ---- (6) Meter-drift control chart ----------------------------------------------
def test_meter_drift_control_chart(con):
    _full(con)
    r = reconciliation.compute_reconciliation(con, period="month")
    md = r["meter_drift"]
    assert md["n_out_of_control"] >= 1
    ranked = md["ranked"]
    assert ranked and ranked[0]["severity"] >= ranked[-1]["severity"]   # ranked by severity
    # at least one drift tank shows the loss-% climbing over time
    assert any(d["trend"] == "rising" for d in ranked)


# ---- (7) Dollarize --------------------------------------------------------------
def test_dollarized_and_ranked(con):
    _full(con)
    r = reconciliation.compute_reconciliation(con, period="month")
    tanks = r["tanks"]
    dollars = [t["dollar_loss_per_yr"] for t in tanks]
    assert dollars == sorted(dollars, reverse=True)       # worst offenders first
    assert r["network"]["recoverable_dollar_per_yr"] > 0
    assert "%" in tanks[0]["vs_network"] and "/yr" in tanks[0]["vs_network"]


# ---- API ------------------------------------------------------------------------
def test_reconciliation_api_flow(client):
    client.post("/api/studio/load-demo", json={"profile": "full"})
    r = client.get("/api/reconciliation?period=month").json()
    assert r["available"] and r["network"]["n_tanks"] >= 5
    assert r["tanks"] and r["net_recon"]["available"]

    cfg = client.get("/api/reconciliation/config").json()
    assert "month" in cfg["period_grains"]

    bad = client.get("/api/reconciliation?period=decade")
    assert bad.status_code == 400


def test_reconciliation_api_locked_on_core(client):
    client.post("/api/studio/load-demo", json={"profile": "core"})
    r = client.get("/api/reconciliation").json()
    assert r["available"] is False
    assert "physical_inventory" in r["missing_fields"]


def test_bol_file_imports_through_wizard(client):
    client.post("/api/studio/reset", json={})
    df = pd.DataFrame({
        "BOL Number": ["B1", "B1", "B2"], "BOL Date": ["2024-05-01", "2024-05-01", "2024-05-02"],
        "Terminal": ["Linden", "Linden", "Albany"], "Product": ["ULSD", "ULSD", "RBOB"],
        "Tank": ["LIN-ULSD-1", "LIN-ULSD-1", "ALB-RBOB-1"], "Meter": ["M1", "M1", "M2"],
        "Compartment": ["B1-C1", "B1-C2", "B2-C1"],
        "Gross Gallons": [3000, 2500, 4000], "Net Gallons": [2990, 2490, 3980],
        "Temp (F)": [62, 62, 70], "API Gravity": [36, 36, 60], "Unit Cost": [2.5, 2.5, 2.3],
    })
    ins = client.post("/api/studio/inspect", files={
        "file": ("bol.csv", io.BytesIO(df.to_csv(index=False).encode()), "text/csv")}).json()
    assert ins["suggested_table"] == "bol_compartments"
    mapping = {c: s["target"] for c, s in ins["suggestions_by_table"]["bol_compartments"].items()}
    for k in ("bol_number", "bol_datetime", "tank_id", "compartment_net_gallons"):
        assert k in mapping.values(), f"{k} should auto-map"
    co = client.post("/api/studio/commit", json={
        "upload_id": ins["upload_id"], "table": "bol_compartments", "mapping": mapping,
        "mode": "replace"}).json()
    assert co["rows_written"] == 3
