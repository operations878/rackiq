"""Tests for the Phase-6 MODELED missing-volume / opportunity engine.

The engine estimates a demand GAP per master customer × family (peak ≈ wallet, MODELED), filters it for
WINNABILITY (shrunk vs under-served), and ranks it three ways — reusing the Phase-1 quadrant for the
spot/rack tag, the weather β·HDD residual for the peak adjustment, and the Phase-2 margin for the dollar
rank. The load-bearing behaviors under test:

  • a steady metronome reads NEAR-PEAK (no phantom winnable upside) — the gut-check;
  • a variable-but-steady account reads UNDER-SERVED with real winnable gallons;
  • a declining account with old peaks reads SHRUNK and is DOWN-WEIGHTED (never silently suppressed);
  • a thin/low-confidence account is FLAGGED provisional, never suppressed;
  • the spot/rack tag equals the variability quadrant's channel (reused, not re-derived);
  • gallons are canonical and the facet is a drop-in superset of the interim opportunity tile.
"""

from __future__ import annotations

import datetime as dt
import warnings

import numpy as np
import pandas as pd

from app import db, generator, opportunity, variability

warnings.filterwarnings("ignore")

START = dt.date(2022, 9, 5)   # a Monday, ~2 years of room so the year-over-year trend applies


def _weekdays(n):
    out, d = [], pd.Timestamp(START)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += pd.Timedelta(days=1)
    return out


def _weeks(n):
    return [pd.Timestamp(START) + pd.Timedelta(weeks=i) for i in range(n)]


def _add(rows, cust, dates, sizes, product="ULSD", terminal="Newark"):
    for d, s in zip(dates, sizes):
        rows.append({"customer_id": cust, "lift_datetime": pd.Timestamp(d),
                     "net_gallons": float(max(200.0, s)), "product": product, "terminal": terminal})


def _commit(con):
    con.execute("INSERT INTO customers (customer_id, name, archetype, home_terminal) "
                "SELECT DISTINCT customer_id, customer_id, 'imported', 'Newark' FROM lifts")


def _build_book(con):
    rows = []
    # METRONOME — daily, near-identical loads → near-peak, no winnable upside
    wd = _weekdays(420)
    _add(rows, "Metro Daily", wd, [5000.0] * len(wd))
    # UNDER-SERVED — regular weekly cadence, variable SIZE, stationary over time, fresh peaks
    wk = _weeks(100)
    pattern = np.array([3000, 13000, 5000, 12000, 4000, 14000])
    sizes = np.resize(pattern, len(wk)).astype(float)
    sizes[-1] = 14000.0                                   # a big load right at the end → fresh peak
    _add(rows, "Under Served", wk, sizes)
    # SHRUNK — big year-1 loads, tapered to small in year-2; last load recent but small
    wk2 = _weeks(104)
    sz = np.where(np.arange(len(wk2)) < 52, 10000.0, 2500.0)
    _add(rows, "Shrunk Co", wk2, sz)
    # THIN — a marine-like account: 16 big irregular parcels → sufficient but LOW confidence
    rng = np.random.default_rng(11)
    offs = np.cumsum(rng.integers(20, 55, 16))
    _add(rows, "Thin Marine", [pd.Timestamp(START) + pd.Timedelta(days=int(o)) for o in offs],
         rng.uniform(60000, 180000, 16))
    # TOO FEW — 3 lifts → not data-sufficient
    _add(rows, "Too Few", _weeks(3), [4000.0, 9000.0, 5000.0])
    db.insert_df(con, "lifts", pd.DataFrame(rows))
    _commit(con)


# =================================================================================================
def test_engine_available_and_master_keyed(con):
    _build_book(con)
    res = opportunity.compute_opportunity(con)
    assert res["available"] is True
    assert res["premise"].startswith("MODELED")
    by = {c["customer_id"]: c for c in res["customers"]}
    assert {"Metro Daily", "Under Served", "Shrunk Co", "Thin Marine", "Too Few"} <= set(by)


def test_steady_metronome_reads_near_peak(con):
    """The gut-check: a steady, consistent daily lifter has no phantom winnable upside."""
    _build_book(con)
    by = {c["customer_id"]: c for c in opportunity.compute_opportunity(con)["customers"]}
    metro = by["Metro Daily"]
    assert metro["kind"] == "matched"
    assert metro["winnability_flag"] == "near_peak"
    assert (metro["winnable_gal_per_yr"] or 0) == 0


def test_under_served_has_real_winnable_volume(con):
    _build_book(con)
    by = {c["customer_id"]: c for c in opportunity.compute_opportunity(con)["customers"]}
    us = by["Under Served"]
    assert us["kind"] == "win"
    assert us["gap_gal_per_yr"] > 0
    assert us["winnable_gal_per_yr"] > 0
    # winnable is the winnability-weighted gap, so it never exceeds the raw gap
    assert us["winnable_gal_per_yr"] <= us["gap_gal_per_yr"]
    assert us["channel"] in ("RACK", "SPOT")          # tagged spot-or-rack from the quadrant


def test_shrunk_is_down_weighted_not_suppressed(con):
    """Declining year-over-year + a stale peak ⇒ shrunk: down-weighted, with the honest reason, but the
    raw gap is still reported (never a silent zero)."""
    _build_book(con)
    by = {c["customer_id"]: c for c in opportunity.compute_opportunity(con)["customers"]}
    sh = by["Shrunk Co"]
    assert sh["kind"] == "shrunk"
    assert sh["trend"] == "declining"
    assert sh["peak_stale"] is True
    assert sh["gap_gal_per_yr"] > 0                    # the raw gap is still surfaced
    # down-weighted: winnable is strictly below the raw gap (winnability damped it)
    assert sh["winnable_gal_per_yr"] < sh["gap_gal_per_yr"]
    assert "shrunk, not winnable" in sh["reason"]


def test_low_confidence_flagged_not_suppressed(con):
    _build_book(con)
    by = {c["customer_id"]: c for c in opportunity.compute_opportunity(con)["customers"]}
    thin = by["Thin Marine"]
    assert thin["available"] is True                   # NOT suppressed
    assert thin["confidence_tier"] == "Low"
    assert thin["provisional"] is True                 # … but flagged
    assert thin["facet"]["provisional"] is True


def test_too_few_lifts_unavailable(con):
    _build_book(con)
    by = {c["customer_id"]: c for c in opportunity.compute_opportunity(con)["customers"]}
    tf = by["Too Few"]
    assert tf["available"] is False
    assert tf["kind"] == "unknown"
    assert (tf["winnable_gal_per_yr"] or 0) == 0


def test_three_independent_rankings(con):
    _build_book(con)
    res = opportunity.compute_opportunity(con)
    by = {c["customer_id"]: c for c in res["customers"]}
    # gap & winnable ranks exist for accounts with a positive gap; they start at 1 and are dense-ish
    gap_ranks = sorted(c["rank_by_gap"] for c in res["customers"] if c["rank_by_gap"] is not None)
    assert gap_ranks and gap_ranks[0] == 1
    assert by["Under Served"]["rank_by_gap"] is not None
    assert by["Under Served"]["rank_by_winnable"] is not None
    # no prices loaded in this hand-built book ⇒ no dollar rank, but gallons rankings still work
    assert res["margin"]["available"] is False
    assert all(c["rank_by_dollars"] is None for c in res["customers"])
    assert res["rankings"]["by_gap"] and res["rankings"]["by_winnable"]
    assert res["rankings"]["by_margin_dollars"] == []


def test_spot_rack_tag_reuses_variability_quadrant(con):
    """The channel tag must EQUAL the Phase-1 quadrant's recommendation — never re-derived here."""
    _build_book(con)
    var = variability.compute_variability(con)
    var_ch = {c["customer_id"]: (c.get("channel") or {}).get("recommended_channel")
              for c in var["customers"]}
    for c in opportunity.compute_opportunity(con, var_result=var)["customers"]:
        if c["available"]:
            assert c["channel"] == var_ch[c["customer_id"]]


def test_gap_units_are_gallons_and_tie_out(con):
    """Gallons are canonical: actual_gal_per_yr × span-in-years ≈ the customer's total net gallons."""
    _build_book(con)
    by = {c["customer_id"]: c for c in opportunity.compute_opportunity(con)["customers"]}
    us = by["Under Served"]
    years = max(us["span_days"], 1) / 365.0
    implied_total = us["actual_gal_per_yr"] * years
    assert 0.7 * us["total_net_gallons"] <= implied_total <= 1.3 * us["total_net_gallons"]


def test_facet_is_drop_in_superset_of_interim(con):
    """The facet must carry every key the interim opportunity tile / frontend adapter reads, so the
    later swap is a data-source change, not a redesign."""
    _build_book(con)
    res = opportunity.compute_opportunity(con)
    facet = next(c["facet"] for c in res["customers"] if c["kind"] == "win")
    for key in ("available", "kind", "winnable_gal_per_yr", "winnable_dollars_per_yr",
                "chase_channel", "note", "interim_note", "source"):
        assert key in facet
    assert facet["source"] == "modeled_peak_demand"
    assert facet["modeled"] is True
    # facets_by_master returns the same facets keyed by master id (the fan-out convenience)
    fb = opportunity.facets_by_master(con)
    assert set(fb) == {c["customer_id"] for c in res["customers"]}


# =================================================================================================
# end-to-end on the synthetic full book — the gut-check exemplars
# =================================================================================================
def test_end_to_end_on_full_synthetic_book(con):
    generator.generate(generator.GenConfig(seed=42, profile="full", months=21), con)
    res = opportunity.compute_opportunity(con)
    assert res["available"]
    by = {c["name"]: c for c in res["customers"]}

    # FuelExpress Retail — a steady High-confidence metronome → near-peak, no winnable upside
    fe = by["FuelExpress Retail"]
    assert fe["confidence_tier"] == "High"
    assert fe["kind"] in ("matched", "shrunk")
    assert (fe["winnable_gal_per_yr"] or 0) == 0

    # Narragansett Marine Fuels — 18 lifts → Low confidence, FLAGGED, never suppressed
    nar = by["Narragansett Marine Fuels"]
    assert nar["available"] is True
    assert nar["confidence_tier"] == "Low"
    assert nar["provisional"] is True

    # the weather β·HDD residual fired on at least one heating-fuel account
    assert res["weather"]["n_adjusted"] >= 1
    # margin (lift-price fallback on the synthetic book) powers the dollar rank
    assert res["margin"]["available"] is True
    assert res["summary"]["n_scored"] > 0


def test_validation_readout_checks_pass(con):
    generator.generate(generator.GenConfig(seed=42, profile="full", months=21), con)
    rep = opportunity.validation_readout(con)
    assert rep["available"]
    assert rep["checks"]["steady_metronome_low_or_no_winnable"]["pass"] is True
    assert rep["checks"]["low_confidence_flagged_not_suppressed"]["pass"] is True
