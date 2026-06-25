"""Tests for the operational demand-hedging engine (Phase 2).

Covers the heart of the module: an **overdue burst buyer raises the buffer** (vs. a recently-lifted
one), a **steady-daily book needs little buffer while a sporadic/bursty book needs a lot**, the
**risk ranking by demand variability**, the **single-lift-exceeds-buffer** flag, the floor/band, the
honest "inventory not connected" note, and the API flow. All day-counting uses the Phase-1
working-day calendar.
"""

from __future__ import annotations

import warnings

import duckdb
import numpy as np
import pandas as pd
import pytest

from app import db, hedging

warnings.filterwarnings("ignore")

END = pd.Timestamp("2026-05-29")   # a Friday — the book's last data date / anchor


def _con():
    c = duckdb.connect(":memory:")
    db.init_db(c)
    return c


def _insert(con, cid, dates, gals, terminal="Linden", product="ULSD"):
    if np.isscalar(gals):
        gals = [float(gals)] * len(dates)
    rows = [(cid, pd.Timestamp(d).to_pydatetime(), float(g), terminal, product)
            for d, g in zip(dates, gals)]
    con.executemany("INSERT INTO lifts (customer_id, lift_datetime, net_gallons, terminal, product) "
                    "VALUES (?,?,?,?,?)", rows)


def _weekdays(start, end):
    return [d for d in pd.date_range(start, end, freq="D") if d.weekday() <= 4]


def _steady_fillers(con, n=3, gal=4000.0, terminal="Linden"):
    """n customers that lift every weekday up to END — a steady, low-variability base."""
    days = _weekdays("2025-06-02", END)
    for i in range(n):
        _insert(con, f"Steady {i}", days, gal, terminal)


def _regular_burst(last, n=16, step_days=21):
    return sorted([last - pd.Timedelta(days=step_days * i) for i in range(n)])


def _irregular_burst(last, n=14, seed=0):
    r = np.random.default_rng(seed)
    gaps = r.integers(8, 45, n)
    out, d = [], pd.Timestamp(last)
    for g in gaps:
        out.append(d)
        d = d - pd.Timedelta(days=int(g))
    return sorted(out)


# ---- the heart: an overdue burst buyer raises the buffer ------------------------
def test_overdue_burst_raises_buffer():
    # Recent: the burst buyer lifted right up to END (not overdue).
    cr = _con(); _steady_fillers(cr); _insert(cr, "Burst Co", _regular_burst(END), 50000.0)
    hr = hedging.compute_hedging(cr, terminal="Linden", today=END)["horizons"][0]

    # Overdue: identical, but the burst buyer went silent ~70 days before END (well past its cadence).
    co = _con(); _steady_fillers(co)
    _insert(co, "Burst Co", _regular_burst(END - pd.Timedelta(days=70)), 50000.0)
    ho = hedging.compute_hedging(co, terminal="Linden", today=END)
    bo = ho["horizons"][0]

    assert bo["coil_buffer"] > hr["coil_buffer"]              # the overdue burst RAISES the buffer
    assert bo["buffer"] > hr["buffer"]
    assert any(d["name"] == "Burst Co" for d in bo["overdue_drivers"])
    assert "overdue" in bo["readout"].lower()
    # the customer view flags it overdue with a working-day count past its working-day cadence
    bc = next(c for c in ho["customers"] if c["customer_id"] == "Burst Co")
    assert bc["overdue"] is True and bc["overdue_ratio"] > 1.0
    assert bc["working_days_since_last"] > bc["cadence_working_days"]


# ---- a steady book needs little; a bursty book needs a lot ----------------------
def test_steady_book_small_buffer_bursty_book_large():
    cs = _con(); _steady_fillers(cs, n=4)
    s = hedging.compute_hedging(cs, terminal="Linden", today=END)["horizons"][0]

    cb = _con()
    for i in range(4):
        _insert(cb, f"Marine {i}", _irregular_burst(END, seed=i), 60000.0)
    b = hedging.compute_hedging(cb, terminal="Linden", today=END)["horizons"][0]

    steady_ratio = s["buffer"] / max(s["expected"], 1.0)
    bursty_ratio = b["buffer"] / max(b["expected"], 1.0)
    assert bursty_ratio > 2 * steady_ratio        # the bursty book needs a far larger relative buffer
    assert s["floor"] > 0                          # the steady book has a reliable floor
    assert s["floor_share"] and s["floor_share"] > 0.5


# ---- risk concentration: ranked by variability, not volume ----------------------
def test_risk_ranking_by_variability():
    con = _con(); _steady_fillers(con, n=3, gal=4000.0)
    _insert(con, "Big Marine", _irregular_burst(END, n=14, seed=3), 80000.0)
    h = hedging.compute_hedging(con, terminal="Linden", today=END)
    wl = h["watch_list"]
    assert wl[0]["name"] == "Big Marine"           # the erratic whale dominates uncertainty
    assert wl[0]["variability_share"] > 0.3
    steady = [w for w in wl if w["name"].startswith("Steady")]
    assert steady and all(w["variability_share"] < wl[0]["variability_share"] for w in steady)


def test_single_lift_exceeds_buffer_flag():
    con = _con(); _steady_fillers(con, n=5, gal=3000.0)
    _insert(con, "Whale", _irregular_burst(END, n=12, seed=7), 200000.0)
    h = hedging.compute_hedging(con, terminal="Linden", today=END)
    whale = next(w for w in h["watch_list"] if w["name"] == "Whale")
    assert whale["single_lift_exceeds_buffer"] is True


# ---- expected band: floor ≤ P50, band nests ------------------------------------
def test_band_and_floor():
    con = _con(); _steady_fillers(con, n=4); _insert(con, "Burst", _irregular_burst(END, seed=1), 50000.0)
    h = hedging.compute_hedging(con, terminal="Linden", today=END)
    for b in h["horizons"]:
        assert b["p10"] <= b["p50"] <= b["p90"]
        assert 0 <= b["floor"] <= b["p50"] + 1.0
        assert b["recommended_staging"] >= b["p50"]   # staging = demand + buffer
    # a longer horizon expects more volume
    assert h["horizons"][1]["expected"] >= h["horizons"][0]["expected"]


# ---- honesty: inventory not connected ------------------------------------------
def test_inventory_not_connected_note():
    con = _con(); _steady_fillers(con, n=3)
    h = hedging.compute_hedging(con, terminal="Linden", today=END)
    assert h["inventory_connected"] is False
    assert h["inventory"] is None
    assert h["inventory_note"] and "TARGET" in h["inventory_note"]


def test_service_level_raises_buffer():
    con = _con(); _steady_fillers(con, n=3); _insert(con, "Burst", _irregular_burst(END, seed=2), 50000.0)
    lo = hedging.compute_hedging(con, terminal="Linden", service_level=0.80, today=END)["horizons"][0]
    hi = hedging.compute_hedging(con, terminal="Linden", service_level=0.99, today=END)["horizons"][0]
    assert hi["band_buffer"] > lo["band_buffer"]
    assert hi["recommended_staging"] > lo["recommended_staging"]


# ---- API flow -------------------------------------------------------------------
def test_api_flow(client):
    assert client.post("/api/studio/load-demo", json={"profile": "full"}).status_code == 200

    r = client.get("/api/hedging")
    assert r.status_code == 200, r.text
    h = r.json()
    assert h["terminal"] in h["terminals"] and h["horizons"]
    assert h["primary_horizon"] in (3, 5) and h["readout"]

    # the service-level slider re-derives the buffer without re-fetching the heavy forecast
    lo = client.get("/api/hedging", params={"service_level": 0.80}).json()["horizons"][0]["band_buffer"]
    hi = client.get("/api/hedging", params={"service_level": 0.99}).json()["horizons"][0]["band_buffer"]
    assert hi > lo

    ov = client.get("/api/hedging/overview").json()
    assert ov["readouts"] and all("terminal" in r for r in ov["readouts"])

    cfg = client.get("/api/hedging/config").json()
    assert "horizons" in cfg["config"]
    assert client.get("/api/hedging?window=bogus").status_code == 400


def test_calendar_api(client):
    client.post("/api/studio/load-demo", json={"profile": "full"})
    cal = client.get("/api/calendar").json()
    assert cal["available"] and cal["network"]["by_weekday"]
    assert cal["saturday_weights"] and "upcoming_exclusions" in cal
    # every terminal got a measured rhythm
    assert set(cal["terminal_names"]) and all(t in cal["saturday_weights"] for t in cal["terminal_names"])
