"""/api/position/* — Phase-7 net position & days-of-cover + the re-uploadable Trips supply source.

Per terminal × product (family): a running net position (gauge-anchored where a verified inventory
snapshot exists, else a net-flow proxy — both honestly labeled), days-of-cover in WORKING days, the
drawdown trend, and a "nominate a barge" cure when cover runs short. Shaped as a **facet-ready
summary** for the converged terminal view (each cell carries a plain-English ``facet`` tile).

Live-computed over the shared connection with a data-signature cache (``date.today()`` is in the
signature so the cover anchor re-rolls at the day boundary). The INBOUND barge-supply source (the
Trips report) is re-uploadable here exactly like the deal book / price-cost sources — idempotent on a
stable key, surviving reset.

Validated on SYNTHETIC data (the real Trips .xls is local-only / gitignored): on the demo ``full``
book inbound comes from the canonical ``receipts`` table and the gauge from
``inventory_snapshots.physical_inventory``. Real-book confirmation is a separate local run.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from .. import barges, db, hedging, schema

router = APIRouter(prefix="/api/position")

_SAMPLE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                           "sample_data", "deals")
_CACHE: dict = {}


def _con():
    return db.get_shared_connection()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _data_sig(con) -> tuple:
    barges.ensure_tables(con)
    n_barges = int(con.execute("SELECT count(*) FROM barge_discharges").fetchone()[0])
    return (db.row_count(con, schema.LIFTS), db.row_count(con, schema.INVENTORY),
            db.row_count(con, schema.RECEIPTS), n_barges,
            str(db.get_meta(con, "last_import_at")), str(db.get_meta(con, "last_barge_import_at")),
            str(db.get_meta(con, "profile")), date.today().isoformat())


def _payload(con, cfg: hedging.PositionConfig, terminal: str | None, product: str | None) -> dict:
    sig = (_data_sig(con), json.dumps(cfg.to_dict(), sort_keys=True), terminal or "", product or "")
    if sig not in _CACHE:
        _CACHE.clear()
        _CACHE[sig] = hedging.compute_position(con, terminal, product, pcfg=cfg)
    return _CACHE[sig]


# ---- read endpoints --------------------------------------------------------------
@router.get("")
def position(terminal: str | None = Query(default=None),
             product: str | None = Query(default=None)):
    """The full position readout: per terminal×product mode/position/cover/trend/cure + facet tiles."""
    with db.lock():
        return _payload(_con(), hedging.DEFAULT_POSITION_CONFIG, terminal, product)


@router.get("/summary")
def summary():
    """Inbound barge-supply store counts + which inbound source the engine is currently using."""
    with db.lock():
        con = _con()
        _cells, src, label = hedging._inbound_flows(con)
        return {"stores": barges.store_counts(con),
                "inbound_source": src, "inbound_source_label": label,
                "last_barge_import_at": db.get_meta(con, "last_barge_import_at")}


@router.get("/config")
def config():
    return {"config": hedging.DEFAULT_POSITION_CONFIG.to_dict()}


class RecomputeRequest(BaseModel):
    overrides: dict | None = Field(default=None)
    terminal: str | None = None
    product: str | None = None


@router.post("/recompute")
def recompute(req: RecomputeRequest):
    """Recompute with config overrides (busts the cache) — tune cover thresholds / lookback / the
    nominate-a-barge target without a code change."""
    cfg = hedging.DEFAULT_POSITION_CONFIG.with_overrides(req.overrides)
    with db.lock():
        _CACHE.clear()
        return hedging.compute_position(_con(), req.terminal, req.product, pcfg=cfg)


# ---- the re-uploadable INBOUND Trips supply source (Data Studio) ------------------
@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    """Re-uploadable Trips report (inbound barge supply). Idempotent: upserts on a stable key, so
    re-running a file never double-counts. Volumes are read in BARRELS and converted to gallons
    (×42) exactly once during the parse; the response carries the conversion audit."""
    content = await file.read()
    suffix = os.path.splitext(file.filename or "trips.xls")[1] or ".xls"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        path = tmp.name
    try:
        with db.lock():
            con = _con()
            res = barges.load_trips_supply_file(con, path, _now())
            if res["discharges_written"]:
                db.set_meta(con, "last_barge_import_at", _now())   # bust the position cache
            db.log_import(con, _now(), "position:trips", file.filename or "trips",
                          res["discharges_written"], "upsert")
            _CACHE.clear()
            res["stores"] = barges.store_counts(con)
        return res
    finally:
        os.unlink(path)


@router.post("/load-samples")
def load_samples():
    """Load any Trips report found in sample_data/deals/ into the barge-supply store (dev convenience).

    The cloud DB is synthetic and ships no Trips file, so this is a no-op there; the position engine
    falls back to the canonical receipts/inventory for inbound supply."""
    if not os.path.isdir(_SAMPLE_DIR):
        raise HTTPException(status_code=404, detail=f"no sample dir at {_SAMPLE_DIR}")
    with db.lock():
        con = _con()
        report = barges.load_barges_dir(con, _SAMPLE_DIR, _now())
        if report["stores"]["barge_discharges"]:
            db.set_meta(con, "last_barge_import_at", _now())
        _CACHE.clear()
    return report
