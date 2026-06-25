"""Operational demand-hedging API — the per-terminal morning staging readout.

The heavy per-customer scoring (forecast + behavior + working-day cadence/recency) is computed once
and cached per ``(data-signature, window, date)`` so the **service-level slider** and **terminal**
selector re-derive only the cheap aggregation. The calendar (and thus the day boundary) is part of
the signature via ``date.today()``.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import calendar_days, db, hedging, scoring
from ..scoring_config import DEFAULT_CONFIG as SCORING_DEFAULT
from ..scoring_config import WINDOWS

router = APIRouter(prefix="/api/hedging")


def _con():
    return db.get_shared_connection()


# Heavy scoring cache (shared across terminals + service levels for one data/window/day).
_SCORE_CACHE: dict = {}


def _data_sig(con) -> tuple:
    return (db.row_count(con, "lifts"), str(db.get_meta(con, "last_import_at")),
            str(db.get_meta(con, "generated_at")), str(db.get_meta(con, "profile")))


def _scored(con, window: str):
    sig = (_data_sig(con), window, date.today().isoformat())
    if sig not in _SCORE_CACHE:
        _SCORE_CACHE.clear()
        cal, _ = calendar_days.from_connection(con, calendar_days.DEFAULT_CONFIG)
        score_res = scoring.compute_scores(con, SCORING_DEFAULT, window)
        last_by_id = hedging._last_lifts(con)
        _SCORE_CACHE[sig] = (score_res, cal, last_by_id)
    return _SCORE_CACHE[sig]


@router.get("")
def hedging_get(terminal: str | None = Query(default=None),
                window: str = Query(default="all"),
                service_level: float = Query(default=0.90)):
    if window not in WINDOWS:
        raise HTTPException(status_code=400, detail=f"window must be one of {WINDOWS}.")
    with db.lock():
        con = _con()
        score_res, cal, last_by_id = _scored(con, window)
        return hedging.compute_hedging(con, terminal, window, service_level,
                                       score_res=score_res, cal=cal, last_by_id=last_by_id)


@router.get("/overview")
def overview(window: str = Query(default="all"), service_level: float = Query(default=0.90)):
    if window not in WINDOWS:
        raise HTTPException(status_code=400, detail=f"window must be one of {WINDOWS}.")
    with db.lock():
        con = _con()
        score_res, cal, last_by_id = _scored(con, window)
        terminals = hedging._terminals(con) or [None]
        readouts = [hedging.compute_hedging(con, t, window, service_level, score_res=score_res,
                                            cal=cal, last_by_id=last_by_id) for t in terminals]
        return {"window": window, "as_of": score_res.get("as_of"),
                "forecast_anchor": score_res.get("forecast_anchor"),
                "terminals": [t for t in terminals if t], "readouts": readouts}


@router.get("/config")
def config():
    return {"config": hedging.DEFAULT_CONFIG.to_dict(), "windows": WINDOWS}


class RecomputeRequest(BaseModel):
    overrides: dict | None = Field(default=None)
    window: str | None = None
    terminal: str | None = None
    service_level: float | None = None


@router.post("/recompute")
def recompute(req: RecomputeRequest):
    hcfg = hedging.DEFAULT_CONFIG.with_overrides(req.overrides)
    window = req.window if req.window in WINDOWS else "all"
    with db.lock():
        _SCORE_CACHE.clear()
        con = _con()
        return hedging.compute_hedging(con, req.terminal, window, req.service_level, hcfg=hcfg)
