"""Working-day calendar — a real day-type model so daily presence / cadence / gap math stops
being corrupted by non-lifting days.

The problem this fixes: every other module counts **calendar** days. Terminals don't operate that
way — almost nobody lifts on Sundays or bank holidays, and Saturdays are sparse. So a steady
Mon–Fri buyer is wrongly read as ~71% present (5/7), a Fri→Mon gap looks like a 3-day silence, and
"days since last lift" over a weekend over-counts. This module replaces that with a **three-day-type
model** (not a crude weekend exclusion):

  1. **NON-LIFTING** — Sundays **and** US bank/federal holidays. **Excluded** from the working-day
     denominator entirely (weight 0): a customer is NOT "absent" on a Sunday/holiday, and such days
     are NOT counted as gap days. A real lift that lands on one (data quirk) keeps its volume but the
     day is treated as an **exception** (weight 0, so it neither rewards nor penalizes presence).
  2. **LOW-ACTIVITY** — Saturdays. NOT fully excluded (that throws away real Saturday lifts) and NOT
     a full day (that makes everyone look less steady). Handled as a **partial day** whose weight is
     **measured from the data per terminal** (its real Saturday activity relative to a full weekday).
  3. **FULL** — Mon–Fri non-holiday. Full weight.

The rhythm is **learned from the loaded book, per terminal** (terminals can differ). Everything is
config-driven (:class:`CalendarConfig`). The module is self-contained (numpy / pandas / the optional
``holidays`` library only — nothing from ``scoring``) so the import graph stays acyclic; the daily /
behavioral / scoring / hedging layers call it.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass, replace

import numpy as np
import pandas as pd

# ---- Day types ------------------------------------------------------------------
FULL = "full"            # Mon–Fri non-holiday — full weight
LOW = "low"              # Saturday — partial, data-driven weight
NONLIFTING = "nonlifting"  # Sunday + holiday — excluded (weight 0)

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]  # pandas weekday(): Mon=0 … Sun=6


@dataclass(frozen=True)
class CalendarConfig:
    """Every knob for the working-day model — none of it hard-coded in the math."""
    country: str = "US"                    # holidays calendar (ISO country code)
    subdiv: str | None = None              # optional state/province (e.g. "NY"); None = federal only
    use_holidays: bool = True              # include the holiday calendar at all
    sunday_weight: float = 0.0             # Sundays are non-lifting
    holiday_weight: float = 0.0            # holidays are non-lifting
    saturday_default_weight: float = 0.35  # fallback Saturday weight when there's too little data
    saturday_min_obs: int = 6              # need this many Saturday *occurrences* in span to trust a measured weight
    saturday_weight_floor: float = 0.0     # clamp the measured Saturday weight …
    saturday_weight_cap: float = 1.0       # … to [floor, cap] (a Saturday is at most a full day)
    full_weekday_weight: float = 1.0       # Mon–Fri full weight

    def to_dict(self) -> dict:
        return asdict(self)

    def with_overrides(self, overrides: dict | None) -> "CalendarConfig":
        if not overrides:
            return self
        known = set(self.__dataclass_fields__)  # type: ignore[attr-defined]
        return replace(self, **{k: v for k, v in overrides.items() if k in known})


DEFAULT_CONFIG = CalendarConfig()


# ---- Holiday loading (offline; graceful if the library is unavailable) ----------
def _holiday_set(cfg: CalendarConfig, years) -> dict:
    """US (or configured) holidays for the given years — algorithmic, no network. Returns a dict-like
    mapping ``date -> name`` (``in`` and ``.get`` both work); an empty dict if disabled/unavailable."""
    if not cfg.use_holidays:
        return {}
    try:
        import holidays as _h
        return _h.country_holidays(cfg.country, subdiv=cfg.subdiv, years=sorted(set(years)))
    except Exception:  # noqa: BLE001 — never let a missing/odd holiday backend break the engines
        return {}


def _years_in(lifts: pd.DataFrame | None) -> range:
    """Year span covered by the book (+ buffer for future horizons & last year's holidays)."""
    today_year = _dt.date.today().year
    if lifts is None or not len(lifts) or "lift_datetime" not in lifts:
        return range(today_year - 3, today_year + 2)
    dts = pd.to_datetime(lifts["lift_datetime"], errors="coerce").dropna()
    if not len(dts):
        return range(today_year - 3, today_year + 2)
    lo = int(dts.min().year) - 1
    hi = max(int(dts.max().year), today_year) + 2
    return range(lo, hi)


# ---- The calendar ---------------------------------------------------------------
class WorkingCalendar:
    """Day-type classification + working-day counting for one book.

    Holidays/Sundays are global (weight 0); the **Saturday weight is per terminal** (learned from
    data, network fallback for unknown terminals). ``working_days_between`` is O(1) via a lazily-built
    per-terminal cumulative-weight series.
    """

    def __init__(self, cfg: CalendarConfig, sat_weights: dict | None, network_sat: float,
                 years, span: tuple | None = None, holiday_set: dict | None = None):
        self.cfg = cfg
        self._sat = dict(sat_weights or {})
        self._network_sat = float(network_sat)
        self._years = sorted(set(years))
        self._holidays = holiday_set if holiday_set is not None else _holiday_set(cfg, self._years)
        self._span = span
        self._cum: dict = {}  # terminal-key -> (DatetimeIndex, cumulative-weight ndarray)

    # ---- classification ----
    @staticmethod
    def _ts(d) -> pd.Timestamp:
        return pd.Timestamp(d).normalize()

    def is_holiday(self, d) -> bool:
        return self._ts(d).date() in self._holidays

    def holiday_name(self, d) -> str | None:
        return self._holidays.get(self._ts(d).date())

    def day_type(self, d) -> str:
        ts = self._ts(d)
        if ts.date() in self._holidays:
            return NONLIFTING
        wd = ts.weekday()
        return NONLIFTING if wd == 6 else LOW if wd == 5 else FULL

    def sat_weight(self, terminal: str | None = None) -> float:
        if terminal is not None and terminal in self._sat:
            return self._sat[terminal]
        return self._network_sat

    def weight(self, d, terminal: str | None = None) -> float:
        """Working-day weight of a single day: 0 for Sun/holiday, the (terminal) Saturday weight for
        Saturdays, full weight for Mon–Fri."""
        ts = self._ts(d)
        if ts.date() in self._holidays:
            return self.cfg.holiday_weight
        wd = ts.weekday()
        if wd == 6:
            return self.cfg.sunday_weight
        if wd == 5:
            return self.sat_weight(terminal)
        return self.cfg.full_weekday_weight

    def weights_for_index(self, idx, terminal: str | None = None) -> np.ndarray:
        """Vectorized per-day weights for a DatetimeIndex (used by the behavioral daily grids)."""
        idx = pd.DatetimeIndex(idx)
        if not len(idx):
            return np.array([], dtype=float)
        wd = idx.weekday.to_numpy()
        satw = self.sat_weight(terminal)
        w = np.where(wd <= 4, self.cfg.full_weekday_weight,
                     np.where(wd == 5, satw, self.cfg.sunday_weight)).astype(float)
        if len(self._holidays):
            hol = np.fromiter((d in self._holidays for d in idx.date), dtype=bool, count=len(idx))
            if hol.any():
                w = np.where(hol, self.cfg.holiday_weight, w)
        return w

    def working_week_length(self, terminal: str | None = None) -> float:
        """Working-day weights in one calendar week (~5.35): 5·full + Saturday."""
        return 5.0 * self.cfg.full_weekday_weight + self.sat_weight(terminal)

    # ---- counting ----
    def _cumulative(self, terminal: str | None):
        key = terminal if terminal in self._sat else None
        cached = self._cum.get(key)
        if cached is not None:
            return cached
        if self._span is not None:
            lo = pd.Timestamp(self._span[0]).normalize() - pd.Timedelta(days=7)
            hi = pd.Timestamp(self._span[1]).normalize() + pd.Timedelta(days=420)
        else:
            today = pd.Timestamp(_dt.date.today())
            lo, hi = today - pd.Timedelta(days=1200), today + pd.Timedelta(days=420)
        idx = pd.date_range(lo, hi, freq="D")
        cum = np.cumsum(self.weights_for_index(idx, terminal))
        cached = (idx, cum)
        self._cum[key] = cached
        return cached

    def cumulative_at(self, dates, terminal: str | None = None) -> np.ndarray:
        """Vectorized cumulative working-day weight at each date (sum of weights for all days ≤ date).
        ``np.diff`` of this over a sorted lift series gives the per-pair working-day gaps in one pass —
        far faster than calling :meth:`working_days_between` per pair in a hot loop."""
        idx, cum = self._cumulative(terminal)
        ts = pd.DatetimeIndex(pd.to_datetime(dates)).normalize()
        pos = np.clip(idx.searchsorted(ts), 0, len(cum) - 1)
        return cum[pos]

    def working_days_between(self, a, b, terminal: str | None = None) -> float:
        """Sum of working-day weights for days in ``(a, b]`` (so consecutive Mon→Tue = 1.0, and a
        Fri→Mon gap ≈ 1 + sat_weight, NOT 3). Returns 0 when ``b <= a``."""
        a = self._ts(a)
        b = self._ts(b)
        if b <= a:
            return 0.0
        idx, cum = self._cumulative(terminal)
        if a >= idx[0] and b <= idx[-1]:
            ia = int(idx.searchsorted(a))
            ib = int(idx.searchsorted(b))
            return float(cum[ib] - cum[ia])           # weights for idx[ia+1 .. ib] == days in (a, b]
        rng = pd.date_range(a + pd.Timedelta(days=1), b, freq="D")  # out-of-span fallback
        return float(self.weights_for_index(rng, terminal).sum())

    def window_working_days(self, start, end, terminal: str | None = None) -> float:
        """Working-day weights for the half-open window ``[start, end)`` (e.g. the next H days)."""
        start = self._ts(start)
        end = self._ts(end)
        if end <= start:
            return 0.0
        return self.working_days_between(start - pd.Timedelta(days=1), end - pd.Timedelta(days=1), terminal)

    def holidays_in(self, start, end) -> list[dict]:
        """The holidays (date + name) observed within ``[start, end]`` — what the model excludes."""
        s, e = self._ts(start).date(), self._ts(end).date()
        return [{"date": str(d), "name": str(n)} for d, n in sorted(self._holidays.items()) if s <= d <= e]

    def add_working_days(self, start, n: float, terminal: str | None = None) -> pd.Timestamp:
        """The calendar date reached by accumulating ``n`` working-day weights forward from ``start``
        (used to express a working-day horizon as a real 'by <date>')."""
        start = self._ts(start)
        if n <= 0:
            return start
        d = start
        acc = 0.0
        for _ in range(2000):  # generous cap; horizons are small
            d = d + pd.Timedelta(days=1)
            acc += self.weight(d, terminal)
            if acc >= n - 1e-9:
                return d
        return d


# ---- Learning the rhythm from data ----------------------------------------------
def _rhythm_group(g: pd.DataFrame, holset: dict, cfg: CalendarConfig) -> dict:
    """Measured day-of-week rhythm for one group (a terminal, or the whole network)."""
    dts = g["_dt"]
    day = dts.dt.normalize()
    first, last = day.min(), day.max()
    allidx = pd.date_range(first, last, freq="D")
    wd_all = allidx.weekday.to_numpy()
    is_hol_all = np.fromiter((d in holset for d in allidx.date), dtype=bool, count=len(allidx))
    occ = {w: int(np.sum((wd_all == w) & (~is_hol_all))) for w in range(7)}  # non-holiday occurrences
    hol_days = int(is_hol_all.sum())

    wd = dts.dt.weekday.to_numpy()
    is_hol_lift = np.fromiter((d in holset for d in day.dt.date), dtype=bool, count=len(g))
    net = g["_net"].to_numpy(dtype=float)
    nonhol = ~is_hol_lift
    lifts_wd = {w: int(np.sum((wd == w) & nonhol)) for w in range(7)}
    vol_wd = {w: float(np.sum(net[(wd == w) & nonhol])) for w in range(7)}
    total_lifts = int(len(g))
    total_vol = float(net.sum())

    act = {w: (lifts_wd[w] / occ[w]) if occ[w] else 0.0 for w in range(7)}
    full_occ = sum(occ[w] for w in range(5))
    full_base = (sum(lifts_wd[w] for w in range(5)) / full_occ) if full_occ else 0.0  # lifts per full weekday

    sat_occ, sat_lifts = occ[5], lifts_wd[5]
    measured = bool(full_base > 0 and sat_occ >= cfg.saturday_min_obs)
    sat_w = (act[5] / full_base) if measured else cfg.saturday_default_weight
    sat_w = float(min(cfg.saturday_weight_cap, max(cfg.saturday_weight_floor, sat_w)))

    exception_lifts = int(np.sum(is_hol_lift)) + lifts_wd[6]  # lifts on holidays + Sundays
    idx_norm = {w: (round(act[w] / full_base, 3) if full_base else None) for w in range(7)}

    return {
        "first_lift": str(first.date()), "last_lift": str(last.date()),
        "n_lifts": total_lifts, "total_net_gallons": round(total_vol, 1),
        "by_weekday": [{
            "weekday": WEEKDAY_NAMES[w], "dow": w,
            "occurrences": occ[w], "lifts": lifts_wd[w],
            "lift_share": round(lifts_wd[w] / total_lifts, 4) if total_lifts else 0.0,
            "volume": round(vol_wd[w], 1),
            "volume_share": round(vol_wd[w] / total_vol, 4) if total_vol else 0.0,
            "activity_per_day": round(act[w], 3),
            "activity_index": idx_norm[w],
            "day_type": NONLIFTING if w == 6 else LOW if w == 5 else FULL,
        } for w in range(7)],
        "saturday_weight": round(sat_w, 3), "saturday_measured": measured,
        "saturday_occurrences": sat_occ, "saturday_lifts": sat_lifts,
        "full_weekday_activity": round(full_base, 3),
        "holiday_count_in_span": hol_days,
        "exception_lifts": exception_lifts,
        "exception_share": round(exception_lifts / total_lifts, 4) if total_lifts else 0.0,
    }


def measure_rhythm(lifts: pd.DataFrame, cfg: CalendarConfig = DEFAULT_CONFIG) -> tuple[dict, dict, range]:
    """Per-terminal + network day-of-week rhythm. Returns ``(report, holiday_set, years)``."""
    df = lifts.copy()
    df["_dt"] = pd.to_datetime(df["lift_datetime"], errors="coerce")
    df = df[df["_dt"].notna()]
    years = _years_in(df)
    holset = _holiday_set(cfg, years)
    if not len(df):
        return {"network": None, "terminals": {}}, holset, years
    df["_net"] = (pd.to_numeric(df["net_gallons"], errors="coerce").fillna(0.0)
                  if "net_gallons" in df else 0.0)
    report = {"network": _rhythm_group(df, holset, cfg), "terminals": {}}
    if "terminal" in df.columns and df["terminal"].notna().any():
        for t, g in df.groupby(df["terminal"].fillna("(unknown)")):
            report["terminals"][str(t)] = _rhythm_group(g, holset, cfg)
    return report, holset, years


def from_lifts(lifts: pd.DataFrame | None, cfg: CalendarConfig = DEFAULT_CONFIG) -> tuple[WorkingCalendar, dict]:
    """Build a :class:`WorkingCalendar` (with per-terminal Saturday weights learned from the data)
    plus the measured rhythm report. Empty/▢ input → a default calendar (holidays + default Sat)."""
    if lifts is None or not len(lifts):
        return (WorkingCalendar(cfg, {}, cfg.saturday_default_weight, _years_in(None)),
                {"network": None, "terminals": {}})
    report, holset, years = measure_rhythm(lifts, cfg)
    sat_weights = {t: r["saturday_weight"] for t, r in report["terminals"].items()}
    network_sat = report["network"]["saturday_weight"] if report["network"] else cfg.saturday_default_weight
    dts = pd.to_datetime(lifts["lift_datetime"], errors="coerce").dropna()
    span = (dts.min().normalize(), dts.max().normalize()) if len(dts) else None
    cal = WorkingCalendar(cfg, sat_weights, network_sat, years, span, holiday_set=holset)
    return cal, report


def default_calendar(cfg: CalendarConfig = DEFAULT_CONFIG, lifts: pd.DataFrame | None = None) -> WorkingCalendar:
    """A calendar with the **default** Saturday weight (no per-terminal measurement) but the correct
    holiday set for the data's year span — used when there's no book-level calendar to pass in
    (direct/test calls). The terminal Saturday rhythm should be learned via :func:`from_lifts`."""
    return WorkingCalendar(cfg, {}, cfg.saturday_default_weight, _years_in(lifts))


def from_connection(con, cfg: CalendarConfig = DEFAULT_CONFIG) -> tuple[WorkingCalendar, dict]:
    """Build the calendar from the loaded ``lifts`` over the shared connection."""
    try:
        lifts = con.execute(
            "SELECT customer_id, lift_datetime, net_gallons, terminal FROM lifts "
            "WHERE lift_datetime IS NOT NULL").df()
    except Exception:  # noqa: BLE001 — empty/pre-init store
        lifts = pd.DataFrame()
    if len(lifts):
        lifts["lift_datetime"] = pd.to_datetime(lifts["lift_datetime"], errors="coerce")
    return from_lifts(lifts, cfg)


def upcoming_exclusions(cal: WorkingCalendar, today, days: int = 14) -> list[dict]:
    """The non-lifting (Sunday / holiday) days in the next ``days`` — for the UI 'what's excluded'."""
    today = pd.Timestamp(today).normalize()
    out = []
    for i in range(days):
        d = today + pd.Timedelta(days=i)
        if cal.day_type(d) == NONLIFTING:
            reason = cal.holiday_name(d) or ("Sunday" if d.weekday() == 6 else "non-lifting")
            out.append({"date": str(d.date()), "weekday": WEEKDAY_NAMES[d.weekday()], "reason": reason})
    return out
