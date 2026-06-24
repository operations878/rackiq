"""Tests for the customer scoring engine: VAR lane, sub-scores, base value, archetypes."""

from __future__ import annotations

import warnings

import pandas as pd
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


# ---- VAR as a forecast (forward projection, excursions, trend, book forecast) ----
def test_forward_projection_per_customer(full_book):
    res = scoring.compute_scores(full_book, DEFAULT_CONFIG, "all")
    # every established (status ok) account gets a forward projection that nests lo<=expected<=hi
    projected = [c for c in res["customers"] if c["forecast"]["available"]]
    assert projected, "expected at least some forward projections"
    for c in projected:
        hs = {h["days"]: h for h in c["forecast"]["horizons"]}
        assert set(hs) == set(DEFAULT_CONFIG.forecast_horizons)
        for h in hs.values():
            assert h["lo"] <= h["expected"] <= h["hi"]
        # longer horizon ⇒ more expected volume and a wider absolute band
        assert hs[7]["expected"] <= hs[30]["expected"] <= hs[90]["expected"]
        assert (hs[90]["hi"] - hs[90]["lo"]) >= (hs[7]["hi"] - hs[7]["lo"])
        assert "gal over the next 30 days" in c["forecast"]["plain"]
        assert len(c["forecast_series"]) >= 1  # dotted forward continuation


def test_forward_band_tighter_for_high_var(full_book):
    res = scoring.compute_scores(full_book, DEFAULT_CONFIG, "all")
    rows = [c for c in res["customers"] if c["forecast"]["available"] and c["var"]["score"]]
    def rel_band(c):
        h = next(h for h in c["forecast"]["horizons"] if h["days"] == 30)
        return (h["hi"] - h["lo"]) / h["expected"] if h["expected"] else None
    hi_var = [rel_band(c) for c in rows if c["var"]["score"] >= 70]
    lo_var = [rel_band(c) for c in rows if c["var"]["score"] < 60]
    hi_var = [x for x in hi_var if x is not None]
    lo_var = [x for x in lo_var if x is not None]
    if hi_var and lo_var:  # a tight lane forecasts proportionally narrower than a wide one
        assert (sum(hi_var) / len(hi_var)) < (sum(lo_var) / len(lo_var))


def test_excursions_weather_pattern(full_book):
    res = scoring.compute_scores(full_book, DEFAULT_CONFIG, "all")
    # weather_distillate accounts should show a cold-snap-driven lane-break pattern
    wd = [c for c in res["customers"]
          if c["archetype_true"] == "weather_distillate" and c["excursions"]["n_breaks"] >= 3]
    assert wd, "expected weather distillate accounts with lane breaks"
    cold = [c for c in wd if (c["excursions"]["pattern"] or {}).get("type") == "cold_snap"]
    assert cold, "weather distillate breaks should cluster on cold snaps"
    c = cold[0]
    b = c["excursions"]["breaks"][0]
    assert b["kind"] in {"spike", "shortfall", "no_show"}
    assert "weeks" in c["excursions"]["pattern"]["note"]


def test_var_trend_over_time(full_book):
    res = scoring.compute_scores(full_book, DEFAULT_CONFIG, "all")
    moved = [c for c in res["customers"] if c["var_trend"]["available"]]
    assert moved
    for c in moved:
        q = c["var_trend"]["comparisons"]["quarter"]
        assert q["direction"] in {"tightening", "widening", "steady", "insufficient"}
        if q["direction"] != "insufficient":
            assert "VAR" in q["note"]


def test_book_forecast_bottom_up_and_filters(full_book):
    res = scoring.compute_scores(full_book, DEFAULT_CONFIG, "all")
    agg = scoring.aggregate_book_forecast(res["customers"], DEFAULT_CONFIG)
    assert agg["n_customers"] > 0
    assert 0.0 <= agg["predictable_share"] <= 1.0
    h30 = next(h for h in agg["horizons"] if h["days"] == 30)
    assert h30["lo"] <= h30["expected"] <= h30["hi"]
    # a terminal filter is a strict subset of the whole book
    full_30 = h30["expected"]
    term = sorted({t for c in res["customers"]
                   for k in (c["facts"].get("tp_share") or {}) for t in [k.split("|")[0]]
                   if t and t != "(unknown)"})[0]
    sub = scoring.aggregate_book_forecast(res["customers"], DEFAULT_CONFIG, terminal=term)
    sub_30 = next(h for h in sub["horizons"] if h["days"] == 30)["expected"]
    assert 0 < sub_30 <= full_30 + 1


# ---- Plain-English reads & honest confidence (polish-pass edge cases) ------------
def test_plural_and_fmt_helpers():
    assert scoring._plural(1, "order") == "order"
    assert scoring._plural(2, "order") == "orders"
    assert scoring._plural(0, "lift") == "lifts"
    assert scoring._plural(None, "week") == "weeks"   # never raises on a missing count
    assert scoring._fmt_gal(None) == "—"
    assert scoring._fmt_gal(8400) == "8,400"
    assert scoring._fmt_gal(1_250_000) == "1.2MM"


def test_var_explanation_none_safe():
    """An "ok" status with degenerate (None) lane fields must format, not raise (defence)."""
    core = {"lane": {"in_band_rate": None, "tightness": None, "excursion_penalty": None,
                     "base_level": 0.0, "method": "seasonal_median"},
            "var_status": "ok", "var_score": 50.0, "volume_var": 50.0, "cadence_var": 50.0,
            "grain": "weekly", "n_lifts": 9, "n_weeks": 8}
    s = scoring._var_explanation(core, DEFAULT_CONFIG)
    assert "VAR 50" in s and "%" in s


def _insert_lifts(con, cid, day_offsets, end, gal=6000.0):
    rows = [(cid, (end - pd.Timedelta(days=int(d))).to_pydatetime(), float(gal), "Linden", "ULSD")
            for d in day_offsets]
    con.executemany(
        "INSERT INTO lifts (customer_id, lift_datetime, net_gallons, terminal, product) "
        "VALUES (?,?,?,?,?)", rows)


def test_thin_history_plain_reads(con):
    """One-time / sparse / few-week accounts each read naturally — never '0 lift(s)'."""
    generator.generate(generator.GenConfig(seed=42, n_customers=14, months=18, profile="full"), con)
    end = pd.Timestamp(con.execute("SELECT max(lift_datetime) FROM lifts").fetchone()[0])
    _insert_lifts(con, "OneShot Diesel", [3], end)                                  # 1 lift
    _insert_lifts(con, "Sparse Hauling", [2, 12, 23, 35, 46], end)                  # 5 lifts / ~7 wk
    _insert_lifts(con, "ShortSpan Co", [1, 5, 11, 17, 22, 28, 33, 39, 46], end)     # 9 lifts / ~7 wk

    res = scoring.compute_scores(con, DEFAULT_CONFIG, "all")
    by = {c["customer_id"]: c for c in res["customers"]}

    one = by["OneShot Diesel"]
    assert one["var"]["status"] == "insufficient_history" and one["var"]["score"] is None
    assert one["var"]["descriptor"] == "Thin history"
    assert "bought just once" in one["var"]["plain"]
    assert one["forecast"]["available"] is False and one["forecast"]["horizons"] == []

    assert "only 5 lifts" in by["Sparse Hauling"]["var"]["plain"]
    assert "only 9 lifts" in by["ShortSpan Co"]["var"]["plain"]
    # never the robotic "(s)" placeholder; each read is a real, complete sentence
    for cid in ("OneShot Diesel", "Sparse Hauling", "ShortSpan Co"):
        p = by[cid]["var"]["plain"]
        assert "(s)" not in p and len(p) > 20 and p.rstrip().endswith(".")


def test_forecast_pluralization_and_rough_flag(full_book):
    res = scoring.compute_scores(full_book, DEFAULT_CONFIG, "all")
    avail = [c for c in res["customers"] if c["forecast"]["available"]]
    assert avail
    for c in avail:
        p = c["forecast"]["plain"]
        assert "order(s)" not in p and "roughly 1 orders" not in p   # pluralization fixed
        assert isinstance(c["forecast"]["rough"], bool)              # honest-confidence flag present
        h30 = next(h for h in c["forecast"]["horizons"] if h["days"] == 30)
        if h30.get("expected_orders") == 1:
            assert "roughly 1 order," in p
    # the rough flag actually discriminates: a wide-lane account is flagged, a tight one isn't
    roughs = [c["forecast"]["rough"] for c in avail]
    assert any(roughs) and not all(roughs)
    # and the wording follows the flag
    for c in avail:
        choppy = "treat this as a rough range" in c["forecast"]["plain"]
        assert choppy == c["forecast"]["rough"]


# ---- API ------------------------------------------------------------------------
def test_book_forecast_api(client):
    client.post("/api/studio/load-demo", json={"profile": "full"})
    bf = client.get("/api/scores/book-forecast?window=all").json()
    assert bf["n_customers"] > 0 and bf["terminals"] and bf["products"]
    assert any(h["days"] == 30 for h in bf["horizons"])
    assert bf["predictable_share"] is not None
    # filtered call returns a subset
    t = bf["terminals"][0]
    bft = client.get(f"/api/scores/book-forecast?window=all&terminal={t}").json()
    assert bft["terminal"] == t and bft["n_customers"] <= bf["n_customers"]
    # the ranked list carries the slim forecast + trend, but not the heavy series
    s = client.get("/api/scores?window=all").json()
    top = s["customers"][0]
    assert "forecast" in top and "var_trend" in top
    assert "forecast_series" not in top and "excursions" not in top


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
