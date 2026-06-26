"""/api/opportunity/* — the Phase-6 MODELED missing-volume / opportunity layer.

The real modeled engine behind the convergence layer's interim opportunity tile (which rides the
channel-mismatch volume). Per master customer × product family it estimates the demand GAP (peak ≈
wallet, MODELED — never measured), filters it for winnability (shrunk vs under-served), and ranks the
opportunity three ways (raw gallons · gap × margin · gap × winnability). Each row is tagged spot/rack
via the reused Phase-1 quadrant; margin is ranking-only.

Live-computed over the shared connection with a data-signature cache busted by a BOL load, a deal/price
upload, a weather (HDD) upload, or the day boundary — the same signals every reused engine caches on.
The per-customer ``facet`` is a drop-in superset of ``api/profile._opportunity`` so the fan-out can pull
this and the interim tile can swap data source without a redesign.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, HTTPException

from .. import db, opportunity, pricegrid, schema

router = APIRouter(prefix="/api/opportunity")

_CACHE: dict = {}


def _con():
    return db.get_shared_connection()


def _sig(con) -> tuple:
    """Bust when lifts / deals / prices / weather / the day change — the union of every reused engine's
    inputs (variability + margin + weather)."""
    pricegrid.ensure_tables(con)
    counts = pricegrid.store_counts(con)
    return (db.row_count(con, schema.LIFTS), db.deals_count(con),
            counts["price_grid_rows"], counts["landed_cost_trips"],
            str(db.get_meta(con, "last_import_at")), str(db.get_meta(con, "last_deal_import_at")),
            str(db.get_meta(con, "last_price_import_at")), str(db.get_meta(con, "last_weather_import_at")),
            str(date.today()))


def _result(con) -> dict:
    sig = _sig(con)
    if _CACHE.get("sig") != sig:
        _CACHE.clear()
        _CACHE["sig"] = sig
        _CACHE["full"] = opportunity.compute_opportunity(con)
    return _CACHE["full"]


@router.get("")
def opportunity_all():
    with db.lock():
        return _result(_con())


@router.get("/rankings")
def rankings():
    """The three ranked worklists + the headline summary (lighter than the full per-customer payload)."""
    with db.lock():
        res = _result(_con())
        return {"available": res.get("available"), "as_of": res.get("as_of"),
                "premise": res.get("premise"), "summary": res.get("summary"),
                "margin": res.get("margin"), "weather": res.get("weather"),
                "rankings": res.get("rankings", {})}


@router.get("/validation")
def validation():
    """The gut-check gate (synthetic-data honest; real-book is a separate local run)."""
    with db.lock():
        return opportunity.validation_readout(_con())


@router.get("/config")
def config():
    return asdict(opportunity.DEFAULT_OPP_CONFIG)


@router.get("/customer/{customer_id}")
def customer(customer_id: str):
    with db.lock():
        res = _result(_con())
        if not res.get("available"):
            return {"available": False, "reason": res.get("reason"), "premise": res.get("premise")}
        row = next((c for c in res["customers"] if c["customer_id"] == customer_id), None)
        if row is None:
            raise HTTPException(status_code=404, detail=f"customer '{customer_id}' not found")
        return {"available": True, "as_of": res["as_of"], "premise": res["premise"], "customer": row}
