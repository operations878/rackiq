"""Tests for the working-day calendar (Phase 1).

Covers the three day types (Sunday + US holidays excluded; Saturday a data-driven partial day;
Mon–Fri full), the per-terminal **data-driven Saturday weight** (and the min-observations fallback),
**working-day gap counting** (a Fri→Mon gap is ~1, not 3), exception handling for a lift that lands on
a non-lifting day, and that terminals can carry different weights.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app import calendar_days as cd

CFG = cd.DEFAULT_CONFIG


def _book(terminal, sat_every=None, start="2024-01-01", end="2024-12-31"):
    """A book where the customer lifts every weekday, optionally on every ``sat_every``-th week's
    Saturday, and never on Sundays — so the measured Saturday weight ≈ the Saturday fraction."""
    rng = pd.date_range(start, end, freq="D")
    rows = []
    for d in rng:
        wd = d.weekday()
        if wd <= 4:
            rows.append((terminal, d, 5000.0))
        elif wd == 5 and sat_every and (int(d.isocalendar().week) % sat_every == 0):
            rows.append((terminal, d, 4000.0))
    return rows


def _frame(rows):
    df = pd.DataFrame(rows, columns=["terminal", "lift_datetime", "net_gallons"])
    df["customer_id"] = "C"
    return df


# ---- day types: Sunday + holiday exclusion --------------------------------------
def test_sunday_and_holiday_are_nonlifting():
    cal = cd.default_calendar(CFG, _frame(_book("T", sat_every=1)))
    sunday = pd.Timestamp("2024-06-09")     # a Sunday
    assert sunday.weekday() == 6
    assert cal.day_type(sunday) == cd.NONLIFTING
    assert cal.weight(sunday, "T") == 0.0

    jul4 = pd.Timestamp("2024-07-04")       # Independence Day (a Thursday in 2024)
    assert cal.is_holiday(jul4) and cal.holiday_name(jul4)
    assert cal.day_type(jul4) == cd.NONLIFTING and cal.weight(jul4, "T") == 0.0

    mon = pd.Timestamp("2024-06-10")        # a normal Monday
    assert mon.weekday() == 0
    assert cal.day_type(mon) == cd.FULL and cal.weight(mon, "T") == 1.0


def test_observed_holiday_shift():
    """The library applies observed shifts: July 4 2026 is a Saturday → observed Friday July 3."""
    cal = cd.default_calendar(CFG, _frame(_book("T", start="2026-01-01", end="2026-12-31")))
    assert cal.is_holiday(pd.Timestamp("2026-07-03"))            # observed
    assert "Independence Day" in (cal.holiday_name(pd.Timestamp("2026-07-03")) or "")


# ---- data-driven Saturday weight ------------------------------------------------
def test_saturday_weight_is_data_driven_per_terminal():
    rows = (_book("FullSat", sat_every=1)        # a lift every Saturday → weight ≈ 1.0
            + _book("NoSat", sat_every=None)     # never Saturdays → weight ≈ 0.0
            + _book("ThirdSat", sat_every=3))    # ~1/3 of Saturdays → weight ≈ 0.33
    cal, report = cd.from_lifts(_frame(rows), CFG)
    w = report["terminals"]
    assert w["FullSat"]["saturday_measured"] and w["FullSat"]["saturday_weight"] >= 0.85
    assert w["NoSat"]["saturday_measured"] and w["NoSat"]["saturday_weight"] <= 0.05
    assert 0.2 <= w["ThirdSat"]["saturday_weight"] <= 0.45
    # the calendar applies the per-terminal weight
    some_sat = next(d for d in pd.date_range("2024-03-01", "2024-03-31") if d.weekday() == 5)
    assert cal.weight(some_sat, "FullSat") > cal.weight(some_sat, "ThirdSat") > cal.weight(some_sat, "NoSat")


def test_saturday_weight_min_obs_fallback():
    """Too few Saturday occurrences to trust a measurement → fall back to the default weight."""
    rows = _book("Short", sat_every=1, start="2024-01-01", end="2024-01-28")  # only ~4 Saturdays < min_obs
    _cal, report = cd.from_lifts(_frame(rows), CFG)
    r = report["terminals"]["Short"]
    assert r["saturday_occurrences"] < CFG.saturday_min_obs
    assert r["saturday_measured"] is False
    assert r["saturday_weight"] == CFG.saturday_default_weight


# ---- working-day gap counting ---------------------------------------------------
def test_fri_to_mon_gap_is_not_three_days():
    cal = cd.default_calendar(CFG, _frame(_book("T", sat_every=1)))  # default Saturday weight 0.35
    fri = pd.Timestamp("2024-06-07")
    mon = pd.Timestamp("2024-06-10")
    assert fri.weekday() == 4 and mon.weekday() == 0
    gap = cal.working_days_between(fri, mon, "T")        # Sat(0.35) + Sun(0) + Mon(1)
    assert abs(gap - 1.35) < 1e-6
    assert gap < (mon - fri).days                         # < the naive 3 calendar days
    # consecutive weekdays are exactly one working day
    assert cal.working_days_between(mon, pd.Timestamp("2024-06-11"), "T") == 1.0


def test_days_since_over_a_weekend_and_holiday():
    cal, _ = cd.from_lifts(_frame(_book("T", sat_every=None)), CFG)  # measured: no Saturday activity → weight 0
    # last lift Friday before July 4 2024 (Thu holiday); "today" the following Monday
    fri = pd.Timestamp("2024-06-28")
    nxt_mon = pd.Timestamp("2024-07-01")
    assert cal.working_days_between(fri, nxt_mon, "T") == 1.0   # Sat 0 + Sun 0 + Mon 1
    # spanning the July 4 holiday week: Fri 6/28 → Fri 7/5 excludes the Thu holiday
    span = cal.working_days_between(pd.Timestamp("2024-06-28"), pd.Timestamp("2024-07-05"), "T")
    assert abs(span - 4.0) < 1e-6   # Mon,Tue,Wed,(Thu=holiday 0),Fri = 4 working days


def test_window_working_days_and_add_working_days():
    cal, _ = cd.from_lifts(_frame(_book("T", sat_every=None)), CFG)  # measured: Saturday weight 0
    mon = pd.Timestamp("2024-06-10")
    # next 7 calendar days from a Monday = Mon..Sun = 5 working days (Sat/Sun excluded)
    assert cal.window_working_days(mon, mon + pd.Timedelta(days=7), "T") == 5.0
    # 3 working days forward from Monday lands on Thursday
    assert cal.add_working_days(mon, 3, "T").date() == pd.Timestamp("2024-06-13").date()


# ---- exception: a real lift on a non-lifting day --------------------------------
def test_exception_lift_kept_but_excluded_from_presence():
    from app import behavioral
    from app.scoring_config import DEFAULT_CONFIG as SCFG
    as_of = pd.Timestamp("2024-06-28")      # a Friday
    cal = cd.default_calendar(CFG, None)
    # weekday lifts + one big lift on a Sunday (a data quirk / exception)
    days = [d for d in pd.date_range("2024-06-03", as_of) if d.weekday() <= 4]
    cl = pd.DataFrame({"lift_datetime": list(days) + [pd.Timestamp("2024-06-09")],  # 6/9 is a Sunday
                       "net_gallons": [5000.0] * len(days) + [99000.0]})
    prof = behavioral.daily_profile(cl, SCFG, as_of, name="Exc", cal=cal, terminal="T")
    head = prof["windows"]["all"]
    # the Sunday's big volume is kept in size-when-present (max ≥ the exception load)…
    assert head["size_when_present"]["max"] >= 99000.0
    # …but Sundays contribute 0 to the working-day denominator (presence reads near-fully-present)
    assert head["presence"]["active_day_rate"] >= 0.9


def test_rhythm_report_shape():
    cal, report = cd.from_lifts(_frame(_book("T", sat_every=2)), CFG)
    net = report["network"]
    assert net["n_lifts"] > 0 and len(net["by_weekday"]) == 7
    assert {"weekday", "lifts", "lift_share", "activity_index", "day_type"} <= set(net["by_weekday"][0])
    # holidays in the 2024 span are surfaced (what the model excludes)
    hol = cal.holidays_in("2024-01-01", "2024-12-31")
    assert any("Independence Day" in h["name"] for h in hol)
