"""Customer scoring engine — the VAR lane model, behavioral facts, sub-scores, base value,
and archetype classifier.

Reads the **resolved** canonical store (customer ids are already rewritten to their master id
at commit time, and names come from the crosswalk), computes every score over rolling
30/90/365-day windows plus all-time, flags data-sufficiency per customer, and **capability-gates
every metric** (each carries ``available: true/false + reason`` so the UI greys out what the
data can't support). All weights live in :class:`scoring_config.ScoringConfig`.

Layout:
  Part 1  VAR base-range (lane) model on net volume — base / base-range / variability-range,
          VAR score (volume + cadence lanes, blended), the persisted per-period series.
  Part 2  Layer-1 behavioral facts per customer.
  Part 3  Layer-2 sub-scores (percentile-ranked across the active book unless noted).
  Part 4  Layer-3 Base Value Score (EGP − friction − credit = RFAP → percentile blend).
  Part 5  Archetype classifier (primary + secondary from sub-score signatures, posture).
  Plus    Account Value, Recency gap, customer_scores persistence, and a backtest helper.

DuckDB SQL views back the straightforward facts (``v_customer_facts``); Python
(pandas/numpy/statsmodels/scipy) does STL, regressions, and percentile ranking.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import db, schema, weather
from .scoring_config import (ARCHETYPE_POSTURE, ARCHETYPES, DEFAULT_CONFIG, WINDOWS,
                             ScoringConfig, grade)

# ---- Persistence (derived caches; recomputed from canonical data) ---------------
# NOTE: `window` is a reserved word in DuckDB (window functions) — the column is `score_window`
# (the JSON/API still exposes "window"), mirroring the `at`→`ts` convention elsewhere.
SCORING_DDL = [
    """CREATE TABLE IF NOT EXISTS customer_scores (
        customer_id VARCHAR, score_window VARCHAR, computed_at VARCHAR, name VARCHAR,
        var_score DOUBLE, var_grade VARCHAR, volume_var DOUBLE, cadence_var DOUBLE,
        base_value DOUBLE, base_value_grade VARCHAR, account_value DOUBLE, recency_gap DOUBLE,
        primary_archetype VARCHAR, secondary_archetype VARCHAR, archetype_confidence DOUBLE,
        ambiguous BOOLEAN, evr DOUBLE, price_sensitivity DOUBLE, churn_risk DOUBLE,
        discount_efficiency DOUBLE, explainability DOUBLE, profitability DOUBLE, quadrant VARCHAR,
        data_sufficient BOOLEAN, total_net_gallons DOUBLE, n_lifts INTEGER, detail VARCHAR,
        PRIMARY KEY (customer_id, score_window)
    )""",
    """CREATE TABLE IF NOT EXISTS customer_lane (
        customer_id VARCHAR, score_window VARCHAR, grain VARCHAR, period_start VARCHAR,
        base DOUBLE, base_lo DOUBLE, base_hi DOUBLE, var_lo DOUBLE, var_hi DOUBLE, actual DOUBLE
    )""",
]

# SQL view of the SQL-friendly Layer-1 facts (all-time grain). Windowed facts are computed
# in pandas; this view documents the "DuckDB SQL views where possible" path.
FACTS_VIEW = """
CREATE OR REPLACE VIEW v_customer_facts AS
SELECT
    c.customer_id,
    c.name,
    c.archetype          AS archetype_true,
    c.home_terminal,
    count(l.customer_id)                               AS n_lifts,
    coalesce(sum(l.net_gallons), 0)                    AS total_net_gallons,
    avg(l.net_gallons)                                 AS order_size_mean,
    median(l.net_gallons)                              AS order_size_median,
    stddev_samp(l.net_gallons)                         AS order_size_sd,
    max(l.lift_datetime)                               AS last_lift,
    min(l.lift_datetime)                               AS first_lift,
    count(DISTINCT l.product)                          AS n_products,
    count(DISTINCT date_trunc('month', l.lift_datetime)) AS active_months
FROM customers c
LEFT JOIN lifts l USING (customer_id)
GROUP BY 1, 2, 3, 4
"""


def ensure_tables(con) -> None:
    for ddl in SCORING_DDL:
        con.execute(ddl)
    try:
        con.execute(FACTS_VIEW)
    except Exception:  # noqa: BLE001 — empty store before any lifts table is fine
        pass


# ---- Tiny stats helpers ---------------------------------------------------------
def _robust_sigma(x: np.ndarray) -> float:
    """MAD-based robust σ (1.4826·MAD); falls back to std for degenerate input."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 2:
        return 0.0
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med)))
    s = 1.4826 * mad
    if s <= 0:
        s = float(np.std(x))
    return s


def _pct_rank(values: dict) -> dict:
    """Percentile rank (0–100) of each non-null value across the book; None stays None."""
    from scipy.stats import rankdata

    items = [(k, v) for k, v in values.items()
             if v is not None and not (isinstance(v, float) and math.isnan(v))]
    out = {k: None for k in values}
    if not items:
        return out
    ks = [k for k, _ in items]
    vs = np.array([float(v) for _, v in items])
    if len(vs) == 1:
        out[ks[0]] = 50.0
        return out
    r = rankdata(vs, method="average")
    pct = (r - 0.5) / len(vs) * 100.0
    for k, p in zip(ks, pct):
        out[k] = round(float(p), 1)
    return out


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _ols(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float]:
    """Plain OLS via lstsq; returns (coef incl. intercept, SSE)."""
    A = np.column_stack([np.ones(len(y)), X])
    coef, _res, _rank, _sv = np.linalg.lstsq(A, y, rcond=None)
    pred = A @ coef
    sse = float(np.sum((y - pred) ** 2))
    return coef, sse


def _hdd_cdd(dt: pd.Timestamp) -> tuple[float, float]:
    """Climatological degree-days from a date (seasonal proxy; live fetch lives in ``weather``)."""
    return weather.seasonal_hdd_cdd(dt)


# ---- Data loading ---------------------------------------------------------------
def _load(con) -> dict:
    lifts = con.execute(
        "SELECT customer_id, lift_datetime, net_gallons, product, terminal, unit_price, unit_cost "
        "FROM lifts WHERE customer_id IS NOT NULL AND lift_datetime IS NOT NULL "
        "AND net_gallons IS NOT NULL").df()
    customers = con.execute(
        "SELECT customer_id, name, archetype, home_terminal FROM customers").df()
    invoices = con.execute(
        "SELECT customer_id, invoice_date, due_date, paid_date, invoice_amount, credit_limit "
        "FROM invoices").df() if db.row_count(con, schema.INVOICES) else pd.DataFrame()
    market = con.execute(
        "SELECT price_date, product, terminal, market_price, rack_benchmark, street_rack "
        "FROM market_prices").df() if db.row_count(con, schema.MARKET) else pd.DataFrame()
    quotes = con.execute(
        "SELECT customer_id, quote_time, product, quoted_price, market_price_at_quote, outcome, "
        "final_gallons, time_to_decision FROM quotes").df() if db.row_count(con, schema.QUOTES) else pd.DataFrame()

    if len(lifts):
        lifts["lift_datetime"] = pd.to_datetime(lifts["lift_datetime"])
        lifts["net_gallons"] = pd.to_numeric(lifts["net_gallons"], errors="coerce")
    as_of = pd.to_datetime(lifts["lift_datetime"]).max() if len(lifts) else None

    has_margin = bool(len(lifts) and lifts["unit_price"].notna().any() and lifts["unit_cost"].notna().any())
    has_market = bool(len(market) and market["market_price"].notna().any())
    has_rack = bool(len(market) and market["rack_benchmark"].notna().any())
    has_quotes = bool(len(quotes))
    has_ar = bool(len(invoices) and invoices["invoice_date"].notna().any())

    return {
        "lifts": lifts, "customers": customers, "invoices": invoices, "market": market,
        "quotes": quotes, "as_of": as_of,
        "has_margin": has_margin, "has_market": has_market, "has_rack": has_rack,
        "has_quotes": has_quotes, "has_ar": has_ar,
    }


def _availability(data: dict) -> dict:
    """Per-metric capability gate: available true/false + a reason the UI shows when greyed."""
    a = {}

    def g(key, ok, ok_reason, no_reason):
        a[key] = {"available": bool(ok), "reason": ok_reason if ok else no_reason}

    g("var", True, "From lifts (volume + cadence).", "")
    g("margin", data["has_margin"], "unit_price & unit_cost present.",
      "No unit_price/unit_cost — margin metrics off.")
    g("price_elasticity", data["has_quotes"] or (data["has_rack"] and data["has_margin"]),
      "From the quote log / rack benchmark." if data["has_quotes"] else "From lifts vs rack benchmark.",
      "Needs a quote log or rack_benchmark + unit_price — collecting.")
    g("evr", data["has_market"], "Demand model vs naive seasonal.",
      "Needs market prices for the demand model.")
    g("discount_efficiency", data["has_margin"] and (data["has_quotes"] or data["has_rack"]),
      "From margin + estimated volume elasticity.",
      "Needs margin + elasticity (quotes / rack benchmark).")
    g("market_sensitivity", data["has_market"], "Corr(volume, market level/momentum/vol).",
      "Needs market prices.")
    g("weather_sensitivity", True,
      "Seasonal HDD/CDD proxy (live NOAA/ERA5 fetch powers the lane-break weather).", "")
    g("quote_score", data["has_quotes"], "From the quote log.",
      "Needs a quote log — collecting.")
    g("churn_risk", True, "Recency + volume trend (+ accept decline if quoted).", "")
    g("base_value", True, "EGP − friction − credit.", "")
    g("credit", data["has_ar"], "From AR (days-to-pay, exposure).",
      "No AR — credit cost treated as zero.")
    return a


# ---- Part 1: VAR base-range (lane) model ----------------------------------------
def _period_grain(gaps: np.ndarray, cfg: ScoringConfig) -> str:
    if len(gaps) == 0:
        return "weekly"
    return "monthly" if float(np.median(gaps)) > cfg.monthly_gap_threshold_days else "weekly"


def _bucket(ts: pd.Series, grain: str) -> pd.Series:
    if grain == "monthly":
        return ts.dt.to_period("M").dt.start_time
    return ts.dt.to_period("W").dt.start_time


def _seasonal_fitted(y: np.ndarray, months: np.ndarray) -> np.ndarray:
    """Seasonally-aware robust fallback: overall median × month-of-year factor."""
    overall = float(np.median(y)) if len(y) else 0.0
    if overall <= 0:
        return np.full(len(y), overall)
    factors = {}
    for m in np.unique(months):
        vals = y[months == m]
        factors[m] = (float(np.median(vals)) / overall) if len(vals) else 1.0
    return np.array([overall * factors.get(m, 1.0) for m in months])


def _stl_or_seasonal(y: np.ndarray, starts: pd.DatetimeIndex, grain: str,
                     fast: bool = False) -> tuple[np.ndarray, str]:
    period = 52 if grain == "weekly" else 12
    if not fast and len(y) >= 2 * period and period >= 2:
        try:
            from statsmodels.tsa.seasonal import STL
            res = STL(pd.Series(y), period=period, robust=True).fit()
            return (res.trend + res.seasonal).to_numpy(), "stl"
        except Exception:  # noqa: BLE001
            pass
    months = np.array([s.month for s in starts])
    return _seasonal_fitted(y, months), "seasonal_median"


def _lane(periods: pd.DataFrame, cfg: ScoringConfig, grain: str,
          with_diagnostics: bool = True) -> dict:
    """Fit the base / base-range / variability-range lane on a customer's per-period volume.

    ``with_diagnostics=False`` skips the heavy statistics layer (bootstrap CI, STL strengths,
    Ljung-Box, …) and the STL fit itself — used by the cheap point-in-time VAR-trend re-fits.
    """
    y = periods["actual"].to_numpy(dtype=float)
    starts = pd.DatetimeIndex(periods["period_start"])
    n = len(y)
    fitted, method = _stl_or_seasonal(y, starts, grain, fast=not with_diagnostics)
    resid = y - fitted
    sigma = _robust_sigma(resid)
    base_level = float(fitted[-1]) if n else 0.0

    if cfg.base_range_mode == "percent":
        half = cfg.base_range_pct * np.abs(fitted)
    else:
        half = cfg.base_range_sigma_k * sigma
    base_lo = fitted - half
    base_hi = fitted + half
    var_half = cfg.variability_sigma_k * sigma
    var_lo = fitted - var_half
    var_hi = fitted + var_half

    in_band = float(np.mean((y >= base_lo) & (y <= base_hi))) if n else 0.0
    scale = float(np.median(fitted)) if np.median(fitted) > 0 else (float(np.mean(np.abs(fitted))) or 1.0)
    tightness = _clamp(1.0 - sigma / scale) if scale else 0.0
    excursion = float(np.mean((y < var_lo) | (y > var_hi))) if n else 0.0
    score = 100.0 * (cfg.var_w_in_band * in_band + cfg.var_w_tightness * tightness
                     + cfg.var_w_excursion * (1.0 - excursion))

    series = [{
        "period_start": str(pd.Timestamp(s).date()),
        "base": round(float(f), 1), "base_lo": round(float(max(0.0, bl)), 1),
        "base_hi": round(float(bh), 1), "var_lo": round(float(max(0.0, vl)), 1),
        "var_hi": round(float(vh), 1), "actual": round(float(a), 1),
    } for s, f, bl, bh, vl, vh, a in zip(starts, fitted, base_lo, base_hi, var_lo, var_hi, y)]

    diagnostics = (_lane_diagnostics(y, fitted, resid, starts, grain, sigma, cfg)
                   if with_diagnostics else None)
    steadiness = (_steadiness_trend(y, base_lo, base_hi, cfg)
                  if with_diagnostics else None)

    return {
        "base_level": round(base_level, 1), "sigma": round(sigma, 1),
        "in_band_rate": round(in_band, 3), "tightness": round(tightness, 3),
        "excursion_penalty": round(excursion, 3), "score": round(score, 1),
        "method": method, "n_periods": n, "series": series,
        "diagnostics": diagnostics, "steadiness": steadiness,
    }


def _cadence_lane(gaps: np.ndarray, cfg: ScoringConfig) -> dict:
    """Cadence lane: base cadence (typical days between lifts) with its own band + sub-score."""
    if len(gaps) < 2:
        return {"base_cadence_days": float(np.median(gaps)) if len(gaps) else None,
                "score": None, "in_band_rate": None, "tightness": None,
                "excursion_penalty": None, "sigma": None, "cv": None}
    base = float(np.median(gaps))
    sigma = _robust_sigma(gaps)
    lo, hi = base - cfg.base_range_sigma_k * sigma, base + cfg.base_range_sigma_k * sigma
    vlo, vhi = base - cfg.variability_sigma_k * sigma, base + cfg.variability_sigma_k * sigma
    in_band = float(np.mean((gaps >= lo) & (gaps <= hi)))
    tightness = _clamp(1.0 - sigma / base) if base else 0.0
    excursion = float(np.mean((gaps < vlo) | (gaps > vhi)))
    score = 100.0 * (cfg.var_w_in_band * in_band + cfg.var_w_tightness * tightness
                     + cfg.var_w_excursion * (1.0 - excursion))
    return {"base_cadence_days": round(base, 2), "score": round(score, 1),
            "in_band_rate": round(in_band, 3), "tightness": round(tightness, 3),
            "excursion_penalty": round(excursion, 3), "sigma": round(sigma, 2),
            "cv": round(sigma / base, 3) if base else None}


# ---- Part 1b: advanced VAR statistics (diagnostics — these NEVER change the score) ----
# A transparency/statistics layer on top of the (frozen) VAR lane: it explains *why* a
# customer is predictable and how confident we are, without touching the headline number.
def _safe(fn, default=None):
    """Run a stat that may fail on degenerate input; never let it break scoring."""
    try:
        v = fn()
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            return default
        return v
    except Exception:  # noqa: BLE001
        return default


def _spectral_entropy_forecastability(y: np.ndarray) -> float | None:
    """Forecastability (0–100) = 1 − normalized spectral entropy of the demand series.

    A series whose power concentrates at a few frequencies (regular cycles) is highly
    forecastable; white-noise demand spreads power evenly → entropy max → forecastability 0.
    """
    n = len(y)
    if n < 6:
        return None
    yy = y - y.mean()
    if not np.any(yy):
        return 100.0  # perfectly flat ⇒ perfectly forecastable
    spec = np.abs(np.fft.rfft(yy)) ** 2
    spec = spec[1:]  # drop the DC (mean) component
    s = float(spec.sum())
    if s <= 0 or len(spec) < 2:
        return None
    p = spec / s
    p = p[p > 0]
    H = float(-np.sum(p * np.log(p)))
    Hmax = math.log(len(spec))
    return round(_clamp(1.0 - H / Hmax) * 100.0, 1) if Hmax > 0 else None


def _one_step_skill(y: np.ndarray, starts, grain: str) -> dict | None:
    """Predictability via a one-step skill score: 1 − MAE(seasonal lane) / MAE(naive-last)."""
    n = len(y)
    if n < 6:
        return None
    e_model, e_naive = [], []
    for t in range(3, n):
        fit, _ = _stl_or_seasonal(y[:t], starts[:t], grain)
        pred = fit[-1] if len(fit) else float(np.median(y[:t]))
        e_model.append(abs(y[t] - pred))
        e_naive.append(abs(y[t] - y[t - 1]))
    if not e_naive:
        return None
    mae_model, mae_naive = float(np.mean(e_model)), float(np.mean(e_naive))
    if mae_naive <= 0:
        return None
    skill = 1.0 - mae_model / mae_naive
    return {"mae_model": round(mae_model, 1), "mae_naive": round(mae_naive, 1),
            "skill_vs_naive": round(skill, 3), "predictability": round(_clamp(skill) * 100.0, 1)}


def _mann_kendall(y: np.ndarray, p_sig: float) -> dict | None:
    """Non-parametric Mann–Kendall trend test (Kendall's τ) — robust to outliers & non-normal."""
    if len(y) < 6:
        return None
    from scipy.stats import kendalltau
    tau, p = kendalltau(np.arange(len(y)), y)
    if tau is None or math.isnan(tau):
        return None
    direction = "flat"
    if p < p_sig:
        direction = "rising" if tau > 0 else "falling"
    return {"tau": round(float(tau), 3), "p_value": round(float(p), 4),
            "significant": bool(p < p_sig), "direction": direction}


def _residual_diagnostics(resid: np.ndarray) -> dict:
    """Lag-1 autocorrelation + Ljung–Box white-noise test on the lane residuals.

    White-noise residuals (high p) mean the lane has captured the structure — what's left is
    irreducible noise; significant autocorrelation means there's pattern the lane misses.
    """
    n = len(resid)
    out = {"acf1": None, "ljung_box_p": None, "white_noise": None}
    if n < 5:
        return out
    r = resid - resid.mean()
    denom = float(np.sum(r * r))
    if denom > 0:
        out["acf1"] = round(float(np.sum(r[1:] * r[:-1]) / denom), 3)

    def _lb():
        from statsmodels.stats.diagnostic import acorr_ljungbox
        lags = max(1, min(10, n // 2))
        res = acorr_ljungbox(resid, lags=[lags], return_df=True)
        return float(res["lb_pvalue"].iloc[-1])

    p = _safe(_lb)
    if p is not None:
        out["ljung_box_p"] = round(p, 4)
        out["white_noise"] = bool(p > 0.05)
    return out


def _bootstrap_base_ci(fitted: np.ndarray, resid: np.ndarray, cfg: ScoringConfig) -> dict | None:
    """Residual bootstrap of the expected base volume → a confidence band on the lane line."""
    n = len(resid)
    if n < 5:
        return None
    base = float(fitted[-1])
    rc = resid - resid.mean()
    rng = np.random.default_rng(20240601 + n)  # deterministic per series length
    samples = base + rng.choice(rc, size=(int(cfg.var_bootstrap_iters), n), replace=True).mean(axis=1)
    lo_q = (1 - cfg.var_bootstrap_ci) / 2 * 100.0
    hi_q = (1 + cfg.var_bootstrap_ci) / 2 * 100.0
    lo, hi = np.percentile(samples, [lo_q, hi_q])
    return {"base": round(base, 1), "lo": round(float(max(0.0, lo)), 1), "hi": round(float(hi), 1),
            "se": round(float(samples.std()), 1), "ci": cfg.var_bootstrap_ci}


def _stl_strengths(y: np.ndarray, grain: str) -> dict | None:
    """Hyndman STL feature strengths: trend strength & seasonal strength in [0, 1]."""
    period = 52 if grain == "weekly" else 12
    if len(y) < 2 * period or period < 2:
        return None

    def _calc():
        from statsmodels.tsa.seasonal import STL
        res = STL(pd.Series(y), period=period, robust=True).fit()
        trend, seasonal, remainder = res.trend.to_numpy(), res.seasonal.to_numpy(), res.resid.to_numpy()
        var_r = float(np.var(remainder))
        ts = max(0.0, 1.0 - var_r / max(1e-9, float(np.var(remainder + trend))))
        ss = max(0.0, 1.0 - var_r / max(1e-9, float(np.var(remainder + seasonal))))
        return {"trend_strength": round(ts, 3), "seasonal_strength": round(ss, 3)}

    return _safe(_calc)


def _steadiness_trend(y: np.ndarray, base_lo: np.ndarray, base_hi: np.ndarray,
                      cfg: ScoringConfig) -> dict:
    """Is the customer getting MORE or LESS steady? Two-proportion z-test on the in-band rate
    of the recent half of the history vs the prior half (same lane)."""
    n = len(y)
    in_mask = ((y >= base_lo) & (y <= base_hi)).astype(float)
    if n < cfg.var_steadiness_min_periods:
        return {"direction": "insufficient", "delta": None, "in_band_recent": None,
                "in_band_prior": None, "z": None, "p_value": None, "significant": False}
    half = n // 2
    prior, recent = in_mask[:half], in_mask[half:]
    p1, p2 = float(prior.mean()), float(recent.mean())
    delta = p2 - p1
    n1, n2 = len(prior), len(recent)
    pool = (float(prior.sum()) + float(recent.sum())) / (n1 + n2)
    se = math.sqrt(pool * (1 - pool) * (1 / n1 + 1 / n2)) if 0 < pool < 1 else 0.0
    z = delta / se if se > 0 else 0.0
    p_value = None
    if se > 0:
        from scipy.stats import norm
        p_value = round(float(2 * norm.sf(abs(z))), 4)
    direction = ("improving" if delta >= cfg.var_steadiness_delta_band
                 else "deteriorating" if delta <= -cfg.var_steadiness_delta_band else "steady")
    return {"direction": direction, "delta": round(delta, 3), "in_band_recent": round(p2, 3),
            "in_band_prior": round(p1, 3), "z": round(float(z), 2), "p_value": p_value,
            "significant": bool(p_value is not None and p_value < cfg.var_trend_sig_p)}


def _lane_diagnostics(y: np.ndarray, fitted: np.ndarray, resid: np.ndarray,
                      starts, grain: str, sigma: float, cfg: ScoringConfig) -> dict:
    """Bundle the advanced statistics for one customer's volume lane (all guarded)."""
    n = len(y)
    r2 = (_safe(lambda: round(float(1 - np.sum(resid ** 2) / np.sum((y - y.mean()) ** 2)), 3))
          if n >= 3 and np.var(y) > 0 else None)
    cv = _safe(lambda: round(float(sigma / abs(np.mean(y))), 3)) if n and np.mean(y) else None
    return {
        "n_periods": n,
        "r2": r2,
        "coef_variation": cv,
        "robust_sigma": round(float(sigma), 1),
        "forecastability": _spectral_entropy_forecastability(y),
        "skill": _one_step_skill(y, starts, grain),
        "trend_test": _mann_kendall(y, cfg.var_trend_sig_p),
        "residuals": _residual_diagnostics(resid),
        "base_ci": _bootstrap_base_ci(fitted, resid, cfg),
        "stl": _stl_strengths(y, grain),
        "n_outliers_3sigma": int(np.sum(np.abs(resid) > 3 * sigma)) if sigma > 0 else 0,
    }


# ---- Per-customer computation ---------------------------------------------------
def _customer_core(cl: pd.DataFrame, cfg: ScoringConfig, as_of: pd.Timestamp,
                   light: bool = False) -> dict:
    """VAR lane + cadence + raw volume/timing facts for one customer's (windowed) lifts.

    ``light=True`` skips the expensive diagnostics layer — used by the point-in-time VAR-trend
    re-fits where only the headline score is needed.
    """
    cl = cl.sort_values("lift_datetime")
    dts = pd.to_datetime(cl["lift_datetime"])
    vols = cl["net_gallons"].to_numpy(dtype=float)
    n = len(cl)
    gaps = (np.diff(dts.to_numpy().astype("datetime64[ns]").astype("int64")) / (1e9 * 86400.0)
            if n >= 2 else np.array([]))
    grain = _period_grain(gaps, cfg)

    # per-period series over the active span (interior zeros = missed periods → excursions)
    buckets = _bucket(dts, grain)
    span = pd.DataFrame({"period_start": buckets, "net": vols}).groupby("period_start")["net"].sum()
    if len(span):
        freq = "MS" if grain == "monthly" else "W-MON"
        full_idx = pd.date_range(span.index.min(), span.index.max(), freq=freq)
        span = span.reindex(full_idx, fill_value=0.0)
    periods = pd.DataFrame({"period_start": span.index, "actual": span.to_numpy()})
    n_weeks = len(periods) if grain == "weekly" else len(periods) * 4

    lane = _lane(periods, cfg, grain, with_diagnostics=not light) if len(periods) >= 2 else {
        "base_level": float(np.median(vols)) if n else 0.0, "sigma": 0.0, "in_band_rate": None,
        "tightness": None, "excursion_penalty": None, "score": None, "method": "insufficient",
        "n_periods": len(periods), "series": []}
    cadence = _cadence_lane(gaps, cfg)

    sufficient_var = n >= cfg.var_min_lifts and n_weeks >= cfg.var_min_weeks
    vol_var = lane["score"] if sufficient_var else None
    cad_var = cadence["score"] if sufficient_var else None
    if vol_var is not None and cad_var is not None:
        headline = cfg.var_blend_volume * vol_var + cfg.var_blend_cadence * cad_var
    else:
        headline = vol_var
    var_status = "ok" if sufficient_var else "insufficient_history"

    last_lift = dts.max()
    days_since = float((as_of - last_lift).days) if pd.notna(last_lift) else None
    # recent-vs-prior trend over the periods
    a = periods["actual"].to_numpy()
    if len(a) >= 6:
        third = max(1, len(a) // 3)
        prior, recent = a[:third].mean(), a[-third:].mean()
        trend_pct = float((recent - prior) / prior * 100.0) if prior > 0 else 0.0
    else:
        trend_pct = 0.0

    return {
        "grain": grain, "n_lifts": n, "n_weeks": round(n_weeks, 1),
        "vols": vols, "gaps": gaps, "periods": periods, "lane": lane, "cadence": cadence,
        "var_score": round(headline, 1) if headline is not None else None,
        "var_grade": grade(headline, cfg) if headline is not None else None,
        "volume_var": vol_var, "cadence_var": cad_var, "var_status": var_status,
        "days_since_last": days_since, "trend_pct": round(trend_pct, 1),
        "base_cadence_days": cadence.get("base_cadence_days"),
        "last_lift": last_lift,
    }


# ---- Part 1c: VAR as a FORECAST — forward projection from the lane ---------------
# The lane describes past behavior; this turns it into a forward expectation. Expected volume
# over the next H days = base_per_period · (H / period_days); the confidence band widens with the
# lane width (independent-period √-aggregation), so a tight lane (high VAR) projects narrow and
# a wide lane (low VAR) projects wide. The VAR score itself is untouched.
def _forward_projection(core: dict, cfg: ScoringConfig) -> dict:
    lane = core["lane"]
    grain = core["grain"]
    series = lane.get("series") or []
    sigma = lane.get("sigma") or 0.0
    cad = core.get("base_cadence_days")
    period_days = 30.44 if grain == "monthly" else 7.0
    # Project from the trailing RUN-RATE (their normal pace) rather than the seasonal endpoint:
    # `fitted[-1]` collapses to ~0 for sparse/erratic accounts whose recent periods are empty,
    # which would (wrongly) drop them from the forecast. The run-rate is robust and ≥0 for anyone
    # who buys, so erratic customers still get a (wide-banded) forward number.
    actuals = np.array([p["actual"] for p in series], dtype=float)
    cycle = 52 if grain != "monthly" else 12
    recent = actuals[-cycle:] if len(actuals) > cycle else actuals
    base = float(np.mean(recent)) if len(recent) else 0.0
    if core["var_status"] != "ok" or base <= 0 or not series:
        return {"available": False, "grain": grain,
                "reason": (f"Need a fitted lane (≥{cfg.var_min_lifts} lifts over "
                           f"≥{cfg.var_min_weeks} weeks) to project forward."),
                "horizons": [], "series": []}

    z = cfg.forecast_band_z
    horizons = []
    for h in cfg.forecast_horizons:
        k = float(h) / period_days
        expected = base * k
        sigma_h = sigma * math.sqrt(k)
        horizons.append({
            "days": int(h), "expected": round(expected, 0),
            "lo": round(max(0.0, expected - z * sigma_h), 0), "hi": round(expected + z * sigma_h, 0),
            "expected_orders": (round(float(h) / cad) if cad and cad > 0 else None),
        })

    # dotted forward continuation of the lane (flat at base, same ±1σ / ±2σ bands)
    if cfg.base_range_mode == "percent":
        bhalf = cfg.base_range_pct * abs(base)
        vhalf = (cfg.variability_sigma_k / max(cfg.base_range_sigma_k, 1e-9)) * bhalf
    else:
        bhalf, vhalf = cfg.base_range_sigma_k * sigma, cfg.variability_sigma_k * sigma
    step = pd.offsets.MonthBegin(1) if grain == "monthly" else pd.Timedelta(days=7)
    n_fwd = max(1, math.ceil(cfg.forecast_max_horizon_days / period_days))
    cur = pd.Timestamp(lane["series"][-1]["period_start"])
    series = []
    for _ in range(n_fwd):
        cur = cur + step
        series.append({
            "period_start": str(pd.Timestamp(cur).date()), "base": round(base, 1),
            "base_lo": round(max(0.0, base - bhalf), 1), "base_hi": round(base + bhalf, 1),
            "var_lo": round(max(0.0, base - vhalf), 1), "var_hi": round(base + vhalf, 1),
        })

    h30 = next((h for h in horizons if h["days"] == 30), horizons[len(horizons) // 2])
    per = "month" if grain == "monthly" else "week"
    plain = (f"Expect about {_fmt_gal(h30['expected'])} gal over the next {h30['days']} days "
             f"(likely {_fmt_gal(h30['lo'])}–{_fmt_gal(h30['hi'])})")
    if h30.get("expected_orders"):
        plain += f" — roughly {h30['expected_orders']} order(s)"
    plain += f", if they hold their pattern of ~{_fmt_gal(base)} gal/{per}."
    return {"available": True, "grain": grain, "period_days": round(period_days, 2),
            "base_per_period": round(base, 1), "sigma_per_period": round(sigma, 1),
            "band_z": z, "horizons": horizons, "series": series, "plain": plain}


# ---- Part 1d: excursion explanation (lane breaks + weather pattern) --------------
# When an actual lift lands outside the variability range, tag it (spike / shortfall / no-show)
# and attach the weather that period. A pattern across the breaks separates a predictable-looking-
# erratic account (cold-snap buyer) from a truly random one.
def _excursion_pattern(breaks: list, cfg: ScoringConfig) -> dict:
    n = len(breaks)
    if n == 0:
        return {"type": "none", "n_breaks": 0,
                "note": "No lane breaks in this window — they've stayed inside their normal range."}
    cold = sum(1 for b in breaks if b["cold_snap"])
    hot = sum(1 for b in breaks if b["hot_spell"])
    if n < cfg.excursion_min_breaks:
        return {"type": "too_few", "n_breaks": n, "n_cold_snap": cold, "n_hot_spell": hot,
                "note": f"Only {n} lane break(s) so far — not enough to read a weather pattern."}
    if cold / n >= cfg.excursion_pattern_share:
        spikes = sum(1 for b in breaks if b["cold_snap"] and b["kind"] == "spike")
        extra = " (mostly buying spikes)" if spikes >= max(1, cold // 2) else ""
        return {"type": "cold_snap", "n_breaks": n, "n_cold_snap": cold, "n_hot_spell": hot,
                "note": f"{cold} of {n} lane breaks landed on cold-snap weeks{extra} — "
                        "looks weather-driven, not random."}
    if hot / n >= cfg.excursion_pattern_share:
        return {"type": "hot_spell", "n_breaks": n, "n_cold_snap": cold, "n_hot_spell": hot,
                "note": f"{hot} of {n} lane breaks landed on hot spells — looks weather-driven, not random."}
    return {"type": "random", "n_breaks": n, "n_cold_snap": cold, "n_hot_spell": hot,
            "note": (f"{n} lane breaks with no clear weather tie ({cold} on cold snaps, "
                     f"{hot} on hot spells) — looks like genuine noise, not weather.")}


def _excursions(core: dict, terminal: str | None, con, cfg: ScoringConfig, live: bool = False) -> dict:
    """Lane breaks + weather pattern. ``live=False`` (the bulk path) uses the free seasonal proxy
    for speed; the per-customer detail re-runs with ``live=True`` to auto-fetch real NOAA/ERA5
    degree-days for just that terminal (cached thereafter)."""
    lane = core["lane"]
    series = lane.get("series") or []
    if core["var_status"] != "ok" or not series:
        return {"available": False, "n_breaks": 0, "breaks": [], "pattern": None, "weather_source": None}
    starts = [p["period_start"] for p in series]
    grain = core["grain"]
    try:
        wx = weather.period_series(con, terminal, starts, grain, allow_fetch=live)
    except Exception:  # noqa: BLE001 — never let weather break scoring
        wx = {}
    hdds = [wx[s]["hdd"] for s in starts if wx.get(s) and wx[s].get("hdd") is not None]
    cdds = [wx[s]["cdd"] for s in starts if wx.get(s) and wx[s].get("cdd") is not None]
    hdd_thr = float(np.quantile(hdds, cfg.weather_snap_quantile)) if len(hdds) >= 4 else None
    cdd_thr = float(np.quantile(cdds, cfg.weather_snap_quantile)) if len(cdds) >= 4 else None
    base = lane.get("base_level") or 0.0

    breaks = []
    for p in series:
        a, vlo, vhi = p["actual"], p["var_lo"], p["var_hi"]
        if vlo <= a <= vhi:
            continue
        w = wx.get(p["period_start"]) or {}
        hdd, cdd = w.get("hdd"), w.get("cdd")
        kind = "spike" if a > vhi else ("no_show" if a <= 0 else "shortfall")
        breaks.append({
            "period_start": p["period_start"], "kind": kind,
            "actual": round(float(a), 0), "expected": round(base, 0),
            "delta_pct": (round((a - base) / base * 100, 0) if base else None),
            "var_range": [round(float(vlo), 0), round(float(vhi), 0)],
            "hdd": hdd, "cdd": cdd,
            "cold_snap": bool(hdd is not None and hdd_thr is not None and hdd >= hdd_thr and hdd > 0),
            "hot_spell": bool(cdd is not None and cdd_thr is not None and cdd >= cdd_thr and cdd > 0),
            "weather_source": w.get("source"),
        })
    breaks.sort(key=lambda b: b["period_start"], reverse=True)
    src = None
    if wx:
        src = "open-meteo" if any(v.get("source") == "open-meteo" for v in wx.values()) else "climatology"
    return {"available": True, "n_breaks": len(breaks), "breaks": breaks,
            "pattern": _excursion_pattern(breaks, cfg), "weather_source": src}


def customer_excursions(con, customer: dict, cfg: ScoringConfig | None = None) -> dict:
    """Recompute one customer's lane breaks with LIVE weather (the per-customer detail path).

    Rebuilds the minimal lane context from a (cached) scored-customer record so the detail
    endpoint can auto-fetch real degree-days for just that terminal without re-deriving scores.
    """
    cfg = cfg or DEFAULT_CONFIG
    v = customer.get("var") or {}
    pseudo = {"lane": {"series": customer.get("lane_series") or [], "base_level": v.get("base_level") or 0.0},
              "var_status": v.get("status", "insufficient"), "grain": customer.get("grain", "weekly")}
    terminal = customer.get("weather_terminal") or customer.get("home_terminal")
    return _excursions(pseudo, terminal, con, cfg, live=True)


# ---- Part 1e: VAR trend over time (lane tightening vs widening) ------------------
# Re-fit the (cheap, diagnostics-free) lane at an earlier as-of and compare the VAR score:
# is the lane getting tighter (more reliable) or wider (a developing problem)?
def _trend_comp(now_s, now_g, prior_s, prior_g, label: str, cfg: ScoringConfig) -> dict:
    if now_s is None or prior_s is None:
        return {"direction": "insufficient", "delta": None, "score_now": now_s, "score_prior": prior_s,
                "grade_now": now_g, "grade_prior": prior_g,
                "note": f"Not enough history to compare this {label} vs the prior one."}
    delta = round(now_s - prior_s, 1)
    direction = ("tightening" if delta >= cfg.var_trend_move_band
                 else "widening" if delta <= -cfg.var_trend_move_band else "steady")
    verb = {"tightening": "tightening — more reliable",
            "widening": "widening — becoming harder to plan",
            "steady": "holding steady"}[direction]
    gp = f" ({prior_g}→{now_g})" if (prior_g and now_g and prior_g != now_g) else ""
    return {"direction": direction, "delta": delta, "score_now": now_s, "score_prior": prior_s,
            "grade_now": now_g, "grade_prior": prior_g,
            "note": f"Lane {verb}: VAR {round(prior_s)}→{round(now_s)}{gp} over the last {label}."}


def _var_trend(full_cl: pd.DataFrame, cfg: ScoringConfig, as_of: pd.Timestamp) -> dict:
    if as_of is None or not len(full_cl):
        return {"available": False, "comparisons": {}}
    look = pd.Timedelta(days=cfg.var_trend_lookback_days)

    def score_at(point):
        sub = full_cl[(full_cl["lift_datetime"] <= point) & (full_cl["lift_datetime"] >= point - look)]
        if len(sub) < cfg.var_min_lifts:
            return None, None
        c = _customer_core(sub, cfg, point, light=True)
        return c["var_score"], c["var_grade"]

    now_s, now_g = score_at(as_of)
    out = {"available": now_s is not None, "score_now": now_s, "grade_now": now_g,
           "lookback_days": cfg.var_trend_lookback_days, "comparisons": {}}
    for label, shift in (("month", cfg.var_trend_month_days), ("quarter", cfg.var_trend_quarter_days)):
        prior_s, prior_g = score_at(as_of - pd.Timedelta(days=shift))
        out["comparisons"][label] = _trend_comp(now_s, now_g, prior_s, prior_g, label, cfg)
    return out


# ---- Book-level bottom-up forecast (sum the per-customer lanes) ------------------
def _filter_share(c: dict, terminal: str | None, product: str | None) -> float:
    """A customer's share of volume matching the (terminal, product) filter (1.0 if no filter)."""
    if not terminal and not product:
        return 1.0
    tp = ((c.get("facts") or {}).get("tp_share")) or {}
    s = 0.0
    for key, val in tp.items():
        t, _, p = key.partition("|")
        if terminal and t != terminal:
            continue
        if product and p != product:
            continue
        s += float(val)
    return s


def aggregate_book_forecast(customers: list, cfg: ScoringConfig,
                            terminal: str | None = None, product: str | None = None) -> dict:
    """Sum every customer's forward projection into a total expected-demand band for the book.

    Optionally filtered by terminal / product (via each customer's volume mix). Variances add
    independently → band = z·√Σσ². Also returns the A/B-vs-C/D volume split (the forecastability
    headline) with its quarter-over-quarter trend.
    """
    horizons = [int(h) for h in cfg.forecast_horizons]
    agg = {h: {"expected": 0.0, "var": 0.0} for h in horizons}
    ref_h = 30 if 30 in agg else horizons[len(horizons) // 2]
    grade_vol = {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0}
    prior_vol = {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0}
    n = 0
    for c in customers:
        fc = c.get("forecast") or {}
        if not fc.get("available"):
            continue
        share = _filter_share(c, terminal, product)
        if share <= 0:
            continue
        n += 1
        hmap = {h["days"]: h for h in fc.get("horizons", [])}
        for h in horizons:
            row = hmap.get(h)
            if not row:
                continue
            exp = (row["expected"] or 0.0) * share
            sig_h = ((row["hi"] - row["expected"]) / cfg.forecast_band_z) if cfg.forecast_band_z else 0.0
            agg[h]["expected"] += exp
            agg[h]["var"] += (sig_h * share) ** 2
        ref = hmap.get(ref_h)
        ref_exp = (ref["expected"] or 0.0) * share if ref else 0.0
        g = (c.get("var") or {}).get("grade")
        if g in grade_vol:
            grade_vol[g] += ref_exp
        pg = (((c.get("var_trend") or {}).get("comparisons") or {}).get("quarter") or {}).get("grade_prior")
        if pg in prior_vol:
            prior_vol[pg] += ref_exp

    out_h = []
    for h in horizons:
        e = agg[h]["expected"]
        band = cfg.forecast_band_z * math.sqrt(agg[h]["var"])
        out_h.append({"days": h, "expected": round(e, 0),
                      "lo": round(max(0.0, e - band), 0), "hi": round(e + band, 0)})

    ab = grade_vol["A"] + grade_vol["B"]
    cd = grade_vol["C"] + grade_vol["D"]
    tot = ab + cd
    pred_share = (ab / tot) if tot else None
    prior_ab = prior_vol["A"] + prior_vol["B"]
    prior_tot = prior_ab + prior_vol["C"] + prior_vol["D"]
    prior_share = (prior_ab / prior_tot) if prior_tot else None
    return {
        "horizons": out_h, "ref_horizon_days": ref_h, "n_customers": n,
        "grade_volume": {k: round(v, 0) for k, v in grade_vol.items()},
        "predictable_volume": round(ab, 0), "erratic_volume": round(cd, 0),
        "predictable_share": round(pred_share, 3) if pred_share is not None else None,
        "predictable_share_prior": round(prior_share, 3) if prior_share is not None else None,
        "predictable_share_delta": (round(pred_share - prior_share, 3)
                                    if (pred_share is not None and prior_share is not None) else None),
    }


def _facts(cl: pd.DataFrame, core: dict, inv: pd.DataFrame, q: pd.DataFrame,
           cfg: ScoringConfig, data: dict) -> dict:
    """Part 2 — Layer-1 behavioral facts for one customer."""
    vols = core["vols"]
    gaps = core["gaps"]
    n = core["n_lifts"]
    months_active = max(1.0, (cl["lift_datetime"].max() - cl["lift_datetime"].min()).days / 30.44)

    def cv(x):
        x = np.asarray(x, float)
        m = x.mean() if len(x) else 0.0
        return float(x.std() / m) if m else None

    # product mix + concentration (HHI)
    mix = cl.groupby("product")["net_gallons"].sum()
    total = float(mix.sum()) or 1.0
    mix_share = {str(k): round(float(v) / total, 3) for k, v in mix.items()}
    hhi = float(sum((v / total) ** 2 for v in mix.values)) if len(mix) else 1.0

    # joint terminal×product mix (powers the book-forecast terminal/product filters)
    net_total = float(cl["net_gallons"].sum()) or 1.0
    tcol = cl["terminal"].fillna("(unknown)") if "terminal" in cl else pd.Series("(unknown)", index=cl.index)
    pcol = cl["product"].fillna("(unknown)") if "product" in cl else pd.Series("(unknown)", index=cl.index)
    tp = cl.groupby([tcol, pcol])["net_gallons"].sum()
    tp_share = {f"{t}|{p}": round(float(v) / net_total, 4) for (t, p), v in tp.items()}
    term_share = {str(t): round(float(v) / net_total, 4)
                  for t, v in cl.groupby(tcol)["net_gallons"].sum().items()}

    # margin facts
    margin_mean = margin_cv = None
    if data["has_margin"] and {"unit_price", "unit_cost"} <= set(cl.columns):
        mg = (cl["unit_price"] - cl["unit_cost"]).dropna()
        if len(mg):
            margin_mean = round(float(mg.mean()), 4)
            margin_cv = cv(mg.to_numpy())

    # friction proxies
    small_order_rate = float(np.mean(vols < cfg.small_order_gallons)) if n else 0.0
    rush_rate = float(np.mean(gaps < cfg.rush_gap_days)) if len(gaps) else 0.0
    day = cl.assign(d=cl["lift_datetime"].dt.date).groupby("d").size()
    split_rate = float(np.mean(day.to_numpy() > 1)) if len(day) else 0.0
    cancel_rate = None
    if len(q):
        out = q["outcome"].astype(str).str.lower()
        cancel_rate = float(np.mean(out.isin(["reject", "no_response"])))
    friction_tags = sum(1 for r in (small_order_rate, rush_rate, split_rate,
                                    cancel_rate or 0.0) if r and r >= 0.10)

    # AR facts
    terms_days = days_to_pay_mean = days_to_pay_cv = credit_util = late_rate = None
    if data["has_ar"] and len(inv):
        iv = inv.copy()
        for c in ("invoice_date", "due_date", "paid_date"):
            if c in iv.columns:
                iv[c] = pd.to_datetime(iv[c], errors="coerce")
        if iv["due_date"].notna().any() and iv["invoice_date"].notna().any():
            terms_days = round(float((iv["due_date"] - iv["invoice_date"]).dt.days.dropna().mean()), 1)
        paid = iv.dropna(subset=["paid_date", "invoice_date"])
        if len(paid):
            dtp = (paid["paid_date"] - paid["invoice_date"]).dt.days
            days_to_pay_mean = round(float(dtp.mean()), 1)
            days_to_pay_cv = cv(dtp.to_numpy())
            if "due_date" in paid.columns and paid["due_date"].notna().any():
                late_rate = round(float(((paid["paid_date"] - paid["due_date"]).dt.days > 0).mean()), 3)
        if "credit_limit" in iv.columns and iv["credit_limit"].notna().any():
            limit = float(iv["credit_limit"].dropna().median())
            open_bal = float(iv[iv["paid_date"].isna()]["invoice_amount"].sum())
            credit_util = round(open_bal / limit, 3) if limit else None

    return {
        "order_size_mean": round(float(vols.mean()), 1) if n else None,
        "order_size_median": round(float(np.median(vols)), 1) if n else None,
        "order_size_cv": round(cv(vols), 3) if cv(vols) is not None else None,
        "monthly_volume": round(float(vols.sum()) / months_active, 1) if n else 0.0,
        "order_frequency_per_month": round(n / months_active, 2),
        "days_between_mean": round(float(gaps.mean()), 2) if len(gaps) else None,
        "days_between_cv": round(cv(gaps), 3) if cv(gaps) is not None else None,
        "gross_margin_per_gal_mean": margin_mean,
        "gross_margin_per_gal_cv": round(margin_cv, 3) if margin_cv is not None else None,
        "days_since_last_order": core["days_since_last"],
        "product_mix": mix_share, "product_concentration_hhi": round(hhi, 3),
        "tp_share": tp_share, "terminal_mix": term_share,
        "small_order_rate": round(small_order_rate, 3), "rush_rate": round(rush_rate, 3),
        "split_rate": round(split_rate, 3),
        "cancel_rate": round(cancel_rate, 3) if cancel_rate is not None else None,
        "friction_tag_count": int(friction_tags),
        "payment_terms_days": terms_days, "days_to_pay_mean": days_to_pay_mean,
        "days_to_pay_cv": round(days_to_pay_cv, 3) if days_to_pay_cv is not None else None,
        "credit_utilization": credit_util, "late_rate": late_rate,
    }


def _raw_subscore_inputs(cl: pd.DataFrame, core: dict, q: pd.DataFrame, market: pd.DataFrame,
                         cfg: ScoringConfig, data: dict) -> dict:
    """Per-customer raw inputs for Layer-2 sub-scores (turned into 0–100 in a 2nd pass)."""
    out = {"beta_incidence": None, "beta_volume": None, "evr": None,
           "weather_beta": None, "market_corr": None, "market_profile": None,
           "premium_capture": None, "quote_raw": None, "accept_rate": None}
    periods = core["periods"]
    a = periods["actual"].to_numpy()
    starts = pd.DatetimeIndex(periods["period_start"])

    # ---- price elasticity β (prefer quotes: accept incidence vs quoted−reference) ----
    if len(q) >= 6 and q["market_price_at_quote"].notna().any():
        qq = q.dropna(subset=["quoted_price", "market_price_at_quote"]).copy()
        if len(qq) >= 6:
            spread = (qq["quoted_price"] - qq["market_price_at_quote"]).to_numpy(float)
            acc = (qq["outcome"].astype(str).str.lower() == "accept").astype(float).to_numpy()
            if spread.std() > 1e-6 and 0 < acc.mean() < 1:
                out["beta_incidence"] = float(np.polyfit(spread, acc, 1)[0])
            won = qq[qq["outcome"].astype(str).str.lower() == "accept"]
            if len(won) >= 6 and "final_gallons" in won and won["final_gallons"].notna().sum() >= 6:
                fg = won.dropna(subset=["final_gallons"])
                sp = (fg["quoted_price"] - fg["market_price_at_quote"]).to_numpy(float)
                vg = fg["final_gallons"].to_numpy(float)
                if sp.std() > 1e-6:
                    out["beta_volume"] = float(np.polyfit(sp, vg, 1)[0])
            out["accept_rate"] = float(acc.mean())

    # market level/momentum per period for this customer's dominant product
    mlevel = mmom = None
    if data["has_market"] and len(market):
        prod = cl["product"].mode().iloc[0] if cl["product"].notna().any() else None
        mk = market[market["product"] == prod] if prod is not None else market
        if len(mk):
            mk = mk.copy()
            mk["price_date"] = pd.to_datetime(mk["price_date"])
            mser = mk.groupby(mk["price_date"].dt.to_period("W").dt.start_time)["market_price"].mean()
            mlevel = mser.reindex(starts).to_numpy(float)
            mmom = np.concatenate([[0.0], np.diff(np.nan_to_num(mlevel))])

    # fallback elasticity from lifts vs rack benchmark (price spread vs incidence/volume)
    if out["beta_incidence"] is None and data["has_rack"] and data["has_margin"] and len(periods) >= 8:
        prod = cl["product"].mode().iloc[0] if cl["product"].notna().any() else None
        rk = market[market["product"] == prod] if (prod is not None and len(market)) else market
        if len(rk):
            rk = rk.copy(); rk["price_date"] = pd.to_datetime(rk["price_date"])
            ref = rk.groupby(rk["price_date"].dt.to_period("W").dt.start_time)["rack_benchmark"].mean()
            our = cl.assign(w=pd.to_datetime(cl["lift_datetime"]).dt.to_period("W").dt.start_time) \
                    .groupby("w")["unit_price"].mean()
            spread = (our.reindex(starts) - ref.reindex(starts)).to_numpy(float)
            inc = (a > 0).astype(float)
            mask = ~np.isnan(spread)
            if mask.sum() >= 6 and np.nanstd(spread[mask]) > 1e-6 and 0 < inc[mask].mean() < 1:
                out["beta_incidence"] = float(np.polyfit(spread[mask], inc[mask], 1)[0])
                if a[mask].std() > 0:
                    out["beta_volume"] = float(np.polyfit(spread[mask], a[mask], 1)[0])

    # ---- weather β (HDD) ----
    if len(periods) >= 8:
        hdd = np.array([_hdd_cdd(pd.Timestamp(s))[0] for s in starts])
        cdd = np.array([_hdd_cdd(pd.Timestamp(s))[1] for s in starts])
        if hdd.std() > 1e-6:
            out["weather_beta"] = float(np.polyfit(hdd, a, 1)[0])

        # ---- market sensitivity (signed corr to level, momentum, volatility) ----
        if mlevel is not None and np.nanstd(mlevel) > 1e-9:
            valid = ~np.isnan(mlevel)
            if valid.sum() >= 6 and a[valid].std() > 0:
                lvl_c = float(np.corrcoef(a[valid], mlevel[valid])[0, 1])
                mom_c = float(np.corrcoef(a[valid], mmom[valid])[0, 1]) if np.std(mmom[valid]) > 0 else 0.0
                vol = pd.Series(mlevel).rolling(4, min_periods=2).std().to_numpy()
                vol_c = float(np.corrcoef(a[valid], np.nan_to_num(vol[valid]))[0, 1]) if np.nanstd(vol[valid]) > 0 else 0.0
                out["market_corr"] = lvl_c
                out["market_profile"] = {"level": round(lvl_c, 3), "momentum": round(mom_c, 3),
                                         "volatility": round(vol_c, 3)}

        # ---- EVR: 1 − SSE(demand model)/SSE(naive seasonal) ----
        # naive seasonal = pure-calendar model (sin/cos); demand model layers the exogenous
        # drivers (HDD/CDD, market level & momentum, our price spread) on top. Because the
        # demand model is a strict superset, EVR ≥ 0 and measures how much of the variability
        # the *drivers* explain beyond the calendar — the useful-vs-dangerous separator.
        if data["has_market"] or (data["has_rack"] and data["has_margin"]):
            sinx = np.array([math.sin(2 * math.pi * s.month / 12) for s in starts])
            cosx = np.array([math.cos(2 * math.pi * s.month / 12) for s in starts])
            drivers = [hdd, cdd]
            if mlevel is not None:
                drivers += [np.nan_to_num(mlevel), np.nan_to_num(mmom)]
            # our price spread vs rack benchmark (a real demand driver)
            if data["has_rack"] and data["has_margin"] and len(market):
                prod = cl["product"].mode().iloc[0] if cl["product"].notna().any() else None
                rk = market[market["product"] == prod] if prod is not None else market
                if len(rk):
                    rk = rk.copy(); rk["price_date"] = pd.to_datetime(rk["price_date"])
                    ref = rk.groupby(rk["price_date"].dt.to_period("W").dt.start_time)["rack_benchmark"].mean()
                    our = cl.assign(w=pd.to_datetime(cl["lift_datetime"]).dt.to_period("W").dt.start_time) \
                            .groupby("w")["unit_price"].mean()
                    sp = (our.reindex(starts) - ref.reindex(starts)).to_numpy(float)
                    sp_mean = float(np.nanmean(sp)) if np.any(~np.isnan(sp)) else 0.0
                    drivers.append(np.nan_to_num(sp, nan=sp_mean))
            Xb = np.column_stack([sinx, cosx])
            Xb = Xb[:, Xb.std(axis=0) > 1e-9]
            Xf = np.column_stack([sinx, cosx, *drivers])
            Xf = Xf[:, Xf.std(axis=0) > 1e-9]
            if Xb.shape[1] >= 1 and Xf.shape[1] > Xb.shape[1]:
                _cb, sse_naive = _ols(Xb, a)
                _cf, sse_model = _ols(Xf, a)
                if sse_naive > 1e-9:
                    out["evr"] = _clamp(1.0 - sse_model / sse_naive) * 100.0

    # ---- premium capture (our price vs reference) ----
    if data["has_margin"] and data["has_rack"] and len(market):
        prod = cl["product"].mode().iloc[0] if cl["product"].notna().any() else None
        rk = market[market["product"] == prod] if prod is not None else market
        if len(rk) and cl["unit_price"].notna().any():
            ref = float(pd.to_numeric(rk["rack_benchmark"], errors="coerce").dropna().mean())
            our = float(cl["unit_price"].dropna().mean())
            if ref:
                out["premium_capture"] = (our - ref) / ref

    # ---- quote raw score (accept / negotiate / latency / lowest-only) ----
    if len(q):
        out_l = q["outcome"].astype(str).str.lower()
        accept_rate = float((out_l == "accept").mean())
        # negotiate proxy: back-and-forth = share of multi-quote days
        qd = q.assign(d=pd.to_datetime(q["quote_time"]).dt.date).groupby("d").size()
        negotiate = float(np.mean(qd.to_numpy() > 1)) if len(qd) else 0.0
        lat = pd.to_numeric(q["time_to_decision"], errors="coerce").dropna()
        latency = float(lat.mean()) if len(lat) else cfg.quote_latency_norm_min
        latency_score = _clamp(1.0 - latency / cfg.quote_latency_norm_min)
        # lowest-only: rejects when priced above reference (sensitive to being undercut)
        rej = q[out_l == "reject"]
        lowest_only = 0.0
        if len(rej) and rej["market_price_at_quote"].notna().any():
            above = (rej["quoted_price"] - rej["market_price_at_quote"]) > 0
            lowest_only = float(above.mean())
        out["quote_raw"] = 100.0 * (
            cfg.quote_w_accept * accept_rate + cfg.quote_w_negotiate * (1 - negotiate)
            + cfg.quote_w_latency * latency_score + cfg.quote_w_lowest_only * (1 - lowest_only))
    return out


# ---- Part 5: archetype classifier ----------------------------------------------
def _norm100(v):
    return _clamp((v or 0.0) / 100.0) if v is not None else 0.0


def _archetype_scores(s: dict) -> dict:
    """Signature scores (0–1) for each archetype from normalized sub-scores."""
    vol = _norm100(s.get("volume_steadiness"))
    tim = _norm100(s.get("timing_steadiness"))
    price = _norm100(s.get("price_sensitivity"))
    evr = _norm100(s.get("evr"))
    weather = _norm100(s.get("weather_sensitivity"))
    market = _norm100(s.get("market_sensitivity"))
    disc = _norm100(s.get("discount_efficiency"))
    churn = _norm100(s.get("churn_risk"))
    bval = _norm100(s.get("base_value"))
    prof = _norm100(s.get("profitability"))
    credit = _clamp(s.get("credit_pressure", 0.0))
    opex = _clamp(s.get("opex_pressure", 0.0))
    recency = _clamp(s.get("recency_norm", 0.0))

    steady = 0.5 * vol + 0.5 * tim
    not_driven = 1 - max(weather, price)            # steady *regardless* of weather/price
    return {
        # steady, valuable, and not weather/price-whipped → your floor
        "Anchor Base-Load": 0.32 * vol + 0.22 * tim + 0.20 * bval + 0.16 * not_driven + 0.10 * (1 - churn),
        # erratic timing, moderate price reaction, demand explainable
        "Flex Buyer": 0.38 * (1 - tim) + 0.24 * (1 - vol) + 0.22 * (1 - price) + 0.16 * evr,
        "Premium Spot": 0.45 * prof + 0.25 * (1 - price) + 0.15 * (1 - vol) + 0.15 * (1 - evr),
        "Price Shopper": 0.60 * price + 0.22 * (1 - vol) + 0.18 * (1 - prof),
        "Surplus Absorber": 0.42 * price + 0.28 * (1 - tim) + 0.30 * market,
        "Scarcity Buyer": 0.42 * market + 0.30 * (1 - tim) + 0.28 * (1 - vol),
        "Weather-Triggered": 0.70 * weather + 0.18 * (1 - price) + 0.12 * evr,
        "Credit Drag": 0.72 * credit + 0.28 * (1 - prof),
        "Operationally Expensive": 0.72 * opex + 0.28 * (1 - prof),
        "Strategic Platform": 0.40 * bval + 0.32 * evr + 0.28 * vol,
        "Backup-Only": 0.52 * recency + 0.28 * (1 - vol) + 0.20 * (1 - tim),
        # steady but with growth headroom (not yet an anchor) → lock with a contract
        "Contract Candidate": 0.34 * steady + 0.26 * (1 - bval) + 0.22 * not_driven + 0.18 * (1 - churn),
    }


def _classify(s: dict, cfg: ScoringConfig) -> dict:
    scores = _archetype_scores(s)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    primary, p1 = ranked[0]
    secondary, p2 = ranked[1]
    gap = p1 - p2
    confidence = round(_clamp(p1) * (0.5 + 0.5 * _clamp(gap / 0.3)), 3)
    return {
        "primary": primary, "secondary": secondary,
        "confidence": confidence, "ambiguous": bool(gap < cfg.archetype_ambiguous_gap),
        "posture": ARCHETYPE_POSTURE.get(primary, {}),
        "scores": {k: round(float(v), 3) for k, v in ranked},
    }


# ---- Orchestration --------------------------------------------------------------
def _window_cutoff(as_of: pd.Timestamp, window: str):
    if window == "all" or as_of is None:
        return None
    return as_of - pd.Timedelta(days=int(window))


def compute_scores(con, cfg: ScoringConfig | None = None, window: str = "all") -> dict:
    """Compute the full score payload for one window (capability-gated, percentile-ranked)."""
    cfg = cfg or DEFAULT_CONFIG
    if window not in WINDOWS:
        window = "all"
    data = _load(con)
    avail = _availability(data)
    lifts, customers, as_of = data["lifts"], data["customers"], data["as_of"]

    if not len(lifts) or as_of is None:
        return {"window": window, "as_of": None, "availability": avail,
                "config": cfg.to_dict(), "n_customers": 0, "customers": []}

    cutoff = _window_cutoff(as_of, window)
    lw = lifts if cutoff is None else lifts[lifts["lift_datetime"] >= cutoff]
    name_by_id = dict(zip(customers["customer_id"], customers["name"])) if len(customers) else {}
    arche_by_id = dict(zip(customers["customer_id"], customers["archetype"])) if len(customers) else {}
    home_by_id = dict(zip(customers["customer_id"], customers["home_terminal"])) if len(customers) else {}

    inv_by_id = (data["invoices"].groupby("customer_id") if len(data["invoices"]) else None)
    q_by_id = (data["quotes"].groupby("customer_id") if len(data["quotes"]) else None)
    full_by_id = lifts.groupby("customer_id")  # full history (for the window-independent VAR trend)

    # ---- pass 1: per-customer cores, facts, raw sub-score inputs ----
    rows = {}
    for cid, cl in lw.groupby("customer_id"):
        if not len(cl):
            continue
        core = _customer_core(cl, cfg, as_of)
        inv = inv_by_id.get_group(cid) if (inv_by_id is not None and cid in inv_by_id.groups) else pd.DataFrame()
        q = q_by_id.get_group(cid) if (q_by_id is not None and cid in q_by_id.groups) else pd.DataFrame()
        facts = _facts(cl, core, inv, q, cfg, data)
        raw = _raw_subscore_inputs(cl, core, q, data["market"], cfg, data)
        sufficient = core["n_lifts"] >= cfg.suff_min_lifts and core["n_weeks"] >= (cfg.suff_min_days / 7.0)
        # VAR as a forecast: forward projection, lane-break (excursion) weather, and VAR-over-time trend
        term = home_by_id.get(cid)
        if not term and "terminal" in cl and cl["terminal"].notna().any():
            term = cl["terminal"].mode().iloc[0]
        forward = _forward_projection(core, cfg)
        excursions = _excursions(core, term, con, cfg)
        full_cl = full_by_id.get_group(cid) if cid in full_by_id.groups else cl
        var_trend = _var_trend(full_cl, cfg, as_of)
        rows[cid] = {"core": core, "facts": facts, "raw": raw, "inv": inv, "q": q,
                     "data_sufficient": sufficient, "forward": forward,
                     "excursions": excursions, "var_trend": var_trend, "terminal": term}

    if not rows:
        return {"window": window, "as_of": str(as_of.date()), "availability": avail,
                "config": cfg.to_dict(), "n_customers": 0, "customers": []}

    # ---- book-level percentile inputs ----
    def collect(path):
        return {cid: path(r) for cid, r in rows.items()}

    abs_beta = collect(lambda r: abs(r["raw"]["beta_incidence"]) if r["raw"]["beta_incidence"] is not None else None)
    weather_abs = collect(lambda r: abs(r["raw"]["weather_beta"]) if r["raw"]["weather_beta"] is not None else None)
    market_abs = collect(lambda r: abs(r["raw"]["market_corr"]) if r["raw"]["market_corr"] is not None else None)
    premium = collect(lambda r: r["raw"]["premium_capture"])

    # discount efficiency (needs β_volume + margin)
    disc_eff = {}
    for cid, r in rows.items():
        bv = r["raw"]["beta_volume"]
        mgn = r["facts"]["gross_margin_per_gal_mean"]
        base_vol = r["core"]["lane"]["base_level"]
        if bv is not None and mgn is not None and base_vol:
            extra = -bv * cfg.discount_delta            # β_volume is negative for a price cut
            gp_given_up = base_vol * cfg.discount_delta
            inc_gp = extra * max(0.0, mgn - cfg.discount_delta)
            disc_eff[cid] = (inc_gp / gp_given_up) if gp_given_up > 0 else None
        else:
            disc_eff[cid] = None

    pr_beta = _pct_rank(abs_beta)
    pr_weather = _pct_rank(weather_abs)
    pr_market = _pct_rank(market_abs)
    pr_premium = _pct_rank(premium)

    # churn risk
    churn = {}
    for cid, r in rows.items():
        core = r["core"]
        cad = core["base_cadence_days"] or 7.0
        recency_norm = _clamp((core["days_since_last"] or 0.0) / (cfg.churn_recency_cadence_mult * cad))
        neg_trend = _clamp(-min(0.0, core["trend_pct"]) / 100.0)
        accept_decl = 0.0
        q = r["q"]
        if len(q) >= 8 and "quote_time" in q:
            qs = q.sort_values("quote_time")
            half = len(qs) // 2
            early = (qs.iloc[:half]["outcome"].astype(str).str.lower() == "accept").mean()
            late = (qs.iloc[half:]["outcome"].astype(str).str.lower() == "accept").mean()
            accept_decl = _clamp(float(early - late))
        churn[cid] = round(100.0 * (cfg.churn_w_recency * recency_norm
                                    + cfg.churn_w_neg_trend * neg_trend
                                    + cfg.churn_w_accept_decline * accept_decl), 1)

    # ---- Layer-3 base value (RFAP) pre-percentile ----
    periods_per_year = {cid: (52 if rows[cid]["core"]["grain"] == "weekly" else 12) for cid in rows}
    book_margin = np.median([r["facts"]["gross_margin_per_gal_mean"] for r in rows.values()
                             if r["facts"]["gross_margin_per_gal_mean"] is not None]) if data["has_margin"] else 0.0
    bv_raw = {}
    for cid, r in rows.items():
        core, facts = r["core"], r["facts"]
        ann_gal = core["lane"]["base_level"] * periods_per_year[cid]
        mgn = facts["gross_margin_per_gal_mean"] if facts["gross_margin_per_gal_mean"] is not None else float(book_margin)
        egp = ann_gal * mgn
        opm = core["n_lifts"] / max(1.0, core["n_weeks"] / 4.345)  # orders per month
        orders_yr = opm * 12.0
        friction = (facts["small_order_rate"] * orders_yr * cfg.friction_cost_small_order
                    + facts["rush_rate"] * orders_yr * cfg.friction_cost_rush
                    + facts["split_rate"] * orders_yr * cfg.friction_cost_split)
        # credit cost
        credit_cost = 0.0
        exposure = max(1.0, egp * 0.12)  # ~ implied receivable exposure if no AR
        if data["has_ar"]:
            late = facts["late_rate"] or 0.0
            pd_ = cfg.pd_base + cfg.pd_late_multiplier * late
            dtp = facts["days_to_pay_mean"] or (facts["payment_terms_days"] or 30)
            if facts["credit_utilization"] is not None and egp:
                exposure = max(exposure, facts["credit_utilization"] * egp)
            credit_cost = pd_ * exposure + dtp * exposure * cfg.cost_of_capital / 365.0
        rfap = egp - friction - credit_cost
        rack_hours = orders_yr * cfg.hours_per_order
        ppg = rfap / ann_gal if ann_gal else None
        pprh = rfap / rack_hours if rack_hours else None
        ppc = rfap / exposure if exposure else None
        ppo = rfap / orders_yr if orders_yr else None
        bv_raw[cid] = {"egp": egp, "friction": friction, "credit_cost": credit_cost, "rfap": rfap,
                       "profit_per_gallon": ppg, "profit_per_rackhour": pprh,
                       "profit_per_credit_dollar": ppc, "profit_per_order": ppo,
                       "exposure": exposure, "orders_yr": orders_yr, "annual_gallons": ann_gal}

    constraint_key = {"rackhour": "profit_per_rackhour", "gallon": "profit_per_gallon",
                      "credit": "profit_per_credit_dollar", "order": "profit_per_order"}.get(
                          cfg.default_constraint, "profit_per_rackhour")
    pr_rfap = _pct_rank({cid: bv_raw[cid]["rfap"] for cid in rows})
    pr_constraint = _pct_rank({cid: bv_raw[cid][constraint_key] for cid in rows})

    # profitability axis = margin_per_gal · max(DiscountEff,1) · premium_capture (percentile)
    profit_axis_raw = {}
    for cid, r in rows.items():
        mgn = r["facts"]["gross_margin_per_gal_mean"]
        if mgn is None:
            profit_axis_raw[cid] = None
            continue
        de = disc_eff.get(cid)
        prem = r["raw"]["premium_capture"]
        profit_axis_raw[cid] = mgn * max(de or 1.0, 1.0) * (1.0 + (prem or 0.0))
    pr_profit = _pct_rank(profit_axis_raw)
    pr_disc = _pct_rank({cid: disc_eff[cid] for cid in rows})

    # strategic uplift heuristic
    vol_rank = _pct_rank({cid: rows[cid]["core"]["periods"]["actual"].sum() for cid in rows})

    # ---- pass 2: assemble per-customer outputs ----
    out_customers = []
    # we need base_value percentile → compute strategic first, then base value, then account value
    strategic = {}
    for cid, r in rows.items():
        up = 1.0
        if r["facts"]["product_concentration_hhi"] < 0.6:
            up += 0.12
        if (vol_rank.get(cid) or 0) >= 75:
            up += 0.15
        if (r["core"]["var_score"] or 0) >= 70:
            up += 0.10
        if churn[cid] < 30:
            up += 0.08
        if r["facts"]["credit_utilization"] and r["facts"]["credit_utilization"] > 0.9:
            up -= 0.12
        strategic[cid] = round(min(cfg.strategic_uplift_max, max(cfg.strategic_uplift_min, up)), 3)
    pr_strategic = _pct_rank(strategic)

    base_value = {}
    for cid in rows:
        bv = 100.0 * (cfg.bv_w_rfap * (pr_rfap[cid] or 0) / 100.0
                      + cfg.bv_w_profit_constraint * (pr_constraint[cid] or 0) / 100.0
                      + cfg.bv_w_strategic * (pr_strategic[cid] or 0) / 100.0)
        # apply strategic uplift adjustment, normalized around 1.0
        adj = strategic[cid] / 1.15
        base_value[cid] = round(_clamp(bv * adj, 0, 100), 1)

    # quadrant axes split at the book median (so all four cells can populate)
    evr_vals = [rows[cid]["raw"]["evr"] for cid in rows if rows[cid]["raw"]["evr"] is not None]
    evr_median = float(np.median(evr_vals)) if evr_vals else 0.0
    prof_vals = [pr_profit[cid] for cid in rows if pr_profit[cid] is not None]
    prof_median = float(np.median(prof_vals)) if prof_vals else 50.0

    # account value = normalize(volume × margin × VAR/100)
    av_raw = {}
    for cid, r in rows.items():
        vol = float(r["core"]["periods"]["actual"].sum())
        mgn = r["facts"]["gross_margin_per_gal_mean"]
        mfac = mgn if mgn is not None else (float(book_margin) or 0.05)
        var = r["core"]["var_score"] or 0.0
        av_raw[cid] = vol * max(mfac, 0.0) * (var / 100.0)
    pr_av = _pct_rank(av_raw)

    for cid, r in rows.items():
        core, facts, raw = r["core"], r["facts"], r["raw"]
        subs = {
            "volume_steadiness": {"value": core["volume_var"], "available": True,
                                  "reason": avail["var"]["reason"], "note": "VAR volume-lane score"},
            "timing_steadiness": {"value": core["cadence_var"], "available": True,
                                  "reason": avail["var"]["reason"], "note": "VAR cadence-lane score"},
            "price_sensitivity": {"value": pr_beta[cid], "available": avail["price_elasticity"]["available"],
                                  "reason": avail["price_elasticity"]["reason"],
                                  "beta": round(raw["beta_incidence"], 5) if raw["beta_incidence"] is not None else None,
                                  "collecting": raw["beta_incidence"] is None and avail["price_elasticity"]["available"]},
            "evr": {"value": round(raw["evr"], 1) if raw["evr"] is not None else None,
                    "available": avail["evr"]["available"], "reason": avail["evr"]["reason"]},
            "discount_efficiency": {"value": pr_disc[cid], "ratio": round(disc_eff[cid], 3) if disc_eff[cid] is not None else None,
                                    "available": avail["discount_efficiency"]["available"],
                                    "reason": avail["discount_efficiency"]["reason"]},
            "market_sensitivity": {"value": pr_market[cid], "profile": raw["market_profile"],
                                   "available": avail["market_sensitivity"]["available"],
                                   "reason": avail["market_sensitivity"]["reason"]},
            "weather_sensitivity": {"value": pr_weather[cid],
                                    "beta": round(raw["weather_beta"], 4) if raw["weather_beta"] is not None else None,
                                    "available": avail["weather_sensitivity"]["available"],
                                    "reason": avail["weather_sensitivity"]["reason"]},
            "quote_score": {"value": round(raw["quote_raw"], 1) if raw["quote_raw"] is not None else None,
                            "accept_rate": round(raw["accept_rate"], 3) if raw["accept_rate"] is not None else None,
                            "available": avail["quote_score"]["available"], "reason": avail["quote_score"]["reason"]},
            "churn_risk": {"value": churn[cid], "available": True, "reason": avail["churn_risk"]["reason"]},
        }

        # quadrant
        explain = subs["evr"]["value"]
        profitability = pr_profit[cid]
        quad = None
        if explain is not None and profitability is not None:
            hi_e, hi_p = explain >= evr_median, profitability >= prof_median
            quad = ("Strategic Lever" if (hi_e and hi_p) else "Premium Spot" if (not hi_e and hi_p)
                    else "Managed Cost" if (hi_e and not hi_p) else "Dangerous Noise")

        # archetype signature inputs
        cad = core["base_cadence_days"] or 7.0
        recency_norm = _clamp((core["days_since_last"] or 0.0) / (cfg.churn_recency_cadence_mult * cad))
        sig = {
            "volume_steadiness": core["volume_var"], "timing_steadiness": core["cadence_var"],
            "price_sensitivity": pr_beta[cid], "evr": subs["evr"]["value"],
            "weather_sensitivity": pr_weather[cid], "market_sensitivity": pr_market[cid],
            "discount_efficiency": pr_disc[cid], "churn_risk": churn[cid],
            "base_value": base_value[cid], "profitability": profitability,
            "credit_pressure": _clamp((facts["credit_utilization"] or 0.0)) if data["has_ar"] else _clamp((facts["late_rate"] or 0.0)),
            "opex_pressure": _clamp(facts["small_order_rate"] + facts["rush_rate"] + facts["split_rate"]),
            "recency_norm": recency_norm,
        }
        archetype = _classify(sig, cfg)

        bv = bv_raw[cid]
        out_customers.append({
            "customer_id": cid, "name": name_by_id.get(cid, cid),
            "archetype_true": arche_by_id.get(cid), "home_terminal": home_by_id.get(cid),
            "window": window, "grain": core["grain"],
            "data_sufficient": r["data_sufficient"],
            "n_lifts": core["n_lifts"], "n_weeks": core["n_weeks"],
            "total_net_gallons": round(float(core["periods"]["actual"].sum()), 1),
            "monthly_volume": facts["monthly_volume"], "trend_pct": core["trend_pct"],
            "recency_gap": round((core["days_since_last"] or 0.0) / cad, 2),
            "var": _var_block(core, facts, name_by_id.get(cid, cid), cfg),
            "var_trend": r["var_trend"],
            "forecast": {k: v for k, v in r["forward"].items() if k != "series"},
            "forecast_series": r["forward"].get("series", []),
            "excursions": r["excursions"],
            "weather_terminal": r["terminal"],
            "lane_series": core["lane"]["series"],
            "base_value": {"score": base_value[cid], "grade": grade(base_value[cid], cfg),
                           "egp": round(bv["egp"], 0), "friction_cost": round(bv["friction"], 0),
                           "credit_cost": round(bv["credit_cost"], 0), "rfap": round(bv["rfap"], 0),
                           "profit_per_gallon": round(bv["profit_per_gallon"], 4) if bv["profit_per_gallon"] is not None else None,
                           "profit_per_rackhour": round(bv["profit_per_rackhour"], 2) if bv["profit_per_rackhour"] is not None else None,
                           "profit_per_credit_dollar": round(bv["profit_per_credit_dollar"], 3) if bv["profit_per_credit_dollar"] is not None else None,
                           "profit_per_order": round(bv["profit_per_order"], 2) if bv["profit_per_order"] is not None else None,
                           "strategic_uplift": strategic[cid],
                           "annual_gallons": round(bv["annual_gallons"], 0),
                           "available": True},
            "account_value": pr_av[cid],
            "subscores": subs,
            "quadrant": {"explainability": explain, "profitability": profitability, "quadrant": quad},
            "archetype": archetype,
            "facts": facts,
        })

    out_customers.sort(key=lambda c: (c["var"]["score"] if c["var"]["score"] is not None else -1), reverse=True)
    return {"window": window, "as_of": str(as_of.date()), "availability": avail,
            "config": cfg.to_dict(), "n_customers": len(out_customers), "customers": out_customers}


def _var_explanation(core: dict, cfg: ScoringConfig) -> str:
    lane = core["lane"]
    if core["var_status"] != "ok":
        return (f"Insufficient history — need ≥{cfg.var_min_lifts} lifts over ≥{cfg.var_min_weeks} "
                f"weeks (has {core['n_lifts']} lifts / ~{core['n_weeks']:.0f} weeks).")
    return (f"VAR {core['var_score']} = blend of volume-lane {core['volume_var']} (70%) and "
            f"cadence-lane {core['cadence_var']} (30%). Volume lane: {lane['in_band_rate']:.0%} of "
            f"periods inside the base range, tightness {lane['tightness']:.2f}, "
            f"{lane['excursion_penalty']:.0%} beyond ±2σ. Base ≈ {lane['base_level']:,.0f} gal/"
            f"{core['grain'][:-2]}; fit: {lane['method']}.")


def _fmt_gal(x) -> str:
    """Compact gallons for a plain-English sentence: 8400 → '8,400', 1.2e6 → '1.2MM'."""
    if x is None:
        return "—"
    if abs(x) >= 1e6:
        return f"{x / 1e6:.1f}MM"
    return f"{round(float(x)):,}"


def _descriptor(grade: str | None, status: str) -> str:
    """A two-word steadiness label for the ranked list."""
    if status != "ok" or grade is None:
        return "Thin history"
    return {"A": "Very predictable", "B": "Fairly predictable",
            "C": "Somewhat erratic", "D": "Erratic"}.get(grade, "—")


def _plain_read(core: dict, facts: dict, name: str, cfg: ScoringConfig) -> str:
    """One non-technical sentence an ops person reads and immediately gets.

    e.g. "7 Oil buys about 8,400 gal every ~6 days and stays within their usual range 82% of
    the time — very predictable."
    """
    nm = name or "This account"
    if core["var_status"] != "ok":
        return (f"{nm} doesn't have enough history yet to read a reliable buying pattern "
                f"({core['n_lifts']} lift(s) so far).")
    size = facts.get("order_size_median") or facts.get("order_size_mean")
    cad = core.get("base_cadence_days")
    ib = core["lane"].get("in_band_rate") or 0.0
    desc = {"A": "very predictable", "B": "fairly predictable",
            "C": "somewhat up-and-down", "D": "erratic and hard to plan around"}.get(core["var_grade"], "")
    if cad is None:
        cad_phrase = ""
    elif cad >= 26:
        cad_phrase = "about once a month"
    elif cad >= 12:
        cad_phrase = f"every ~{round(cad)} days"
    elif cad >= 2:
        cad_phrase = f"every ~{round(cad)} days"
    else:
        cad_phrase = "almost daily"
    head = f"{nm} buys about {_fmt_gal(size)} gal {cad_phrase}".rstrip()
    sentence = f"{head} and stays within their usual range {round(ib * 100)}% of the time — {desc}."
    st = core["lane"].get("steadiness") or {}
    if st.get("direction") == "improving":
        sentence += " Their steadiness has been improving lately."
    elif st.get("direction") == "deteriorating":
        sentence += " Their steadiness has been slipping lately."
    return sentence


def _var_block(core: dict, facts: dict, name: str, cfg: ScoringConfig) -> dict:
    """Assemble the full VAR block: the (frozen) score + the transparency/statistics layer.

    The headline ``score``/``grade``/``volume_var``/``cadence_var`` are unchanged; everything
    else here EXPLAINS that number — the base range, the variability range, the three score
    components, the cadence lane, the steadiness drift test, and the advanced diagnostics.
    """
    lane = core["lane"]
    base = lane["base_level"]
    sig = lane.get("sigma") or 0.0
    if cfg.base_range_mode == "percent":
        bhalf = cfg.base_range_pct * abs(base)
        vhalf = (cfg.variability_sigma_k / max(cfg.base_range_sigma_k, 1e-9)) * bhalf
    else:
        bhalf = cfg.base_range_sigma_k * sig
        vhalf = cfg.variability_sigma_k * sig
    base_range = [round(max(0.0, base - bhalf), 1), round(base + bhalf, 1)]
    var_range = [round(max(0.0, base - vhalf), 1), round(base + vhalf, 1)]

    components = None
    if lane.get("in_band_rate") is not None:
        excursion_ctrl = 1.0 - (lane.get("excursion_penalty") or 0.0)
        components = [
            {"key": "in_band", "label": "In-band rate", "value": lane["in_band_rate"],
             "weight": cfg.var_w_in_band,
             "contribution": round(100 * cfg.var_w_in_band * lane["in_band_rate"], 1),
             "description": "How often they buy within their normal range."},
            {"key": "tightness", "label": "Lane tightness", "value": lane.get("tightness"),
             "weight": cfg.var_w_tightness,
             "contribution": round(100 * cfg.var_w_tightness * (lane.get("tightness") or 0.0), 1),
             "description": "How narrow that normal range is — less scatter around the base."},
            {"key": "excursion_control", "label": "Excursion control", "value": round(excursion_ctrl, 3),
             "weight": cfg.var_w_excursion,
             "contribution": round(100 * cfg.var_w_excursion * excursion_ctrl, 1),
             "description": "How rarely they swing far outside their range (wild outliers)."},
        ]

    cad = core["cadence"]
    cadence_block = {"base_cadence_days": cad.get("base_cadence_days"), "score": cad.get("score"),
                     "in_band_rate": cad.get("in_band_rate"), "tightness": cad.get("tightness"),
                     "cv": cad.get("cv"), "sigma_days": cad.get("sigma")}

    return {
        "score": core["var_score"], "grade": core["var_grade"],
        "volume_var": core["volume_var"], "cadence_var": core["cadence_var"],
        "status": core["var_status"], "base_level": base,
        "base_cadence_days": core["base_cadence_days"],
        "in_band_rate": lane.get("in_band_rate"), "tightness": lane.get("tightness"),
        "excursion_penalty": lane.get("excursion_penalty"), "method": lane.get("method"),
        "sigma": lane.get("sigma"),
        "base_range": base_range, "variability_range": var_range,
        "components": components,
        "cadence": cadence_block,
        "steadiness": lane.get("steadiness"),
        "diagnostics": lane.get("diagnostics"),
        "descriptor": _descriptor(core["var_grade"], core["var_status"]),
        "plain": _plain_read(core, facts, name, cfg),
        "explanation": _var_explanation(core, cfg),
    }


# ---- Persistence + backtest -----------------------------------------------------
def recompute_and_persist(con, cfg: ScoringConfig | None = None) -> dict:
    """Recompute every window and write the customer_scores + customer_lane tables."""
    cfg = cfg or DEFAULT_CONFIG
    ensure_tables(con)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute("DELETE FROM customer_scores")
    con.execute("DELETE FROM customer_lane")
    summary = {}
    for window in WINDOWS:
        res = compute_scores(con, cfg, window)
        summary[window] = res["n_customers"]
        for c in res["customers"]:
            con.execute(
                "INSERT INTO customer_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [c["customer_id"], window, now, c["name"],
                 c["var"]["score"], c["var"]["grade"], c["var"]["volume_var"], c["var"]["cadence_var"],
                 c["base_value"]["score"], c["base_value"]["grade"], c["account_value"], c["recency_gap"],
                 c["archetype"]["primary"], c["archetype"]["secondary"], c["archetype"]["confidence"],
                 c["archetype"]["ambiguous"], c["subscores"]["evr"]["value"],
                 c["subscores"]["price_sensitivity"]["value"], c["subscores"]["churn_risk"]["value"],
                 c["subscores"]["discount_efficiency"]["value"], c["quadrant"]["explainability"],
                 c["quadrant"]["profitability"], c["quadrant"]["quadrant"], c["data_sufficient"],
                 c["total_net_gallons"], c["n_lifts"],
                 json.dumps({"facts": c["facts"], "subscores": c["subscores"],
                             "base_value": c["base_value"], "var": c["var"], "archetype": c["archetype"],
                             "forecast": c["forecast"], "var_trend": c["var_trend"]})])
            if window == "all":
                for p in c["lane_series"]:
                    con.execute("INSERT INTO customer_lane VALUES (?,?,?,?,?,?,?,?,?,?)",
                                [c["customer_id"], window, c["grain"], p["period_start"],
                                 p["base"], p["base_lo"], p["base_hi"], p["var_lo"], p["var_hi"], p["actual"]])
    db.set_meta(con, "scores_computed_at", now)
    return {"ok": True, "computed_at": now, "windows": summary}


def backtest(con, cfg: ScoringConfig | None = None) -> dict:
    """Per-customer one-step-ahead forecast error by method (naive-last, seasonal, lane-base)."""
    cfg = cfg or DEFAULT_CONFIG
    data = _load(con)
    lifts, as_of = data["lifts"], data["as_of"]
    if not len(lifts):
        return {"customers": [], "methods": ["naive_last", "seasonal", "lane_base"], "summary": {}}
    rows = []
    agg = {"naive_last": [], "seasonal": [], "lane_base": []}
    for cid, cl in lifts.groupby("customer_id"):
        core = _customer_core(cl, cfg, as_of)
        a = core["periods"]["actual"].to_numpy(float)
        starts = pd.DatetimeIndex(core["periods"]["period_start"])
        if len(a) < 6:
            continue
        errs = {"naive_last": [], "seasonal": [], "lane_base": []}
        for t in range(3, len(a)):
            actual = a[t]
            naive = a[t - 1]
            fit, _ = _stl_or_seasonal(a[:t], starts[:t], core["grain"])
            seasonal = fit[-1] if len(fit) else np.median(a[:t])
            lane_base = float(np.median(a[:t]))
            errs["naive_last"].append(abs(actual - naive))
            errs["seasonal"].append(abs(actual - seasonal))
            errs["lane_base"].append(abs(actual - lane_base))
        mae = {m: round(float(np.mean(v)), 1) for m, v in errs.items() if v}
        if not mae:
            continue
        best = min(mae, key=mae.get)
        for m, v in mae.items():
            agg[m].append(v)
        rows.append({"customer_id": cid, "name": data["customers"].set_index("customer_id")["name"].get(cid, cid)
                     if len(data["customers"]) else cid, "grain": core["grain"], "mae": mae, "best": best})
    rows.sort(key=lambda r: r["mae"].get("seasonal", 1e18))
    summary = {m: round(float(np.mean(v)), 1) for m, v in agg.items() if v}
    return {"customers": rows, "methods": ["naive_last", "seasonal", "lane_base"], "summary": summary}
