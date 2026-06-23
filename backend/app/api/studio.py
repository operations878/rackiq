"""Data Studio API — upload, column-mapping, validation, commit, and saved profiles.

This is the write side of RackIQ. Unlike the read endpoints (which can run against a
read-only connection), Data Studio mutates the store while the server is live, so it uses
the shared read/write connection guarded by ``db.lock()``. The flow is:

    inspect (file)  ->  validate (mapping)  ->  commit (mapping)

On commit we coerce the mapped columns, hand the frame to the Hygiene Studio pipeline,
write the canonical table, derive the customers dimension (for lifts), and recompute the
capability matrix from the fields actually present.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .. import capabilities, db, generator, hygiene, ingest, schema
from . import queries

router = APIRouter(prefix="/api/studio")


def _con():
    return db.get_shared_connection()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- Request bodies -------------------------------------------------------------
class ValidateRequest(BaseModel):
    upload_id: str
    table: str
    mapping: dict[str, str]


class CommitRequest(BaseModel):
    upload_id: str
    table: str
    mapping: dict[str, str]
    mode: str = "replace"                       # "replace" | "append"
    save_profile: str | None = None


class SaveProfileRequest(BaseModel):
    name: str
    table: str
    mapping: dict[str, str]
    source_columns: list[str] = Field(default_factory=list)


class LoadDemoRequest(BaseModel):
    profile: str = "full"
    seed: int = 42
    n_customers: int = 40
    months: int = 21


# ---- Helpers --------------------------------------------------------------------
def _match_profile(con, source_columns: list[str]) -> dict | None:
    """Find the most recent saved profile whose source columns the file satisfies."""
    file_cols = {c for c in source_columns}
    for p in db.list_import_profiles(con):
        try:
            saved = set(json.loads(p["source_columns"] or "[]"))
        except json.JSONDecodeError:
            continue
        if saved and saved.issubset(file_cols):
            return {
                "name": p["name"],
                "target_table": p["target_table"],
                "mapping": json.loads(p["mapping"] or "{}"),
            }
    return None


def _state(con) -> dict:
    """The post-write snapshot the UI needs to refresh itself in one round-trip."""
    return {
        "summary": queries.get_summary(con),
        "capabilities": capabilities.compute_capabilities(con),
    }


# ---- Endpoints ------------------------------------------------------------------
@router.get("/targets")
def targets():
    """Static registry powering the mapping dropdowns (tables, fields, required keys)."""
    return {
        "tables": schema.IMPORTABLE_TABLES,
        "table_labels": schema.TABLE_LABELS,
        "targets_by_table": {t: schema.import_targets(t) for t in schema.IMPORTABLE_TABLES},
        "required_keys": {t: schema.required_import_keys(t) for t in schema.IMPORTABLE_TABLES},
    }


@router.post("/inspect")
async def inspect(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    try:
        df = ingest.parse_file(content, file.filename or "upload")
    except ingest.IngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    upload_id = ingest.stash_upload(df, file.filename or "upload")
    payload = ingest.inspect(df, file.filename or "upload")
    payload["upload_id"] = upload_id

    with db.lock():
        payload["matched_profile"] = _match_profile(_con(), list(df.columns))
    return payload


@router.post("/validate")
def validate(req: ValidateRequest):
    up = ingest.get_upload(req.upload_id)
    if up is None:
        raise HTTPException(status_code=404, detail="Upload expired — please re-upload the file.")
    if req.table not in schema.IMPORTABLE_TABLES:
        raise HTTPException(status_code=400, detail=f"Unknown target table '{req.table}'.")
    return ingest.validate(up.df, req.table, req.mapping)


@router.post("/commit")
def commit(req: CommitRequest):
    up = ingest.get_upload(req.upload_id)
    if up is None:
        raise HTTPException(status_code=404, detail="Upload expired — please re-upload the file.")
    if req.table not in schema.IMPORTABLE_TABLES:
        raise HTTPException(status_code=400, detail=f"Unknown target table '{req.table}'.")
    if req.mode not in ("replace", "append"):
        raise HTTPException(status_code=400, detail="mode must be 'replace' or 'append'.")

    report = ingest.validate(up.df, req.table, req.mapping)
    if not report["can_commit"]:
        raise HTTPException(status_code=422, detail={"message": "Mapping is not valid.",
                                                     "errors": report["errors"]})

    # Coerce to canonical types, then hand off to the Hygiene Studio pipeline.
    mapped, _ = ingest.build_mapped_frame(up.df, req.table, req.mapping)
    cleaned, hygiene_report = hygiene.run_pipeline(mapped, req.table)

    with db.lock():
        con = _con()
        if req.mode == "replace":
            db.truncate(con, req.table)
        rows_written = db.insert_df(con, req.table, cleaned)
        if req.table == schema.LIFTS:
            db.rebuild_customers_from_lifts(con, replace=(req.mode == "replace"))

        db.set_meta(con, "profile", "imported")
        db.set_meta(con, "last_import_at", _now())
        db.set_meta(con, "last_import_table", req.table)
        db.set_meta(con, "last_import_filename", up.filename)
        db.log_import(con, _now(), req.table, up.filename, rows_written, req.mode)

        if req.save_profile:
            db.save_import_profile(
                con, req.save_profile.strip(), req.table,
                json.dumps(req.mapping), json.dumps(list(up.df.columns)), _now())

        state = _state(con)

    return {
        "ok": True,
        "table": req.table,
        "mode": req.mode,
        "rows_written": rows_written,
        "rows_in_file": int(len(up.df)),
        "hygiene": hygiene_report,
        "saved_profile": req.save_profile or None,
        **state,
    }


@router.get("/profiles")
def list_profiles():
    with db.lock():
        rows = db.list_import_profiles(_con())
    out = []
    for r in rows:
        out.append({
            "name": r["name"],
            "target_table": r["target_table"],
            "mapping": json.loads(r["mapping"] or "{}"),
            "source_columns": json.loads(r["source_columns"] or "[]"),
            "created_at": r["created_at"],
        })
    return {"profiles": out}


@router.post("/profiles")
def save_profile(req: SaveProfileRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Profile name is required.")
    with db.lock():
        db.save_import_profile(
            _con(), name, req.table, json.dumps(req.mapping),
            json.dumps(req.source_columns), _now())
    return {"ok": True, "name": name}


@router.delete("/profiles/{name}")
def delete_profile(name: str):
    with db.lock():
        db.delete_import_profile(_con(), name)
    return {"ok": True}


@router.get("/history")
def history():
    with db.lock():
        return {"imports": db.list_import_log(_con())}


@router.post("/load-demo")
def load_demo(req: LoadDemoRequest):
    if req.profile not in ("core", "lite", "full"):
        raise HTTPException(status_code=400, detail="profile must be core, lite, or full.")
    cfg = generator.GenConfig(
        seed=req.seed, n_customers=req.n_customers, months=req.months, profile=req.profile)
    with db.lock():
        con = _con()
        generator.generate(cfg, con)
        state = _state(con)
    return {"ok": True, "profile": req.profile, **state}


@router.post("/reset")
def reset():
    with db.lock():
        con = _con()
        db.reset_data(con)
        state = _state(con)
    return {"ok": True, **state}
