"""Tests for the Pricing Sandbox + Pricing Engine (Blueprint I).

Covers the capability gate (locked without unit_price / rack_benchmark), the acceptance model
(per-segment logistic fit + a monotone accept-vs-spread curve), the sandbox (margin-vs-spread
curve and the margin-maximizing post), the recommendation engine (GP-maximizing quote price, the
shadow-price floor never discounts below street under a binding constraint, underpriced ranking),
reading P5's persisted forecast, and the API flow.
"""

from __future__ import annotations

import duckdb
import pytest

from app import capabilities, db, demand, generator, pricing, scoring
from app.pricing_config import DEFAULT_CONFIG


@pytest.fixture(scope="module")
def full_con():
    """A full-profile book (lifts + market rack benchmark + quote log) for the engine tests."""
    c = duckdb.connect(":memory:")
    db.init_db(c)
    generator.generate(generator.GenConfig(seed=42, n_customers=36, months=20, profile="full"), c)
    scoring.ensure_tables(c)
    demand.ensure_tables(c)
    yield c
    c.close()


def _feature(caps, key):
    return next(f for f in caps["features"] if f["key"] == key)


# ---- capability gating -----------------------------------------------------------
def test_gate_locked_without_price_and_rack():
    c = duckdb.connect(":memory:")
    db.init_db(c)
    generator.generate(generator.GenConfig(seed=5, n_customers=16, months=14, profile="lite"), c)
    av = pricing.availability(c)
    assert av["available"] is False
    assert "unit_price" in av["missing_fields"] and "rack_benchmark" in av["missing_fields"]
    base = pricing.build_base(c, DEFAULT_CONFIG, None, "all", None)
    assert base["available"] is False and base["customers"] == []
    # The capability feature is locked on a lite book and enabled on a full one.
    assert _feature(capabilities.compute_capabilities(c), "pricing_engine")["status"] == "locked"
    c.close()


def test_gate_enabled_on_full(full_con):
    av = pricing.availability(full_con)
    assert av["available"] is True and av["has_cost"] is True
    assert av["acceptance_source"] == "quote_model"
    assert av["collecting"]["rack_benchmark"]["matured"] and av["collecting"]["quotes"]["matured"]
    assert _feature(capabilities.compute_capabilities(full_con), "pricing_engine")["status"] == "enabled"


# ---- acceptance model ------------------------------------------------------------
def test_acceptance_fits_and_is_downward_sloping(full_con):
    base = pricing.build_base(full_con, DEFAULT_CONFIG, None, "all", None)
    acc = base["acceptance"]
    assert acc["source"] == "quote_model"
    assert acc["b_spread"] is not None and acc["b_spread"] < 0      # accept falls as price rises
    # P(accept) must strictly decrease as the posted spread climbs, for a representative customer.
    cust = base["customers"][0]
    p_low = pricing.accept_prob(acc, cust, -0.04, None, DEFAULT_CONFIG)
    p_mid = pricing.accept_prob(acc, cust, 0.00, None, DEFAULT_CONFIG)
    p_high = pricing.accept_prob(acc, cust, 0.06, None, DEFAULT_CONFIG)
    assert p_low > p_mid > p_high
    assert 0.0 <= p_high and p_low <= 1.0


def test_acceptance_proxy_when_no_quotes():
    """Without a quote log the engine still runs via the elasticity proxy (gated only on price+rack)."""
    c = duckdb.connect(":memory:")
    db.init_db(c)
    generator.generate(generator.GenConfig(seed=7, n_customers=20, months=16, profile="full"), c)
    c.execute("DELETE FROM quotes")
    scoring.ensure_tables(c)
    av = pricing.availability(c)
    assert av["available"] is True and av["acceptance_source"] == "elasticity_proxy"
    base = pricing.build_base(c, DEFAULT_CONFIG, None, "all", None)
    assert base["acceptance"]["source"] == "elasticity_proxy"
    sb = pricing.sandbox(base, DEFAULT_CONFIG)
    assert sb["optimal_spread"] is not None
    c.close()


# ---- sandbox ---------------------------------------------------------------------
def test_sandbox_curve_and_optimum(full_con):
    base = pricing.build_base(full_con, DEFAULT_CONFIG, None, "all", None)
    sb = pricing.sandbox(base, DEFAULT_CONFIG)
    grid = sb["grid"]
    assert len(grid) > 5 and sb["has_cost"] is True
    assert sb["n_customers"] > 0
    # The margin-maximizing post is on the grid and beats leaving spreads where they are.
    assert grid[0] <= sb["optimal_spread"] <= grid[-1]
    assert sb["optimal_margin"] >= sb["realized_margin"] - 1.0
    assert sb["margin_uplift"] >= -1.0
    # Each customer carries a full-length response curve so the frontend can toggle + re-sum.
    for cu in sb["customers"]:
        assert len(cu["volume_curve"]) == len(grid)
        assert len(cu["margin_curve"]) == len(grid)
        assert cu["elasticity_class"] in {"price_driven", "captive", "mixed"}
    # The aggregate curve max equals the reported optimum (frontend re-derivation matches).
    best = max(sb["total_margin_curve"], key=lambda p: p["margin"])
    assert abs(best["spread"] - sb["optimal_spread"]) < 1e-6


def test_sandbox_aggregate_is_sum_of_customers(full_con):
    base = pricing.build_base(full_con, DEFAULT_CONFIG, None, "all", None)
    sb = pricing.sandbox(base, DEFAULT_CONFIG)
    # Sum each customer's margin_curve at the optimal index → the aggregate optimum (toggle math).
    idx = sb["grid"].index(sb["optimal_spread"])
    summed = sum(cu["margin_curve"][idx] for cu in sb["customers"] if cu["margin_curve"][idx] is not None)
    assert abs(summed - sb["optimal_margin"]) <= 0.05 * abs(sb["optimal_margin"]) + 50.0


def test_price_driven_vs_captive_flagged(full_con):
    base = pricing.build_base(full_con, DEFAULT_CONFIG, None, "all", None)
    sb = pricing.sandbox(base, DEFAULT_CONFIG)
    assert sb["n_captive"] > 0  # a book of 36 accounts has some near-inelastic (captive) buyers
    # price-driven accounts carry a larger |β| than captive ones on average.
    pd_beta = [abs(c["beta"]) for c in sb["customers"] if c["elasticity_class"] == "price_driven"]
    cap_beta = [abs(c["beta"]) for c in sb["customers"] if c["elasticity_class"] == "captive"]
    if pd_beta and cap_beta:
        assert sum(pd_beta) / len(pd_beta) > sum(cap_beta) / len(cap_beta)


# ---- recommendation engine -------------------------------------------------------
def test_recommendation_maximizes_gp(full_con):
    base = pricing.build_base(full_con, DEFAULT_CONFIG, None, "all", None)
    rec = pricing.recommendations(base, DEFAULT_CONFIG, {"inventory": "balanced", "market": "flat"})
    assert rec["n"] > 0
    for r in rec["recommendations"]:
        # Maximizing expected GP over a grid that includes the current spread ⇒ never worse.
        assert r["expected_gp"] >= r["current_gp"] - 1.0
        assert 0.0 <= r["accept_prob"] <= 1.0
    # Optimized book GP beats the realized book GP.
    assert rec["optimized_gp_per_yr"] >= rec["current_gp_per_yr"]


def test_shadow_price_floor_never_discounts_when_binding(full_con):
    base = pricing.build_base(full_con, DEFAULT_CONFIG, None, "all", None)
    tight = pricing.recommendations(base, DEFAULT_CONFIG,
                                    {"inventory": "tight", "market": "rising", "capacity": "constrained"})
    assert tight["shadow_price"] > 0
    # With a positive shadow price, no recommendation posts a discount below the street reference.
    assert all(r["recommended_spread"] >= -1e-9 for r in tight["recommendations"])
    # The floor is enforced: recommended spread is at/above cost_rel + shadow.
    assert all(r["recommended_spread"] >= r["floor_spread"] - 1e-6 for r in tight["recommendations"])


def test_underpriced_ranking(full_con):
    base = pricing.build_base(full_con, DEFAULT_CONFIG, None, "all", None)
    rec = pricing.recommendations(base, DEFAULT_CONFIG, {"inventory": "balanced", "market": "flat"})
    up = rec["top_underpriced"]
    assert all(r["underpriced"] and r["price_gap"] > 0 for r in up)
    # Sorted by GP uplift descending (today's biggest pricing opportunities first).
    uplifts = [r["gp_uplift"] for r in up]
    assert uplifts == sorted(uplifts, reverse=True)


# ---- P5 forecast linkage ---------------------------------------------------------
def test_reads_persisted_p5_forecast(full_con):
    demand.persist(full_con, window="all")
    base = pricing.build_base(full_con, DEFAULT_CONFIG, None, "all", None)
    assert any(c["forecast_source"] == "P5_persisted" for c in base["customers"])


# ---- orchestration + scope -------------------------------------------------------
def test_compute_pricing_full_payload(full_con):
    res = pricing.compute_pricing(full_con, DEFAULT_CONFIG, None, "all", None,
                                  {"inventory": "balanced"})
    assert res["available"] is True
    assert res["sandbox"]["optimal_spread"] is not None
    assert res["recommendations"]["n"] > 0
    assert res["acceptance"]["source"] in {"quote_model", "elasticity_proxy"}
    assert res["terminal"] is None or res["terminal"] in res["terminals"]  # None = all terminals


# ---- API flow --------------------------------------------------------------------
def test_api_pricing_flow(client):
    assert client.post("/api/studio/load-demo", json={"profile": "full"}).status_code == 200

    r = client.get("/api/pricing")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["sandbox"]["optimal_spread"] is not None
    assert body["recommendations"]["n"] > 0
    assert body["acceptance"]["source"] in {"quote_model", "elasticity_proxy"}

    # regime changes the recommendations (tight supply ⇒ positive shadow price)
    tight = client.get("/api/pricing/recommendations",
                       params={"inventory": "tight", "capacity": "constrained"}).json()
    assert tight["recommendations"]["shadow_price"] > 0

    cfg = client.get("/api/pricing/config")
    assert cfg.status_code == 200 and "spread_step" in cfg.json()["config"]


def test_api_pricing_locked_on_lite(client):
    assert client.post("/api/studio/load-demo", json={"profile": "lite"}).status_code == 200
    body = client.get("/api/pricing").json()
    assert body["available"] is False
    assert "rack_benchmark" in body["availability"]["missing_fields"]
