"""Demand Cockpit engine — the per-terminal operating forecast.

This is the brain behind the **Demand Cockpit** (the per-terminal operating view). For one
``terminal × product`` it:

  1. Forecasts each customer's near-term volume with **Holt-Winters** exponential smoothing
     (additive trend, additive seasonal where there are ≥2 seasonal cycles; Holt's linear
     trend for moderate history), falling back to a **seasonal-naive** model for short
     history (and a flat level for the very thinnest accounts).
  2. **Rolls the per-customer forecasts up to the terminal** as a P10/P50/P90 band. The band
     is *derived from historical forecast error* (an expanding one-step backtest gives each
     customer a relative-error σ) and is **VAR-weighted**: erratic (low-VAR) customers widen
     the band more than steady ones, via ``1 + λ·(1 − VAR/100)``.
  3. If inventory + ``tank_capacity`` are present, computes **current inventory**, **days of
     cover**, and a **forecast burn-down vs. the tank level** (with fast/slow demand paths).
  4. Turns the demand distribution + (optional) lot size / lead time into a plain-English
     **recommended action** at a chosen **service level** — "buy ~X gal by <date> to hold a
     95% service level." With no supply data it gives a *target carry* and notes the gap.
  5. Reports a **forecast-accuracy strip** (recent MAPE / bias from a terminal-level backtest).

Everything is **capability-gated**: the payload carries an ``availability`` block so the UI
greys out the cover / burn-down / buy-by-date pieces when inventory data is absent.

The per-customer and terminal forecast distributions are **persisted** (``persist``) to the
``demand_forecast_customer`` / ``demand_forecast_terminal`` derived caches so downstream phases
(P6 allocation, P7 pricing, P10 S&OP) can read one canonical forecast. Like the scoring and
daily caches these are created by :func:`ensure_tables` (NOT ``init_db``), so they survive a
demo reload / reset.

All weights, horizons, and planning constants live in :class:`DemandConfig`.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import capabilities, db, scoring
from .scoring_config import DEFAULT_CONFIG as SCORING_DEFAULT
from .scoring_config import WINDOWS

# ---- Persistence (derived caches; recomputed from canonical data) ---------------
# The per-customer and terminal forecast distributions. Downstream phases (P6/P7/P10) read
# these instead of re-deriving a forecast, so the whole app shares one demand number.
DEMAND_DDL = [
    """CREATE TABLE IF NOT EXISTS demand_forecast_customer (
        customer_id VARCHAR, name VARCHAR, terminal VARCHAR, product VARCHAR,
        score_window VARCHAR, computed_at VARCHAR, grain VARCHAR, method VARCHAR,
        h_index INTEGER, period_start VARCHAR,
        p10 DOUBLE, p50 DOUBLE, p90 DOUBLE, mape DOUBLE, bias DOUBLE, var_score DOUBLE
    )""",
    """CREATE TABLE IF NOT EXISTS demand_forecast_terminal (
        terminal VARCHAR, product VARCHAR, score_window VARCHAR, computed_at VARCHAR,
        grain VARCHAR, h_index INTEGER, period_start VARCHAR,
        p10 DOUBLE, p50 DOUBLE, p90 DOUBLE, daily_p50 DOUBLE,
        n_customers INTEGER, mape DOUBLE, bias DOUBLE
    )""",
]

ALL_PRODUCTS = "(all)"  # sentinel for the "all products" rollup at a terminal


def ensure_tables(con) -> None:
    for ddl in DEMAND_DDL:
        con.execute(ddl)


# ---- Configuration (every weight / horizon / planning constant a parameter) -----
@dataclass(frozen=True)
class DemandConfig:
    # Terminal operating grain. Weekly aligns every customer onto one period index so the
    # per-customer forecasts can be summed; days-of-cover then divides by 7.
    grain: str = "weekly"
    period_days: float = 7.0
    horizon_periods: int = 13              # forecast a quarter ahead (weeks)
    history_periods_shown: int = 26        # trailing actuals returned for the chart

    # Method selection thresholds (counted in *active*, non-zero periods — zero-padding a
    # sparse account's span must not promote it to a trend model it can't support).
    min_periods_forecast: int = 3          # below this → flat level
    min_holt_periods: int = 8              # at/above → Holt's linear trend
    min_holt_active_frac: float = 0.30     # need this share of weeks non-zero to trust a trend
    seasonal_periods: int = 52             # weekly seasonal cycle
    min_seasonal_cycles: float = 2.0       # need ≥ this many cycles for Holt-Winters seasonal

    # Reliability shrinkage — blend the model path toward the recent run-rate; the weight on
    # the model falls as its backtest error rises, which curbs overforecasting on thin series.
    shrink_model_max: float = 0.85
    shrink_model_min: float = 0.35
    recent_level_periods: int = 8          # weeks averaged for the recent run-rate anchor

    # Historical-error backtest (drives the band σ and the accuracy strip).
    backtest_steps: int = 8                # expanding one-step over the last N periods
    backtest_min_train: int = 6
    mape_floor_gallons: float = 500.0      # denominator floor so a near-zero week can't blow MAPE
    rel_sigma_default: float = 0.35        # relative-error σ when a customer can't be backtested
    rel_sigma_floor: float = 0.08
    sigma_floor_gallons: float = 250.0     # absolute σ floor per customer-period

    # Band construction.
    band_z: float = 1.2816                 # P10/P90 (10th–90th) under a normal approx
    var_band_lambda: float = 0.5           # VAR weighting: σ_i ×= 1 + λ·(1 − VAR/100)
    var_default: float = 50.0              # VAR used when a customer is too thin to score
    horizon_sigma_growth: bool = True      # widen σ ∝ √h with horizon (random-walk-style)
    sigma_growth_cap_periods: int = 8      # …but the √h widening plateaus after this many weeks

    # Inventory / days-of-cover.
    cover_lookahead_periods: int = 4       # near-term avg daily burn from the first N weeks
    burndown_max_days: int = 120

    # Supply planning (order-up-to). Lead time / lot size are request inputs; these are defaults.
    default_service_level: float = 0.95
    default_lead_time_days: float = 5.0
    review_period_days: float = 7.0        # reorder review cadence (the "R" in order-up-to)
    service_level_min: float = 0.50
    service_level_max: float = 0.999

    def to_dict(self) -> dict:
        return asdict(self)

    def with_overrides(self, overrides: dict | None) -> "DemandConfig":
        if not overrides:
            return self
        known = set(self.__dataclass_fields__)  # type: ignore[attr-defined]
        return replace(self, **{k: v for k, v in overrides.items() if k in known})


DEFAULT_CONFIG = DemandConfig()


# ---- Forecasting models ---------------------------------------------------------
def _seasonal_naive(y: np.ndarray, months: np.ndarray, future_months: np.ndarray,
                    cfg: DemandConfig) -> np.ndarray:
    """Seasonally-aware naive: recent level × month-of-year factor (robust median ratios)."""
    overall = float(np.median(y)) if len(y) else 0.0
    recent = float(np.median(y[-min(len(y), 8):])) if len(y) else 0.0
    if overall <= 0:
        return np.full(len(future_months), max(0.0, recent))
    factors = {}
    for m in np.unique(months):
        vals = y[months == m]
        factors[m] = (float(np.median(vals)) / overall) if len(vals) else 1.0
    return np.array([max(0.0, recent * factors.get(m, 1.0)) for m in future_months])


def _holt_winters(y: np.ndarray, horizon: int, seasonal: bool, period: int) -> np.ndarray:
    """Fit a (damped) Holt-Winters model and forecast ``horizon`` steps (additive)."""
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # convergence / init chatter on short books
        model = ExponentialSmoothing(
            np.asarray(y, dtype=float),
            trend="add", damped_trend=True,
            seasonal="add" if seasonal else None,
            seasonal_periods=period if seasonal else None,
            initialization_method="estimated",
        ).fit()
        fc = np.asarray(model.forecast(horizon), dtype=float)
    return fc


def _forecast_method(method: str, y: np.ndarray, starts: pd.DatetimeIndex,
                     future_starts: pd.DatetimeIndex, cfg: DemandConfig) -> np.ndarray:
    """Forward path for one named method, clamped ≥ 0 (additive models can dip negative)."""
    y = np.maximum(np.asarray(y, dtype=float), 0.0)
    horizon = len(future_starts)
    months = np.array([pd.Timestamp(s).month for s in starts]) if len(y) else np.array([])
    fmonths = np.array([pd.Timestamp(s).month for s in future_starts])
    period = cfg.seasonal_periods
    try:
        if method == "holt_winters_seasonal":
            fc = _holt_winters(y, horizon, seasonal=True, period=period)
        elif method == "holt_linear":
            fc = _holt_winters(y, horizon, seasonal=False, period=period)
        elif method == "seasonal_naive":
            fc = _seasonal_naive(y, months, fmonths, cfg)
        else:  # flat
            fc = np.full(horizon, float(np.mean(y)) if len(y) else 0.0)
    except Exception:  # noqa: BLE001 — any fit failure degrades to seasonal-naive
        fc = _seasonal_naive(y, months, fmonths, cfg)
    return np.maximum(np.nan_to_num(fc, nan=0.0), 0.0)


def _feasible_methods(y: np.ndarray, cfg: DemandConfig) -> list[str]:
    """Which models the history can support (counted in *active*, non-zero weeks)."""
    n = len(y)
    n_active = int(np.count_nonzero(y))
    active_frac = (n_active / n) if n else 0.0
    trend_ok = n >= cfg.min_holt_periods and active_frac >= cfg.min_holt_active_frac
    methods = ["flat"]
    if n_active >= cfg.min_periods_forecast:
        methods.append("seasonal_naive")
    if trend_ok:
        methods.append("holt_linear")
    if trend_ok and n_active >= cfg.min_seasonal_cycles * cfg.seasonal_periods:
        methods.append("holt_winters_seasonal")
    return methods


def _backtest_method(method: str, y: np.ndarray, starts: pd.DatetimeIndex,
                     cfg: DemandConfig) -> dict:
    """Expanding one-step backtest of one method → mape / bias / rel_sigma / n.

    This is the *historical forecast error* the P10/P90 band is derived from. Relative errors
    are floored by ``mape_floor_gallons`` so a near-zero week can't dominate.
    """
    n = len(y)
    if n < cfg.backtest_min_train + 1:
        return {"mape": None, "bias": None, "rel_sigma": cfg.rel_sigma_default, "n": 0}
    start = max(cfg.backtest_min_train, n - cfg.backtest_steps)
    rel_errs: list[float] = []
    for t in range(start, n):
        pred = float(_forecast_method(method, y[:t], starts[:t], starts[t:t + 1], cfg)[0])
        denom = max(abs(float(y[t])), cfg.mape_floor_gallons)
        rel_errs.append((float(y[t]) - pred) / denom)
    if not rel_errs:
        return {"mape": None, "bias": None, "rel_sigma": cfg.rel_sigma_default, "n": 0}
    arr = np.array(rel_errs)
    return {"mape": round(float(np.mean(np.abs(arr))) * 100.0, 1),
            "bias": round(float(np.mean(arr)) * 100.0, 1),
            "rel_sigma": max(cfg.rel_sigma_floor, float(np.std(arr))), "n": len(rel_errs)}


# Richest → simplest, used to break ties / pick when a series is too short to backtest.
_METHOD_RANK = {"holt_winters_seasonal": 3, "holt_linear": 2, "seasonal_naive": 1, "flat": 0}


def select_and_forecast(y: np.ndarray, starts: pd.DatetimeIndex,
                        future_starts: pd.DatetimeIndex, cfg: DemandConfig) -> tuple[np.ndarray, str, dict]:
    """Pick the lowest-backtest-error feasible model, then forecast forward with it.

    Per-account model selection means the weather-driven distillate (highly seasonal) lands on
    seasonal-naive while a ratable gasoline account lands on Holt — automatically, by skill.
    Returns ``(p50_path, method, backtest_stats)``.
    """
    feasible = _feasible_methods(y, cfg)
    scored = [(m, _backtest_method(m, y, starts, cfg)) for m in feasible]
    backtestable = [(m, bt) for m, bt in scored if bt["mape"] is not None]
    if backtestable:
        method, bt = min(backtestable, key=lambda mb: (mb[1]["mape"], -_METHOD_RANK[mb[0]]))
    else:  # too short to backtest — take the richest feasible model
        method = max(feasible, key=lambda m: _METHOD_RANK[m])
        bt = next(bt for m, bt in scored if m == method)
    path = _forecast_method(method, y, starts, future_starts, cfg)
    return path, method, bt


# ---- Period series helpers ------------------------------------------------------
def _weekly_series(dts: pd.Series, vols: np.ndarray) -> pd.DataFrame:
    """Bucket lifts into weekly (Mon-start) sums over the active span, interior gaps = 0.

    A trailing **partial** week (the book ends mid-week, so its bucket under-counts) is dropped
    so it can't drag the forecast down or inflate the backtest error — we model from the last
    *complete* week, the standard operating convention.
    """
    dts = pd.to_datetime(dts)
    buckets = dts.dt.to_period("W").dt.start_time
    span = pd.DataFrame({"period_start": buckets, "net": vols}).groupby("period_start")["net"].sum()
    if len(span):
        full = pd.date_range(span.index.min(), span.index.max(), freq="W-MON")
        span = span.reindex(full, fill_value=0.0)
        # Drop the final week if the data doesn't reach its Saturday (clearly incomplete).
        last_start = span.index[-1]
        if len(span) > 1 and dts.max() < last_start + pd.Timedelta(days=5):
            span = span.iloc[:-1]
    return pd.DataFrame({"period_start": span.index, "actual": span.to_numpy(dtype=float)})


def _future_starts(last_start: pd.Timestamp, horizon: int) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([pd.Timestamp(last_start) + pd.Timedelta(weeks=i + 1)
                             for i in range(horizon)])


# ---- Data loading ---------------------------------------------------------------
def _load_scope(con, terminal: str | None, product: str | None) -> pd.DataFrame:
    """Lifts for a terminal (+ optional product), resolved & non-null on the core fields."""
    sql = ("SELECT customer_id, lift_datetime, net_gallons, product, terminal FROM lifts "
           "WHERE customer_id IS NOT NULL AND lift_datetime IS NOT NULL AND net_gallons IS NOT NULL")
    params: list = []
    if terminal is not None:
        sql += " AND terminal = ?"
        params.append(terminal)
    if product is not None:
        sql += " AND product = ?"
        params.append(product)
    df = con.execute(sql, params).df()
    if len(df):
        df["lift_datetime"] = pd.to_datetime(df["lift_datetime"])
        df["net_gallons"] = pd.to_numeric(df["net_gallons"], errors="coerce")
    return df


def _latest_inventory(con, terminal: str | None, product: str | None) -> dict | None:
    """Latest book inventory / capacity / heel for a terminal (+ product, else summed)."""
    if db.row_count(con, "inventory_snapshots") == 0:
        return None
    where = []
    params: list = []
    if terminal is not None:
        where.append("terminal = ?")
        params.append(terminal)
    if product is not None:
        where.append("product = ?")
        params.append(product)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    # Most-recent snapshot per (terminal, product) tank, then sum across the products in scope.
    rows = con.execute(
        f"""
        WITH latest AS (
            SELECT terminal, product, max(snapshot_datetime) AS mx
            FROM inventory_snapshots{clause} GROUP BY terminal, product
        )
        SELECT sum(s.inventory_snapshot), sum(s.tank_capacity), sum(s.min_heel),
               max(s.snapshot_datetime)
        FROM inventory_snapshots s JOIN latest l
          ON s.terminal = l.terminal AND s.product = l.product AND s.snapshot_datetime = l.mx
        """, params).fetchone()
    if rows is None or rows[0] is None:
        return None
    inv, cap, heel, when = rows
    if inv is None or cap is None:
        return None
    return {"inventory": float(inv), "capacity": float(cap),
            "min_heel": float(heel or 0.0),
            "as_of": str(pd.Timestamp(when).date()) if when is not None else None}


# ---- Per-customer + terminal forecast -------------------------------------------
def _customer_forecasts(scope: pd.DataFrame, var_by_id: dict, name_by_id: dict,
                        future_starts: pd.DatetimeIndex, cfg: DemandConfig) -> list[dict]:
    """One forecast record per customer in scope (p50 path + historical-error stats + VAR)."""
    out = []
    for cid, cl in scope.groupby("customer_id"):
        cl = cl.sort_values("lift_datetime")
        periods = _weekly_series(cl["lift_datetime"], cl["net_gallons"].to_numpy(dtype=float))
        y = periods["actual"].to_numpy(dtype=float)
        starts = pd.DatetimeIndex(periods["period_start"])
        p50, method, bt = select_and_forecast(y, starts, future_starts, cfg)
        # Reliability shrinkage: blend the model path toward the recent run-rate, trusting the
        # model less when its backtest error is high. Stabilizes thin / intermittent accounts.
        recent_level = float(y[-min(len(y), cfg.recent_level_periods):].mean()) if len(y) else 0.0
        w = max(cfg.shrink_model_min, min(cfg.shrink_model_max, 1.2 - bt["rel_sigma"]))
        p50 = np.maximum(0.0, w * p50 + (1.0 - w) * recent_level)
        out.append({
            "customer_id": cid, "name": name_by_id.get(cid, cid),
            "n_periods": len(y), "method": method,
            "p50": p50, "rel_sigma": bt["rel_sigma"], "mape": bt["mape"], "bias": bt["bias"],
            "var_score": var_by_id.get(cid), "recent_volume": recent_level,
        })
    return out


def _sigma_growth(h: int, cfg: DemandConfig) -> float:
    """Horizon σ multiplier ∝ √h (random-walk-style), plateauing after the cap."""
    if not cfg.horizon_sigma_growth:
        return 1.0
    return math.sqrt(min(h + 1, cfg.sigma_growth_cap_periods))


def _customer_sigma(mu: float, c: dict, h: int, cfg: DemandConfig, var_weight: bool = True) -> float:
    """Per-customer forecast σ for horizon step ``h`` (historical error, grown, VAR-weighted)."""
    sigma = max(mu * c["rel_sigma"], cfg.sigma_floor_gallons) * _sigma_growth(h, cfg)
    if var_weight:
        v01 = (c["var_score"] if c["var_score"] is not None else cfg.var_default) / 100.0
        sigma *= 1.0 + cfg.var_band_lambda * (1.0 - max(0.0, min(1.0, v01)))
    return sigma


def _rollup(cust_forecasts: list[dict], future_starts: pd.DatetimeIndex,
            cfg: DemandConfig) -> list[dict]:
    """VAR-weighted P10/P50/P90 rollup: sum per-customer P50, combine error variances.

    Each customer's per-period σ = max(p50·rel_sigma, floor) grown ∝√h and inflated for low
    VAR (``1 + λ·(1 − VAR/100)``). Independent customers → terminal σ = √(Σ σ_i²).
    """
    horizon = len(future_starts)
    band = []
    for h in range(horizon):
        p50 = 0.0
        var_sum = 0.0
        for c in cust_forecasts:
            mu = float(c["p50"][h]) if h < len(c["p50"]) else 0.0
            p50 += mu
            var_sum += _customer_sigma(mu, c, h, cfg) ** 2
        sigma = math.sqrt(var_sum)
        band.append({
            "period_start": str(pd.Timestamp(future_starts[h]).date()),
            "p10": round(max(0.0, p50 - cfg.band_z * sigma), 1),
            "p50": round(p50, 1),
            "p90": round(p50 + cfg.band_z * sigma, 1),
            "sigma": sigma,
        })
    return band


def _terminal_accuracy(scope: pd.DataFrame, cfg: DemandConfig) -> dict:
    """Recent MAPE / bias of the terminal forecast (backtest of the aggregate weekly series)."""
    if not len(scope):
        return {"mape": None, "bias": None, "n": 0, "by_method": {}}
    periods = _weekly_series(scope["lift_datetime"],
                             scope["net_gallons"].to_numpy(dtype=float))
    y = periods["actual"].to_numpy(dtype=float)
    starts = pd.DatetimeIndex(periods["period_start"])
    # The terminal "model" is the per-series selected model; report its backtest as the headline.
    _, method, bt = select_and_forecast(y, starts, starts[-1:], cfg)
    # Comparison strip: naive-last vs seasonal-naive vs the selected model over the same holdout.
    by_method: dict[str, list[float]] = {"naive_last": [], "seasonal_naive": [], "model": []}
    if len(y) >= cfg.backtest_min_train + 1:
        start = max(cfg.backtest_min_train, len(y) - cfg.backtest_steps)
        for t in range(start, len(y)):
            denom = max(abs(float(y[t])), cfg.mape_floor_gallons)
            naive = float(y[t - 1])
            seasonal = float(_forecast_method("seasonal_naive", y[:t], starts[:t], starts[t:t + 1], cfg)[0])
            model = float(_forecast_method(method, y[:t], starts[:t], starts[t:t + 1], cfg)[0])
            by_method["naive_last"].append(abs(y[t] - naive) / denom)
            by_method["seasonal_naive"].append(abs(y[t] - seasonal) / denom)
            by_method["model"].append(abs(y[t] - model) / denom)
    method_mape = {m: round(float(np.mean(v)) * 100.0, 1) for m, v in by_method.items() if v}
    return {"mape": bt["mape"], "bias": bt["bias"], "n": bt["n"],
            "method": method, "by_method": method_mape}


# ---- Days of cover + burn-down --------------------------------------------------
def _near_term_daily(band: list[dict], cfg: DemandConfig) -> tuple[float, float]:
    """Near-term daily demand mean & σ from the first few forecast weeks."""
    k = max(1, min(cfg.cover_lookahead_periods, len(band)))
    mu = float(np.mean([b["p50"] for b in band[:k]])) / cfg.period_days
    sig = math.sqrt(float(np.mean([b["sigma"] ** 2 for b in band[:k]]))) / cfg.period_days
    return mu, sig


def _burndown(inv: dict, mu_d: float, sigma_d: float, days_of_cover: float,
              lead_time_days: float, z: float, cfg: DemandConfig) -> dict:
    """Daily inventory burn-down vs heel & capacity, with fast (P90) / slow (P10) demand paths."""
    horizon_days = int(min(cfg.burndown_max_days,
                           max(days_of_cover * 1.4 + lead_time_days,
                               cfg.horizon_periods * cfg.period_days)))
    inv0, cap, heel = inv["inventory"], inv["capacity"], inv["min_heel"]
    series = []
    breach_day = None
    base = pd.Timestamp(inv["as_of"]) if inv.get("as_of") else pd.Timestamp.today().normalize()
    for d in range(horizon_days + 1):
        drift = mu_d * d
        spread = z * sigma_d * math.sqrt(max(d, 0))
        p50 = max(0.0, inv0 - drift)
        fast = max(0.0, inv0 - drift - spread)   # demand ran high → inventory lower
        slow = max(0.0, inv0 - drift + spread)
        series.append({"day": d, "date": str((base + pd.Timedelta(days=d)).date()),
                       "p50": round(p50, 1), "fast": round(fast, 1), "slow": round(slow, 1),
                       "heel": round(heel, 1), "capacity": round(cap, 1)})
        if breach_day is None and fast <= heel:
            breach_day = d
    return {"horizon_days": horizon_days, "breach_day": breach_day, "series": series}


# ---- Recommended action (order-up-to under a service level) ----------------------
def _z_for(service_level: float, cfg: DemandConfig) -> float:
    from scipy.stats import norm
    sl = max(cfg.service_level_min, min(cfg.service_level_max, service_level))
    return float(norm.ppf(sl))


def _round_lot(q: float, lot: float | None) -> float:
    if lot and lot > 0:
        return math.ceil(q / lot) * lot
    return q


def _recommend(inv: dict | None, band: list[dict], as_of: str | None, cfg: DemandConfig,
               service_level: float, lead_time_days: float, lot_size: float | None) -> dict:
    """Plain-English buy guidance from the demand distribution + (optional) supply constraints."""
    z = _z_for(service_level, cfg)
    sl_pct = round(max(cfg.service_level_min, min(cfg.service_level_max, service_level)) * 100)
    L = float(lead_time_days)
    R = cfg.review_period_days
    mu_d, sigma_d = _near_term_daily(band, cfg)

    # Order-up-to S and reorder point s, expressed as on-hand *above the heel*.
    cycle = L + R
    order_up_to = mu_d * cycle + z * sigma_d * math.sqrt(cycle)
    reorder_point = mu_d * L + z * sigma_d * math.sqrt(L)
    safety_stock = z * sigma_d * math.sqrt(L)

    rec = {
        "service_level": sl_pct, "lead_time_days": round(L, 1), "review_period_days": round(R, 1),
        "lot_size": lot_size, "daily_demand_p50": round(mu_d, 1), "daily_demand_sigma": round(sigma_d, 1),
        "safety_stock": round(safety_stock, 0), "reorder_point_above_heel": round(reorder_point, 0),
        "order_up_to_above_heel": round(order_up_to, 0),
        "target_cover_days": round(cycle, 1),
    }

    if inv is None:
        # No supply constraints — give a target carry and note the gap.
        rec.update({
            "mode": "target_only", "supply_gap": True,
            "target_inventory": round(order_up_to, 0),
            "days_of_cover": None, "buy_by_date": None, "buy_quantity": None,
            "headline": (
                f"No inventory / tank data for this terminal. To hold a {sl_pct}% service level "
                f"over a {L:.0f}-day lead + {R:.0f}-day review cycle, carry ≈ {_gal(order_up_to)} "
                f"above the heel (~{cycle:.0f} days of demand)."),
            "gap_note": ("Provide inventory_snapshot + tank_capacity + min_heel to unlock "
                         "days-of-cover, a burn-down, and a concrete buy-by date."),
        })
        return rec

    available = max(0.0, inv["inventory"] - inv["min_heel"])
    ullage = max(0.0, inv["capacity"] - inv["min_heel"])     # max we can hold above heel
    days_of_cover = (available / mu_d) if mu_d > 1e-9 else None
    base = pd.Timestamp(inv.get("as_of") or as_of or pd.Timestamp.today())

    if available <= reorder_point:
        days_to_reorder = 0.0
    elif mu_d > 1e-9:
        days_to_reorder = (available - reorder_point) / mu_d
    else:
        days_to_reorder = float("inf")

    if math.isinf(days_to_reorder):
        rec.update({"mode": "no_demand", "supply_gap": False,
                    "days_of_cover": None, "buy_by_date": None, "buy_quantity": None,
                    "headline": "No forward demand in scope — nothing to buy."})
        return rec

    # Quantity to bring on-hand (above heel) back up to the order-up-to level, capped at ullage.
    raw_q = max(0.0, order_up_to - (available if available <= reorder_point else reorder_point))
    q = min(_round_lot(raw_q, lot_size), ullage)
    capped = _round_lot(raw_q, lot_size) > ullage + 1e-6
    by_date = base + pd.Timedelta(days=float(days_to_reorder))

    if days_to_reorder <= 0.5:
        headline = (f"Buy ~{_gal(q)} now to hold a {sl_pct}% service level — on-hand above the "
                    f"heel ({_gal(available)}) is at/under the {L:.0f}-day reorder point "
                    f"({_gal(reorder_point)}).")
    else:
        headline = (f"Buy ~{_gal(q)} by {by_date.date()} to hold a {sl_pct}% service level "
                    f"(covers the {L:.0f}-day lead + {R:.0f}-day review). "
                    f"{_days(days_of_cover)} of cover now.")
    if capped:
        headline += f" Capped at tank ullage ({_gal(ullage)})."

    rec.update({
        "mode": "buy", "supply_gap": False,
        "inventory": round(inv["inventory"], 0), "capacity": round(inv["capacity"], 0),
        "min_heel": round(inv["min_heel"], 0), "available_above_heel": round(available, 0),
        "days_of_cover": round(days_of_cover, 1) if days_of_cover is not None else None,
        "days_to_reorder": round(days_to_reorder, 1),
        "buy_by_date": str(by_date.date()), "buy_quantity": round(q, 0),
        "quantity_capped": capped, "ullage": round(ullage, 0),
        "headline": headline,
    })
    return rec


# ---- Formatting -----------------------------------------------------------------
def _gal(x: float | None) -> str:
    if x is None:
        return "—"
    if abs(x) >= 1e6:
        return f"{x / 1e6:.2f}MM gal"
    if abs(x) >= 1e3:
        return f"{x / 1e3:.0f}k gal"
    return f"{x:,.0f} gal"


def _days(x: float | None) -> str:
    return "—" if x is None else f"{x:.0f} days"


# ---- Orchestration --------------------------------------------------------------
def _resolve_scope(con, terminal: str | None, product: str | None):
    """Resolve the terminal/product selection against what's actually in the book."""
    terminals = [r[0] for r in con.execute(
        "SELECT DISTINCT terminal FROM lifts WHERE terminal IS NOT NULL ORDER BY 1").fetchall()]
    products = [r[0] for r in con.execute(
        "SELECT DISTINCT product FROM lifts WHERE product IS NOT NULL ORDER BY 1").fetchall()]
    if terminals and (terminal is None or terminal not in terminals):
        terminal = terminals[0]
    if not terminals:
        terminal = None
    prod = product if (product and product in products) else None
    return terminal, prod, terminals, products


def forecast_terminal(con, terminal: str | None = None, product: str | None = None,
                      window: str = "all", cfg: DemandConfig | None = None, scfg=None) -> dict:
    """The heavy, **service-level-independent** half of the cockpit (cacheable per scope).

    Builds the per-customer forecasts, the VAR-weighted P10/P50/P90 terminal band, the accuracy
    strip, and (capability-gated) inventory / days-of-cover / burn-down. The recommended action
    — which depends on the live service-level / lead-time / lot-size inputs — is layered on by
    :func:`recommend`, so the slider stays snappy.
    """
    cfg = cfg or DEFAULT_CONFIG
    scfg = scfg or SCORING_DEFAULT
    if window not in WINDOWS:
        window = "all"

    caps = capabilities.compute_capabilities(con)
    enabled = {f["key"]: f["enabled"] for f in caps["features"]}
    inv_feature_on = enabled.get("inventory_days_of_supply", False)

    terminal, prod, terminals, products = _resolve_scope(con, terminal, product)
    scope = _load_scope(con, terminal, prod)

    availability = {
        "demand_forecast": {"available": bool(len(scope)),
                            "reason": "Forecast from lifts (Holt-Winters / seasonal-naive)."
                            if len(scope) else "No lifts in scope."},
        "inventory_cover": {"available": inv_feature_on,
                            "reason": "Days-of-cover & burn-down from inventory + tank_capacity + min_heel."
                            if inv_feature_on else
                            "Needs inventory_snapshot + tank_capacity + min_heel — burn-down off."},
        "supply_planning": {"available": inv_feature_on,
                            "reason": "Buy-by date from inventory vs. the demand distribution."
                            if inv_feature_on else
                            "No supply constraints — showing a target carry instead of a buy-by date."},
    }

    base = {"terminal": terminal, "terminals": terminals, "product": prod or ALL_PRODUCTS,
            "products": products, "window": window, "windows": WINDOWS,
            "grain": cfg.grain, "config": cfg.to_dict(), "availability": availability}

    if not len(scope):
        return {**base, "as_of": None, "n_customers": 0, "history": [], "forecast": [],
                "customer_forecasts": [],
                "accuracy": {"mape": None, "bias": None, "n": 0, "by_method": {}},
                "inventory": None, "days_of_cover": None, "burndown": None}

    as_of = pd.to_datetime(scope["lift_datetime"]).max()
    if window != "all":   # rolling-day windows mirror the scoring engine
        scope = scope[scope["lift_datetime"] >= as_of - pd.Timedelta(days=int(window))]

    # VAR scores (steadiness) for the band weighting come from the scoring engine.
    score_res = scoring.compute_scores(con, scfg, window)
    var_by_id = {c["customer_id"]: c["var"]["score"] for c in score_res["customers"]}
    name_by_id = {c["customer_id"]: c["name"] for c in score_res["customers"]}

    term_periods = _weekly_series(scope["lift_datetime"], scope["net_gallons"].to_numpy(dtype=float))
    future_starts = _future_starts(pd.Timestamp(term_periods["period_start"].iloc[-1]), cfg.horizon_periods)

    cust = _customer_forecasts(scope, var_by_id, name_by_id, future_starts, cfg)
    band = _rollup(cust, future_starts, cfg)
    accuracy = _terminal_accuracy(scope, cfg)

    hist = term_periods.tail(cfg.history_periods_shown)
    history = [{"period_start": str(pd.Timestamp(r.period_start).date()), "actual": round(float(r.actual), 1)}
               for r in hist.itertuples(index=False)]
    # Forecast points keep σ so :func:`recommend` can re-derive the daily demand distribution.
    forecast = [{"period_start": b["period_start"], "p10": b["p10"], "p50": b["p50"],
                 "p90": b["p90"], "sigma": round(b["sigma"], 1)} for b in band]

    # Inventory / days-of-cover / burn-down (capability-gated, service-level independent).
    inv = _latest_inventory(con, terminal, prod) if inv_feature_on else None
    mu_d, sigma_d = _near_term_daily(band, cfg)
    days_of_cover = burndown = None
    if inv is not None and mu_d > 1e-9:
        days_of_cover = round(max(0.0, inv["inventory"] - inv["min_heel"]) / mu_d, 1)
        burndown = _burndown(inv, mu_d, sigma_d, days_of_cover, cfg.default_lead_time_days,
                             cfg.band_z, cfg)

    cust_out = sorted(cust, key=lambda c: float(np.sum(c["p50"])), reverse=True)
    customer_forecasts = [{
        "customer_id": c["customer_id"], "name": c["name"], "method": c["method"],
        "n_periods": c["n_periods"], "var_score": c["var_score"],
        "mape": c["mape"], "bias": c["bias"],
        "next_p50": round(float(c["p50"][0]), 1) if len(c["p50"]) else 0.0,
        "horizon_p50": round(float(np.sum(c["p50"])), 1),
    } for c in cust_out]

    return {**base, "as_of": str(as_of.date()), "n_customers": len(cust),
            "history": history, "forecast": forecast,
            "customer_forecasts": customer_forecasts, "accuracy": accuracy,
            "inventory": inv, "days_of_cover": days_of_cover, "burndown": burndown}


def recommend(payload: dict, cfg: DemandConfig | None = None, service_level: float | None = None,
              lead_time_days: float | None = None, lot_size: float | None = None) -> dict:
    """Layer the (cheap) recommended action onto a :func:`forecast_terminal` payload."""
    cfg = cfg or DEFAULT_CONFIG
    sl = cfg.default_service_level if service_level is None else float(service_level)
    lt = cfg.default_lead_time_days if lead_time_days is None else float(lead_time_days)
    if not payload.get("forecast"):
        return None
    return _recommend(payload.get("inventory"), payload["forecast"], payload.get("as_of"),
                      cfg, sl, lt, lot_size)


def cockpit(con, terminal: str | None = None, product: str | None = None, window: str = "all",
            service_level: float | None = None, lead_time_days: float | None = None,
            lot_size: float | None = None, cfg: DemandConfig | None = None, scfg=None) -> dict:
    """Full Demand Cockpit payload for one terminal × product (forecast + recommendation)."""
    cfg = cfg or DEFAULT_CONFIG
    payload = forecast_terminal(con, terminal, product, window, cfg, scfg)
    payload["recommendation"] = recommend(payload, cfg, service_level, lead_time_days, lot_size)
    return payload


# ---- Persistence (the P6/P7/P10 contract) ---------------------------------------
def persist(con, window: str = "all", cfg: DemandConfig | None = None, scfg=None) -> dict:
    """Recompute & persist per-customer + terminal forecast distributions for every scope.

    Writes ``demand_forecast_customer`` and ``demand_forecast_terminal`` for each terminal ×
    product (plus the all-products rollup per terminal) so downstream phases read one forecast.
    """
    cfg = cfg or DEFAULT_CONFIG
    scfg = scfg or SCORING_DEFAULT
    if window not in WINDOWS:
        window = "all"
    ensure_tables(con)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    terminals = [r[0] for r in con.execute(
        "SELECT DISTINCT terminal FROM lifts WHERE terminal IS NOT NULL ORDER BY 1").fetchall()] or [None]
    products = [r[0] for r in con.execute(
        "SELECT DISTINCT product FROM lifts WHERE product IS NOT NULL ORDER BY 1").fetchall()]

    score_res = scoring.compute_scores(con, scfg, window)
    var_by_id = {c["customer_id"]: c["var"]["score"] for c in score_res["customers"]}
    name_by_id = {c["customer_id"]: c["name"] for c in score_res["customers"]}

    con.execute("DELETE FROM demand_forecast_customer WHERE score_window = ?", [window])
    con.execute("DELETE FROM demand_forecast_terminal WHERE score_window = ?", [window])

    cust_rows = term_rows = 0
    scopes: list[tuple[str | None, str | None]] = []
    for t in terminals:
        scopes.append((t, None))                       # all-products rollup
        for p in products:
            scopes.append((t, p))

    for terminal, product in scopes:
        scope = _load_scope(con, terminal, product)
        if not len(scope):
            continue
        as_of = pd.to_datetime(scope["lift_datetime"]).max()
        if window != "all":
            scope = scope[scope["lift_datetime"] >= as_of - pd.Timedelta(days=int(window))]
        if not len(scope):
            continue
        term_periods = _weekly_series(scope["lift_datetime"],
                                      scope["net_gallons"].to_numpy(dtype=float))
        future = _future_starts(pd.Timestamp(term_periods["period_start"].iloc[-1]), cfg.horizon_periods)
        cust = _customer_forecasts(scope, var_by_id, name_by_id, future, cfg)
        band = _rollup(cust, future, cfg)
        acc = _terminal_accuracy(scope, cfg)
        pkey = product if product is not None else ALL_PRODUCTS
        tname = terminal if terminal is not None else ALL_PRODUCTS

        # Per-customer distributions (band from each customer's own historical error σ).
        for c in cust:
            for h in range(len(future)):
                mu = float(c["p50"][h])
                sigma_i = _customer_sigma(mu, c, h, cfg, var_weight=False)
                con.execute(
                    "INSERT INTO demand_forecast_customer VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [c["customer_id"], c["name"], tname, pkey, window, now, cfg.grain, c["method"],
                     h, str(pd.Timestamp(future[h]).date()),
                     round(max(0.0, mu - cfg.band_z * sigma_i), 1), round(mu, 1),
                     round(mu + cfg.band_z * sigma_i, 1), c["mape"], c["bias"], c["var_score"]])
                cust_rows += 1
        # Terminal rollup distribution.
        for h, b in enumerate(band):
            con.execute(
                "INSERT INTO demand_forecast_terminal VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [tname, pkey, window, now, cfg.grain, h, b["period_start"],
                 b["p10"], b["p50"], b["p90"], round(b["p50"] / cfg.period_days, 1),
                 len(cust), acc["mape"], acc["bias"]])
            term_rows += 1

    db.set_meta(con, "demand_computed_at", now)
    return {"ok": True, "computed_at": now, "window": window,
            "terminals": [t for t in terminals if t], "products": products,
            "customer_rows": cust_rows, "terminal_rows": term_rows}


def read_forecasts(con, terminal: str | None = None, product: str | None = None,
                   level: str = "terminal", window: str | None = None) -> dict:
    """Read back the persisted forecast distributions (the P6/P7/P10 read path)."""
    ensure_tables(con)
    table = "demand_forecast_terminal" if level == "terminal" else "demand_forecast_customer"
    where: list[str] = []
    params: list = []
    for col, val in (("terminal", terminal), ("product", product), ("score_window", window)):
        if val:
            where.append(f"{col} = ?")
            params.append(val)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = con.execute(f"SELECT * FROM {table}{clause} ORDER BY terminal, product, h_index",
                       params).df()
    return {"level": level, "computed_at": db.get_meta(con, "demand_computed_at"),
            "count": int(len(rows)), "rows": rows.to_dict(orient="records")}
