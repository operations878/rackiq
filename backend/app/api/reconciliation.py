"""Reconciliation & loss-control API — book-vs-physical gain/loss, net-recon, meter drift.

Live-computes over the shared connection with a small in-process cache keyed by the data
signature + config + period grain. Capability-gated: when physical inventory / receipt detail
are absent the payload returns ``available: false`` with the missing feeds for the UI lock.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db, reconciliation, schema
from ..reconciliation_config import DEFAULT_CONFIG, PERIOD_GRAINS, ReconConfig

router = APIRouter(prefix="/api/reconciliation")


def _con():
    return db.get_shared_connection()


_CACHE: dict = {}


def _data_sig(con) -> tuple:
    return (db.row_count(con, schema.INVENTORY), db.row_count(con, schema.BOL),
            db.row_count(con, schema.RECEIPTS), str(db.get_meta(con, "last_import_at")),
            str(db.get_meta(con, "generated_at")), str(db.get_meta(con, "profile")))


def _result(con, cfg: ReconConfig, period: str) -> dict:
    sig = (_data_sig(con), tuple(sorted(cfg.to_dict().items())), period)
    if sig not in _CACHE:
        _CACHE.clear()
        _CACHE[sig] = reconciliation.compute_reconciliation(con, cfg, period)
    return _CACHE[sig]


class RecomputeRequest(BaseModel):
    overrides: dict | None = Field(default=None)
    period: str | None = None


@router.get("")
def reconcile(period: str = Query(default=DEFAULT_CONFIG.period_grain)):
    if period not in PERIOD_GRAINS:
        raise HTTPException(status_code=400, detail=f"period must be one of {PERIOD_GRAINS}.")
    with db.lock():
        return _result(_con(), DEFAULT_CONFIG, period)


@router.get("/config")
def config():
    return {"config": DEFAULT_CONFIG.to_dict(), "period_grains": PERIOD_GRAINS}


@router.post("/recompute")
def recompute(req: RecomputeRequest):
    cfg = DEFAULT_CONFIG.with_overrides(req.overrides)
    period = req.period if req.period in PERIOD_GRAINS else cfg.period_grain
    with db.lock():
        _CACHE.clear()
        return reconciliation.compute_reconciliation(_con(), cfg, period)
