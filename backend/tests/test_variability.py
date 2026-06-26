"""Tests for the TWO-AXIS variability score + the rebuilt spot/rack channel recommendation.

The axes are INDEPENDENT: cadence consistency (regularity of WHEN they lift) and size consistency
(how alike each load is, on ACTIVE days only). The headline rebuild (Stage 2): the 2×2 quadrant is
read off the two SCORES with regularity cutoffs — so a regular weekly/biweekly lifter is a metronome,
NOT bunched into "spot" — and each quadrant carries a rack/spot channel, a confidence tier, and a
current-vs-recommended mismatch. Margin is a ranking note only and never moves a channel.
"""

from __future__ import annotations

import datetime as dt
import warnings

import numpy as np
import pandas as pd

from app import db, dealbook, variability

warnings.filterwarnings("ignore")

START = dt.date(2023, 1, 2)   # a Monday


def _weekday_dates(n_weeks):
    out, d = [], pd.Timestamp(START)
    while len(out) < n_weeks * 5:
        if d.weekday() < 5:
            out.append(d)
        d += pd.Timedelta(days=1)
    return out


def _add(rows, cust, dates, sizes, product="ULSD", terminal="Newark"):
    for d, s in zip(dates, sizes):
        rows.append({"customer_id": cust, "lift_datetime": pd.Timestamp(d),
                     "net_gallons": float(max(200.0, s)), "product": product, "terminal": terminal})


def _commit(con):
    con.execute("INSERT INTO customers (customer_id, name, archetype, home_terminal) "
                "SELECT DISTINCT customer_id, customer_id, 'imported', 'Newark' FROM lifts")


# ---- the four-quadrant book (weekly/biweekly REAL-book-like lifters) -------------
def _build_book(con):
    rng = np.random.default_rng(7)
    rows = []
    wk = [pd.Timestamp(START) + pd.Timedelta(weeks=i) for i in range(60)]        # weekly, 60 lifts
    # METRONOME — regular weekly, consistent size
    _add(rows, "Metro Weekly", wk, 8000 + rng.normal(0, 300, len(wk)))
    # PREDICTABLE TIMING — regular weekly, WILDLY variable size (the size axis must stay loud)
    _add(rows, "Lumpy Weekly", wk, rng.uniform(2000, 14000, len(wk)))
    # PREDICTABLE SIZE — irregular gaps, identical size
    offs = np.cumsum(rng.integers(4, 40, 30))
    _add(rows, "Irregular Same", [pd.Timestamp(START) + pd.Timedelta(days=int(o)) for o in offs],
         [5000.0] * 30)
    # UNPREDICTABLE — irregular gaps, variable size (true spot)
    offs2 = np.cumsum(rng.integers(3, 50, 25))
    _add(rows, "True Spot", [pd.Timestamp(START) + pd.Timedelta(days=int(o)) for o in offs2],
         rng.uniform(800, 15000, 25))
    db.insert_df(con, "lifts", pd.DataFrame(rows))
    _commit(con)


def test_two_axes_present_and_independent(con):
    _build_book(con)
    res = variability.compute_variability(con)
    assert res["available"]
    by = {c["customer_id"]: c for c in res["customers"]}
    metro, lumpy = by["Metro Weekly"], by["Lumpy Weekly"]

    for c in (metro, lumpy, by["Irregular Same"]):
        assert c["cadence_consistency"] is not None and c["size_consistency"] is not None

    # AXIS 1: both weekly accounts are REGULAR cadence even though neither is "frequent"
    assert metro["cadence_consistency"] > 65
    assert lumpy["cadence_consistency"] > 65
    # AXIS 2 (the honesty test): the lumpy account's size variance is NOT hidden
    assert lumpy["size_consistency"] < metro["size_consistency"] - 25
    assert metro["size_consistency"] > 75


def test_four_quadrants_and_channels(con):
    """The all-spot FIX: weekly/biweekly regular lifters spread across all four quadrants — they are
    NOT all called 'spot'. Channel follows the quadrant."""
    _build_book(con)
    res = variability.compute_variability(con)
    by = {c["customer_id"]: c for c in res["customers"]}

    assert by["Metro Weekly"]["quadrant"] == "metronome"
    assert by["Lumpy Weekly"]["quadrant"] == "predictable_timing"
    assert by["Irregular Same"]["quadrant"] == "predictable_size"
    assert by["True Spot"]["quadrant"] == "unpredictable"

    # channel: only the genuinely erratic account is spot; the steady ones are rack/term
    assert by["Metro Weekly"]["channel"]["recommended_channel"] == "RACK"
    assert by["Metro Weekly"]["channel"]["term_eligible"] is True
    assert by["Irregular Same"]["channel"]["recommended_channel"] == "RACK"
    assert by["True Spot"]["channel"]["recommended_channel"] == "SPOT"

    # the book is NOT all-spot
    summ = res["channel_summary"]
    assert summ["recommended"]["RACK"] >= 3
    assert summ["recommended"]["SPOT"] <= 1
    assert len([q for q in ("metronome", "predictable_timing", "predictable_size", "unpredictable")
                if res["distribution"]["quadrants"].get(q, 0) > 0]) >= 3


def test_confidence_tiers_from_lift_count(con):
    """High/Medium/Low from lift count + span. A thin account is Low-confidence and FLAGGED provisional
    but STILL gets a rec (never suppressed)."""
    rng = np.random.default_rng(3)
    rows = []
    # HIGH: daily-ish over ~90 weeks → ~450 lifts, >365-day span
    _add(rows, "Big Steady", _weekday_dates(90), 5000 + rng.normal(0, 150, 90 * 5))
    # MEDIUM: 2×/week over 60 weeks → 120 lifts, >180-day span, <200 lifts
    twpw = [pd.Timestamp(START) + pd.Timedelta(days=int(7 * (i // 2) + (0 if i % 2 == 0 else 3)))
            for i in range(120)]
    _add(rows, "Mid Buyer", twpw, 6000 + rng.normal(0, 250, 120))
    # LOW: weekly over 25 weeks → 25 lifts (≈ a thin account like Van Varick's ~88)
    _add(rows, "Small Weekly", [pd.Timestamp(START) + pd.Timedelta(weeks=i) for i in range(25)],
         7000 + rng.normal(0, 250, 25))
    db.insert_df(con, "lifts", pd.DataFrame(rows))
    _commit(con)
    res = variability.compute_variability(con)
    by = {c["customer_id"]: c for c in res["customers"]}

    assert by["Big Steady"]["confidence"]["tier"] == "High"
    assert by["Mid Buyer"]["confidence"]["tier"] == "Medium"
    assert by["Small Weekly"]["confidence"]["tier"] == "Low"
    # the Low account is flagged provisional but STILL recommended
    sw = by["Small Weekly"]["channel"]
    assert sw["provisional"] is True
    assert "provisional" in (sw["confidence_flag"] or "")
    assert sw["recommended_channel"] in ("RACK", "SPOT")   # never suppressed


def test_current_vs_recommended_mismatch(con):
    """A metronome stuck on spot → upgrade_to_rack; an unpredictable account on term → downgrade."""
    _build_book(con)
    # Metro Weekly is on SPOT today; True Spot is on a TERM contract today.
    deals = [
        {"source": "spot", "customer_raw": "Metro Weekly", "product_raw": "ULSD",
         "product_family": "ULSD", "terminal": None, "month": dt.date(2023, 3, 1),
         "committed_gallons": None, "realized_gallons": 9000.0, "price": 2.7, "price_type": "realized",
         "commitment_type": "firm", "volume_basis": "net", "deal_date": dt.date(2023, 3, 5),
         "representative": None},
        {"source": "term", "customer_raw": "True Spot", "product_raw": "ULSD",
         "product_family": "ULSD", "terminal": None, "month": dt.date(2023, 3, 1),
         "committed_gallons": 40000.0, "realized_gallons": None, "price": 0.07, "price_type": "basis",
         "commitment_type": "firm", "volume_basis": "net", "deal_date": None, "representative": None},
    ]
    for d in deals:
        d["deal_key"] = dealbook.deal_key(d["source"], d["customer_raw"], "ULSD", None, d["month"],
                                          d.get("deal_date"))
    db.upsert_deals(con, deals, "mixed", "f", "now")
    for nm in ("Metro Weekly", "True Spot"):
        db.upsert_crosswalk_entries(con, [{"variant_key": nm, "master_id": nm, "master_name": nm,
                                           "confidence": 1.0, "status": "confirmed", "source": "test",
                                           "updated_at": "now"}])
    db.resolve_deal_masters(con)
    res = variability.compute_variability(con)
    by = {c["customer_id"]: c for c in res["customers"]}

    metro = by["Metro Weekly"]["channel"]
    assert metro["mismatch"] is True
    assert metro["mismatch_direction"] == "upgrade_to_rack"
    assert metro["mismatch_strength"] == "strong"

    spot = by["True Spot"]["channel"]
    assert spot["mismatch"] is True
    assert spot["mismatch_direction"] == "downgrade_to_spot"

    rep = res["mismatches"]
    assert rep["n_mismatches"] >= 2
    assert any(m["name"] == "Metro Weekly" for m in rep["stuck_on_spot_should_be_rack"])
    assert any(m["name"] == "True Spot" for m in rep["committed_should_be_spot"])


def test_margin_is_ranking_only_never_flips_channel(con):
    """The audit gate: the recommended channel always equals the quadrant's own primary channel —
    margin is a ranking note and can never move a customer between rack and spot."""
    _build_book(con)
    res = variability.compute_variability(con)
    for c in res["customers"]:
        if not c["data_sufficient"]:
            continue
        expected = variability._quadrant_meta(c["quadrant"])["primary_channel"]
        assert c["channel"]["recommended_channel"] == expected
    val = variability.validation_readout(con)
    assert val["margin_audit"]["channels_flipped_by_margin"] == 0


def test_validation_readout_spreads_not_all_spot(con):
    _build_book(con)
    val = variability.validation_readout(con)
    assert val["available"]
    qs = val["quadrant_spread"]
    assert qs["n_quadrants_populated"] >= 3
    assert qs["not_all_spot"] is True
    # one named customer is walked end-to-end per populated quadrant
    assert len(val["four_quadrant_walk"]) >= 3
    for w in val["four_quadrant_walk"]:
        assert "recommended_channel" in w and "confidence" in w and "rationale" in w


def test_commitment_annotation_attaches_only_to_resolved(con):
    _build_book(con)
    rows = [{
        "source": "term", "customer_raw": "Metro Weekly", "product_raw": "ULSD",
        "product_family": "ULSD", "terminal": None, "month": dt.date(2023, 3, 1),
        "committed_gallons": 50000.0, "realized_gallons": None, "price": 0.07, "price_type": "basis",
        "commitment_type": "firm", "volume_basis": "net", "deal_date": None, "representative": None,
    }]
    rows[0]["deal_key"] = dealbook.deal_key("term", "Metro Weekly", "ULSD", None, dt.date(2023, 3, 1), None)
    db.upsert_deals(con, rows, "term", "f", "now")
    db.upsert_crosswalk_entries(con, [{"variant_key": "Metro Weekly", "master_id": "Metro Weekly",
                                       "master_name": "Metro Weekly", "confidence": 1.0,
                                       "status": "confirmed", "source": "test", "updated_at": "now"}])
    db.resolve_deal_masters(con)
    res = variability.compute_variability(con)
    by = {c["customer_id"]: c for c in res["customers"]}
    assert by["Metro Weekly"]["commitment"]["available"] is True
    assert by["True Spot"]["commitment"]["label"] == "no commitment data"


def test_insufficient_history_not_scored_but_still_gets_a_provisional_rec(con):
    db.insert_df(con, "lifts", pd.DataFrame({
        "customer_id": ["Tiny"] * 2, "lift_datetime": pd.to_datetime(["2024-01-01", "2024-02-01"]),
        "net_gallons": [1000.0, 2000.0], "product": ["ULSD"] * 2, "terminal": ["Newark"] * 2}))
    con.execute("INSERT INTO customers VALUES ('Tiny','Tiny','imported','Newark')")
    res = variability.compute_variability(con)
    tiny = next(c for c in res["customers"] if c["customer_id"] == "Tiny")
    assert tiny["data_sufficient"] is False
    assert tiny["cadence_consistency"] is None
    # still carries a confidence tier + a (provisional, no-channel) rec rather than crashing
    assert tiny["confidence"]["tier"] == "Low"
    assert tiny["channel"]["recommended_channel"] is None
