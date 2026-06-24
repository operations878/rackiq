"""Credit & Account-Risk API (P9) — credit score, the VAR × credit map, conversion targeting.

Live-computes over the shared connection with a small in-process cache keyed by the data
signature + config + window (the engine itself folds in the P3 VAR scores). Capability-gated:
when the AR ledger is absent the payload returns ``available: false`` with the missing fields
for the UI lock. ``/recompute`` writes the ``customer_credit`` derived cache and busts the cache.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import credit, db, schema
from ..credit_config import DEFAULT_CONFIG, QUADRANT_ORDER, CreditConfig
from ..scoring_config import DEFAULT_CONFIG as SCORING_DEFAULT
from ..scoring_config import WINDOWS

router = APIRouter(prefix="/api/credit")


def _con():
    return db.get_shared_connection()


_CACHE: dict = {}


def _data_sig(con) -> tuple:
    return (db.row_count(con, schema.INVOICES), db.row_count(con, schema.LIFTS),
            str(db.get_meta(con, "last_import_at")), str(db.get_meta(con, "generated_at")),
            str(db.get_meta(con, "profile")))


def _result(con, cfg: CreditConfig, window: str) -> dict:
    sig = (_data_sig(con), tuple(sorted(cfg.to_dict().items())))
    wmap = _CACHE.get(sig)
    if wmap is None:
        _CACHE.clear()
        wmap = {}
        _CACHE[sig] = wmap
    if window not in wmap:
        wmap[window] = credit.compute_credit(con, cfg, SCORING_DEFAULT, window)
    return wmap[window]


def _check_window(window: str) -> str:
    if window not in WINDOWS:
        raise HTTPException(status_code=400, detail=f"window must be one of {WINDOWS}.")
    return window


class RecomputeRequest(BaseModel):
    overrides: dict | None = Field(default=None)


# A slim per-customer row for the account-risk map / Book Overview merge (drops the heavy
# per-customer components blob; the full record is available on the customer drill-down).
def _slim(c: dict) -> dict:
    cr = c["credit"]
    return {
        "customer_id": c["customer_id"], "name": c["name"], "home_terminal": c["home_terminal"],
        "credit_score": cr["score"], "credit_grade": cr["grade"], "quadrant": c["quadrant"],
        "var_score": c["var_score"], "var_grade": c["var_grade"],
        "dso_days": cr["dso_days"], "avg_days_late": cr["avg_days_late"], "pct_late": cr["pct_late"],
        "utilization": cr["utilization"], "open_exposure": cr["open_exposure"],
        "trend_days_late": cr["trend_days_late"], "total_net_gallons": c["total_net_gallons"],
        "archetype": c["archetype"], "explanation": cr["explanation"],
    }


@router.get("")
def credit_overview(window: str = Query(default="all")):
    window = _check_window(window)
    with db.lock():
        con = _con()
        credit.ensure_tables(con)
        res = _result(con, DEFAULT_CONFIG, window)
    if not res.get("available"):
        return res
    return {
        "available": True, "window": res["window"], "as_of": res["as_of"],
        "windows": WINDOWS, "n_customers": res["n_customers"],
        "axis_cuts": res["axis_cuts"], "quadrant_order": QUADRANT_ORDER,
        "quadrant_counts": res["quadrant_counts"], "network": res["network"],
        "elasticity_available": res["elasticity_available"],
        "customers": [_slim(c) for c in res["customers"]],
        "conversion_targets": res["conversion_targets"],
        "grow_me": res["grow_me"], "revenue_at_risk": res["revenue_at_risk"],
    }


@router.get("/config")
def config():
    return {"config": DEFAULT_CONFIG.to_dict(), "windows": WINDOWS,
            "quadrant_order": QUADRANT_ORDER}


@router.get("/customer/{customer_id}")
def customer(customer_id: str, window: str = Query(default="all")):
    window = _check_window(window)
    with db.lock():
        res = _result(_con(), DEFAULT_CONFIG, window)
    if not res.get("available"):
        raise HTTPException(status_code=409, detail=res.get("reason", "Credit module locked."))
    match = next((c for c in res["customers"] if c["customer_id"] == customer_id), None)
    if match is None:
        raise HTTPException(status_code=404,
                            detail=f"No credit-scored customer '{customer_id}' in window {window}.")
    return {"window": window, "as_of": res["as_of"], "customer": match}


@router.post("/recompute")
def recompute(req: RecomputeRequest):
    cfg = DEFAULT_CONFIG.with_overrides(req.overrides)
    with db.lock():
        con = _con()
        out = credit.recompute_and_persist(con, cfg, SCORING_DEFAULT)
        _CACHE.clear()
    return out
