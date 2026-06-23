"""Tests for the customer scoring engine: VAR lane, sub-scores, base value, archetypes."""

from __future__ import annotations

import warnings

import pytest

from app import generator, scoring
from app.scoring_config import ARCHETYPES, DEFAULT_CONFIG, WINDOWS, grade

warnings.filterwarnings("ignore")


@pytest.fixture()
def full_book(con):
    generator.generate(generator.GenConfig(seed=42, n_customers=30, months=20, profile="full"), con)
    return con


# ---- Engine ---------------------------------------------------------------------
def test_compute_scores_full_book(full_book):
    res = scoring.compute_scores(full_book, DEFAULT_CONFIG, "all")
    assert res["n_customers"] == 30
    assert res["as_of"] is not None
    # every metric available on a full book
    assert all(v["available"] for v in res["availability"].values())

    c = res["customers"][0]
    assert c["var"]["score"] is not None and c["var"]["grade"] in {"A", "B", "C", "D"}
    assert c["var"]["volume_var"] is not None and c["var"]["cadence_var"] is not None
    assert c["base_value"]["score"] is not None
    assert c["archetype"]["primary"] in ARCHETYPES
    assert c["archetype"]["secondary"] in ARCHETYPES
    assert len(c["lane_series"]) >= 8
    pt = c["lane_series"][0]
    assert pt["var_lo"] <= pt["base_lo"] and pt["base_hi"] <= pt["var_hi"]  # bands nest
    assert c["quadrant"]["quadrant"] in {"Strategic Lever", "Premium Spot", "Managed Cost", "Dangerous Noise"}


def test_var_blend_and_guard(full_book):
    res = scoring.compute_scores(full_book, DEFAULT_CONFIG, "all")
    for c in res["customers"]:
        if c["var"]["status"] == "ok":
            blend = (0.70 * c["var"]["volume_var"] + 0.30 * c["var"]["cadence_var"])
            assert abs(blend - c["var"]["score"]) < 0.2
        else:
            assert "insufficient" in c["var"]["explanation"].lower()


def test_capability_gating_on_lite(con):
    generator.generate(generator.GenConfig(seed=7, n_customers=14, months=14, profile="lite"), con)
    res = scoring.compute_scores(con, DEFAULT_CONFIG, "all")
    av = res["availability"]
    assert av["var"]["available"] and av["weather_sensitivity"]["available"]
    for off in ("margin", "evr", "market_sensitivity", "price_elasticity", "quote_score", "credit"):
        assert not av[off]["available"] and av[off]["reason"]
    c = res["customers"][0]
    assert c["subscores"]["evr"]["available"] is False
    assert c["subscores"]["evr"]["value"] is None
    assert c["subscores"]["volume_steadiness"]["available"] is True


def test_windows_and_sufficiency(full_book):
    suff = {}
    for w in WINDOWS:
        res = scoring.compute_scores(full_book, DEFAULT_CONFIG, w)
        suff[w] = sum(1 for c in res["customers"] if c["data_sufficient"])
    # a 30-day slice should flag fewer established accounts than all-time
    assert suff["30"] <= suff["365"] <= suff["all"]
    assert suff["all"] >= suff["90"]


def test_config_overrides_change_blend(full_book):
    base = scoring.compute_scores(full_book, DEFAULT_CONFIG, "all")
    cfg2 = DEFAULT_CONFIG.with_overrides({"var_blend_volume": 1.0, "var_blend_cadence": 0.0})
    alt = scoring.compute_scores(full_book, cfg2, "all")
    b0 = next(c for c in base["customers"] if c["var"]["status"] == "ok")
    a0 = next(c for c in alt["customers"] if c["customer_id"] == b0["customer_id"])
    assert abs(a0["var"]["score"] - a0["var"]["volume_var"]) < 0.2  # pure volume lane now


def test_recompute_persists_tables(full_book):
    out = scoring.recompute_and_persist(full_book, DEFAULT_CONFIG)
    assert out["ok"] and set(out["windows"]) == set(WINDOWS)
    n = full_book.execute("SELECT count(*) FROM customer_scores").fetchone()[0]
    assert n == sum(out["windows"].values())
    lane = full_book.execute("SELECT count(*) FROM customer_lane WHERE score_window='all'").fetchone()[0]
    assert lane > 0
    # the SQL facts view is created
    assert full_book.execute("SELECT count(*) FROM v_customer_facts").fetchone()[0] == 30


def test_backtest_methods(full_book):
    bt = scoring.backtest(full_book, DEFAULT_CONFIG)
    assert set(bt["methods"]) == {"naive_last", "seasonal", "lane_base"}
    assert bt["customers"]
    for r in bt["customers"][:5]:
        assert r["best"] in bt["methods"]
        assert all(m in r["mae"] for m in bt["methods"])


def test_grade_thresholds():
    assert grade(85, DEFAULT_CONFIG) == "A"
    assert grade(65, DEFAULT_CONFIG) == "B"
    assert grade(45, DEFAULT_CONFIG) == "C"
    assert grade(20, DEFAULT_CONFIG) == "D"
    assert grade(None, DEFAULT_CONFIG) is None


# ---- API ------------------------------------------------------------------------
def test_scores_api_flow(client):
    client.post("/api/studio/load-demo", json={"profile": "full"})
    s = client.get("/api/scores?window=all").json()
    assert s["n_customers"] > 0
    top = s["customers"][0]
    assert "lane_series" not in top  # trimmed from the table list
    cid = top["customer_id"]

    detail = client.get(f"/api/scores/customer/{cid}?window=all").json()
    assert detail["customer"]["lane_series"]
    assert detail["customer"]["var"]["explanation"]

    quad = client.get("/api/scores/quadrant?window=all").json()
    assert quad["points"] and "explainability" in quad["points"][0]

    bt = client.get("/api/scores/backtest").json()
    assert bt["summary"]

    rc = client.post("/api/scores/recompute", json={"overrides": {"grade_a": 90}}).json()
    assert rc["ok"]

    bad = client.get("/api/scores?window=bogus")
    assert bad.status_code == 400
