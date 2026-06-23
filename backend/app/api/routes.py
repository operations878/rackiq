"""HTTP API — read endpoints over the shared DuckDB connection.

All API access (reads here, writes in ``studio.py``) goes through the one shared
read/write connection guarded by ``db.lock()``. Data Studio mutates the store while the
server is live, so reads must see those writes — a single shared connection guarantees it.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from .. import __version__, capabilities, db
from . import queries

router = APIRouter(prefix="/api")


def _con():
    return db.get_shared_connection()


@router.get("/health")
def health():
    with db.lock():
        con = _con()
        return {"status": "ok", "version": __version__, "profile": db.get_meta(con, "profile", "empty")}


@router.get("/summary")
def summary():
    with db.lock():
        return queries.get_summary(_con())


@router.get("/schema")
def schema_endpoint():
    with db.lock():
        return queries.get_schema(_con())


@router.get("/capabilities")
def capabilities_endpoint():
    with db.lock():
        return capabilities.compute_capabilities(_con())


@router.get("/customers")
def customers():
    with db.lock():
        return queries.get_customers(_con())


@router.get("/market-prices")
def market_prices(product: str | None = Query(default=None)):
    with db.lock():
        return queries.get_market_prices(_con(), product)


@router.get("/monthly-volume")
def monthly_volume():
    with db.lock():
        return queries.get_monthly_volume(_con())
