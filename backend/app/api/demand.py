"""Demand Cockpit API — the per-terminal operating forecast.

``/api/demand/cockpit`` returns the per-terminal P10/P50/P90 forecast band, days-of-cover and
burn-down (capability-gated on inventory), the recommended buy action at a chosen service level,
and the forecast-accuracy strip. The *heavy* half (forecasts + band + inventory) is cached per
``(data signature, terminal, product, window)`` so the service-level / lead-time / lot-size
slider re-derives only the cheap recommended action — the cockpit stays snappy.

``/api/demand/persist`` writes the per-customer and terminal forecast distributions to the
``demand_forecast_customer`` / ``demand_forecast_terminal`` caches (the P6/P7/P10 read contract);
``/api/demand/forecasts`` reads them back.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from .. import db, demand, schema
from ..demand import DEFAULT_CONFIG

router = APIRouter(prefix="/api/demand")


def _con():
    return db.get_shared_connection()


# ---- live-compute cache for the heavy (service-level-independent) half -----------
_CACHE: dict = {}


def _data_sig(con) -> tuple:
    return (db.row_count(con, schema.LIFTS), db.row_count(con, "inventory_snapshots"),
            str(db.get_meta(con, "last_import_at")), str(db.get_meta(con, "generated_at")),
            str(db.get_meta(con, "profile")))


def _norm_product(product: str | None) -> str | None:
    if product in (None, "", demand.ALL_PRODUCTS):
        return None
    return product


def _heavy(con, terminal: str | None, product: str | None, window: str) -> dict:
    sig = _data_sig(con)
    bucket = _CACHE.get(sig)
    if bucket is None:
        _CACHE.clear()
        bucket = {}
        _CACHE[sig] = bucket
    key = (terminal or "", product or "", window)
    if key not in bucket:
        bucket[key] = demand.forecast_terminal(con, terminal, product, window)
    return bucket[key]


def _bust():
    _CACHE.clear()


@router.get("/config")
def config():
    return {"config": DEFAULT_CONFIG.to_dict()}


@router.get("/cockpit")
def cockpit(terminal: str | None = Query(default=None),
            product: str | None = Query(default=None),
            window: str = Query(default="all"),
            service_level: float = Query(default=DEFAULT_CONFIG.default_service_level, ge=0.5, le=0.999),
            lead_time_days: float = Query(default=DEFAULT_CONFIG.default_lead_time_days, ge=0.0),
            lot_size: float | None = Query(default=None, ge=0.0)):
    prod = _norm_product(product)
    lot = lot_size if (lot_size and lot_size > 0) else None
    with db.lock():
        con = _con()
        demand.ensure_tables(con)
        heavy = _heavy(con, terminal, prod, window)
        rec = demand.recommend(heavy, DEFAULT_CONFIG, service_level=service_level,
                               lead_time_days=lead_time_days, lot_size=lot)
    return {**heavy, "recommendation": rec,
            "inputs": {"service_level": service_level, "lead_time_days": lead_time_days,
                       "lot_size": lot}}


class PersistRequest(BaseModel):
    window: str = "all"
    overrides: dict | None = Field(default=None)


@router.post("/persist")
def persist(req: PersistRequest):
    cfg = DEFAULT_CONFIG.with_overrides(req.overrides)
    with db.lock():
        con = _con()
        out = demand.persist(con, window=req.window, cfg=cfg)
        _bust()
    return out


@router.get("/forecasts")
def forecasts(terminal: str | None = Query(default=None),
              product: str | None = Query(default=None),
              level: str = Query(default="terminal"),
              window: str | None = Query(default=None)):
    """Read back the persisted forecast distributions (the P6/P7/P10 read path)."""
    level = "customer" if level == "customer" else "terminal"
    with db.lock():
        con = _con()
        return demand.read_forecasts(con, terminal=terminal, product=_norm_product(product),
                                     level=level, window=window)
