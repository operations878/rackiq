"""HTTP API — thin endpoints that open a read-only DuckDB connection per request."""

from __future__ import annotations

from fastapi import APIRouter, Query

from .. import __version__, capabilities, db
from . import queries

router = APIRouter(prefix="/api")


def _con():
    return db.get_connection(read_only=True)


@router.get("/health")
def health():
    con = _con()
    try:
        return {"status": "ok", "version": __version__, "profile": db.get_meta(con, "profile", "unknown")}
    finally:
        con.close()


@router.get("/summary")
def summary():
    con = _con()
    try:
        return queries.get_summary(con)
    finally:
        con.close()


@router.get("/schema")
def schema_endpoint():
    con = _con()
    try:
        return queries.get_schema(con)
    finally:
        con.close()


@router.get("/capabilities")
def capabilities_endpoint():
    con = _con()
    try:
        return capabilities.compute_capabilities(con)
    finally:
        con.close()


@router.get("/customers")
def customers():
    con = _con()
    try:
        return queries.get_customers(con)
    finally:
        con.close()


@router.get("/market-prices")
def market_prices(product: str | None = Query(default=None)):
    con = _con()
    try:
        return queries.get_market_prices(con, product)
    finally:
        con.close()


@router.get("/monthly-volume")
def monthly_volume():
    con = _con()
    try:
        return queries.get_monthly_volume(con)
    finally:
        con.close()
