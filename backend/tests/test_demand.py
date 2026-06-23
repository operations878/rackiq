"""Tests for the Demand Cockpit — per-terminal forecast band, days-of-cover, recommended
action, accuracy strip, capability gating, and the persisted forecast distributions."""

from __future__ import annotations

import duckdb
import numpy as np
import pytest

from app import db, demand, generator, scoring


@pytest.fixture(scope="module")
def full_con():
    """A full-profile book (lifts + inventory + market + feeds) for the engine tests."""
    c = duckdb.connect(":memory:")
    db.init_db(c)
    generator.generate(generator.GenConfig(seed=13, n_customers=28, months=20, profile="full"), c)
    scoring.ensure_tables(c)
    demand.ensure_tables(c)
    yield c
    c.close()


# ---- forecast band ---------------------------------------------------------------
def test_forecast_band_monotone_and_nonnegative(full_con):
    ck = demand.cockpit(full_con)
    assert ck["terminal"] in ck["terminals"]
    assert ck["n_customers"] > 0
    assert ck["history"] and ck["forecast"]
    assert len(ck["forecast"]) == demand.DEFAULT_CONFIG.horizon_periods
    for b in ck["forecast"]:
        assert b["p10"] <= b["p50"] <= b["p90"]
        assert b["p10"] >= 0.0


def test_band_widens_with_horizon(full_con):
    ck = demand.cockpit(full_con, terminal=ck_terminal(full_con), product=None)
    widths = [b["p90"] - b["p10"] for b in ck["forecast"]]
    # The uncertainty band should be wider at the end of the horizon than at the start.
    assert widths[-1] >= widths[0]


def ck_terminal(con):
    return demand.cockpit(con)["terminal"]


def test_terminal_rollup_sums_customers(full_con):
    ck = demand.cockpit(full_con, product=None)
    summed = sum(c["next_p50"] for c in ck["customer_forecasts"])
    p50_next = ck["forecast"][0]["p50"]
    # The terminal P50 is the sum of the per-customer P50s (modulo rounding accumulation).
    assert abs(summed - p50_next) <= 0.02 * max(p50_next, 1.0) + 5.0


# ---- forecast accuracy strip -----------------------------------------------------
def test_selected_model_not_worse_than_seasonal(full_con):
    """Per-customer model selection means the chosen model never loses to seasonal-naive."""
    ck = demand.cockpit(full_con, product=None)
    bm = ck["accuracy"]["by_method"]
    if "model" in bm and "seasonal_naive" in bm:
        assert bm["model"] <= bm["seasonal_naive"] + 0.1
    assert ck["accuracy"]["mape"] is not None
    assert ck["accuracy"]["method"]


def test_holt_winters_seasonal_runs_on_long_history():
    """A ≥2-cycle weekly series should be eligible for (and able to fit) Holt-Winters seasonal."""
    cfg = demand.DEFAULT_CONFIG
    n = 2 * cfg.seasonal_periods + 10
    starts = __import__("pandas").date_range("2022-01-03", periods=n, freq="W-MON")
    t = np.arange(n)
    y = 10000 + 50 * t + 3000 * np.sin(2 * np.pi * t / cfg.seasonal_periods) + 200 * np.sin(t)
    methods = demand._feasible_methods(y, cfg)
    assert "holt_winters_seasonal" in methods
    fut = demand._future_starts(starts[-1], 8)
    fc = demand._forecast_method("holt_winters_seasonal", y, starts, fut, cfg)
    assert len(fc) == 8 and np.all(fc >= 0)


# ---- days of cover + burn-down (capability-gated on inventory) -------------------
def test_days_of_cover_and_burndown_when_inventory_present(full_con):
    ck = demand.cockpit(full_con)
    assert ck["availability"]["inventory_cover"]["available"] is True
    assert ck["days_of_cover"] is not None and ck["days_of_cover"] > 0
    bd = ck["burndown"]
    assert bd and bd["series"]
    first = bd["series"][0]
    assert first["heel"] >= 0 and first["capacity"] >= first["heel"]
    # The fast-demand path should never sit above the P50 path (it burns down faster).
    assert all(p["fast"] <= p["p50"] + 1e-6 for p in bd["series"])


# ---- recommended action ----------------------------------------------------------
def test_recommendation_buy_mentions_service_level(full_con):
    ck = demand.cockpit(full_con, service_level=0.95, lead_time_days=5)
    rec = ck["recommendation"]
    assert rec["mode"] in {"buy", "no_demand"}
    assert "service level" in rec["headline"].lower() or rec["mode"] == "no_demand"
    if rec["mode"] == "buy":
        assert rec["buy_quantity"] is not None and rec["buy_by_date"]
        assert rec["days_of_cover"] is not None


def test_service_level_raises_safety_stock_and_quantity(full_con):
    payload = demand.forecast_terminal(full_con, terminal=ck_terminal(full_con), product=None)
    lo = demand.recommend(payload, service_level=0.80, lead_time_days=5)
    hi = demand.recommend(payload, service_level=0.99, lead_time_days=5)
    assert hi["safety_stock"] > lo["safety_stock"]
    if lo["mode"] == "buy" and hi["mode"] == "buy":
        assert hi["buy_quantity"] >= lo["buy_quantity"]


def test_lot_size_rounding(full_con):
    payload = demand.forecast_terminal(full_con, terminal=ck_terminal(full_con), product=None)
    lot = 25000.0
    rec = demand.recommend(payload, service_level=0.95, lead_time_days=5, lot_size=lot)
    if rec["mode"] == "buy" and rec["buy_quantity"] and not rec["quantity_capped"]:
        assert abs(rec["buy_quantity"] % lot) < 1e-6


# ---- capability gating (no inventory → target carry + gap note) ------------------
def test_gating_without_inventory_gives_target_and_gap():
    c = duckdb.connect(":memory:")
    db.init_db(c)
    generator.generate(generator.GenConfig(seed=5, n_customers=18, months=16, profile="lite"), c)
    scoring.ensure_tables(c)
    demand.ensure_tables(c)
    ck = demand.cockpit(c, service_level=0.95)
    # Forecast still works; cover / burn-down are gated off; the action degrades to a target.
    assert ck["availability"]["demand_forecast"]["available"] is True
    assert ck["availability"]["inventory_cover"]["available"] is False
    assert ck["days_of_cover"] is None and ck["burndown"] is None
    rec = ck["recommendation"]
    assert rec["mode"] == "target_only" and rec["supply_gap"] is True
    assert rec["target_inventory"] is not None and rec["gap_note"]
    assert rec["buy_by_date"] is None
    c.close()


def test_core_profile_forecasts_without_terminals():
    c = duckdb.connect(":memory:")
    db.init_db(c)
    generator.generate(generator.GenConfig(seed=9, n_customers=14, months=15, profile="core"), c)
    scoring.ensure_tables(c)
    demand.ensure_tables(c)
    ck = demand.cockpit(c)
    assert ck["terminals"] == [] and ck["n_customers"] > 0
    assert ck["forecast"] and ck["availability"]["inventory_cover"]["available"] is False
    c.close()


# ---- persistence (the P6/P7/P10 contract) ----------------------------------------
def test_persist_writes_and_reads_distributions(full_con):
    out = demand.persist(full_con, window="all")
    assert out["ok"] and out["customer_rows"] > 0 and out["terminal_rows"] > 0
    n_term = full_con.execute("SELECT count(*) FROM demand_forecast_terminal").fetchone()[0]
    assert n_term == out["terminal_rows"]

    rt = demand.read_forecasts(full_con, terminal=out["terminals"][0], level="terminal")
    assert rt["count"] > 0
    for row in rt["rows"]:
        assert row["p10"] <= row["p50"] <= row["p90"]
        assert row["daily_p50"] >= 0.0
    rc = demand.read_forecasts(full_con, terminal=out["terminals"][0], level="customer")
    assert rc["count"] > 0


def test_demand_tables_survive_like_caches():
    """The forecast caches are created by ensure_tables (not init_db), so they survive a reset."""
    c = duckdb.connect(":memory:")
    db.init_db(c)
    demand.ensure_tables(c)
    assert c.execute("SELECT count(*) FROM demand_forecast_terminal").fetchone()[0] == 0
    c.close()


# ---- API flow --------------------------------------------------------------------
def test_api_cockpit_and_persist(client):
    assert client.post("/api/studio/load-demo", json={"profile": "full"}).status_code == 200

    r = client.get("/api/demand/cockpit")
    assert r.status_code == 200, r.text
    ck = r.json()
    assert ck["forecast"] and ck["recommendation"] and "availability" in ck
    assert ck["terminal"] in ck["terminals"]

    # service-level slider changes the action without re-fetching the heavy forecast
    lo = client.get("/api/demand/cockpit", params={"service_level": 0.80}).json()
    hi = client.get("/api/demand/cockpit", params={"service_level": 0.99}).json()
    assert hi["recommendation"]["safety_stock"] > lo["recommendation"]["safety_stock"]

    # persist + read back the distributions
    p = client.post("/api/demand/persist", json={"window": "all"})
    assert p.status_code == 200 and p.json()["terminal_rows"] > 0
    f = client.get("/api/demand/forecasts", params={"level": "terminal"})
    assert f.status_code == 200 and f.json()["count"] > 0


def test_api_config(client):
    r = client.get("/api/demand/config")
    assert r.status_code == 200
    assert "horizon_periods" in r.json()["config"]
