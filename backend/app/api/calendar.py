"""Working-day calendar API — the measured day-of-week rhythm, the Saturday weights, and what the
day-type model excludes (Sundays / holidays).

Live-computed over the shared connection with a small in-process cache keyed by the data signature
+ config + the current date (the upcoming-exclusions list rolls at the day boundary).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import calendar_days, db

router = APIRouter(prefix="/api/calendar")


def _con():
    return db.get_shared_connection()


_CACHE: dict = {}


def _data_sig(con) -> tuple:
    return (db.row_count(con, "lifts"), str(db.get_meta(con, "last_import_at")),
            str(db.get_meta(con, "generated_at")), str(db.get_meta(con, "profile")))


def _payload(con, cfg: calendar_days.CalendarConfig) -> dict:
    cal, rhythm = calendar_days.from_connection(con, cfg)
    today = pd.Timestamp(date.today())
    net = rhythm.get("network")
    span = None
    if net:
        span = (net["first_lift"], net["last_lift"])
    sat_weights = {"network": (net or {}).get("saturday_weight")}
    sat_weights.update({t: r["saturday_weight"] for t, r in rhythm.get("terminals", {}).items()})
    return {
        "available": bool(net),
        "config": cfg.to_dict(),
        "today": str(today.date()),
        "network": net,
        "terminals": rhythm.get("terminals", {}),
        "terminal_names": sorted(rhythm.get("terminals", {}).keys()),
        "saturday_weights": sat_weights,
        "holidays_in_span": cal.holidays_in(span[0], span[1]) if span else [],
        "upcoming_exclusions": calendar_days.upcoming_exclusions(cal, today, 21),
    }


@router.get("")
def calendar():
    with db.lock():
        con = _con()
        sig = (_data_sig(con), tuple(sorted(calendar_days.DEFAULT_CONFIG.to_dict().items())),
               date.today().isoformat())
        if sig not in _CACHE:
            _CACHE.clear()
            _CACHE[sig] = _payload(con, calendar_days.DEFAULT_CONFIG)
        return _CACHE[sig]


@router.get("/config")
def config():
    return {"config": calendar_days.DEFAULT_CONFIG.to_dict()}


class RecomputeRequest(BaseModel):
    overrides: dict | None = Field(default=None)


@router.post("/recompute")
def recompute(req: RecomputeRequest):
    cfg = calendar_days.DEFAULT_CONFIG.with_overrides(req.overrides)
    with db.lock():
        _CACHE.clear()
        return _payload(_con(), cfg)
