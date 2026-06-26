"""Missing-volume / opportunity engine (Phase 6) — the MODELED demand-gap layer.

This is the real modeled engine that the convergence layer's INTERIM opportunity tile (which rides the
channel-mismatch volume, see ``api/profile._opportunity``) will later swap to. It estimates, per master
customer × product family, the GAP between what a customer *could* pull from us and what they actually
pull, filters that gap for **winnability**, and ranks the resulting opportunity three independent ways.

PREMISE — surfaced everywhere, NEVER presented as fact:

    True demand ≈ a customer's weather-normalized PEAK with us. "Peak-with-us ≈ their whole wallet."

Everything below rests on that proxy. Every output is labelled MODELED, not measured demand.

THE MODEL (sequential — a quiet error here produces confidently-wrong opportunity dollars):

  1. TRUE-DEMAND PROXY (per customer × family). On ACTIVE days only (never zero-diluted — zero-dilution
     was the original all-spot bug), take the customer's top-decile highest-volume days (floored for thin
     lifters so an ~18-lift account still yields a *guarded* read, not a divide-by-noise), **weather-
     adjust** them with the existing ``weather_model`` β·HDD residual (so a peak that was just a cold snap
     doesn't overstate true demand — matters for heating fuels), and average → the true-demand proxy.
  2. GAP. Actual = the customer's normal weather-adjusted average active-day volume. Gap = proxy − actual,
     scaled per active day and annualized (per family AND total). A typical day within ``min_gap_frac`` of
     the peak is normal operating variation, not unmet demand — the NOISE FLOOR below which we claim no
     missing volume (this is what keeps a steady metronome from showing phantom upside).
  3. WINNABILITY (the load-bearing judgment). Separate genuinely-SHRUNK from UNDER-SERVED:
       • shrunk / not winnable — trending DOWN (year-over-year, seasonally fair) AND a stale peak (their
         big days are old). The wallet really shrank. Down-weighted, never silently suppressed; the facet
         says "looks shrunk, not winnable" instead of dangling a tempting number.
       • under-served / winnable — steady or growing but consistently below their own weather-adjusted
         peak. This is the real upside.
     A 0–100 winnability score (trend freshness × peak freshness) and a flag + plain reason come out.
  4. RANK three ways: (1) raw gallons (size of gap), (2) gap × margin (reuse Phase-2 ``margin`` — dollar
     value; margin is RANKING-ONLY and never flips a channel), (3) gap × winnability (realistic). Each row
     is tagged spot-or-rack via the Phase-1 two-axis QUADRANT (reuse ``variability`` — never re-derive the
     channel call).

REUSE, never re-derive: the two-axis quadrant + channel + confidence (``variability``), the β·HDD residual
(``weather_model``), the value margin (``margin``). Master names come from the resolved BOL book (variability
already rewrote ids → master at commit). Gallons are canonical throughout (no barrels here).

VALIDATED ON SYNTHETIC DATA (the real Excel book is local-only / gitignored). Real-book confirmation is a
separate local run — see ``validation_readout`` / ``rackiq-opportunity``.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from . import db, schema, variability, weather_model
from .dealbook import HEATING_FAMILIES, product_family

PREMISE = ("MODELED proxy: a customer's weather-normalized PEAK with us ≈ their whole wallet "
           "(peak ≈ wallet) — this is an estimate of opportunity, not measured demand.")


@dataclass(frozen=True)
class OpportunityConfig:
    # --- true-demand proxy: top-N weather-adjusted peak ACTIVE days (peak ≈ wallet) -------------
    # N = the top decile of a customer's active days, FLOORED so a thin lifter still yields a
    # (guarded) read, and CAPPED so a high-frequency account's "peak" stays a peak (not a broad mean).
    peak_top_frac: float = 0.10
    peak_min_days: int = 2
    peak_max_days: int = 20
    min_active_days_product: int = 3      # a family needs ≥ this many active days to model its own gap

    # --- gap noise floor + annualization ------------------------------------------------------
    # A typical day within this fraction of the peak day is normal operating variation, NOT unmet
    # demand. Below it we claim no winnable volume (keeps a steady metronome from phantom upside).
    # Calibrated on the demo book: steady cstore/ratable land ~0.21–0.28, variable accounts ~0.34+.
    min_gap_frac: float = 0.30
    # A CONSISTENT-SIZE account (reused quadrant flag) lifts near-identical loads, so its peak-vs-mean
    # spread is mostly sampling noise — require a larger observed spread before believing it's real
    # opportunity. This is principled (noise scales with consistency), not tuned, and it also hardens
    # the real book (weekly lifters → fewer active days → a noisier top-decile for steady accounts).
    min_gap_frac_consistent: float = 0.38
    max_active_days_per_year: float = 313.0   # annualization cap (~working days/yr; no absurd rates)

    # --- winnability: shrunk (down + stale peak) vs under-served (steady/below own peak) -------
    yoy_min_span_days: int = 400          # ≥ this span ⇒ use a seasonally-fair year-over-year trend
    yoy_window_days: int = 90             # the trailing window compared to the same window a year back
    recent_frac: float = 0.34             # fallback (short history): trailing share treated as "recent"
    trend_down_ratio: float = 0.85        # recent < this × prior ⇒ trending down
    trend_up_ratio: float = 1.10          # recent > this × prior ⇒ growing
    shrink_floor: float = 0.50            # recent at ≤ 50% of prior ⇒ trend_factor 0
    peak_stale_days: int = 180            # newest peak day older than this (vs the data date) ⇒ stale
    peak_fresh_window_days: float = 365.0 # freshness_factor decays linearly to 0 across this window
    w_trend: float = 0.5                  # winnability = 100·(w_trend·trend_factor + w_fresh·fresh)
    w_fresh: float = 0.5
    winnable_cutoff: float = 55.0         # winnability ≥ this ⇒ "under-served" (clearly winnable)
    account_stale_days: int = 90          # silent longer than this (vs the data date) ⇒ flag the account

    # --- data sufficiency (mirror variability so the two layers agree) -------------------------
    min_lifts: int = 6
    min_active_days: int = 3


DEFAULT_OPP_CONFIG = OpportunityConfig()


# =================================================================================================
# small helpers
# =================================================================================================
def _eff_n(n_active: int, cfg: OpportunityConfig) -> int:
    """Top-N peak days = the top decile, floored for thin lifters and capped for frequent ones."""
    return int(min(cfg.peak_max_days, max(cfg.peak_min_days, round(cfg.peak_top_frac * n_active))))


def _family_day_series(cl_fam: pd.DataFrame, fam: str | None, terminal: str | None,
                       model: dict | None) -> tuple[pd.Series, pd.Series, bool, dict]:
    """Per-active-day volume for ONE (customer, family): weather-adjusted and raw.

    Weather adjustment REUSES ``weather_model.adjusted_sizes`` (the β·HDD residual, re-centred so the
    LEVEL is preserved and only weather-driven peaks are pulled down). The per-lift adjusted sizes are
    summed to the calendar day; because the residual is re-centred, the active-day MEAN is preserved
    while cold-snap peak days drop — exactly the effect we want for the proxy. Non-positive day totals
    (pure reversals/corrections) are dropped — they are not demand days.

    Returns (adjusted_day_volume, raw_day_volume, weather_adjusted, diag).
    """
    cl_fam = cl_fam.sort_values("lift_datetime")
    raw = pd.to_numeric(cl_fam["net_gallons"], errors="coerce").to_numpy(float)
    weather_adjusted, diag = False, {}
    vals = raw
    if model is not None:
        sizes, weather_adjusted, diag = weather_model.adjusted_sizes(cl_fam, fam, terminal, model)
        if weather_adjusted and len(sizes) == len(cl_fam):
            vals = sizes
        else:
            weather_adjusted = False  # alignment guard / kept-raw → use raw, honestly
    day = pd.to_datetime(cl_fam["lift_datetime"], errors="coerce").dt.normalize()
    frame = pd.DataFrame({"day": day.to_numpy(), "adj": vals, "raw": raw})
    frame = frame[frame["day"].notna()]
    adj_day = frame.groupby("day")["adj"].sum()
    raw_day = frame.groupby("day")["raw"].sum()
    adj_day = adj_day[adj_day > 0]
    raw_day = raw_day[raw_day > 0]
    return adj_day, raw_day, weather_adjusted, diag


def _family_gap(cl_fam: pd.DataFrame, fam: str | None, terminal: str | None, model: dict | None,
                customer_span_days: int, cfg: OpportunityConfig) -> dict | None:
    """The modeled per-active-day gap for one (customer, family). None if too thin to read."""
    adj_day, _raw_day, weather_adjusted, diag = _family_day_series(cl_fam, fam, terminal, model)
    n_active = int(len(adj_day))
    if n_active < cfg.min_active_days_product:
        return None
    v = adj_day.to_numpy(float)
    n_top = _eff_n(n_active, cfg)
    proxy = float(np.sort(v)[-n_top:].mean())          # avg of the top-decile (weather-adjusted) days
    actual = float(v.mean())                            # normal weather-adjusted average active day
    gap_per_day = max(proxy - actual, 0.0)
    span = max(customer_span_days, 1)
    active_days_per_year = min(n_active * 365.0 / span, cfg.max_active_days_per_year)
    return {
        "product": _family_display(cl_fam, fam),
        "family": fam,
        "n_active_days": n_active,
        "top_n": n_top,
        "proxy_per_active_day": round(proxy, 0),
        "actual_per_active_day": round(actual, 0),
        "gap_per_active_day": round(gap_per_day, 0),
        "active_days_per_year": round(active_days_per_year, 1),
        "gap_gal_per_yr": round(gap_per_day * active_days_per_year, 0),
        "actual_gal_per_yr": round(actual * active_days_per_year, 0),
        "weather_adjusted": bool(weather_adjusted),
        "weather_beta": diag.get("beta"),
        "weather_beta_source": diag.get("beta_source"),
    }


def _family_display(cl_fam: pd.DataFrame, fam: str | None) -> str | None:
    """The dominant RAW product code inside a family group (e.g. family OTHER → 'B10') — more
    meaningful to read than the family bucket while the math stays family-correct."""
    prods = cl_fam["product"].dropna()
    if prods.empty:
        return fam
    return str(prods.mode().iloc[0])


def _trend(day_series: pd.Series, as_of: pd.Timestamp, span_days: int,
           cfg: OpportunityConfig) -> dict:
    """Seasonally-fair demand trend on the customer's TOTAL active-day series.

    Primary: a YEAR-OVER-YEAR comparison of the trailing window vs the same window a year earlier
    (so a heating account measured in summer isn't called "declining" for being off its winter peak).
    Fallback (short history): the trailing third vs the earlier history, by median. Anchored to the
    DATA date (as_of), so a uniformly-stale book doesn't make everyone look like they're shrinking.
    """
    idx = day_series.index
    ratio, method, reliable = 1.0, "flat_unknown", False
    if span_days >= cfg.yoy_min_span_days:
        r_lo = as_of - pd.Timedelta(days=cfg.yoy_window_days)
        p_hi = as_of - pd.Timedelta(days=365)
        p_lo = p_hi - pd.Timedelta(days=cfg.yoy_window_days)
        recent = day_series[(idx > r_lo) & (idx <= as_of)]
        prior = day_series[(idx > p_lo) & (idx <= p_hi)]
        if len(recent) >= 2 and len(prior) >= 2 and float(prior.sum()) > 0:
            ratio, method, reliable = float(recent.sum() / prior.sum()), "year_over_year", True
    if method == "flat_unknown":
        cut = as_of - pd.Timedelta(days=max(1, int(span_days * cfg.recent_frac)))
        recent = day_series[idx > cut]
        hist = day_series[idx <= cut]
        if len(recent) >= 2 and len(hist) >= 2 and float(hist.median()) > 0:
            ratio, method, reliable = float(recent.median() / hist.median()), "recent_vs_prior", False
    if method == "flat_unknown":
        label = "unknown"
    elif ratio < cfg.trend_down_ratio:
        label = "declining"
    elif ratio > cfg.trend_up_ratio:
        label = "growing"
    else:
        label = "steady"
    return {"ratio": round(ratio, 3), "label": label, "method": method, "reliable": reliable}


def _peak_recency(total_day: pd.Series, as_of: pd.Timestamp, cfg: OpportunityConfig) -> dict:
    """When did the customer last pull one of its biggest loads (top-decile day)? Anchored to the data
    date so a uniformly-stale book doesn't flag everyone."""
    v = total_day.to_numpy(float)
    n = len(v)
    if n == 0:
        return {"days_since_peak": None, "stale": False, "newest_peak": None}
    n_top = _eff_n(n, cfg)
    thresh = np.sort(v)[-n_top:][0]              # the smallest of the top-N day volumes
    peak_days = total_day[total_day >= thresh].index
    newest = max(peak_days)
    days_since_peak = int((as_of - newest).days)
    return {"days_since_peak": days_since_peak, "stale": days_since_peak > cfg.peak_stale_days,
            "newest_peak": str(pd.Timestamp(newest).date())}


def _winnability(trend: dict, peak: dict, cfg: OpportunityConfig) -> float | None:
    """0–100: how *gettable* the gap is. High = steady/growing AND big days are recent."""
    if trend["method"] == "flat_unknown" and peak["days_since_peak"] is None:
        return None
    trend_factor = _clamp((trend["ratio"] - cfg.shrink_floor) / max(1e-9, 1.0 - cfg.shrink_floor))
    dsp = peak["days_since_peak"]
    fresh_factor = 1.0 if dsp is None else _clamp(1.0 - dsp / cfg.peak_fresh_window_days)
    return round(100.0 * (cfg.w_trend * trend_factor + cfg.w_fresh * fresh_factor), 1)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# =================================================================================================
# the engine
# =================================================================================================
def compute_opportunity(con, cfg: OpportunityConfig | None = None,
                        var_result: dict | None = None) -> dict:
    """Modeled missing-volume / opportunity for every master customer in the BOL book.

    ``var_result`` lets a caller pass a precomputed ``variability.compute_variability`` payload (so the
    API can share its cache); otherwise it is computed here.
    """
    cfg = cfg or DEFAULT_OPP_CONFIG
    var = var_result or variability.compute_variability(con)
    if not var.get("available"):
        return {"available": False, "reason": var.get("reason", "no BOL lifts loaded"),
                "premise": PREMISE, "customers": [], "n_customers": 0}

    lifts = con.execute(
        "SELECT customer_id, lift_datetime, net_gallons, product, terminal FROM lifts "
        "WHERE customer_id IS NOT NULL AND lift_datetime IS NOT NULL AND net_gallons IS NOT NULL").df()
    lifts["lift_datetime"] = pd.to_datetime(lifts["lift_datetime"], errors="coerce")
    lifts = lifts[lifts["lift_datetime"].notna()].copy()
    lifts["family"] = lifts["product"].map(product_family)
    as_of = lifts["lift_datetime"].max()

    model = weather_model.build_model(con)
    margins, margin_basis, margin_available = _margin_map(con)
    var_by_id = {c["customer_id"]: c for c in var.get("customers", [])}
    lifts_by_cust = {cid: g for cid, g in lifts.groupby("customer_id")}

    customers: list[dict] = []
    for cid, c in var_by_id.items():
        g = lifts_by_cust.get(cid)
        rec = _assemble(cid, c, g, model, margins.get(cid), as_of, cfg)
        customers.append(rec)

    _rank(customers)
    for rec in customers:
        if rec.get("available"):
            rec["headline"] = _headline(rec)          # built AFTER ranking so it can name the rank
        rec["facet"] = _facet(rec)

    return {
        "available": True,
        "as_of": str(pd.Timestamp(as_of).date()),
        "premise": PREMISE,
        "n_customers": len(customers),
        "config": asdict(cfg),
        "margin": {"available": margin_available, "basis": margin_basis,
                   "note": "Dollar value = annual gap × the Phase-2 margin ¢/gal. Ranking only — "
                           "margin never moves a channel call." if margin_available else
                           "Margin not loaded — dollar ranking unavailable; gallons and winnability "
                           "rankings still apply."},
        "weather": {"available": bool(model.get("available")),
                    "n_adjusted": sum(1 for r in customers if r.get("weather_adjusted")),
                    "heating_families": sorted(HEATING_FAMILIES),
                    "note": "Weather adjustment (β·HDD residual) applies to heating families "
                            f"{sorted(HEATING_FAMILIES)} only — other products keep raw peaks."},
        "summary": _summary(customers, margin_available),
        "rankings": _ranking_lists(customers),
        "customers": customers,
    }


def _assemble(cid: str, c: dict, g: pd.DataFrame | None, model: dict | None,
              margin_row: dict | None, as_of: pd.Timestamp, cfg: OpportunityConfig) -> dict:
    """Join one master customer's modeled gap + winnability + the reused quadrant/channel tag."""
    ch = c.get("channel") or {}
    conf = c.get("confidence") or {}
    tier = conf.get("tier")
    provisional = bool(conf.get("provisional"))
    channel = ch.get("recommended_channel")            # 'RACK' | 'SPOT' | None  (reused quadrant tag)
    term_eligible = bool(ch.get("term_eligible"))
    chase = _chase_channel(channel, term_eligible)
    margin_cents = (margin_row or {}).get("book_cents_gal")

    base = {
        "customer_id": cid, "name": c.get("name"),
        "n_lifts": c.get("n_lifts"), "n_active_days": c.get("n_active_days"),
        "span_days": c.get("span_days"), "total_net_gallons": c.get("total_net_gallons"),
        "home_terminal": c.get("home_terminal"), "dominant_product": c.get("dominant_product"),
        "data_sufficient": bool(c.get("data_sufficient")),
        "confidence_tier": tier, "provisional": provisional,
        "confidence_reason": conf.get("reason"), "confidence_flag": conf.get("flag"),
        # the reused two-axis quadrant / channel tag (Phase 1 — never re-derived here)
        "quadrant": c.get("quadrant"), "quadrant_label": c.get("quadrant_label"),
        "channel": channel, "channel_label": ch.get("channel_label"),
        "term_eligible": term_eligible, "chase_channel": chase,
        "weather_sensitive": bool(c.get("weather_sensitive")),
        "margin_cents_gal": margin_cents,
    }

    if not c.get("data_sufficient") or g is None or g.empty:
        base.update(_insufficient(c))
        return base

    span_days = int(c.get("span_days") or 0)
    # per-family modeled gap
    by_product = []
    for fam, gf in g.groupby("family", dropna=False):
        fam = None if (isinstance(fam, float) and pd.isna(fam)) else fam
        res = _family_gap(gf, fam, _term(gf), model, span_days, cfg)
        if res is not None:
            by_product.append(res)
    by_product.sort(key=lambda r: -(r["gap_gal_per_yr"] or 0))
    if not by_product:                                 # sufficient overall, but no family has the depth
        base.update({
            "available": True, "kind": "matched", "winnability_flag": "thin_per_product",
            "gap_gal_per_yr": 0, "gap_dollars_per_yr": None, "winnable_gal_per_yr": 0,
            "winnable_dollars_per_yr": None, "gap_frac": 0.0, "actual_gal_per_yr": 0,
            "winnability": None, "reason": "Active, but no single product has enough history to model a "
                                           "demand gap yet — no modeled upside to chase.",
            "trend": "unknown", "trend_ratio": None, "trend_method": "flat_unknown",
            "peak_stale": False, "days_since_peak": None, "days_since_last_lift": None,
            "account_stale": False, "weather_adjusted": False, "by_product": [],
        })
        return base

    gap_gal = round(sum(r["gap_gal_per_yr"] for r in by_product), 0)
    actual_gal = round(sum(r["actual_gal_per_yr"] for r in by_product), 0)
    gap_frac = (gap_gal / actual_gal) if actual_gal > 0 else 0.0
    any_weather = any(r["weather_adjusted"] for r in by_product)

    # winnability on the customer's TOTAL active-day series (all families), raw, seasonally fair
    total_day = _total_day_series(g)
    days_since_last = int((as_of - total_day.index.max()).days) if len(total_day) else None
    account_stale = bool(days_since_last is not None and days_since_last > cfg.account_stale_days)
    trend = _trend(total_day, as_of, span_days, cfg)
    peak = _peak_recency(total_day, as_of, cfg)
    winnability = _winnability(trend, peak, cfg)

    flag, kind, reason = _classify(gap_frac, winnability, trend, peak, provisional,
                                   bool(c.get("consistent_size")), cfg)
    winnable_gal = _winnable_gallons(kind, gap_gal, winnability)
    gap_dollars = _dollars(gap_gal, margin_cents)
    winnable_dollars = _dollars(winnable_gal, margin_cents)

    base.update({
        "available": True,
        "gap_gal_per_yr": gap_gal,
        "gap_dollars_per_yr": gap_dollars,
        "winnable_gal_per_yr": winnable_gal,
        "winnable_dollars_per_yr": winnable_dollars,
        "gap_frac": round(gap_frac, 3),
        "actual_gal_per_yr": actual_gal,
        "winnability": winnability,
        "winnability_flag": flag,
        "kind": kind,
        "reason": reason,
        "trend": trend["label"], "trend_ratio": trend["ratio"], "trend_method": trend["method"],
        "peak_stale": peak["stale"], "days_since_peak": peak["days_since_peak"],
        "days_since_last_lift": days_since_last, "account_stale": account_stale,
        "weather_adjusted": any_weather,
        "by_product": by_product,
    })
    return base


def _term(gf: pd.DataFrame) -> str | None:
    return gf["terminal"].mode().iloc[0] if gf["terminal"].notna().any() else None


def _insufficient(c: dict) -> dict:
    n = c.get("n_lifts") or 0
    return {
        "available": False, "kind": "unknown", "winnability_flag": "insufficient",
        "gap_gal_per_yr": 0, "gap_dollars_per_yr": None, "winnable_gal_per_yr": 0,
        "winnable_dollars_per_yr": None, "gap_frac": None, "actual_gal_per_yr": 0,
        "winnability": None, "reason": f"only {n} lifts so far — too new to model a demand pattern yet",
        "trend": "unknown", "trend_ratio": None, "trend_method": "flat_unknown",
        "peak_stale": False, "days_since_peak": None, "days_since_last_lift": None,
        "account_stale": False, "weather_adjusted": False, "by_product": [],
        "headline": f"{c.get('name')} — too new to read a demand pattern yet "
                    f"({n} lifts). Modeled opportunity not available.",
    }


def _total_day_series(g: pd.DataFrame) -> pd.Series:
    """Customer's total positive volume per active calendar day (all products)."""
    day = pd.to_datetime(g["lift_datetime"], errors="coerce").dt.normalize()
    net = pd.to_numeric(g["net_gallons"], errors="coerce")
    frame = pd.DataFrame({"day": day.to_numpy(), "net": net.to_numpy()})
    frame = frame[frame["day"].notna()]
    s = frame.groupby("day")["net"].sum().sort_index()
    return s[s > 0]


def _classify(gap_frac: float, winnability: float | None, trend: dict, peak: dict,
              provisional: bool, consistent_size: bool, cfg: OpportunityConfig) -> tuple[str, str, str]:
    """(winnability_flag, facet kind, plain-English reason). The load-bearing judgment.

    kind ∈ win | shrunk | matched | unknown drives the worklist/home-tile aggregation; the flag is the
    finer category. Low confidence FLAGS (appends to the reason) but never changes the category.
    """
    prov = " (provisional — thin history, treat as a guess)" if provisional else ""
    noise_floor = cfg.min_gap_frac_consistent if consistent_size else cfg.min_gap_frac
    if gap_frac < noise_floor:
        return ("near_peak", "matched",
                "Already lifts close to their weather-adjusted peak — little upside to chase." + prov)
    shrunk = trend["label"] == "declining" and peak["stale"]
    if shrunk:
        return ("shrunk", "shrunk",
                "Buying less than they used to (down year-over-year) and their big days are old — "
                "looks shrunk, not winnable; confirm they're still active before chasing." + prov)
    if winnability is not None and winnability >= cfg.winnable_cutoff:
        return ("under_served", "win",
                "Steady/growing but consistently below their own weather-adjusted peak — under-served, "
                "real room to win more volume." + prov)
    return ("watch", "win",
            "Below their weather-adjusted peak, but the signal is mixed (a recent dip or an aging "
            "peak) — winnable with a check-in first." + prov)


def _winnable_gallons(kind: str, gap_gal: float, winnability: float | None) -> float:
    """Realistic winnable gallons = gap × winnability. Matched (within the noise floor) → 0 (nothing to
    win). Shrunk is DOWN-WEIGHTED via winnability, never hard-zeroed (never silently suppressed)."""
    if kind in ("matched", "unknown"):
        return 0
    w = (winnability if winnability is not None else 0.0) / 100.0
    return round(gap_gal * w, 0)


def _dollars(gallons: float, cents: float | None) -> float | None:
    return None if cents is None or gallons is None else round(gallons * cents / 100.0, 0)


def _chase_channel(channel: str | None, term_eligible: bool) -> str | None:
    if channel == "RACK":
        return "rack/term" if term_eligible else "rack"
    if channel == "SPOT":
        return "spot"
    return None


# =================================================================================================
# margin (reused, ranking-only) + ranking
# =================================================================================================
def _margin_map(con) -> tuple[dict, str | None, bool]:
    """Per-master BOOK ¢/gal from the Phase-2 margin layer (ranking-only). Best-effort; honors whatever
    basis the margin engine reports (book vs lift-price fallback)."""
    try:
        from . import margin
        res = margin.compute_margin(con)
    except Exception:  # noqa: BLE001 — margin is an optional layer; never break the opportunity read
        return {}, None, False
    if not res.get("available") or not res.get("customers"):
        return {}, None, False
    by = {c["customer_id"]: c for c in res["customers"]}
    av = res.get("availability") or {}
    basis = av.get("cost_basis") or av.get("sell_basis") or "book (Phase-2 margin)"
    return by, basis, True


def _rank(customers: list[dict]) -> None:
    """Assign the three independent ranks across data-sufficient customers (1 = top)."""
    def assign(key: str, rank_key: str, predicate) -> None:
        eligible = [c for c in customers if predicate(c)]
        eligible.sort(key=lambda c: -(c.get(key) or 0))
        for i, c in enumerate(eligible):
            c[rank_key] = i + 1

    for c in customers:
        c["rank_by_gap"] = None
        c["rank_by_dollars"] = None
        c["rank_by_winnable"] = None
    assign("gap_gal_per_yr", "rank_by_gap",
           lambda c: c.get("available") and (c.get("gap_gal_per_yr") or 0) > 0)
    assign("gap_dollars_per_yr", "rank_by_dollars",
           lambda c: c.get("available") and (c.get("gap_dollars_per_yr") or 0) > 0)
    assign("winnable_gal_per_yr", "rank_by_winnable",
           lambda c: c.get("available") and (c.get("winnable_gal_per_yr") or 0) > 0)


def _ranking_lists(customers: list[dict], top: int = 20) -> dict:
    def lst(rank_key: str, value_key: str) -> list[dict]:
        rows = [c for c in customers if c.get(rank_key) is not None]
        rows.sort(key=lambda c: c[rank_key])
        return [{"rank": c[rank_key], "customer_id": c["customer_id"], "name": c["name"],
                 "value": c.get(value_key), "kind": c.get("kind"), "channel": c.get("channel"),
                 "winnability": c.get("winnability"), "confidence_tier": c.get("confidence_tier"),
                 "product": (c["by_product"][0]["product"] if c.get("by_product") else None)}
                for c in rows[:top]]
    return {
        "by_gap": lst("rank_by_gap", "gap_gal_per_yr"),
        "by_margin_dollars": lst("rank_by_dollars", "gap_dollars_per_yr"),
        "by_winnable": lst("rank_by_winnable", "winnable_gal_per_yr"),
    }


def _summary(customers: list[dict], margin_available: bool) -> dict:
    scored = [c for c in customers if c.get("available")]
    win = [c for c in scored if c.get("kind") == "win"]
    return {
        "n_scored": len(scored),
        "n_winnable": len(win),
        "n_shrunk": sum(1 for c in scored if c.get("kind") == "shrunk"),
        "n_near_peak": sum(1 for c in scored if c.get("kind") == "matched"),
        "n_insufficient": sum(1 for c in customers if not c.get("available")),
        "n_low_confidence": sum(1 for c in scored if c.get("confidence_tier") == "Low"),
        "total_gap_gal_per_yr": round(sum(c.get("gap_gal_per_yr") or 0 for c in scored), 0),
        "total_winnable_gal_per_yr": round(sum(c.get("winnable_gal_per_yr") or 0 for c in win), 0),
        "total_winnable_dollars_per_yr": (
            round(sum(c.get("winnable_dollars_per_yr") or 0 for c in win), 0) if margin_available else None),
    }


# =================================================================================================
# the facet — a drop-in superset of api/profile._opportunity, so the fan-out can pull this and the
# interim tile can swap data source without a redesign (the interim adapter reads exactly these keys).
# =================================================================================================
def _facet(rec: dict) -> dict:
    win_gal = rec.get("winnable_gal_per_yr") or 0
    return {
        "available": bool(rec.get("available")),
        "source": "modeled_peak_demand",     # vs the interim's "channel_mismatch"
        "modeled": True,
        "premise": PREMISE,
        "kind": rec.get("kind"),
        "winnable_gal_per_yr": win_gal,
        "winnable_dollars_per_yr": rec.get("winnable_dollars_per_yr"),
        "gap_gal_per_yr": rec.get("gap_gal_per_yr") or 0,
        "gap_dollars_per_yr": rec.get("gap_dollars_per_yr"),
        "winnability": rec.get("winnability"),
        "winnability_flag": rec.get("winnability_flag"),
        "chase_channel": rec.get("chase_channel"),
        "channel": rec.get("channel"),
        "term_eligible": rec.get("term_eligible"),
        "confidence_tier": rec.get("confidence_tier"),
        "provisional": rec.get("provisional"),
        "stale": rec.get("account_stale", False),
        "weather_adjusted": rec.get("weather_adjusted", False),
        "rank_by_gap": rec.get("rank_by_gap"),
        "rank_by_dollars": rec.get("rank_by_dollars"),
        "rank_by_winnable": rec.get("rank_by_winnable"),
        "note": rec.get("reason"),
        "headline": rec.get("headline"),
        "product": (rec["by_product"][0]["product"] if rec.get("by_product") else None),
        # caveat aliases so the existing frontend adapter (`full.interim_note ?? OPP_CAVEAT`) shows the
        # MODELED premise once it swaps to this source, never letting an estimate read as ground truth.
        "interim_note": PREMISE,
        "caveat": PREMISE,
    }


# =================================================================================================
# plain-English headline
# =================================================================================================
def _headline(rec: dict) -> str:
    """The plain-English facet sentence, built AFTER ranking so it can name the gap×margin rank."""
    by_product = rec.get("by_product") or []
    name = rec.get("name") or rec.get("customer_id")
    term = rec.get("home_terminal")
    at_term = f" at {term}" if term else ""
    top_prod = by_product[0]["product"] if by_product else rec.get("dominant_product")
    tag = _tag_label(rec.get("channel"), rec.get("term_eligible"))
    kind = rec.get("kind")
    prov = " Provisional — only {} lifts.".format(_int(rec.get("n_lifts"))) \
        if rec.get("confidence_tier") == "Low" else ""

    if kind == "win":
        rank_str = ""
        if rec.get("rank_by_dollars"):
            rank_str = f", gap×margin ranks #{rec['rank_by_dollars']}"
        elif rec.get("rank_by_winnable"):
            rank_str = f", winnability ranks #{rec['rank_by_winnable']}"
        money = f" (≈ ${_compact(rec.get('winnable_dollars_per_yr'))}/yr)" \
            if rec.get("winnable_dollars_per_yr") else ""
        return (f"≈ {_compact(rec.get('winnable_gal_per_yr'))} gal/yr of winnable {top_prod} "
                f"upside{at_term}{money} — {tag}{rank_str}. MODELED (peak ≈ wallet), winnability "
                f"{_int(rec.get('winnability'))}/100.{prov}")
    if kind == "shrunk":
        return (f"{name}{at_term}: a ~{_compact(rec.get('gap_gal_per_yr'))} gal/yr {top_prod} gap on "
                f"paper, but they're buying less year-over-year and their big days are old — looks "
                f"shrunk, not winnable.{prov}")
    if kind == "matched":
        return (f"{name}{at_term}: already lifts near their weather-adjusted {top_prod} peak — "
                f"little modeled upside to chase.{prov}")
    return rec.get("reason") or name


def _tag_label(channel: str | None, term_eligible: bool) -> str:
    if channel == "RACK":
        return "rack/term-eligible" if term_eligible else "rack-eligible (capped)"
    if channel == "SPOT":
        return "spot-suited"
    return "channel unrated"


# =================================================================================================
# validation readout (CLI + API) — the gut-check on the demo exemplars
# =================================================================================================
def validation_readout(con, cfg: OpportunityConfig | None = None) -> dict:
    """Blunt real-book-style gate. On the SYNTHETIC demo book it gut-checks the exemplars; on the real
    book (local-only) repeat against Rastall / Super Quality / Van Varick."""
    cfg = cfg or DEFAULT_OPP_CONFIG
    res = compute_opportunity(con, cfg)
    if not res.get("available"):
        return {"available": False, "reason": res.get("reason"), "premise": PREMISE}
    by_name = {c["name"]: c for c in res["customers"]}

    gut_names = ["FuelExpress Retail", "Cornerstone Retail", "Keystone Energy",
                 "Frontier Oil & Propane", "Hearth Fuel Oil", "Yankee Heating Oil",
                 "Spot Trading", "Bluewater Marine Fuels", "Narragansett Marine Fuels",
                 "Rastall", "Super Quality", "Van Varick"]
    gut = []
    for nm in gut_names:
        c = by_name.get(nm)
        if not c:
            continue
        gut.append({
            "name": nm, "n_lifts": c.get("n_lifts"), "quadrant": c.get("quadrant_label"),
            "channel": c.get("channel"), "confidence": c.get("confidence_tier"),
            "kind": c.get("kind"), "winnability": c.get("winnability"),
            "winnability_flag": c.get("winnability_flag"), "gap_frac": c.get("gap_frac"),
            "gap_gal_per_yr": c.get("gap_gal_per_yr"),
            "winnable_gal_per_yr": c.get("winnable_gal_per_yr"),
            "weather_adjusted": c.get("weather_adjusted"), "reason": c.get("reason"),
        })

    # the assertions the gut-check exists to prove
    fe = by_name.get("FuelExpress Retail")
    nar = by_name.get("Narragansett Marine Fuels")
    checks = {
        "synthetic_data": True,
        "note": "Cloud DB is SYNTHETIC — real-book confirmation is a separate local run.",
        "steady_metronome_low_or_no_winnable": (
            None if not fe else {"name": "FuelExpress Retail", "kind": fe.get("kind"),
                                 "winnable_gal_per_yr": fe.get("winnable_gal_per_yr"),
                                 "pass": fe.get("kind") in ("matched", "shrunk")
                                 or (fe.get("winnable_gal_per_yr") or 0) == 0}),
        "low_confidence_flagged_not_suppressed": (
            None if not nar else {"name": "Narragansett Marine Fuels",
                                  "confidence": nar.get("confidence_tier"),
                                  "available": nar.get("available"),
                                  "provisional": nar.get("provisional"),
                                  "pass": nar.get("confidence_tier") == "Low"
                                  and bool(nar.get("available")) and bool(nar.get("provisional"))}),
    }

    return {
        "available": True, "as_of": res["as_of"], "premise": PREMISE,
        "summary": res["summary"], "margin": res["margin"], "weather": res["weather"],
        "gut_check": gut, "checks": checks,
        "rankings": res["rankings"],
    }


def facets_by_master(con, cfg: OpportunityConfig | None = None) -> dict:
    """{master_id: facet} — the convenience the fan-out (api/profile) pulls to swap the interim tile."""
    res = compute_opportunity(con, cfg)
    if not res.get("available"):
        return {}
    return {c["customer_id"]: c["facet"] for c in res["customers"]}


# ---- compact formatting (local; no false precision) -----------------------------------------
def _int(x) -> str:
    try:
        return f"{int(x):,}"
    except Exception:  # noqa: BLE001
        return str(x)


def _compact(x) -> str:
    if x is None:
        return "—"
    a = abs(x)
    if a >= 1e6:
        return f"{x / 1e6:.1f}M"
    if a >= 1e3:
        return f"{x / 1e3:.0f}k" if a >= 1e4 else f"{x / 1e3:.1f}k"
    return f"{round(x)}"
