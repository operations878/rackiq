"""Tests for the TWO-AXIS variability score.

The whole point: cadence consistency and size consistency are INDEPENDENT axes. A daily-but-lumpy
account and a sparse-but-identical account are opposite cases and must NOT get the same label — and
crucially, a lumpy account's size variance must NEVER be smoothed away (it lands on AXIS 2, loud).
"""

from __future__ import annotations

import datetime as dt
import warnings

import numpy as np
import pandas as pd

from app import db, dealbook, variability

warnings.filterwarnings("ignore")

START = dt.date(2024, 1, 1)


def _weekday_dates(n_weeks):
    """Mon–Fri dates over n_weeks (working days)."""
    out = []
    d = pd.Timestamp(START)
    while len(out) < n_weeks * 5:
        if d.weekday() < 5:
            out.append(d)
        d += pd.Timedelta(days=1)
    return out


def _build_book(con):
    rng = np.random.default_rng(7)
    rows = []

    def add(cust, dates, sizes):
        for d, s in zip(dates, sizes):
            rows.append({"customer_id": cust, "lift_datetime": pd.Timestamp(d),
                         "net_gallons": float(s), "product": "ULSD", "terminal": "Newark"})

    wd = _weekday_dates(24)                                          # ~120 working days
    # METRONOME: every working day, near-identical size
    add("Metro Co", wd, 5000 + rng.normal(0, 120, len(wd)))
    # DAILY-LUMPY: every working day, wildly variable size (500..8000) — cadence steady, size NOT
    add("Lumpy Daily", wd, rng.uniform(500, 8000, len(wd)))
    # SPARSE-IDENTICAL: every ~14 calendar days, identical size
    sparse = [pd.Timestamp(START) + pd.Timedelta(days=14 * i) for i in range(9)]
    add("Sparse Same", sparse, [3000.0] * len(sparse))
    # SPORADIC-BURSTY: irregular gaps, variable size
    offs = np.cumsum(rng.integers(3, 40, 10))
    add("Sporadic Co", [pd.Timestamp(START) + pd.Timedelta(days=int(o)) for o in offs],
        rng.uniform(800, 9000, 10))

    db.insert_df(con, "lifts", pd.DataFrame(rows))
    con.execute("INSERT INTO customers (customer_id, name, archetype, home_terminal) "
                "SELECT DISTINCT customer_id, customer_id, 'imported', 'Newark' FROM lifts")


def test_two_axes_present_and_independent(con):
    _build_book(con)
    res = variability.compute_variability(con)
    assert res["available"]
    by = {c["customer_id"]: c for c in res["customers"]}

    metro, lumpy = by["Metro Co"], by["Lumpy Daily"]
    sparse, sporadic = by["Sparse Same"], by["Sporadic Co"]

    # both axes are reported as separate numbers
    for c in (metro, lumpy, sparse):
        assert c["cadence_consistency"] is not None and c["size_consistency"] is not None

    # AXIS 1: the two DAILY accounts both have steady cadence; the sparse/sporadic ones do not
    assert metro["cadence_consistency"] > 65
    assert lumpy["cadence_consistency"] > 65
    assert sparse["cadence_consistency"] < metro["cadence_consistency"]

    # AXIS 2 (the honesty test): the lumpy account's size variance is NOT hidden — it scores far
    # lower on size than the metronome, even though both lift every day (500-8000 swing → ~C-grade).
    assert lumpy["size_consistency"] < 60
    assert metro["size_consistency"] > 75
    assert lumpy["size_consistency"] < metro["size_consistency"] - 25


def test_quadrants_separate_the_cases(con):
    _build_book(con)
    res = variability.compute_variability(con)
    by = {c["customer_id"]: c for c in res["customers"]}
    # daily-lumpy and sparse-identical are OPPOSITE cases — must not share a label
    assert by["Metro Co"]["quadrant"] == "metronome"
    assert by["Lumpy Daily"]["quadrant"] == "daily_variable_size"
    assert by["Sparse Same"]["quadrant"] == "infrequent_identical"
    assert by["Lumpy Daily"]["quadrant"] != by["Sparse Same"]["quadrant"]


def test_distribution_and_coverage_present(con):
    _build_book(con)
    res = variability.compute_variability(con)
    d = res["distribution"]
    assert set(d) >= {"cadence_consistency", "size_consistency", "quadrants"}
    assert d["cadence_consistency"]["n"] >= 3
    assert res["coverage"]["pct_volume_scored"] > 0


def test_commitment_annotation_attaches_only_to_resolved(con):
    _build_book(con)
    # a deal for Metro Co that resolves (same master) annotates; an unmapped deal does not
    rows = [{
        "source": "term", "customer_raw": "Metro Co", "product_raw": "ULSD", "product_family": "ULSD",
        "terminal": None, "month": dt.date(2024, 3, 1), "committed_gallons": 50000.0,
        "realized_gallons": None, "price": 0.07, "price_type": "basis", "commitment_type": "firm",
        "volume_basis": "net", "deal_date": None, "representative": None,
    }]
    rows[0]["deal_key"] = dealbook.deal_key("term", "Metro Co", "ULSD", None, dt.date(2024, 3, 1), None)
    db.upsert_deals(con, rows, "term", "f", "now")
    # confirm the bridge (Metro Co → Metro Co master)
    db.upsert_crosswalk_entries(con, [{"variant_key": "Metro Co", "master_id": "Metro Co",
                                       "master_name": "Metro Co", "confidence": 1.0,
                                       "status": "confirmed", "source": "test", "updated_at": "now"}])
    db.resolve_deal_masters(con)
    res = variability.compute_variability(con)
    by = {c["customer_id"]: c for c in res["customers"]}
    assert by["Metro Co"]["commitment"]["available"] is True
    assert by["Metro Co"]["commitment"]["has_term"] is True
    assert by["Sporadic Co"]["commitment"]["label"] == "no commitment data"


def test_insufficient_history_not_scored(con):
    db.insert_df(con, "lifts", pd.DataFrame({
        "customer_id": ["Tiny"] * 2, "lift_datetime": pd.to_datetime(["2024-01-01", "2024-02-01"]),
        "net_gallons": [1000.0, 2000.0], "product": ["ULSD"] * 2, "terminal": ["Newark"] * 2}))
    con.execute("INSERT INTO customers VALUES ('Tiny','Tiny','imported','Newark')")
    res = variability.compute_variability(con)
    tiny = next(c for c in res["customers"] if c["customer_id"] == "Tiny")
    assert tiny["data_sufficient"] is False
    assert tiny["cadence_consistency"] is None
