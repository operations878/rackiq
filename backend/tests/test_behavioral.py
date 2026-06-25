"""Tests for the daily, presence-aware behavioral profile.

Covers the core move — splitting PRESENCE (over all calendar days, zeros incl.) from SIZE-WHEN-
PRESENT (active days only) — the median-0/mean>0 intermittency (misleading-average) detector, the
FREQUENCY × SIZE-CONSISTENCY classifier, the full descriptive-stats panel, and the integration into
the scoring payload / API (slim on the ranked list, full block on the customer detail).

The design test: a steady daily buyer ("Taylor") and a silent-then-spiky buyer ("Super Quality")
can share a weekly total yet must look OBVIOUSLY different here, and the average must be flagged as
misleading for the bursty one.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

from app import behavioral, generator, scoring
from app.scoring_config import DEFAULT_CONFIG as CFG

warnings.filterwarnings("ignore")

AS_OF = pd.Timestamp("2026-06-01")


def _cl(offsets, gals):
    """A one-customer lift frame from day-offsets (days back from AS_OF) and gallons."""
    if np.isscalar(gals):
        gals = [float(gals)] * len(offsets)
    return pd.DataFrame({
        "lift_datetime": [AS_OF - pd.Timedelta(days=int(d)) for d in offsets],
        "net_gallons": [float(g) for g in gals],
    })


def _prof(offsets, gals, name="Acct"):
    return behavioral.daily_profile(_cl(offsets, gals), CFG, AS_OF, name=name)


# ---- The design test: Taylor (steady daily) vs Super Quality (sporadic/bursty) ----
def _taylor():
    # lifts on ~24 of the last ~28 days (skip every 7th), ~39k with a steady wobble
    offs = [d for d in range(1, 29) if d % 7 != 0]
    gals = [[39000, 39000, 51000, 28000][i % 4] for i in range(len(offs))]
    return _prof(offs, gals, "Taylor")


def _super_quality():
    # silent most days, then a big ~55k load on a few IRREGULAR days (gaps vary widely even in
    # working-day terms) — a genuine buffer-risk burst buyer (marine-parcel-like), not a weekly rhythm
    offs = [4, 11, 38, 52, 95, 130]
    gals = [60000, 50000, 58000, 52000, 61000, 49000]
    return _prof(offs, gals, "Super Quality")


def test_taylor_reads_steady_daily():
    b = _taylor()
    assert b["available"]
    assert b["frequency_class"] in ("daily", "frequent")
    assert b["size_class"] == "tight"
    assert b["label"] in ("Steady Daily", "Steady Frequent")
    # a near-daily buyer's daily average is meaningful → NOT flagged intermittent
    assert b["intermittent"] is False
    assert b["misleading_severity"] is None
    head = b["windows"][b["primary_window"]]
    # the all-days mean is close to the active-day median (the average is NOT misleading)
    assert head["all_days"]["median"] > 0
    assert head["all_days"]["mean"] >= 0.6 * head["size_when_present"]["median"]


def test_super_quality_reads_sporadic_and_flags_misleading_average():
    b = _super_quality()
    assert b["available"]
    assert b["frequency_class"] in ("occasional", "rare")
    assert b["label"] == "Sporadic/Bursty"
    # the headline fact: silent most days, big loads → the daily average is misleading
    assert b["intermittent"] is True and b["misleading_average"] is True
    assert b["misleading_severity"] == "high"
    head = b["windows"][b["primary_window"]]
    assert head["all_days"]["median"] == 0 < head["all_days"]["mean"]
    # the active-day load is MUCH bigger than the smeared daily average — that's the whole point
    assert head["size_when_present"]["median"] >= 3 * head["all_days"]["mean"]
    # the read says so in plain words
    assert "misleading" in (b["headline"] or "")


def test_taylor_and_super_quality_look_obviously_different():
    """Same kind of business, very different daily profile — the enriched score must separate them."""
    t, s = _taylor(), _super_quality()
    th, sh = t["windows"][t["primary_window"]], s["windows"][s["primary_window"]]
    # presence is wildly different even if active-day size is comparable
    assert th["presence"]["active_day_rate"] > 3 * sh["presence"]["active_day_rate"]
    assert t["label"] != s["label"]
    # the misleading-average flag fires for the bursty one only
    assert s["misleading_severity"] == "high" and t["misleading_severity"] is None


# ---- The intermittency detector: all-days median 0 but mean > 0 -----------------
def test_intermittency_median_zero_mean_positive():
    # an established sporadic account: lifts every ~10 days, big loads, > half the days silent
    offs = [0, 10, 21, 33, 44, 55]
    gals = [60000, 50000, 60000, 50000, 60000, 50000]
    head = _prof(offs, gals)["windows"]["all"]
    assert head["all_days"]["median"] == 0
    assert head["all_days"]["mean"] > 0
    assert head["intermittent"] is True and head["misleading_average"] is True


def test_daily_buyer_is_not_intermittent():
    # active on almost every day → median > 0 → not intermittent
    offs = list(range(0, 24))
    head = _prof(offs, 8000.0)["windows"]["all"]
    assert head["all_days"]["median"] > 0
    assert head["intermittent"] is False


def test_intermittent_min_days_guard():
    """A 5-day snapshot is too short for the flag to mean anything (need ≥ behavior_intermittent_min_days)."""
    # one lift only in a 5-day-ish span → the 'all' window is < the min-days guard
    b = _prof([0, 4], [60000, 50000])
    short = b["windows"]["all"]
    assert short["n_days"] < CFG.behavior_intermittent_min_days
    assert short["intermittent"] is False  # not enough calendar span to call it intermittent


# ---- The classifier matrix ------------------------------------------------------
def test_frequency_and_size_buckets():
    assert behavioral._frequency_class(0.9, CFG) == "daily"
    assert behavioral._frequency_class(0.35, CFG) == "frequent"
    assert behavioral._frequency_class(0.15, CFG) == "occasional"
    assert behavioral._frequency_class(0.02, CFG) == "rare"
    assert behavioral._size_class(0.1, CFG) == "tight"
    assert behavioral._size_class(0.4, CFG) == "variable"
    assert behavioral._size_class(0.9, CFG) == "erratic"
    assert behavioral._size_class(None, CFG) == "unknown"


def test_label_matrix():
    L = lambda f, s, t, interm=False: behavioral._label(f, s, t, interm, 10, CFG)
    assert L("daily", "tight", "regular") == "Steady Daily"
    assert L("daily", "erratic", "irregular") == "Erratic Daily"
    assert L("frequent", "tight", "regular") == "Steady Frequent"
    assert L("frequent", "erratic", "irregular") == "Erratic Frequent"
    # predictable bursts vs sporadic ones split on timing regularity
    assert L("occasional", "tight", "regular") == "Steady Intermittent"
    assert L("occasional", "tight", "irregular") == "Sporadic/Bursty"
    assert L("occasional", "erratic", "regular") == "Sporadic/Bursty"
    assert L("rare", "variable", "irregular") == "Sporadic/Bursty"
    assert L("rare", "tight", "regular") == "Rare but Regular"
    # too few active days to classify
    assert behavioral._label("rare", "unknown", "unknown", False, 1, CFG) == "New / Sparse"


# ---- Presence stats are correct (zeros are data) --------------------------------
def test_presence_stats_known_grid():
    # AS_OF 2026-06-01 is a Monday; offsets [0,2,4,6] land on Mon, Sat, Thu, Tue. The 7-day span
    # contains one Sunday (excluded, weight 0) and one Saturday (partial, default weight 0.35), so
    # presence is read over WORKING days: denom = 5×1 + 1×0.35 = 5.35, active-weighted = Tue+Thu+Mon
    # (3×1) + Sat (0.35) = 3.35 → rate 3.35/5.35 ≈ 0.626 (vs the naive 4/7 = 0.571).
    head = _prof([0, 2, 4, 6], 5000.0)["windows"]["all"]
    pres = head["presence"]
    assert pres["n_days"] == 7 and pres["n_active_days"] == 4
    assert abs(pres["working_days"] - 5.35) < 0.1   # 5.35 (reported rounded to 1 dp)
    assert abs(pres["active_day_rate"] - 3.35 / 5.35) < 1e-3
    # gaps in working days: Tue→Thu = 2, Thu→Sat = 1.35, Sat→Mon = 1.0 → median ~1.35
    assert abs(pres["median_gap_days"] - 1.35) < 0.1
    assert pres["longest_silent_days"] == 1.0   # at most one inactive working day between lifts


def test_longest_silent_stretch():
    # one early lift, then a ~20-calendar-day silence, then daily for a week. Measured in WORKING
    # days the silence excludes the weekends it spans, so ~21 calendar days ≈ ~14 working days.
    offs = [27] + list(range(0, 7))
    head = _prof(offs, 5000.0)["windows"]["all"]
    assert 12.0 <= head["presence"]["longest_silent_days"] <= 16.0


# ---- Full descriptive stats panel ----------------------------------------------
def test_full_descriptive_stats_present():
    head = _taylor()["windows"]["30"]
    for block in ("size_when_present", "all_days"):
        s = head[block]
        assert s is not None
        for k in ("mean", "median", "min", "max", "range", "std", "cv", "p10", "p50", "p90", "mode"):
            assert k in s, f"{block} missing {k}"
    mode = head["size_when_present"]["mode"]
    assert mode and {"lo", "hi", "center", "count", "width"} <= set(mode)


def test_windows_and_bars_present():
    b = _taylor()
    assert set(b["windows"]) == {"7", "30", "90", "all"}
    for w, s in b["windows"].items():
        assert s["bars"] and {"date", "gallons", "lifts"} <= set(s["bars"][0])
        assert s["headline"] and s["presence_lane"]  # each window is self-describing
        assert len(s["bars"]) <= CFG.behavior_max_bar_days


def test_slim_behavior_drops_heavy_fields():
    slim = behavioral.slim_behavior(_super_quality())
    assert slim["available"] and "windows" not in slim and "bars" not in slim
    for k in ("label", "frequency_class", "size_class", "intermittent", "misleading_severity",
              "active_day_rate", "size_median_active", "all_days_mean", "all_days_median", "headline"):
        assert k in slim
    assert behavioral.slim_behavior(None) is None
    assert behavioral.slim_behavior({"available": False})["available"] is False


# ---- Real generated book: archetypes separate cleanly ---------------------------
@pytest.fixture()
def full_book(con):
    generator.generate(generator.GenConfig(seed=42, n_customers=30, months=20, profile="full"), con)
    return con


def test_real_archetypes_separate(full_book):
    res = scoring.compute_scores(full_book, CFG, "all")
    by_arche: dict[str, list] = {}
    for c in res["customers"]:
        b = c.get("behavior") or {}
        if b.get("available"):
            by_arche.setdefault(c["archetype_true"], []).append(b)

    # marine = the Super-Quality-type: rare/occasional bursts, daily average misleading
    marine = by_arche.get("marine", [])
    assert marine, "expected marine accounts on the full book"
    assert any(b["label"] == "Sporadic/Bursty" for b in marine)
    assert all(b["frequency_class"] in ("rare", "occasional") for b in marine)
    assert all(b["intermittent"] for b in marine)

    # ratable = the Taylor-type: tight loads, buys at least occasionally (never rare), never a
    # burst buyer. (Some slow ratable accounts land "occasional"/"Steady Intermittent" — still
    # steady, never sporadic.)
    ratable = by_arche.get("ratable", [])
    assert ratable, "expected ratable accounts on the full book"
    assert all(b["frequency_class"] != "rare" for b in ratable)             # buys at least occasionally
    assert all(b["size_class"] in ("tight", "variable") for b in ratable)   # consistent loads
    assert not any(b["label"] == "Sporadic/Bursty" for b in ratable)        # never a burst buyer
    # the steadiest baseload labels are exclusive to the steady group — they look obviously different
    assert {b["label"] for b in marine}.isdisjoint({"Steady Daily", "Steady Frequent"})


def test_scoring_payload_carries_behavior(full_book):
    res = scoring.compute_scores(full_book, CFG, "all")
    c = res["customers"][0]
    b = c["behavior"]
    assert b["available"] and b["primary_window"] in b["windows"]
    assert b["headline"] and b["label"]


# ---- API: slim on the ranked list, full block on the detail ---------------------
def test_behavior_api_flow(client):
    client.post("/api/studio/load-demo", json={"profile": "full"})
    s = client.get("/api/scores?window=all").json()
    top = s["customers"][0]
    # ranked rows carry the slim behavior (label + axes) but NOT the heavy windows/bars
    assert top["behavior"]["available"] and "label" in top["behavior"]
    assert "windows" not in top["behavior"]

    cid = top["customer_id"]
    detail = client.get(f"/api/scores/customer/{cid}?window=all").json()
    b = detail["customer"]["behavior"]
    assert b["windows"] and set(b["windows"]) == {"7", "30", "90", "all"}
    assert b["windows"][b["primary_window"]]["bars"]
