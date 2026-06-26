"""Tests for the Stage-1 weather model: HDD→demand β, the size-axis residual rewrite, OOS, anchor.

Heating fuels ONLY. The size axis for a weather-driven heating customer is measured on the residual
after removing β·HDD (so it reads steadier), but a genuinely lumpy (non-weather) heating customer is
NOT over-smoothed. β must be positive (cold → more demand); gasoline is never touched.
"""

from __future__ import annotations

import datetime as dt
import warnings

import numpy as np
import pandas as pd

from app import db, weather, weather_hdd, weather_model

warnings.filterwarnings("ignore")


def _hdd(d: dt.date) -> float:
    return weather.seasonal_hdd_cdd(d)[0]      # the deterministic proxy (pytest disables live fetch)


def _build(con):
    """A heating book at one terminal: one weather-DRIVEN customer, one NON-weather lumpy customer,
    plus a gasoline customer (control). HDD comes from the seasonal proxy."""
    rng = np.random.default_rng(11)
    rows = []
    days = [dt.date(2022, 1, 1) + dt.timedelta(days=3 * i) for i in range(220)]   # ~21 months, every 3d

    def add(cust, product, sizer):
        for d in days:
            rows.append({"customer_id": cust, "lift_datetime": pd.Timestamp(d),
                         "net_gallons": float(max(200.0, sizer(d))), "product": product,
                         "terminal": "Bronx"})

    # weather-driven ULSHO: size = 5000 + 80·HDD + small noise → swings with cold, steady underneath
    add("Weather HO", "ULSHO", lambda d: 5000 + 80 * _hdd(d) + rng.normal(0, 250))
    # NON-weather lumpy ULSHO: size is uniform noise, independent of HDD → must NOT be smoothed
    add("Lumpy HO", "ULSHO", lambda d: rng.uniform(2000, 12000))
    # gasoline control: never weather-adjusted
    add("Gas Co", "RBOB", lambda d: 6000 + rng.normal(0, 300))
    db.insert_df(con, "lifts", pd.DataFrame(rows))
    con.execute("INSERT INTO customers (customer_id, name, archetype, home_terminal) "
                "SELECT DISTINCT customer_id, customer_id, 'imported', 'Bronx' FROM lifts")


def test_demand_beta_positive_and_beats_blind_oos(con):
    _build(con)
    model = weather_model.build_model(con)
    assert model["available"]
    beta = model["demand_beta"].get("Bronx|ULSHO")
    assert beta and beta["available"]
    # β is positive (cold → more demand) and the right sign; trust (R² gate) is reported honestly and
    # is modest here because a NON-weather lumpy account deliberately dilutes the aggregate signal.
    assert beta["beta"] > 0 and beta["sign_ok"]
    # out-of-sample, the weather-aware fit still beats the weather-blind mean
    assert beta["oos"] is not None and beta["oos"]["beats_blind"] is True


def test_weather_driven_customer_gets_steadier_lumpy_one_does_not(con):
    _build(con)
    model = weather_model.build_model(con)
    lifts = con.execute("SELECT customer_id, lift_datetime, net_gallons, product, terminal FROM lifts").df()

    def axes(cust, fam):
        cl = lifts[lifts["customer_id"] == cust].sort_values("lift_datetime")
        return weather_model.adjusted_sizes(cl, fam, "Bronx", model)

    sizes_w, adj_w, diag_w = axes("Weather HO", "ULSHO")
    sizes_l, adj_l, diag_l = axes("Lumpy HO", "ULSHO")

    # the weather-driven account IS adjusted and its size CV drops materially
    assert adj_w is True
    assert diag_w["beta"] > 0
    assert diag_w["adj_cv"] < diag_w["raw_cv"] - 0.05
    # the genuinely lumpy account is NOT over-smoothed (weather doesn't explain its swings)
    assert adj_l is False
    assert diag_l["adj_cv"] is None or diag_l["adj_cv"] >= diag_l["raw_cv"] - 0.02


def test_gasoline_never_weather_adjusted(con):
    _build(con)
    model = weather_model.build_model(con)
    cl = con.execute("SELECT customer_id, lift_datetime, net_gallons, product, terminal FROM lifts "
                     "WHERE customer_id = 'Gas Co'").df()
    _sizes, adjusted, diag = weather_model.adjusted_sizes(cl, "GAS", "Bronx", model)
    assert adjusted is False
    assert diag["reason"] == "not a heating fuel"


def test_station_coverage_labels_modeled_vs_proxy(con):
    _build(con)
    # upload LGA HDD; Bronx → LGA → should read 'modeled', a non-loaded terminal stays 'proxy'
    obs = [{"station": "LGA", "day": dt.date(2022, 1, 1) + dt.timedelta(days=i),
            "hdd": round(_hdd(dt.date(2022, 1, 1) + dt.timedelta(days=i)), 1),
            "tmean": None, "hdd_normal": None, "hdd_5yr": None, "hdd_10yr": None}
           for i in range(700)]
    weather_hdd.upsert_observations(con, obs, "f", "now")
    days = [dt.date(2022, 1, 1) + dt.timedelta(days=i) for i in range(700)]
    _m, src, cov = weather_model.hdd_daily(con, "Bronx", days)
    assert cov == "modeled" and src.startswith("uploaded:LGA")
    # an unmapped terminal with no uploaded station → proxy, never borrowing LGA
    _m2, src2, cov2 = weather_model.hdd_daily(con, "Baltimore", days)
    assert cov2 == "proxy"


def test_ho_sold_anchor_agrees_in_sign(con):
    _build(con)
    # monthly HO SOLD that rises with HDD → a positive anchor β that should agree with the BOL β
    anchor = []
    for m in range(1, 13):
        hd = sum(_hdd(dt.date(2022, m, d)) for d in range(1, 28))
        anchor.append({"station": "LGA", "month": dt.date(2022, m, 1),
                       "ho_sold": 2000 + 5 * hd, "hdd_month": hd})
    weather_hdd.upsert_anchor(con, anchor, "f", "now")
    model = weather_model.build_model(con)
    anc = model["anchor"]
    assert anc and "LGA" in anc
    assert anc["LGA"]["anchor_sign_positive"] is True
    assert anc["LGA"]["agrees"] is True


def test_forward_hdd_seam_returns_a_curve(con):
    _build(con)
    fwd = weather_model.forward_hdd(con, "Bronx", dt.date(2024, 1, 1))
    assert len(fwd["hdd"]) == weather_model.DEFAULT_WEATHER_CONFIG.forward_horizon_days
    assert fwd["is_live"] is False                      # baseline, not a live feed (labelled)
    assert all(h >= 0 for h in fwd["hdd"])
    assert fwd["hdd"][0] > 0                             # Jan-1 is a cold (high-HDD) day
