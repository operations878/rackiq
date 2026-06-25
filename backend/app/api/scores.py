"""Customer scoring API — the VAR lane model, sub-scores, base value, and archetypes.

Read endpoints compute live over the shared connection (fast for a book of this size) with a
small in-process cache keyed by (data signature, config); ``/recompute`` writes the
customer_scores + customer_lane tables and busts the cache. Every metric is capability-gated:
the payload carries ``availability`` so the UI greys out what the data can't support.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import behavioral, db, schema, scoring
from ..scoring_config import ARCHETYPE_POSTURE, ARCHETYPES, DEFAULT_CONFIG, WINDOWS, ScoringConfig

router = APIRouter(prefix="/api/scores")


def _con():
    return db.get_shared_connection()


# ---- live-compute cache ---------------------------------------------------------
_CACHE: dict = {}


def _data_sig(con) -> tuple:
    # `date.today()` is part of the signature so forecasts re-anchor when the calendar day rolls
    # over (the forecast anchor is "today", not the last data date).
    return (db.row_count(con, schema.LIFTS), str(db.get_meta(con, "last_import_at")),
            str(db.get_meta(con, "generated_at")), str(db.get_meta(con, "profile")),
            str(date.today()))


def _result(con, cfg: ScoringConfig, window: str) -> dict:
    sig = (_data_sig(con), tuple(sorted(cfg.to_dict().items())))
    wmap = _CACHE.get(sig)
    if wmap is None:
        _CACHE.clear()
        wmap = {}
        _CACHE[sig] = wmap
    if window not in wmap:
        wmap[window] = scoring.compute_scores(con, cfg, window)
    return wmap[window]


def _bust():
    _CACHE.clear()


def _recency_fields(res: dict) -> dict:
    """The data-recency block surfaced on every scores response (anchor / lag / honest note)."""
    return {k: res.get(k) for k in
            ("data_through", "forecast_anchor", "data_lag_days", "recency_note")}


class RecomputeRequest(BaseModel):
    overrides: dict | None = Field(default=None)


def _check_window(window: str) -> str:
    if window not in WINDOWS:
        raise HTTPException(status_code=400, detail=f"window must be one of {WINDOWS}.")
    return window


_TABLE_FIELDS = ("customer_id", "name", "archetype_true", "home_terminal", "window", "grain",
                 "data_sufficient", "n_lifts", "total_net_gallons", "monthly_volume", "trend_pct",
                 "recency_gap", "var", "behavior", "base_value", "account_value", "quadrant",
                 "archetype", "forecast", "var_trend")


# A slim set of Layer-1 facts the Book Overview table needs (margin, credit, product mix)
# without shipping the whole heavy facts blob per row.
_TABLE_FACTS = ("gross_margin_per_gal_mean", "credit_utilization", "late_rate",
                "product_mix", "days_since_last_order", "monthly_volume")


def _slim_var(v: dict) -> dict:
    """Just the VAR fields the ranked list needs — drops the heavy diagnostics/components."""
    st = v.get("steadiness") or {}
    return {
        "score": v.get("score"), "grade": v.get("grade"), "status": v.get("status"),
        "base_level": v.get("base_level"), "sigma": v.get("sigma"),
        "base_cadence_days": v.get("base_cadence_days"), "in_band_rate": v.get("in_band_rate"),
        "descriptor": v.get("descriptor"), "plain": v.get("plain"),
        "base_range": v.get("base_range"), "variability_range": v.get("variability_range"),
        "volume_var": v.get("volume_var"), "cadence_var": v.get("cadence_var"),
        "steadiness": {"direction": st.get("direction")} if v.get("steadiness") else None,
    }


def _table_row(c: dict) -> dict:
    """Trim a full customer record to the ranked-table fields (drops the heavy lane series)."""
    row = {k: c[k] for k in _TABLE_FIELDS if k in c}
    if "var" in row:
        row["var"] = _slim_var(row["var"])
    if "behavior" in row:
        row["behavior"] = behavioral.slim_behavior(row["behavior"])
    row["subscores"] = {k: {kk: vv for kk, vv in v.items() if kk != "profile"}
                        for k, v in c["subscores"].items()}
    facts = c.get("facts") or {}
    row["facts"] = {k: facts.get(k) for k in _TABLE_FACTS}
    return row


@router.get("")
def scores(window: str = Query(default="all")):
    window = _check_window(window)
    with db.lock():
        con = _con()
        scoring.ensure_tables(con)
        res = _result(con, DEFAULT_CONFIG, window)
    return {
        "window": res["window"], "as_of": res["as_of"], "availability": res["availability"],
        "windows": WINDOWS, "n_customers": res["n_customers"],
        "customers": [_table_row(c) for c in res["customers"]],
        "scores_computed_at": None, **_recency_fields(res),
    }


@router.get("/book-forecast")
def book_forecast(window: str = Query(default="all"),
                  terminal: str | None = Query(default=None),
                  product: str | None = Query(default=None)):
    """Bottom-up book demand forecast (7/30/90 days) summed from every customer's lane,
    optionally filtered by terminal / product, plus the A/B-vs-C/D forecastability headline."""
    window = _check_window(window)
    with db.lock():
        con = _con()
        scoring.ensure_tables(con)
        res = _result(con, DEFAULT_CONFIG, window)
    terms, prods = set(), set()
    for c in res["customers"]:
        for key in ((c.get("facts") or {}).get("tp_share") or {}):
            t, _, p = key.partition("|")
            if t and t != "(unknown)":
                terms.add(t)
            if p and p != "(unknown)":
                prods.add(p)
    agg = scoring.aggregate_book_forecast(res["customers"], DEFAULT_CONFIG,
                                          terminal or None, product or None)
    return {"window": window, "as_of": res["as_of"], "windows": WINDOWS,
            "terminal": terminal or None, "product": product or None,
            "terminals": sorted(terms), "products": sorted(prods), **agg, **_recency_fields(res)}


@router.get("/config")
def config():
    return {"config": DEFAULT_CONFIG.to_dict(), "windows": WINDOWS,
            "archetypes": ARCHETYPES, "posture": ARCHETYPE_POSTURE}


@router.get("/backtest")
def backtest():
    with db.lock():
        return scoring.backtest(_con(), DEFAULT_CONFIG)


@router.get("/forecast-backtest")
def forecast_backtest():
    """The proof: per-customer walk-forward comparison of the new forecasting engine vs the old
    flat run-rate vs a naive-last baseline, with the aggregate accuracy improvement."""
    with db.lock():
        return scoring.forecast_backtest(_con(), DEFAULT_CONFIG)


@router.get("/quadrant")
def quadrant(window: str = Query(default="all")):
    window = _check_window(window)
    with db.lock():
        res = _result(_con(), DEFAULT_CONFIG, window)
    points = []
    for c in res["customers"]:
        q = c["quadrant"]
        if q["explainability"] is None or q["profitability"] is None:
            continue
        points.append({
            "customer_id": c["customer_id"], "name": c["name"],
            "explainability": q["explainability"], "profitability": q["profitability"],
            "quadrant": q["quadrant"], "primary_archetype": c["archetype"]["primary"],
            "var_score": c["var"]["score"], "base_value": c["base_value"]["score"],
            "total_net_gallons": c["total_net_gallons"], "data_sufficient": c["data_sufficient"],
        })
    return {"window": window, "as_of": res["as_of"], "points": points,
            "axes": {"x": "Explainability (EVR)", "y": "Profitability (percentile)"}}


@router.get("/customer/{customer_id}")
def customer(customer_id: str, window: str = Query(default="all")):
    window = _check_window(window)
    with db.lock():
        res = _result(_con(), DEFAULT_CONFIG, window)
    match = next((c for c in res["customers"] if c["customer_id"] == customer_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"No scored customer '{customer_id}' in window {window}.")
    # Lane breaks: upgrade this one customer's weather to a live NOAA/ERA5 fetch (cached per
    # terminal). The bulk list stays on the fast seasonal proxy; only the opened account fetches.
    with db.lock():
        match["excursions"] = scoring.customer_excursions(_con(), match, DEFAULT_CONFIG)
    return {"window": window, "as_of": res["as_of"], "availability": res["availability"],
            "customer": match, **_recency_fields(res)}


@router.post("/recompute")
def recompute(req: RecomputeRequest):
    cfg = DEFAULT_CONFIG.with_overrides(req.overrides)
    with db.lock():
        con = _con()
        out = scoring.recompute_and_persist(con, cfg)
        _bust()
    return out
