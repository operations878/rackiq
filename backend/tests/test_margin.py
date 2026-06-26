"""Tests for the Phase-2 margin engine (margin.py).

Covers the plausibility gate (¢/gal sanity — the "$1/gal" units bug is caught, not shipped), 100%
coverage + a worked end-to-end example on the synthetic full book, the value-vs-volume contrast, the
BOOK-vs-REPLACEMENT split, deal-type margins respecting index-on-index physics (term flat-cancel,
forward locked−landed, spot realized−landed), forward-fixed mark-to-market, and the margin-priced
gap helper Phase-3 calls.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pandas as pd
import pytest

from app import db, dealbook, generator, margin, pricegrid
from app.margin_config import DEFAULT_CONFIG


@pytest.fixture(scope="module")
def full_con():
    c = duckdb.connect(":memory:")
    db.init_db(c)
    generator.generate(generator.GenConfig(seed=42, n_customers=36, months=20, profile="full"), c)
    yield c
    c.close()


# ---- helpers ---------------------------------------------------------------------
def _barge(con, terminal, fam, date, all_in, logistics, fixed_diff=None, vol_gal=3_528_000.0):
    row = {
        "terminal": terminal, "product_family": fam, "product_raw": fam, "discharge_date": date,
        "barge_cost": logistics, "inspector_cost": 0.0, "operational_cost": 0.0, "gainloss_cost": 0.0,
        "logistics_cost": logistics, "est_trip_value": (all_in or 0) * vol_gal,
        "pricing_type": "Fixed Diff" if fixed_diff is not None else "Monthly Average",
        "fixed_differential": fixed_diff, "volume_bbl": vol_gal / 42.0, "volume_gal": vol_gal,
        "vol_unit": "bbl", "vef": 1.0, "all_in_landed": all_in,
        "cost_basis": "all_in" if all_in is not None else "logistics_only",
    }
    pricegrid.upsert_landed_costs(con, [row], "trips.xlsx", "now")


def _deal(con, source, cust, fam, terminal, month, price, committed=None, realized=None,
          price_type="basis", deal_date=None):
    r = {"source": source, "customer_master": cust, "customer_raw": cust, "product_family": fam,
         "product_raw": fam, "terminal": terminal, "month": month, "committed_gallons": committed,
         "realized_gallons": realized, "price": price, "price_type": price_type,
         "commitment_type": "firm", "volume_basis": "net", "deal_date": deal_date,
         "representative": None}
    r["deal_key"] = dealbook.deal_key(source, cust, fam, terminal, month, deal_date)
    db.upsert_deals(con, [r], source, "deals.xlsx", "now")


def _lifts(con, terminal, fam, dates, gallons, unit_price=None, unit_cost=None):
    df = pd.DataFrame({
        "customer_id": ["Summa"] * len(dates),
        "lift_datetime": pd.to_datetime(dates),
        "net_gallons": gallons, "terminal": terminal, "product": fam,
        "unit_price": unit_price, "unit_cost": unit_cost})
    db.insert_df(con, "lifts", df)
    db.rebuild_customers_from_lifts(con, replace=False)


# ---- plausibility + coverage on the full book ------------------------------------
def test_plausibility_gate_single_digit_cents(full_con):
    p = margin.compute_margin(full_con, window="all")
    assert p["available"] is True
    pl = p["plausibility"]
    # rack diesel margins read single-digit to low-double-digit ¢/gal — NOT near $1/gal (100¢)
    assert pl["units_warning"] is False
    assert 0.0 < pl["vol_weighted_cents_gal"] < 35.0
    assert abs(pl["vol_weighted_cents_gal"]) < 90.0     # the explicit "$1/gal" guard


def test_full_coverage_and_worked_example(full_con):
    p = margin.compute_margin(full_con, window="all")
    assert p["coverage"]["coverage_pct"] == 100.0       # full book has unit_price + unit_cost
    ex = p["worked_example"]
    # the worked example's arithmetic reconciles
    assert abs((ex["sell_per_gal"] - ex["book_cost_per_gal"]) - ex["book_margin_per_gal"]) < 1e-6
    assert abs(ex["book_margin_cents_gal"] - ex["book_margin_per_gal"] * 100) < 1e-6


def test_value_vs_volume_contrast(full_con):
    p = margin.compute_margin(full_con, window="all")
    cust = p["customers"]
    assert len(cust) > 5
    # ranking by margin is genuinely different from ranking by volume for some accounts
    assert any(c["rank_delta"] != 0 for c in cust)
    vv = p["value_vs_volume"]
    assert vv["fat_margin_movers"] or vv["thin_margin_movers"]
    # a "fat margin mover" ranks better on margin than volume
    for m in vv["fat_margin_movers"]:
        assert m["rank_by_margin"] < m["rank_by_volume"]


def test_units_warning_fires_on_dollar_margin(con):
    # margins ~ $1/gal (3.50 sell − 2.50 cost) ⇒ a units/basis error ⇒ flagged, never trusted
    _lifts(con, "Newark", "ULSD", pd.date_range("2025-01-01", periods=10, freq="3D"),
           [5000.0] * 10, unit_price=3.50, unit_cost=2.50)
    p = margin.compute_margin(con, window="all")
    assert p["plausibility"]["units_warning"] is True
    assert "$1/gal" in p["plausibility"]["note"] or "do NOT trust" in p["plausibility"]["note"]


# ---- BOOK vs REPLACEMENT ---------------------------------------------------------
def test_book_vs_replacement_diverge(con):
    pricegrid.ensure_tables(con)
    # an expensive old barge then a cheap recent one → replacement (latest) cost < running WAC
    _barge(con, "Newark", "ULSD", dt.date(2025, 1, 2), all_in=2.60, logistics=0.02)
    _barge(con, "Newark", "ULSD", dt.date(2025, 6, 1), all_in=2.40, logistics=0.02)
    # lifts priced off the grid-less fallback (unit_price), cost comes from Trips
    _lifts(con, "Newark", "ULSD", ["2025-06-15"] * 4, [10000.0] * 4, unit_price=2.70)
    base = margin.build_base(con, DEFAULT_CONFIG, "all", None)
    bt = margin.terminal_rollup(base)
    row = next(r for r in bt if r["terminal"] == "Newark")
    # replacement margin uses the cheaper latest cost ⇒ higher than book margin
    assert row["repl_cents_gal"] > row["book_cents_gal"]


# ---- deal-type margins (the index-on-index core) ---------------------------------
def test_deal_type_margins_term_forward_spot(con):
    pricegrid.ensure_tables(con)
    # one all-in barge for Newark×ULSD: landed 2.528, logistics 0.028, cargo differential 0.05
    _barge(con, "Newark", "ULSD", dt.date(2025, 1, 5), all_in=2.528, logistics=0.028, fixed_diff=0.05)
    # need a sell source so the base is "available"
    _lifts(con, "Newark", "ULSD", ["2025-02-15"] * 3, [8000.0] * 3, unit_price=2.60)
    _deal(con, dealbook.SOURCE_TERM, "Summa", "ULSD", "Newark", dt.date(2025, 2, 1),
          price=0.14, committed=100000.0, price_type="basis")
    _deal(con, dealbook.SOURCE_FORWARD, "Summa", "ULSD", "Newark", dt.date(2025, 2, 1),
          price=2.60, committed=50000.0, price_type="fixed")
    _deal(con, dealbook.SOURCE_SPOT, "Summa", "ULSD", "Newark", dt.date(2025, 2, 1),
          price=2.55, realized=30000.0, price_type="realized", deal_date=dt.date(2025, 2, 10))

    base = margin.build_base(con, DEFAULT_CONFIG, "all", None)
    dtm = margin.deal_type_margins(con, base)
    by = {s["source"]: s for s in dtm["by_source"]}
    # TERM: sell_diff 0.14 − cargo_diff 0.05 − logistics 0.028 − basis 0 = 0.062 = 6.2¢
    assert abs(by["term"]["avg_cents_gal"] - 6.2) < 0.2
    # FORWARD: locked 2.60 − landed 2.528 = 0.072 = 7.2¢
    assert abs(by["forward_fixed"]["avg_cents_gal"] - 7.2) < 0.3
    # SPOT: realized 2.55 − replacement 2.528 = 0.022 = 2.2¢
    assert abs(by["spot"]["avg_cents_gal"] - 2.2) < 0.3
    # all deal-type margins are plausible ¢/gal, never ~$1/gal
    for s in dtm["by_source"]:
        assert s["avg_cents_gal"] is None or abs(s["avg_cents_gal"]) < 35.0
    assert "same index" in dtm["basis_note"]


def test_term_margin_recoverable_without_market_level(con):
    """The term differential margin needs NO flat/market price — only differentials + logistics."""
    pricegrid.ensure_tables(con)
    _barge(con, "Newark", "ULSD", dt.date(2025, 1, 5), all_in=None, logistics=0.028, fixed_diff=0.05)
    _lifts(con, "Newark", "ULSD", ["2025-02-15"] * 3, [8000.0] * 3, unit_price=2.60)
    _deal(con, dealbook.SOURCE_TERM, "Summa", "ULSD", "Newark", dt.date(2025, 2, 1),
          price=0.14, committed=100000.0, price_type="basis")
    base = margin.build_base(con, DEFAULT_CONFIG, "all", None)
    by = {s["source"]: s for s in margin.deal_type_margins(con, base)["by_source"]}
    # 0.14 − 0.05 − 0.028 = 0.062 even though the barge has NO all-in flat (cargo flat unknown)
    assert abs(by["term"]["avg_cents_gal"] - 6.2) < 0.2
    assert by["term"]["priced"] == 1


# ---- forward-fixed mark-to-market ------------------------------------------------
def test_forward_mtm_flags_underwater_and_thin(con):
    pricegrid.ensure_tables(con)
    today = dt.date(2025, 1, 1)
    _barge(con, "Newark", "ULSD", dt.date(2024, 12, 20), all_in=2.55, logistics=0.02)  # replacement 2.55
    # an underwater lock (2.50 < 2.55), a healthy lock (2.70), a thin lock (2.56)
    _deal(con, dealbook.SOURCE_FORWARD, "A", "ULSD", "Newark", dt.date(2025, 3, 1), 2.50, committed=40000.0, price_type="fixed")
    _deal(con, dealbook.SOURCE_FORWARD, "B", "ULSD", "Newark", dt.date(2025, 4, 1), 2.70, committed=60000.0, price_type="fixed")
    _deal(con, dealbook.SOURCE_FORWARD, "C", "ULSD", "Newark", dt.date(2025, 5, 1), 2.56, committed=20000.0, price_type="fixed")
    mtm = margin.forward_mtm(con, None, DEFAULT_CONFIG, today=today)
    assert mtm["open_deals"] == 3 and mtm["priced_deals"] == 3
    assert mtm["underwater_deals"] == 1                  # the 2.50 lock
    assert mtm["thin_deals"] == 1                        # the 2.56 lock (1¢ < 3¢ thin threshold)
    # exposure: (2.50-2.55)*40k + (2.70-2.55)*60k + (2.56-2.55)*20k = -2000 + 9000 + 200 = 7200
    assert abs(mtm["mtm_total_dollars"] - 7200) < 50
    assert mtm["worst"][0]["status"] == "underwater"    # ranked worst-first


def test_forward_mtm_excludes_past_months(con):
    pricegrid.ensure_tables(con)
    _barge(con, "Newark", "ULSD", dt.date(2024, 12, 20), all_in=2.55, logistics=0.02)
    _deal(con, dealbook.SOURCE_FORWARD, "Old", "ULSD", "Newark", dt.date(2024, 6, 1), 2.50, committed=40000.0, price_type="fixed")
    mtm = margin.forward_mtm(con, None, DEFAULT_CONFIG, today=dt.date(2025, 1, 1))
    assert mtm["open_deals"] == 0                        # the 2024-06 commitment is closed/past


# ---- the gap helper (Phase-3 contract) -------------------------------------------
def test_margin_for_gap_splits_committed_and_spot(con):
    pricegrid.ensure_tables(con)
    _barge(con, "Newark", "ULSD", dt.date(2025, 1, 5), all_in=2.528, logistics=0.028, fixed_diff=0.05)
    _lifts(con, "Newark", "ULSD", ["2025-02-15"] * 3, [8000.0] * 3, unit_price=2.60)
    _deal(con, dealbook.SOURCE_TERM, "Summa", "ULSD", "Newark", dt.date(2025, 2, 1), 0.14, committed=100000.0, price_type="basis")
    _deal(con, dealbook.SOURCE_FORWARD, "Summa", "ULSD", "Newark", dt.date(2025, 2, 1), 2.60, committed=50000.0, price_type="fixed")
    _deal(con, dealbook.SOURCE_SPOT, "Summa", "ULSD", "Newark", dt.date(2025, 2, 1), 2.55, realized=30000.0, price_type="realized", deal_date=dt.date(2025, 2, 10))

    gap = margin.margin_for_gap(con, "Newark", "ULSD", 200000.0, DEFAULT_CONFIG, today=dt.date(2025, 1, 15))
    assert gap["available"] is True
    # committed book = 100k term + 50k forward = 150k; gap of 200k ⇒ 150k must-serve, 50k spot
    assert abs(gap["committed_gallons"] - 150000) < 1
    assert abs(gap["spot_gallons"] - 50000) < 1
    # committed blended margin = (6.2¢*100k + 7.2¢*50k)/150k ≈ 6.53¢
    assert abs(gap["committed_margin_cents_gal"] - 6.53) < 0.3
    # spot upside priced off the realized spot margin (≈ 2.2¢)
    assert abs(gap["spot_margin_cents_gal"] - 2.2) < 0.5
    assert gap["total_margin_dollars"] > 0
    # total reconciles to the two legs
    legs = (gap["committed_margin_dollars"] or 0) + (gap["spot_margin_dollars"] or 0)
    assert abs(gap["total_margin_dollars"] - legs) < 1


def test_margin_for_gap_under_committed_is_all_must_serve(con):
    pricegrid.ensure_tables(con)
    _barge(con, "Newark", "ULSD", dt.date(2025, 1, 5), all_in=2.528, logistics=0.028, fixed_diff=0.05)
    _lifts(con, "Newark", "ULSD", ["2025-02-15"] * 2, [8000.0] * 2, unit_price=2.60)
    _deal(con, dealbook.SOURCE_TERM, "Summa", "ULSD", "Newark", dt.date(2025, 2, 1), 0.14, committed=100000.0, price_type="basis")
    gap = margin.margin_for_gap(con, "Newark", "ULSD", 60000.0, DEFAULT_CONFIG, today=dt.date(2025, 1, 15))
    assert abs(gap["committed_gallons"] - 60000) < 1 and gap["spot_gallons"] == 0


# ---- gate ------------------------------------------------------------------------
def test_gate_locked_without_sources(con):
    # lifts with neither price nor cost, and no stores ⇒ not available
    _lifts(con, "Newark", "ULSD", ["2025-01-01"] * 3, [5000.0] * 3)
    av = margin.availability(con)
    assert av["available"] is False
    p = margin.compute_margin(con, window="all")
    assert p["available"] is False


# ---- API flow --------------------------------------------------------------------
def test_api_margin_flow(client):
    con = db.get_shared_connection()
    generator.generate(generator.GenConfig(seed=7, n_customers=20, months=14, profile="full"), con)
    r = client.get("/api/margin?window=all")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["plausibility"]["units_warning"] is False
    assert body["coverage"]["coverage_pct"] == 100.0
    assert len(body["customers"]) > 3
    # the gap endpoint answers in dollars
    g = client.get("/api/margin/gap", params={"terminal": "Linden", "product": "ULSD", "quantity": 500000})
    assert g.status_code == 200 and "total_margin_dollars" in g.json()
    cfg = client.get("/api/margin/config")
    assert cfg.status_code == 200 and "windows" in cfg.json()
