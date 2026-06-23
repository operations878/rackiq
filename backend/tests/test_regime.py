"""Tests for the regime engine (Blueprint C), scorecards (E), and the playbook (G)."""

from __future__ import annotations

import duckdb
import pytest

from app import db, generator, playbook, regime, scoring
from app.regime_config import (DEFAULT_REGIME, REGIME_AXES, normalize_regime,
                               opposite_regime, regime_multiplier, regime_score)


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(":memory:")
    db.init_db(c)
    generator.generate(generator.GenConfig(seed=11, n_customers=24, months=18, profile="full"), c)
    scoring.ensure_tables(c)
    yield c
    c.close()


def test_multiplier_neutral_default():
    # Balanced/flat/normal/normal should be ~neutral for a steady anchor.
    assert regime_multiplier("Anchor Base-Load", DEFAULT_REGIME) == pytest.approx(1.0, abs=1e-9)


def test_multiplier_moves_with_regime():
    long_fall = normalize_regime({"inventory": "long", "market": "falling"})
    tight_rise = normalize_regime({"inventory": "tight", "market": "rising"})
    # Surplus Absorber should be worth more when inventory is long, less when tight.
    assert regime_multiplier("Surplus Absorber", long_fall) > 1.0
    assert regime_multiplier("Surplus Absorber", tight_rise) < 1.0
    # Premium Spot is the mirror image.
    assert regime_multiplier("Premium Spot", tight_rise) > 1.0
    assert regime_multiplier("Premium Spot", long_fall) < 1.0


def test_regime_score_clamped():
    assert regime_score(90.0, "Premium Spot", normalize_regime({"inventory": "tight", "market": "rising"})) <= 100.0
    assert regime_score(0.0, "Price Shopper", DEFAULT_REGIME) >= 0.0
    assert regime_score(None, "Anchor Base-Load", DEFAULT_REGIME) is None


def test_opposite_regime_flips_leadership_axes():
    reg = normalize_regime({"inventory": "long", "market": "falling"})
    opp = opposite_regime(reg)
    assert opp["inventory"] == "tight"
    assert opp["market"] == "rising"


def test_build_daily_has_nine_panels(con):
    out = regime.build_daily(con, normalize_regime({"inventory": "long", "market": "falling"}))
    assert len(out["panels"]) == 9
    keys = {p["key"] for p in out["panels"]}
    assert "today_actions" in keys and "customer_rankings" in keys
    assert out["terminal"] in out["terminals"]
    # rows carry the action contract
    for p in out["panels"]:
        for r in p["rows"]:
            assert r["action"] and r["why_now"] and r["expected_impact"]
            assert "regime_score" in r and "base_value" in r


def test_rankings_reorder_under_regime(con):
    long_fall = normalize_regime({"inventory": "long", "market": "falling"})
    tight_rise = normalize_regime({"inventory": "tight", "market": "rising"})
    a = regime.build_daily(con, long_fall, terminal=None)
    term = a["terminal"]
    b = regime.build_daily(con, tight_rise, terminal=term)

    def ranking(out):
        panel = next(p for p in out["panels"] if p["key"] == "customer_rankings")
        return [r["customer_id"] for r in panel["rows"]]

    # The two regimes should not produce an identical customer ranking.
    assert ranking(a) != ranking(b)


def test_persist_daily_writes_table(con):
    out = regime.persist_daily(con, normalize_regime({"inventory": "tight"}))
    assert out["ok"] and out["rows_written"] > 0
    n = con.execute("SELECT count(*) FROM daily_recommendations").fetchone()[0]
    assert n == out["rows_written"]
    # one run_date, regime stored as json
    rd = con.execute("SELECT DISTINCT run_date FROM daily_recommendations").fetchall()
    assert len(rd) == 1


def test_scorecards_flip_side(con):
    reg = normalize_regime({"inventory": "tight", "market": "rising"})
    sc = regime.scorecards(con, reg)
    assert sc["cards"], "expected scorecards"
    assert sc["archetypes_present"]
    # one exemplar per archetype present
    assert len(sc["exemplars"]) == len(sc["archetypes_present"])
    card = sc["cards"][0]
    assert card["regime_score"] is not None
    assert card["flip"]["regime_label"] != sc["regime_label"]
    assert "line" in card["flip"]
    # breakdown has one multiplier per axis
    assert set(card["regime_breakdown"]) == set(REGIME_AXES)


def test_playbook_covers_all_archetypes():
    pb = playbook.build_playbook()
    assert len(pb["archetypes"]) == 12
    for entry in pb["archetypes"]:
        assert entry["play"].get("say") and entry["play"].get("avoid")
    assert len(pb["regime_cheatsheet"]) == len(REGIME_AXES)
    assert len(pb["morning_routine"]) >= 5
    md = playbook.render_markdown(pb)
    assert "# RackIQ Sales Playbook" in md and "Morning routine" in md


def test_playbook_scopes_to_present(con):
    res = scoring.compute_scores(con, None, "all")
    present = sorted({c["archetype"]["primary"] for c in res["customers"]})
    pb = playbook.build_playbook(present_archetypes=present)
    flagged = {e["archetype"] for e in pb["archetypes"] if e["present"]}
    assert flagged == set(present)
