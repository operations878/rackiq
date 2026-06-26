"""/api/margin/* — the Phase-2 margin layer + the re-uploadable price/cost Data Studio source.

Ranks the book by VALUE (not volume), marks the forward-fixed book to market, and exposes the
margin-priced gap helper Phase-3's hedge calls. Live-computed over the shared connection with a
data-signature cache (the heavy per-lift base is cached per ``(data-sig, window, terminal)`` so the
roll-ups / MTM / gap re-derive cheaply). Self-describes availability + coverage rather than going
through the canonical field matrix (its sell/cost stores aren't canonical fields).

Capability-honest: when there is no sell source AND/OR no cost source the payload returns
``available: false`` with the missing pieces (the lock).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from .. import db, margin, pricegrid, schema
from ..margin_config import DEFAULT_CONFIG, MarginConfig

router = APIRouter(prefix="/api/margin")

_SAMPLE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                           "sample_data", "deals")
_CACHE: dict = {}


def _con():
    return db.get_shared_connection()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _data_sig(con) -> tuple:
    pricegrid.ensure_tables(con)
    counts = pricegrid.store_counts(con)
    return (db.row_count(con, schema.LIFTS), counts["price_grid_rows"], counts["landed_cost_trips"],
            str(db.get_meta(con, "last_import_at")), str(db.get_meta(con, "last_price_import_at")),
            str(db.get_meta(con, "last_deal_import_at")), str(db.get_meta(con, "profile")),
            __import__("datetime").date.today().isoformat())


def _payload(con, cfg: MarginConfig, window: str, terminal: str | None) -> dict:
    sig = (_data_sig(con), json.dumps(cfg.to_dict(), sort_keys=True, default=str), window, terminal or "")
    if sig not in _CACHE:
        _CACHE.clear()
        _CACHE[sig] = margin.compute_margin(con, cfg, window, terminal)
    return _CACHE[sig]


def _check_window(window: str) -> str:
    if window not in margin.WINDOWS:
        raise HTTPException(status_code=400, detail=f"window must be one of {margin.WINDOWS}.")
    return window


# ---- read endpoints --------------------------------------------------------------
@router.get("")
def margin_view(terminal: str | None = Query(default=None), window: str = Query(default="all")):
    """The full margin payload: coverage, plausibility, customer/product/terminal roll-ups (margin
    vs volume contrast), deal-type margins, and the forward mark-to-market."""
    window = _check_window(window)
    with db.lock():
        return _payload(_con(), DEFAULT_CONFIG, window, terminal)


@router.get("/customers")
def customers(terminal: str | None = Query(default=None), window: str = Query(default="all")):
    window = _check_window(window)
    with db.lock():
        p = _payload(_con(), DEFAULT_CONFIG, window, terminal)
        if not p["available"]:
            return p
        return {"window": window, "terminal": terminal, "as_of": p["as_of"], "available": True,
                "customers": p["customers"], "value_vs_volume": p["value_vs_volume"],
                "coverage": p["coverage"]}


@router.get("/mtm")
def mtm(window: str = Query(default="all")):
    """Forward-fixed mark-to-market on the open committed book."""
    with db.lock():
        con = _con()
        base = margin.build_base(con, DEFAULT_CONFIG, "all", None)
        return margin.forward_mtm(con, base if base["available"] else None, DEFAULT_CONFIG)


@router.get("/coverage")
def coverage(terminal: str | None = Query(default=None), window: str = Query(default="all")):
    window = _check_window(window)
    with db.lock():
        p = _payload(_con(), DEFAULT_CONFIG, window, terminal)
        if not p["available"]:
            return {"available": False, "availability": p["availability"]}
        return {"available": True, "coverage": p["coverage"], "plausibility": p["plausibility"],
                "worked_example": p["worked_example"]}


@router.get("/gap")
def gap(terminal: str | None = Query(default=None), product: str | None = Query(default=None),
        quantity: float = Query(..., description="demand quantity in gallons")):
    """Price a demand gap: $ margin at stake split into committed/must-serve vs spot upside.
    This is the HTTP face of the helper Phase-3's hedge calls in-process (``margin.margin_for_gap``)."""
    with db.lock():
        return margin.margin_for_gap(_con(), terminal, product, quantity, DEFAULT_CONFIG)


@router.get("/unmapped-customers")
def unmapped_customers():
    """Raw grid customer names not yet resolved to a crosswalk master (feed the name-map panel)."""
    with db.lock():
        return {"unmapped": pricegrid.unmapped_grid_customers(_con())}


@router.get("/config")
def config():
    return {"config": DEFAULT_CONFIG.to_dict(), "windows": margin.WINDOWS}


class RecomputeRequest(BaseModel):
    overrides: dict | None = Field(default=None)
    window: str = "all"
    terminal: str | None = None


@router.post("/recompute")
def recompute(req: RecomputeRequest):
    """Recompute with config overrides (busts the cache) — tune the cost-basis window, units
    heuristics, the plausibility gate, or the term basis assumption without a code change."""
    cfg = DEFAULT_CONFIG.with_overrides(req.overrides)
    window = req.window if req.window in margin.WINDOWS else "all"
    with db.lock():
        con = _con()
        _CACHE.clear()
        return margin.compute_margin(con, cfg, window, req.terminal)


# ---- the re-uploadable price/cost Data Studio source -----------------------------
def _detect_kind(filename: str, path: str) -> str | None:
    n = (filename or "").lower()
    if "trip" in n:
        return "trips"
    if "wholesale" in n or "price" in n or "cost" in n:
        return "prices"
    # fall back to content: a workbook with Matrix/Benchmarks/terminal sheets ⇒ prices
    try:
        sheets = [s.lower() for s in pricegrid._sheet_names(path)]
        if any(s == "matrix" or "benchmark" in s for s in sheets):
            return "prices"
    except Exception:
        return None
    return None


@router.post("/upload")
async def upload(file: UploadFile = File(...), kind: str | None = Form(default=None)):
    """Re-uploadable price/cost source. ``kind`` is prices|trips (auto-detected if omitted).
    Idempotent: upserts on a stable key, so re-running a file never double-counts."""
    content = await file.read()
    suffix = os.path.splitext(file.filename or "prices.xlsx")[1] or ".xlsx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        path = tmp.name
    try:
        k = kind or _detect_kind(file.filename, path)
        if k not in ("prices", "trips"):
            raise HTTPException(status_code=400, detail="Could not detect kind — pass kind=prices|trips.")
        with db.lock():
            con = _con()
            if k == "prices":
                res = pricegrid.load_price_grid_file(con, path, _now())
            else:
                res = pricegrid.load_trips_file(con, path, _now())
            db.set_meta(con, "last_price_import_at", _now())   # bust the margin cache
            db.log_import(con, _now(), f"margin:{k}", file.filename or k, res.get(
                "prices_written", res.get("trips_written", 0)), "upsert")
            _CACHE.clear()
            res["kind"] = k
            res["stores"] = pricegrid.store_counts(con)
            res["availability"] = margin.availability(con)
        return res
    finally:
        os.unlink(path)


@router.post("/load-samples")
def load_samples():
    """Load the wholesale price workbook + Trips report from sample_data/deals/ (dev convenience)."""
    if not os.path.isdir(_SAMPLE_DIR):
        raise HTTPException(status_code=404, detail=f"no sample dir at {_SAMPLE_DIR}")
    with db.lock():
        con = _con()
        report = pricegrid.load_price_book(con, _SAMPLE_DIR, _now())
        db.set_meta(con, "last_price_import_at", _now())
        _CACHE.clear()
        report["stores"] = pricegrid.store_counts(con)
    return report
