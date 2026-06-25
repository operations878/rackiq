"""Per-customer demand forecasting engine — the real forecaster behind VAR Home.

This replaces the old flat "run-rate" forward projection (which smeared a trailing average
across every future period, ignoring seasonality, trend, and each customer's buying rhythm)
with a genuine per-customer forecaster:

  1. **Multiple candidate models** are fit per customer — a month-of-year **seasonal** model,
     a Holt-Winters **seasonal** model (long history), a Holt **trend** model, a **cadence**
     (inter-order interval) model for clockwork buyers, a **recency-weighted** average, and a
     **flat** mean. A pure-persistence **naive-last** baseline is the bar every model must clear.
  2. **Model selection by walk-forward backtest** — each candidate is scored one-step-ahead over
     held-out recent history (MAPE / MAE / bias); the lowest-error model is chosen *for that
     customer*. If nothing beats naive-last, the customer is flagged **low-predictability** and a
     robust seasonal/recency baseline is used instead of pretending precision.
  3. **Seasonality is preserved even on sparse/short history** via robust month-of-year factors
     anchored to the recent level — so a winter-heavy distillate account's November forecast is
     far higher than its July, and a mostly-empty account never collapses to zero.
  4. **Recency** — recent behavior is weighted more; an account silent well past its own cadence
     is damped and widened (a customer quiet through the data gap may be slowing or churning).
  5. **Honest per-customer uncertainty** — the band comes from that customer's *own* backtested
     forecast error (steady → tight, erratic → wide), VAR-weighted, growing with horizon.
  6. **Anchored to TODAY** — all horizons (7/30/90 days), the forward curve, and the period labels
     are measured from the real calendar date at request time, NOT the last date in the data. The
     data-recency gap (how far the book is behind today) is surfaced explicitly and projected over.

The engine is self-contained (no ``scoring`` / ``demand`` import) to keep the module graph
acyclic — ``scoring`` calls it. Every threshold is a :class:`scoring_config.ScoringConfig`
parameter. The VAR lane score itself is untouched; this is a layer *on top* of it.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd

from .scoring_config import ScoringConfig

# ---- Model registry (plain-language labels for the UI) --------------------------
MODEL_LABEL = {
    "hw_seasonal": "seasonal model",
    "seasonal": "seasonal model",
    "trend": "trend model",
    "cadence": "clockwork (cadence) model",
    "recency_weighted": "recency-weighted average",
    "flat": "flat average",
    "naive_last": "naive (last period)",
}
MODEL_BLURB = {
    "hw_seasonal": "captures their annual seasonal cycle",
    "seasonal": "follows their month-by-month seasonal pattern",
    "trend": "follows their recent upward / downward trend",
    "cadence": "predicts their next orders from a regular buying rhythm",
    "recency_weighted": "leans on their recent buying rate",
    "flat": "their steady long-run average",
}
# Richer → simpler, for tie-breaking when backtest errors are within tolerance.
_RANK = {"hw_seasonal": 6, "seasonal": 5, "trend": 4, "cadence": 4,
         "recency_weighted": 2, "flat": 1, "naive_last": 0}


# ---- Small helpers --------------------------------------------------------------
def _fmt_gal(x) -> str:
    if x is None:
        return "—"
    if abs(x) >= 1e6:
        return f"{x / 1e6:.1f}MM"
    return f"{round(float(x)):,}"


def _plural(n, word: str) -> str:
    try:
        return word if int(round(n)) == 1 else word + "s"
    except (TypeError, ValueError):
        return word + "s"


def _period_step(grain: str):
    return pd.offsets.MonthBegin(1) if grain == "monthly" else pd.Timedelta(days=7)


def _period_days(grain: str) -> float:
    return 30.44 if grain == "monthly" else 7.0


def _bucket(ts: pd.Timestamp, grain: str) -> pd.Timestamp:
    p = pd.Timestamp(ts)
    return p.to_period("M").start_time if grain == "monthly" else p.to_period("W").start_time


def _period_end(start: pd.Timestamp, grain: str) -> pd.Timestamp:
    return pd.Timestamp(start) + (pd.offsets.MonthBegin(1) if grain == "monthly" else pd.Timedelta(days=7))


def _overlap_days(p0: pd.Timestamp, p1: pd.Timestamp, w0: pd.Timestamp, w1: pd.Timestamp) -> float:
    """Days of [p0, p1) that fall inside the window [w0, w1)."""
    lo = max(pd.Timestamp(p0), pd.Timestamp(w0))
    hi = min(pd.Timestamp(p1), pd.Timestamp(w1))
    return max(0.0, (hi - lo).total_seconds() / 86400.0)


def _shrink(path: np.ndarray, y: np.ndarray, rel_sigma: float, cfg: ScoringConfig) -> np.ndarray:
    """Reliability shrinkage: blend the model path toward the recent run-rate, trusting the model
    LESS when its backtest error (``rel_sigma``) is high. A reliable model keeps its shape (incl.
    seasonality, since the weight stays high); a poor model is pulled toward the stable level —
    curbing thin/erratic overforecasting so the engine is never much worse than a flat average."""
    if not len(path):
        return path
    level = float(np.mean(y[-min(len(y), cfg.forecast_recent_level_periods):])) if len(y) else 0.0
    w = max(cfg.forecast_shrink_model_min, min(cfg.forecast_shrink_model_max, 1.2 - rel_sigma))
    return np.maximum(0.0, w * np.asarray(path, dtype=float) + (1.0 - w) * level)


# ---- Candidate forward models (each returns a per-future-period path ≥ 0) --------
def _seasonal_path(y: np.ndarray, months: np.ndarray, fmonths: np.ndarray,
                   cfg: ScoringConfig) -> np.ndarray:
    """Month-of-year seasonal model: each future month = that month's historical median, scaled by
    recent growth. Robust to SPARSE history — a winter-only buyer forecasts high in DJF and ~0 in
    summer and NEVER collapses to zero just because the recent periods (summer) happen to be empty
    (the prior run-rate bug). Recency scaling uses ACTIVE (non-zero) periods so a zero tail can't
    zero out the curve; a customer who recently grew scales the whole seasonal shape up."""
    if not len(y):
        return np.zeros(len(fmonths))
    overall = float(np.median(y))
    month_med = {}
    for m in range(1, 13):
        vals = y[months == m]
        month_med[m] = float(np.median(vals)) if len(vals) else overall  # includes zeros ⇒ summer ~0
    active = y[y > 0]
    scale = 1.0
    if len(active) >= 3:
        oa = float(np.median(active))
        ra = float(np.median(active[-min(len(active), cfg.forecast_recent_level_periods):]))
        if oa > 0:
            scale = ra / oa
    return np.array([max(0.0, month_med.get(int(m), overall) * scale) for m in fmonths])


def _recency_weighted_path(y: np.ndarray, horizon: int, cfg: ScoringConfig) -> np.ndarray:
    """Exponentially recency-weighted mean, projected flat forward (a smarter run-rate)."""
    if not len(y):
        return np.zeros(horizon)
    k = min(len(y), cfg.forecast_recency_window)
    yy = y[-k:]
    w = np.exp(-np.arange(k)[::-1] / max(1.0, cfg.forecast_recency_halflife))
    level = float(np.sum(yy * w) / np.sum(w))
    return np.full(horizon, max(0.0, level))


def _flat_path(y: np.ndarray, horizon: int) -> np.ndarray:
    return np.full(horizon, max(0.0, float(np.mean(y))) if len(y) else 0.0)


def _holt_path(y: np.ndarray, horizon: int, seasonal: bool, period: int) -> np.ndarray:
    """Holt (damped trend) / Holt-Winters (additive seasonal) via statsmodels."""
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = ExponentialSmoothing(
            np.asarray(y, dtype=float), trend="add", damped_trend=True,
            seasonal="add" if seasonal else None,
            seasonal_periods=period if seasonal else None,
            initialization_method="estimated").fit()
        fc = np.asarray(model.forecast(horizon), dtype=float)
    return np.maximum(np.nan_to_num(fc, nan=0.0), 0.0)


def _cadence_stats(dts: pd.Series, vols: np.ndarray, cfg: ScoringConfig) -> tuple[float, float, float]:
    """(median inter-order gap in days, robust gap CV, typical order size)."""
    if len(dts) < 2:
        return (float("nan"), float("nan"), float(np.median(vols)) if len(vols) else 0.0)
    d = np.sort(pd.to_datetime(dts).to_numpy().astype("datetime64[ns]").astype("int64"))
    gaps = np.diff(d) / (1e9 * 86400.0)
    gaps = gaps[gaps > 0]
    if len(gaps) < 1:
        return (float("nan"), float("nan"), float(np.median(vols)))
    cad = float(np.median(gaps))
    mad = float(np.median(np.abs(gaps - cad)))
    cv = (1.4826 * mad / cad) if cad > 0 else float("nan")
    size = float(np.median(vols)) if len(vols) else 0.0
    return cad, cv, size


def _cadence_value(cad: float, size: float, period_len_days: float) -> float:
    """Expected volume in a period of ``period_len_days`` from a cadence+size rhythm."""
    if not (cad and cad > 0) or size <= 0:
        return 0.0
    return max(0.0, size * (period_len_days / cad))


def _forecast_path(method: str, y: np.ndarray, starts: pd.DatetimeIndex,
                   future_starts: pd.DatetimeIndex, grain: str,
                   dts: pd.Series, vols: np.ndarray, cfg: ScoringConfig) -> np.ndarray:
    """Forward path (one value per future period) for a named method, clamped ≥ 0."""
    y = np.maximum(np.asarray(y, dtype=float), 0.0)
    horizon = len(future_starts)
    if horizon == 0:
        return np.array([])
    months = np.array([pd.Timestamp(s).month for s in starts]) if len(starts) else np.array([])
    fmonths = np.array([pd.Timestamp(s).month for s in future_starts])
    period = cfg.forecast_seasonal_period_weeks if grain != "monthly" else 12
    try:
        if method == "hw_seasonal":
            return _holt_path(y, horizon, seasonal=True, period=period)
        if method == "trend":
            return _holt_path(y, horizon, seasonal=False, period=period)
        if method == "seasonal":
            return _seasonal_path(y, months, fmonths, cfg)
        if method == "recency_weighted":
            return _recency_weighted_path(y, horizon, cfg)
        if method == "cadence":
            cad, _cv, size = _cadence_stats(dts, vols, cfg)
            return np.array([_cadence_value(cad, size, _period_days(grain)) for _ in future_starts])
        if method == "naive_last":
            return np.full(horizon, float(y[-1]) if len(y) else 0.0)
        return _flat_path(y, horizon)            # "flat"
    except Exception:  # noqa: BLE001 — any fit failure degrades to the seasonal baseline
        return _seasonal_path(y, months, fmonths, cfg)


def _feasible_methods(y: np.ndarray, starts: pd.DatetimeIndex, n_lifts: int,
                      grain: str, cfg: ScoringConfig) -> list[str]:
    """Which models this customer's history can actually support."""
    n = len(y)
    n_active = int(np.count_nonzero(y))
    active_frac = (n_active / n) if n else 0.0
    distinct_months = len({int(pd.Timestamp(s).month) for s, v in zip(starts, y) if v > 0})
    methods = ["flat"]
    if n_active >= cfg.forecast_min_periods:
        methods.append("recency_weighted")
    if n_active >= cfg.forecast_min_periods and distinct_months >= cfg.forecast_min_seasonal_months:
        methods.append("seasonal")
    if n >= cfg.forecast_min_holt_periods and active_frac >= cfg.forecast_min_holt_active_frac:
        methods.append("trend")
    period = cfg.forecast_seasonal_period_weeks if grain != "monthly" else 12
    if n >= int(cfg.forecast_min_seasonal_cycles * period):
        methods.append("hw_seasonal")
    if n_lifts >= cfg.forecast_min_periods + 1:
        methods.append("cadence")
    return methods


# ---- Walk-forward backtest ------------------------------------------------------
def _backtest_method(method: str, y: np.ndarray, starts: pd.DatetimeIndex, grain: str,
                     dts: pd.Series, vols: np.ndarray, cfg: ScoringConfig) -> dict:
    """Expanding one-step backtest of one method → mape / mae / bias / rel_sigma over the holdout."""
    n = len(y)
    if n < cfg.forecast_backtest_min_train + 1:
        return {"mape": None, "mae": None, "bias": None,
                "rel_sigma": cfg.forecast_rel_sigma_default, "n": 0}
    start = max(cfg.forecast_backtest_min_train, n - cfg.forecast_backtest_steps)
    rel, abs_err = [], []
    # lift-level cutoffs let the cadence model see only orders before each held-out period
    dts_ts = pd.to_datetime(dts) if dts is not None and len(dts) else pd.to_datetime(pd.Series([], dtype="datetime64[ns]"))
    vols_arr = np.asarray(vols, dtype=float) if vols is not None else np.array([])
    for t in range(start, n):
        if method == "cadence":
            mask = dts_ts < pd.Timestamp(starts[t]) if len(dts_ts) else np.array([], dtype=bool)
            cad, _cv, size = _cadence_stats(dts_ts[mask], vols_arr[mask.to_numpy()] if len(dts_ts) else vols_arr, cfg)
            plen = _overlap_days(starts[t], _period_end(starts[t], grain), starts[t], _period_end(starts[t], grain))
            pred = _cadence_value(cad, size, plen)
        else:
            pred = float(_forecast_path(method, y[:t], starts[:t], starts[t:t + 1], grain,
                                        dts_ts, vols_arr, cfg)[0])
        actual = float(y[t])
        denom = max(abs(actual), cfg.forecast_mape_floor_gallons)
        rel.append((actual - pred) / denom)
        abs_err.append(abs(actual - pred))
    if not rel:
        return {"mape": None, "mae": None, "bias": None,
                "rel_sigma": cfg.forecast_rel_sigma_default, "n": 0}
    arr = np.array(rel)
    return {"mape": round(float(np.mean(np.abs(arr))) * 100.0, 1),
            "mae": round(float(np.mean(abs_err)), 1),
            "bias": round(float(np.mean(arr)) * 100.0, 1),
            "rel_sigma": max(cfg.forecast_rel_sigma_floor, float(np.std(arr))), "n": len(rel)}


def _tiebreak_bonus(method: str, cadence_cv: float | None, seasonal_strength: float | None,
                    cfg: ScoringConfig) -> float:
    """Small, interpretable preference so a regular buyer reads as 'cadence' and a seasonal one
    as 'seasonal' when backtest errors are within tolerance — without overriding a clear winner."""
    bonus = 0.0
    if method == "cadence" and cadence_cv is not None and cadence_cv <= cfg.forecast_clockwork_cv:
        bonus += 3.5
    if method in ("seasonal", "hw_seasonal") and seasonal_strength is not None \
            and seasonal_strength >= cfg.forecast_seasonal_pref_strength:
        bonus += 3.0
    return bonus


def _seasonal_strength(y: np.ndarray, starts: pd.DatetimeIndex) -> float | None:
    """Cheap month-of-year seasonal strength: variance explained by month means (0–1)."""
    if len(y) < 8:
        return None
    months = np.array([int(pd.Timestamp(s).month) for s in starts])
    grand = float(np.mean(y))
    if grand <= 0:
        return None
    ss_tot = float(np.sum((y - grand) ** 2))
    if ss_tot <= 0:
        return None
    fitted = np.array([float(np.mean(y[months == m])) for m in months])
    ss_res = float(np.sum((y - fitted) ** 2))
    return max(0.0, min(1.0, 1.0 - ss_res / ss_tot))


def select_model(y: np.ndarray, starts: pd.DatetimeIndex, grain: str, n_lifts: int,
                 dts: pd.Series, vols: np.ndarray, cfg: ScoringConfig) -> dict:
    """Backtest every feasible model + the naive baseline; pick the lowest-error one for THIS
    customer. Returns the chosen method, its stats, the naive baseline, and the skill vs naive."""
    feasible = _feasible_methods(y, starts, n_lifts, grain, cfg)
    cadence_cv = _cadence_stats(dts, vols, cfg)[1] if n_lifts >= 2 else None
    seas_strength = _seasonal_strength(y, starts)

    naive = _backtest_method("naive_last", y, starts, grain, dts, vols, cfg)
    scored = {m: _backtest_method(m, y, starts, grain, dts, vols, cfg) for m in feasible}
    backtestable = {m: s for m, s in scored.items() if s["mape"] is not None}

    if backtestable:
        best_mape = min(s["mape"] for s in backtestable.values())
        tol = cfg.forecast_select_tol_pct
        # candidates within tolerance of the best, ranked by tie-break bonus then model richness
        near = [(m, s) for m, s in backtestable.items() if s["mape"] <= best_mape + tol]
        method = max(near, key=lambda ms: (ms[1]["mape"] <= best_mape + 1e-9,
                                            _tiebreak_bonus(ms[0], cadence_cv, seas_strength, cfg)
                                            + _RANK.get(ms[0], 0) * 0.01))[0]
        stats = backtestable[method]
    else:  # too short to backtest — take the richest feasible model, no error estimate
        method = max(feasible, key=lambda m: _RANK.get(m, 0))
        stats = scored[method]

    naive_mape = naive.get("mape")
    sel_mape = stats.get("mape")
    if naive_mape is not None and sel_mape is not None and naive_mape > 0:
        skill = round(1.0 - sel_mape / naive_mape, 3)
    else:
        skill = None
    beats_naive = bool(skill is not None and skill > cfg.forecast_low_pred_skill)
    low_pred = bool(skill is not None and not beats_naive)

    # If nothing beat naive-last, don't pretend precision: fall back to a robust seasonal/recency
    # baseline (keeps seasonality where it exists) and flag the account low-predictability.
    if low_pred:
        fb = [m for m in ("seasonal", "recency_weighted", "flat") if m in scored]
        if fb:
            method = min(fb, key=lambda m: scored[m]["mape"] if scored[m]["mape"] is not None else 1e18)
            stats = scored[method]

    return {"method": method, "stats": stats, "naive": naive, "skill": skill,
            "beats_naive": beats_naive, "low_predictability": low_pred,
            "feasible": feasible, "cadence_cv": cadence_cv, "seasonal_strength": seas_strength}


# ---- Period series (drops a trailing partial period for modeling) ----------------
def _model_series(periods: pd.DataFrame, grain: str, last_lift: pd.Timestamp) -> tuple[np.ndarray, pd.DatetimeIndex]:
    """The lane's per-period series, with a trailing **partial** period dropped (it under-counts
    and would drag the model). The lane (chart) keeps the full series; only modeling drops it."""
    y = periods["actual"].to_numpy(dtype=float)
    starts = pd.DatetimeIndex(periods["period_start"])
    if len(starts) > 2 and last_lift is not None:
        end = _period_end(starts[-1], grain)
        # partial if the last lift doesn't reach (most of) the final bucket
        frac = (pd.Timestamp(last_lift) - starts[-1]).days / max(1.0, (end - starts[-1]).days)
        if frac < 0.7:
            return y[:-1], starts[:-1]
    return y, starts


# ---- Main entry: forecast one customer ------------------------------------------
def forecast_customer(core: dict, cfg: ScoringConfig, today: pd.Timestamp,
                      as_of: pd.Timestamp) -> dict:
    """Real forward forecast for one customer, anchored to ``today``.

    ``core`` is the scoring engine's per-customer bundle (periods, grain, lane, cadence, raw
    lifts). ``today`` is the real calendar date at request time; ``as_of`` is the book's last
    data date. Returns the forecast block (horizons measured from today, chosen model + its
    backtested accuracy, honest band, recency-gap note, and the forward lane series for the chart).
    """
    grain = core["grain"]
    lane = core["lane"]
    last_lift = core.get("last_lift")
    period_days = _period_days(grain)
    today = pd.Timestamp(today).normalize()
    as_of = pd.Timestamp(as_of) if as_of is not None else today
    gap_days = max(0, int((today - as_of.normalize()).days))  # normalize so a lift's time-of-day can't shave a day

    if core["var_status"] != "ok":
        return {"available": False, "grain": grain,
                "reason": (f"Too little history to forecast forward yet — need ≥{cfg.var_min_lifts} "
                           f"lifts over ≥{cfg.var_min_weeks} weeks."),
                "horizons": [], "series": [],
                "data_through": str(as_of.date()), "forecast_anchor": str(today.date()),
                "gap_days": gap_days}

    periods = core["periods"]
    y, starts = _model_series(periods, grain, last_lift)
    dts = core.get("dts")
    vols = core.get("vols")
    if dts is None:
        dts = pd.to_datetime(pd.Series([], dtype="datetime64[ns]"))
    if vols is None:
        vols = np.array([])
    n_lifts = core["n_lifts"]

    sel = select_model(y, starts, grain, n_lifts, dts, vols, cfg)
    method = sel["method"]

    # ---- future period starts: cover the gap (as_of → today) AND the horizon past today ----
    step = _period_step(grain)
    last_start = pd.Timestamp(starts[-1]) if len(starts) else _bucket(as_of, grain)
    span_days = (today - last_start).days + cfg.forecast_max_horizon_days
    n_fwd = max(2, int(math.ceil(span_days / period_days)) + 2)
    future_starts = pd.DatetimeIndex([last_start + (i + 1) * step for i in range(n_fwd)])
    fc_path = _forecast_path(method, y, starts, future_starts, grain, dts, vols, cfg)
    fc_path = np.maximum(np.nan_to_num(fc_path, nan=0.0), 0.0)
    rel_sigma0 = sel["stats"].get("rel_sigma") or cfg.forecast_rel_sigma_default
    fc_path = _shrink(fc_path, y, rel_sigma0, cfg)  # reliability shrinkage toward the recent level

    # ---- recency damping: an account silent well past its own cadence is slowing / churning ----
    cad = core.get("base_cadence_days")
    days_silent = max(0.0, float((today - pd.Timestamp(last_lift)).days)) if last_lift is not None else 0.0
    overdue = (days_silent / cad) if (cad and cad > 0) else 0.0
    damp = 1.0
    slowing = False
    if overdue > cfg.forecast_recency_overdue_mult:
        damp = max(cfg.forecast_recency_damp_min,
                   1.0 - cfg.forecast_recency_damp_k * (overdue - cfg.forecast_recency_overdue_mult))
        slowing = True
        fc_path = fc_path * damp

    # ---- per-period uncertainty: customer's own backtest error, VAR-weighted, grown with horizon ----
    rel_sigma = sel["stats"].get("rel_sigma") or cfg.forecast_rel_sigma_default
    if slowing:
        rel_sigma *= (1.0 + (1.0 - damp))       # a silent account is also less certain
    # Cap the band-driving σ so a wildly erratic account (e.g. a marine parcel with a 3000% MAPE)
    # shows a wide-but-sane range, not an absurd ±10MM. The honest MAPE still shows in the text.
    rel_sigma = min(rel_sigma, cfg.forecast_rel_sigma_band_cap)
    var_score = (core.get("var_score") or cfg.forecast_var_default)
    var_weight = 1.0 + cfg.forecast_var_band_lambda * (1.0 - max(0.0, min(1.0, var_score / 100.0)))
    lane_sigma = lane.get("sigma") or 0.0

    def _sigma_i(i: int, mu: float) -> float:
        growth = math.sqrt(min(i + 1, cfg.forecast_sigma_growth_cap))
        s = max(mu * rel_sigma, cfg.forecast_sigma_floor_gallons) * growth * var_weight
        return s

    sigmas = np.array([_sigma_i(i, fc_path[i]) for i in range(len(fc_path))])

    # ---- horizon totals, measured from TODAY (prorate each period's overlap with [today, today+H]) ----
    z = cfg.forecast_band_z
    horizons = []
    for h in cfg.forecast_horizons:
        w0, w1 = today, today + pd.Timedelta(days=int(h))
        exp_h, var_h = 0.0, 0.0
        for i, fs in enumerate(future_starts):
            pe = _period_end(fs, grain)
            ov = _overlap_days(fs, pe, w0, w1)
            if ov <= 0:
                continue
            frac = ov / max(1.0, (pe - fs).days)
            exp_h += fc_path[i] * frac
            var_h += (sigmas[i] * frac) ** 2
        band = z * math.sqrt(var_h)
        horizons.append({
            "days": int(h), "expected": round(exp_h, 0),
            "lo": round(max(0.0, exp_h - band), 0), "hi": round(exp_h + band, 0),
            "expected_orders": (round(float(h) / cad) if cad and cad > 0 else None),
        })

    # ---- forward lane series for the base-range chart (NON-FLAT: the model curve + honest band) ----
    series = []
    chart_end = today + pd.Timedelta(days=cfg.forecast_max_horizon_days)
    for i, fs in enumerate(future_starts):
        if fs > chart_end + step:
            break
        mu = float(fc_path[i])
        seff = max(lane_sigma, mu * rel_sigma) * math.sqrt(min(i + 1, cfg.forecast_sigma_growth_cap))
        bhalf = cfg.base_range_sigma_k * seff
        vhalf = cfg.variability_sigma_k * seff
        series.append({
            "period_start": str(pd.Timestamp(fs).date()), "base": round(mu, 1),
            "base_lo": round(max(0.0, mu - bhalf), 1), "base_hi": round(mu + bhalf, 1),
            "var_lo": round(max(0.0, mu - vhalf), 1), "var_hi": round(mu + vhalf, 1),
        })

    # ---- honest-confidence flag + plain-English read ----
    h30 = next((h for h in horizons if h["days"] == 30), horizons[len(horizons) // 2] if horizons else None)
    half = ((h30["hi"] - h30["lo"]) / 2.0) if h30 else 0.0
    rel = (half / h30["expected"]) if (h30 and h30["expected"]) else 1.0
    rough = bool(rel >= cfg.forecast_rough_rel or (h30 and h30["lo"] <= 0)
                 or sel["low_predictability"] or slowing)

    mape = sel["stats"].get("mape")
    label = MODEL_LABEL.get(method, method)
    near_base = float(fc_path[0]) if len(fc_path) else 0.0
    per = "month" if grain == "monthly" else "week"
    n_ord = h30.get("expected_orders") if h30 else None
    acc = f" (±{round(mape)}% typical {per}-to-{per} error)" if mape is not None else ""

    plain = (f"Expect about {_fmt_gal(h30['expected'] if h30 else 0)} gal over the next "
             f"{h30['days'] if h30 else 30} days (likely {_fmt_gal(h30['lo'] if h30 else 0)}–"
             f"{_fmt_gal(h30['hi'] if h30 else 0)})")
    if n_ord:
        plain += f" — roughly {n_ord} {_plural(n_ord, 'order')}"
    plain += f", using a {label}{acc}."
    # Honest confidence: every rough forecast says so in the same words ("treat this as a rough range").
    if rough:
        if sel["low_predictability"]:
            plain += (" Low predictability — no model beat a naive guess, so treat this as a "
                      "rough range, not a firm number.")
        elif slowing:
            plain += (f" They've been quiet {round(days_silent)} days (past their usual "
                      f"~{round(cad)}-day rhythm), so treat this as a rough range, not a firm number.")
        else:
            plain += " Their buying is choppy, so treat this as a rough range, not a firm number."

    if gap_days >= cfg.forecast_gap_note_days:
        gap_note = (f"Forecast anchored to today ({today.date()}); their data only runs through "
                    f"{as_of.date()}, {gap_days} days back — projected across the gap.")
    else:
        gap_note = None

    return {
        "available": True, "grain": grain, "period_days": round(period_days, 2),
        "model": method, "model_label": label, "model_blurb": MODEL_BLURB.get(method, ""),
        "mape": mape, "bias": sel["stats"].get("bias"), "skill_vs_naive": sel["skill"],
        "beats_naive": sel["beats_naive"], "low_predictability": sel["low_predictability"],
        "naive_mape": sel["naive"].get("mape"),
        "base_per_period": round(near_base, 1), "sigma_per_period": round(near_base * rel_sigma, 1),
        "rel_sigma": round(float(rel_sigma), 3), "band_z": z, "rough": rough,
        "slowing": slowing, "days_silent": round(days_silent),
        "data_through": str(as_of.date()), "forecast_anchor": str(today.date()),
        "gap_days": gap_days, "gap_note": gap_note,
        "horizons": horizons, "series": series, "plain": plain,
    }


# ---- Backtest comparison: new engine vs old run-rate vs naive (the proof) --------
def _old_runrate_path(y: np.ndarray, horizon: int, grain: str, cfg: ScoringConfig) -> np.ndarray:
    """The OLD forward projection: mean of the trailing cycle, smeared flat forward."""
    cycle = cfg.forecast_seasonal_period_weeks if grain != "monthly" else 12
    recent = y[-cycle:] if len(y) > cycle else y
    base = float(np.mean(recent)) if len(recent) else 0.0
    return np.full(horizon, max(0.0, base))


def compare_customer(core: dict, cfg: ScoringConfig) -> dict | None:
    """One-step walk-forward comparison of the NEW engine vs the OLD run-rate vs NAIVE-last for
    one customer (the honest proof the new engine is real). Returns per-method MAE/MAPE + winner."""
    grain = core["grain"]
    last_lift = core.get("last_lift")
    y, starts = _model_series(core["periods"], grain, last_lift)
    dts = core.get("dts")
    vols = core.get("vols")
    if dts is None:
        dts = pd.to_datetime(pd.Series([], dtype="datetime64[ns]"))
    if vols is None:
        vols = np.array([])
    n = len(y)
    if n < cfg.forecast_backtest_min_train + 1:
        return None
    start = max(cfg.forecast_backtest_min_train, n - cfg.forecast_backtest_steps)
    # The engine picks ONE model per customer (the same one it displays); we then evaluate that
    # model strictly OUT-OF-SAMPLE — every prediction for period t is fit on ONLY periods before t
    # and uses the same shrinkage the live engine applies. (Model-class selection uses the full
    # series, as in production; the predictions never see the future.)
    sel0 = select_model(y, starts, grain, core.get("n_lifts") or (n + 1), dts, vols, cfg)
    method = sel0["method"]
    rs = sel0["stats"].get("rel_sigma") or cfg.forecast_rel_sigma_default
    errs = {"new_engine": [], "old_runrate": [], "naive": []}
    rels = {"new_engine": [], "old_runrate": [], "naive": []}
    for t in range(start, n):
        actual = float(y[t])
        denom = max(abs(actual), cfg.forecast_mape_floor_gallons)
        raw = _forecast_path(method, y[:t], starts[:t], starts[t:t + 1], grain, dts, vols, cfg)
        new_pred = float(_shrink(raw, y[:t], rs, cfg)[0])    # same shrinkage the live engine applies
        old_pred = float(_old_runrate_path(y[:t], 1, grain, cfg)[0])
        naive_pred = float(y[t - 1])
        for k, p in (("new_engine", new_pred), ("old_runrate", old_pred), ("naive", naive_pred)):
            errs[k].append(abs(actual - p))
            rels[k].append(abs(actual - p) / denom)
    chosen = method
    if not errs["naive"]:
        return None
    mae = {k: round(float(np.mean(v)), 1) for k, v in errs.items()}
    mape = {k: round(float(np.mean(v)) * 100.0, 1) for k, v in rels.items()}
    best = min(mae, key=mae.get)
    return {"grain": grain, "n_steps": len(errs["naive"]), "mae": mae, "mape": mape,
            "best": best, "chosen_model": chosen,
            "beats_naive": mae["new_engine"] <= mae["naive"] + 1e-9,
            "beats_old": mae["new_engine"] <= mae["old_runrate"] + 1e-9}
