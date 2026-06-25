"""Tests for the per-customer demand forecasting engine.

Covers: per-customer model selection (not one formula for everyone), walk-forward backtest,
seasonality preserved on SPARSE history (no zero-collapse — the prior bug), honest per-customer
uncertainty, the low-predictability flag, the new-vs-old-vs-naive comparison harness (the proof),
and — critically — TODAY-anchoring with the data-recency gap surfaced.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

from app import forecasting, generator, scoring
from app.scoring_config import DEFAULT_CONFIG as CFG

warnings.filterwarnings("ignore")


@pytest.fixture()
def full_book(con):
    generator.generate(generator.GenConfig(seed=42, n_customers=30, months=20, profile="full"), con)
    return con


# ---- Model selection: per customer, not one formula ----------------------------
def test_per_customer_model_selection(full_book):
    res = scoring.compute_scores(full_book, CFG, "all")
    avail = [c for c in res["customers"] if c["forecast"]["available"]]
    assert avail
    models = {c["forecast"]["model"] for c in avail}
    # several DIFFERENT models chosen across the book — not one formula forced on everyone
    assert len(models) >= 3, f"expected diverse model selection, got {models}"
    assert models <= set(forecasting.MODEL_LABEL)
    for c in avail:
        f = c["forecast"]
        assert f["model"] in forecasting.MODEL_LABEL and f["model_label"]
        assert "mape" in f and "skill_vs_naive" in f and "beats_naive" in f
        # plain-language read names the chosen model
        assert f["model_label"] in (f["plain"] or "")


def test_seasonal_forecast_is_not_flat(full_book):
    """A seasonal-model customer's forward curve reflects the cycle — it is NOT a flat line."""
    res = scoring.compute_scores(full_book, CFG, "all")
    seasonal = [c for c in res["customers"]
                if c["forecast"]["available"] and c["forecast"]["model"] in ("seasonal", "hw_seasonal")]
    assert seasonal, "expected at least one seasonal-model customer on the full book"
    # at least one seasonal account has a genuinely non-flat forward base curve
    swings = []
    for c in seasonal:
        bases = [p["base"] for p in c["forecast_series"]]
        if len(bases) >= 4:
            swings.append(max(bases) - min(bases))
    assert swings and max(swings) > 1.0, "seasonal forecast curve should not be flat"


def test_seasonal_path_no_zero_collapse_on_sparse():
    """The prior bug: a sparse winter-only buyer collapsed to a zero forecast because the recent
    (summer) periods were empty. The seasonal model must forecast winter HIGH and never zero-out."""
    starts = pd.date_range("2023-09-01", periods=24, freq="MS")
    months = np.array([s.month for s in starts])
    y = np.array([10000.0 if m in (12, 1, 2) else 0.0 for m in months])  # winter-only, zero tail in summer
    fmonths = np.array([7, 8, 12, 1, 2])  # forecast Jul, Aug, Dec, Jan, Feb
    path = forecasting._seasonal_path(y, months, fmonths, CFG)
    assert path[2] > 5000 and path[3] > 5000 and path[4] > 5000   # Dec/Jan/Feb forecast high
    assert path.max() > 0                                          # never an all-zero collapse


# ---- The proof: new engine vs old run-rate vs naive -----------------------------
def test_forecast_backtest_beats_baselines(full_book):
    bt = scoring.forecast_backtest(full_book, CFG)
    assert bt["n_customers"] > 0
    assert set(bt["methods"]) == {"new_engine", "old_runrate", "naive"}
    imp = bt["improvement"]
    # the new engine measurably beats BOTH baselines on the robust (median) metrics
    assert imp["vs_naive_pct"] > 0, imp          # median MAPE vs naive-last
    assert imp["vs_old_pct"] > 0, imp            # median MAPE vs old flat run-rate
    assert imp["mae_vs_naive_pct"] > 0, imp      # mean absolute error vs naive
    assert imp["mae_median_vs_old_pct"] > 0, imp # median absolute error vs old run-rate
    # honesty: the engine does NOT claim to beat naive for every customer
    assert 0 < bt["n_beat_naive"] <= bt["n_customers"]
    assert bt["n_beat_naive"] > bt["n_customers"] // 2  # but it beats naive on the majority
    r = bt["customers"][0]
    assert set(r["mape"]) == {"new_engine", "old_runrate", "naive"}
    assert r["chosen_model"] in forecasting.MODEL_LABEL


def test_compare_customer_walk_forward():
    """The comparison is a true walk-forward (re-selects the model from data before each step)."""
    starts = pd.date_range("2023-01-02", periods=40, freq="W-MON")
    t = np.arange(40)
    y = 10000 + 1500 * np.sin(2 * np.pi * t / 13) + 300 * np.cos(t)  # smooth seasonal-ish
    core = {"grain": "weekly", "periods": pd.DataFrame({"period_start": starts, "actual": y}),
            "dts": pd.Series(starts), "vols": y, "last_lift": starts[-1], "n_lifts": 40,
            "var_status": "ok"}
    comp = forecasting.compare_customer(core, CFG)
    assert comp is not None
    assert set(comp["mape"]) == {"new_engine", "old_runrate", "naive"}
    assert comp["best"] in comp["mae"]


# ---- Honest uncertainty + low-predictability ------------------------------------
def test_low_predictability_is_honest(full_book):
    res = scoring.compute_scores(full_book, CFG, "all")
    for c in res["customers"]:
        f = c["forecast"]
        if f["available"] and f.get("low_predictability"):
            assert f["beats_naive"] is False
            assert f["rough"] is True
            assert "rough range" in (f["plain"] or "")


def test_erratic_account_flagged_rough(con):
    """A wildly erratic buyer gets a wide band / rough (or low-predictability) flag — never false
    precision."""
    generator.generate(generator.GenConfig(seed=3, n_customers=12, months=18, profile="full"), con)
    end = pd.Timestamp(con.execute("SELECT max(lift_datetime) FROM lifts").fetchone()[0])
    # alternating tiny/huge volumes at irregular gaps → genuinely unpredictable
    offs = [2, 9, 13, 27, 31, 48, 52, 70, 74, 95, 99, 120, 124, 150, 154, 175]
    vols = [2000, 35000, 2500, 33000, 1800, 38000, 2200, 31000,
            1900, 36000, 2100, 34000, 2300, 37000, 2000, 32000]
    rows = [("Chaos Trading", (end - pd.Timedelta(days=d)).to_pydatetime(), float(v), "Linden", "ULSD")
            for d, v in zip(offs, vols)]
    con.executemany("INSERT INTO lifts (customer_id, lift_datetime, net_gallons, terminal, product) "
                    "VALUES (?,?,?,?,?)", rows)
    res = scoring.compute_scores(con, CFG, "all")
    chaos = next(c for c in res["customers"] if c["customer_id"] == "Chaos Trading")
    f = chaos["forecast"]
    assert f["available"]                       # still produces a forecast (a wide-banded one)
    assert f["rough"] is True                   # but flags it honestly
    assert "rough range" in f["plain"]


def test_band_tighter_for_steadier_account(full_book):
    """Per-customer band comes from that customer's OWN backtest error → steady accounts tight,
    erratic accounts wide (relative band)."""
    res = scoring.compute_scores(full_book, CFG, "all")
    rows = [c for c in res["customers"] if c["forecast"]["available"] and c["var"]["score"]]

    def rel_band(c):
        h = next(h for h in c["forecast"]["horizons"] if h["days"] == 30)
        return (h["hi"] - h["lo"]) / h["expected"] if h["expected"] else None

    hi = [rel_band(c) for c in rows if c["var"]["score"] >= 70]
    lo = [rel_band(c) for c in rows if c["var"]["score"] < 55]
    hi = [x for x in hi if x is not None]
    lo = [x for x in lo if x is not None]
    if hi and lo:
        assert (sum(hi) / len(hi)) < (sum(lo) / len(lo))


# ---- CRITICAL: anchor forecasts to TODAY, not the last data date ----------------
def test_today_anchoring_and_recency_gap(full_book):
    """Set 'today' well after the last data date and confirm forecasts start from TODAY (not the
    last ship date) and that the data-recency note appears."""
    res0 = scoring.compute_scores(full_book, CFG, "all")
    as_of = pd.Timestamp(res0["as_of"])
    future = as_of + pd.Timedelta(days=60)

    res = scoring.compute_scores(full_book, CFG, "all", today=future)
    # top-level recency block
    assert res["forecast_anchor"] == str(future.date())
    assert res["data_through"] == str(as_of.date())
    assert res["data_lag_days"] == 60
    assert res["recency_note"] and "behind today" in res["recency_note"]

    c = next(c for c in res["customers"] if c["forecast"]["available"])
    f = c["forecast"]
    # the customer's forecast is anchored to today, with the gap surfaced honestly
    assert f["forecast_anchor"] == str(future.date())
    assert f["data_through"] == str(as_of.date())
    assert f["gap_days"] == 60 and f["gap_note"] and "projected across the gap" in f["gap_note"]
    # the forward curve runs PAST today (not stuck at the last data date)
    last = pd.Timestamp(c["forecast_series"][-1]["period_start"])
    assert last > future, "forecast series must extend beyond today, not stop at the last data date"
    assert last >= future + pd.Timedelta(days=CFG.forecast_max_horizon_days - 31)
    # the forward series spans the gap (some points before today, some after) — the projection
    # crosses the data gap rather than silently treating the last data date as 'now'
    pts = [pd.Timestamp(p["period_start"]) for p in c["forecast_series"]]
    assert any(p < future for p in pts) and any(p >= future for p in pts)


def test_anchor_defaults_to_no_gap_on_fresh_book(full_book):
    """With a book that ends ~today (the demo default), there's no meaningful gap or note."""
    res = scoring.compute_scores(full_book, CFG, "all")  # today defaults to real now
    assert res["data_lag_days"] is not None
    # demo data ends on today's date, so the lag is small and no scary note is shown
    assert res["data_lag_days"] < CFG.forecast_gap_note_days
    assert res["recency_note"] is None


def test_silent_account_is_damped_and_flagged(con):
    """A customer silent well past their own cadence (e.g. quiet through the whole data gap) is
    trimmed for a possible slowdown and flagged — the forecast reflects recency, not ignores it."""
    generator.generate(generator.GenConfig(seed=8, n_customers=12, months=18, profile="full"), con)
    end = pd.Timestamp(con.execute("SELECT max(lift_datetime) FROM lifts").fetchone()[0])
    # ~14 regular ~10-day lifts, but the LAST one was 130 days ago → long silence vs a ~10d cadence
    offs = [130 + 10 * i for i in range(14)]
    rows = [("Quiet Co", (end - pd.Timedelta(days=d)).to_pydatetime(), 8000.0, "Linden", "ULSD")
            for d in offs]
    con.executemany("INSERT INTO lifts (customer_id, lift_datetime, net_gallons, terminal, product) "
                    "VALUES (?,?,?,?,?)", rows)
    res = scoring.compute_scores(con, CFG, "all")
    q = next(c for c in res["customers"] if c["customer_id"] == "Quiet Co")
    f = q["forecast"]
    assert f["available"]
    assert f["slowing"] is True and f["days_silent"] >= 120
    assert f["rough"] is True and "rough range" in f["plain"]


def test_forecast_unavailable_for_thin_history(con):
    generator.generate(generator.GenConfig(seed=11, n_customers=12, months=16, profile="full"), con)
    end = pd.Timestamp(con.execute("SELECT max(lift_datetime) FROM lifts").fetchone()[0])
    con.executemany("INSERT INTO lifts (customer_id, lift_datetime, net_gallons, terminal, product) "
                    "VALUES (?,?,?,?,?)", [("OneShot Co", (end - pd.Timedelta(days=3)).to_pydatetime(),
                                           5000.0, "Linden", "ULSD")])
    res = scoring.compute_scores(con, CFG, "all")
    one = next(c for c in res["customers"] if c["customer_id"] == "OneShot Co")
    assert one["forecast"]["available"] is False
    assert one["forecast"]["horizons"] == []


# ---- API ------------------------------------------------------------------------
def test_forecast_backtest_api(client):
    client.post("/api/studio/load-demo", json={"profile": "full"})
    bt = client.get("/api/scores/forecast-backtest").json()
    assert bt["n_customers"] > 0
    assert set(bt["methods"]) == {"new_engine", "old_runrate", "naive"}
    assert bt["improvement"]["vs_naive_pct"] > 0
    assert bt["customers"][0]["chosen_model"]


def test_scores_api_surfaces_recency(client):
    client.post("/api/studio/load-demo", json={"profile": "full"})
    s = client.get("/api/scores?window=all").json()
    assert "forecast_anchor" in s and "data_through" in s and "data_lag_days" in s
    top = s["customers"][0]
    # the ranked-table forecast carries the chosen model + accuracy (but not the heavy series)
    assert "model" in top["forecast"] and "mape" in top["forecast"]
    assert "forecast_series" not in top
