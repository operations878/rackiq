"""/api/deals/* — the deal-book Data Studio source + the crosswalk bridge (the JOIN).

Re-uploadable term / forward-fixed / spot ingestion (idempotent), the deal-book → BOL-master bridge
staging (propose candidates, confirm — never auto-merge), and the match-rate readout that must be
trusted BEFORE any commitment annotation.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from .. import bookload, db, dealbook

router = APIRouter(prefix="/api/deals")

_SAMPLE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                           "sample_data", "deals")


def _con():
    return db.get_shared_connection()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@router.get("/summary")
def summary():
    con = _con()
    with db.lock():
        rows = con.execute("""
            SELECT source, count(*) AS n, count(DISTINCT customer_master) AS masters,
                   count(DISTINCT customer_raw) AS raw_customers,
                   round(sum(coalesce(committed_gallons,0))) AS committed_gal,
                   round(sum(coalesce(realized_gallons,0))) AS realized_gal,
                   min(month) AS month_min, max(month) AS month_max
            FROM deals GROUP BY 1 ORDER BY 1""").df()
    return {"sources": rows.to_dict(orient="records"),
            "total_rows": db.deals_count(con),
            "masters_resolved": int(con.execute(
                "SELECT count(DISTINCT customer_master) FROM deals WHERE customer_master IS NOT NULL"
            ).fetchone()[0])}


@router.get("/bridge")
def bridge():
    """The deal-book → BOL-master bridge: mapped / candidates / unmapped + the match rate."""
    return dealbook.bridge_candidates(_con())


class ConfirmBridgeRequest(BaseModel):
    pairs: list[tuple[str, str]]   # [(deal_raw_name, bol_master), ...]


@router.post("/bridge/confirm")
def bridge_confirm(req: ConfirmBridgeRequest):
    with db.lock():
        con = _con()
        out = dealbook.confirm_bridge(con, req.pairs, _now())
        db.set_meta(con, "last_deal_import_at", _now())   # bust variability cache
    return out


@router.post("/upload")
async def upload(file: UploadFile = File(...), source: str | None = Form(default=None)):
    """Re-uploadable Deals source. ``source`` is term|forward_fixed|spot (auto-detected if omitted).
    Idempotent: upserts on the stable deal key, so re-running a month never double-counts."""
    content = await file.read()
    suffix = os.path.splitext(file.filename or "deal.xlsx")[1] or ".xlsx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        path = tmp.name
    try:
        src = source or dealbook.detect_deal_source(path)
        if src not in dealbook.PARSERS:
            raise HTTPException(status_code=400, detail=(
                "Could not detect the deal source — pass source=term|forward_fixed|spot."))
        with db.lock():
            con = _con()
            res = bookload.load_deal_source(con, src, path, _now())
            db.log_import(con, _now(), "deals", file.filename or src, res["written"], "upsert")
        res["filename"] = file.filename
        res["bridge"] = dealbook.bridge_candidates(con)
        return res
    finally:
        os.unlink(path)


@router.post("/load-samples")
def load_samples():
    """Load the bundled real book (chart → BOLs → deals) from sample_data/deals/ — dev convenience."""
    if not os.path.isdir(_SAMPLE_DIR):
        raise HTTPException(status_code=404, detail=f"no sample dir at {_SAMPLE_DIR}")
    with db.lock():
        con = _con()
        report = bookload.load_real_book(con, _SAMPLE_DIR, _now())
    return report
