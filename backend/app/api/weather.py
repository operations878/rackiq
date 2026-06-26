"""/api/weather/* — the HDD (Heating Degree Day) Data Studio source + the Stage-1 weather model.

Stage 0 (this file's ingestion endpoints): the HDD book — observed degree-days + Normal/5-yr/10-yr
baselines + the BX HO SOLD anchor — becomes a **re-uploadable, idempotent** Data Studio source, the
same UX as the Deals / Prices uploads.

Stage 1 (the model endpoints): the HDD→demand regression per terminal × heating-product, the BX HO
SOLD β anchor, the station coverage map (modeled / proxy / excluded), and the raw-vs-weather-adjusted
size-axis comparison that the variability score consumes. Live-computed over the shared connection
with a small data-signature cache.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, UploadFile

from .. import db, weather_hdd, weather_model

router = APIRouter(prefix="/api/weather")

_SAMPLE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                           "sample_data", "deals")
_CACHE: dict = {}


def _con():
    return db.get_shared_connection()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- Stage 0: the re-uploadable HDD source --------------------------------------
@router.get("/hdd/summary")
def hdd_summary():
    with db.lock():
        con = _con()
        weather_hdd.ensure_tables(con)
        return {"stores": weather_hdd.store_counts(con)}


@router.post("/hdd/upload")
async def hdd_upload(file: UploadFile = File(...)):
    """Re-uploadable HDD source. Parses the 'HDD'S' sheet (empirical header/axis detection),
    lands station × day → HDD + baselines + the BX HO SOLD anchor. Idempotent on (station, day)."""
    content = await file.read()
    suffix = os.path.splitext(file.filename or "hdd.xlsx")[1] or ".xlsx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        path = tmp.name
    try:
        with db.lock():
            con = _con()
            res = weather_hdd.load_hdd_file(con, path, _now())
            if res["observations_written"] == 0:
                raise HTTPException(status_code=400, detail=(
                    "No HDD rows parsed — " + str(res["diagnostics"].get("error", "check the file"))
                    + ". Expected an 'HDD'S' sheet with a date axis and HDD/year columns."))
            db.set_meta(con, "last_hdd_import_at", _now())   # bust the weather-model cache
            db.log_import(con, _now(), "weather:hdd", file.filename or "hdd",
                          res["observations_written"], "upsert")
            _CACHE.clear()
            res["stores"] = weather_hdd.store_counts(con)
        return res
    finally:
        os.unlink(path)


@router.post("/hdd/load-samples")
def hdd_load_samples():
    """Load any HDD workbook found in sample_data/deals/ (dev convenience)."""
    import glob
    if not os.path.isdir(_SAMPLE_DIR):
        raise HTTPException(status_code=404, detail=f"no sample dir at {_SAMPLE_DIR}")
    with db.lock():
        con = _con()
        loaded = []
        for p in sorted(glob.glob(os.path.join(_SAMPLE_DIR, "*"))):
            base = os.path.basename(p).lower()
            if base.endswith((".xlsx", ".xlsm")) and ("hdd" in base or "demand_scenario" in base
                                                      or "forecaster" in base):
                res = weather_hdd.load_hdd_file(con, p, _now())
                if res["observations_written"]:
                    loaded.append(res)
        if loaded:
            db.set_meta(con, "last_hdd_import_at", _now())
            _CACHE.clear()
        return {"loaded": loaded, "stores": weather_hdd.store_counts(con)}


# ---- Stage 1: the weather model ---------------------------------------------------
def _sig(con) -> tuple:
    weather_hdd.ensure_tables(con)
    return (db.row_count(con, "lifts"), str(db.get_meta(con, "last_import_at")),
            str(db.get_meta(con, "last_hdd_import_at")),
            weather_hdd.store_counts(con)["hdd_observations"])


def _model(con):
    sig = _sig(con)
    if _CACHE.get("sig") != sig:
        _CACHE.clear()
        _CACHE["sig"] = sig
        _CACHE["model"] = weather_model.build_model(con)
    return _CACHE["model"]


@router.get("")
def weather_view():
    """The Stage-1 weather readout: station coverage, per terminal×heating-product β/baseline/R²/OOS,
    the BX HO SOLD anchor agreement, and the raw-vs-weather-adjusted size-axis comparison."""
    with db.lock():
        return weather_model.readout(_con(), _model(_con()))


@router.get("/config")
def weather_config():
    from dataclasses import asdict
    return asdict(weather_model.DEFAULT_WEATHER_CONFIG)
