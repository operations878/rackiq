"""Data Studio + Data Hygiene Studio API — upload, profile, map, clean, validate, commit.

This is the write side of RackIQ. It mutates the store while the server is live, so it uses
the shared read/write connection guarded by ``db.lock()``. The full flow is:

    inspect (file + profiling)
      -> crosswalk/propose + crosswalk/confirm   (Customer Master de-duplication)
      -> validate (apply fixes -> run rules)      (drill-down preview + quarantine preview)
      -> commit (apply fixes -> rules -> quarantine split -> write -> audit)

On commit we coerce the mapped columns, apply the approved hygiene fixes (trim, units, net-60
correction, default fill, crosswalk resolution), run the validation rule engine, divert failing
rows to quarantine (never silently dropped), write the clean rows, derive the customers
dimension, log every transformation to the audit table, and recompute the capability matrix.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .. import capabilities, crosswalk, data_health, db, generator, hygiene, ingest, schema, validation
from . import queries

router = APIRouter(prefix="/api/studio")


def _con():
    return db.get_shared_connection()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_us() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- Request bodies -------------------------------------------------------------
class ValidateRequest(BaseModel):
    upload_id: str
    table: str
    mapping: dict[str, str]
    options: dict | None = None


class CommitRequest(BaseModel):
    upload_id: str
    table: str
    mapping: dict[str, str]
    mode: str = "replace"                       # "replace" | "append"
    save_profile: str | None = None
    options: dict | None = None


class SaveProfileRequest(BaseModel):
    name: str
    table: str
    mapping: dict[str, str]
    source_columns: list[str] = Field(default_factory=list)
    hygiene: dict | None = None


class ProposeRequest(BaseModel):
    upload_id: str
    table: str
    mapping: dict[str, str]
    name_source: str | None = None
    threshold: float = crosswalk.DEFAULT_THRESHOLD


class ConfirmRequest(BaseModel):
    groups: list[dict] = Field(default_factory=list)
    rejected_keys: list[str] = Field(default_factory=list)


class QuarantineRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)
    edits: dict[str, dict] = Field(default_factory=dict)


class LoadDemoRequest(BaseModel):
    profile: str = "full"
    seed: int = 42
    n_customers: int = 40
    months: int = 21


class RackBenchmarkEntry(BaseModel):
    price_date: str
    terminal: str
    product: str
    rack_benchmark: float


class RackBenchmarkRequest(BaseModel):
    entries: list[RackBenchmarkEntry] = Field(default_factory=list)


class QuoteEntry(BaseModel):
    customer_id: str
    quote_time: str
    product: str
    quoted_price: float
    outcome: str                              # accept | reject | no_response
    market_price_at_quote: float | None = None
    inventory_state: str | None = None
    capacity_state: str | None = None
    competitor_context: str | None = None
    time_to_decision: float | None = None
    final_gallons: float | None = None


class QuoteRequest(BaseModel):
    entries: list[QuoteEntry] = Field(default_factory=list)


# ---- Helpers --------------------------------------------------------------------
def _match_profile(con, source_columns: list[str]) -> dict | None:
    """Find the most recent saved profile whose source columns the file satisfies."""
    file_cols = set(source_columns)
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
                "hygiene": json.loads(p["hygiene"]) if p.get("hygiene") else None,
            }
    return None


def _state(con) -> dict:
    """The post-write snapshot the UI needs to refresh itself in one round-trip."""
    return {
        "summary": queries.get_summary(con),
        "capabilities": capabilities.compute_capabilities(con),
    }


def _date_targets(table: str) -> set[str]:
    out = {f.name for f in schema.CANONICAL_FIELDS
           if f.table == table and f.dtype in (schema.DType.DATE, schema.DType.TIMESTAMP)}
    out |= {n for n, dt in schema.STRUCTURAL_COLUMNS.get(table, [])
            if dt in (schema.DType.DATE, schema.DType.TIMESTAMP)}
    return out


def _raw_by_target(up_df: pd.DataFrame, index, mapping: dict[str, str], table: str) -> dict:
    """Original source values for each date target, aligned to ``index`` (for parse checks)."""
    dts = _date_targets(table)
    out: dict[str, pd.Series] = {}
    for src, target in mapping.items():
        if target in dts and src in up_df.columns:
            out[target] = up_df[src].reindex(index)
    return out


def _coerce_fix(up, table: str, mapping: dict[str, str], options: dict | None):
    """Map -> coerce -> apply hygiene fixes. Returns (fixed_df, report, audit)."""
    mapped, _, _ = ingest.build_mapped_frame(up.df, table, mapping)
    return hygiene.apply_fixes(mapped, table, options, _con())


def _reason_breakdown(rules: dict) -> dict[str, int]:
    """Count quarantined rows by reason, e.g. {'required_present': 2, 'edi_control_row': 5}."""
    out: dict[str, int] = {}
    for reasons in rules.get("quarantine_reasons", {}).values():
        for rsn in reasons:
            out[rsn] = out.get(rsn, 0) + 1
    return out


def _correction_count(rules: dict) -> int:
    return next((r["count"] for r in rules.get("rules", []) if r["key"] == "volume_corrections"), 0)


# ---- Endpoints ------------------------------------------------------------------
@router.get("/targets")
def targets():
    """Static registry powering the mapping dropdowns (tables, fields, required keys)."""
    return {
        "tables": schema.IMPORTABLE_TABLES,
        "table_labels": schema.TABLE_LABELS,
        "targets_by_table": {t: schema.import_targets(t) for t in schema.IMPORTABLE_TABLES},
        "required_keys": {t: schema.required_import_keys(t) for t in schema.IMPORTABLE_TABLES},
        "customer_key_column": schema.CUSTOMER_KEY_COLUMN,
        "defaultable_fields": schema.DEFAULTABLE_FIELDS,
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
        con = _con()
        payload["matched_profile"] = _match_profile(con, list(df.columns))
        payload["crosswalk_size"] = len(db.get_crosswalk(con))
    return payload


@router.post("/crosswalk/propose")
def crosswalk_propose(req: ProposeRequest):
    up = ingest.get_upload(req.upload_id)
    if up is None:
        raise HTTPException(status_code=404, detail="Upload expired — please re-upload the file.")
    key_col = schema.customer_key_column(req.table)
    if not key_col:
        raise HTTPException(status_code=400,
                            detail=f"{schema.TABLE_LABELS.get(req.table, req.table)} has no customer key to resolve.")
    mapped, _, _ = ingest.build_mapped_frame(up.df, req.table, req.mapping)
    if key_col not in mapped.columns:
        raise HTTPException(status_code=400, detail=f"Map a column to '{key_col}' before resolving customers.")

    keys = mapped[key_col].dropna().astype(str).str.strip()
    keys = keys[keys != ""]
    counts = keys.value_counts().to_dict()

    names = None
    if req.name_source and req.name_source in up.df.columns:
        name_col = up.df[req.name_source]
        tmp = pd.DataFrame({"k": keys, "nm": name_col.reindex(keys.index)})
        names = {}
        for k, grp in tmp.dropna(subset=["k"]).groupby("k"):
            val = next((str(x).strip() for x in grp["nm"]
                        if pd.notna(x) and str(x).strip()), None)
            if val:
                names[k] = val

    with db.lock():
        result = crosswalk.propose(_con(), counts, names, req.threshold)
    result["key_column"] = key_col
    return result


@router.post("/crosswalk/confirm")
def crosswalk_confirm(req: ConfirmRequest):
    with db.lock():
        con = _con()
        out = crosswalk.confirm_groups(con, req.groups, req.rejected_keys, _now())
        out["crosswalk"] = db.list_crosswalk(con)
    return out


@router.get("/crosswalk")
def crosswalk_list():
    with db.lock():
        return {"crosswalk": db.list_crosswalk(_con())}


@router.delete("/crosswalk/{variant_key}")
def crosswalk_delete(variant_key: str):
    with db.lock():
        db.delete_crosswalk_entry(_con(), variant_key)
    return {"ok": True}


@router.post("/crosswalk/clear")
def crosswalk_clear():
    with db.lock():
        db.clear_crosswalk(_con())
    return {"ok": True}


# Header keywords that identify the two columns of a hand-built name map.
_RAW_NAME_KEYWORDS = ("raw", "bol", "variant", "source", "original", "consignee", "as is")
_CODED_NAME_KEYWORDS = ("coded", "master", "clean", "mapped", "canonical", "standard", "resolved")
_RAW_PRODUCT_KEYWORDS = ("raw", "source", "original", "as is", "description", "long", "terminal")
_STD_PRODUCT_KEYWORDS = ("standard", "coded", "clean", "canonical", "mapped", "normalized", "short")


def _detect_two_columns(df: pd.DataFrame, raw_kw: tuple, target_kw: tuple) -> tuple[str, str]:
    """Pick the (raw, target) columns of a two-column reference chart by header keywords; fall
    back to positional (first column = raw, second = target), which matches the documented layout."""
    cols = list(df.columns)
    lower = {c: str(c).lower() for c in cols}
    raw_col = next((c for c in cols if any(k in lower[c] for k in raw_kw)), None)
    tgt_col = next((c for c in cols if c != raw_col and any(k in lower[c] for k in target_kw)), None)
    if raw_col is None or tgt_col is None:
        raw_col, tgt_col = cols[0], cols[1]
    return raw_col, tgt_col


def _detect_name_map_columns(df: pd.DataFrame) -> tuple[str, str]:
    return _detect_two_columns(df, _RAW_NAME_KEYWORDS, _CODED_NAME_KEYWORDS)


def _detect_product_map_columns(df: pd.DataFrame) -> tuple[str, str]:
    return _detect_two_columns(df, _RAW_PRODUCT_KEYWORDS, _STD_PRODUCT_KEYWORDS)


@router.post("/crosswalk/upload-names")
async def crosswalk_upload_names(file: UploadFile = File(...)):
    """Upload a hand-built two-column CSV (Raw BOL Account Name → Coded Account Name).

    Loads every row as a CONFIRMED crosswalk entry (human source of truth — overrides fuzzy
    merges), then RE-APPLIES the crosswalk across the whole store so already-loaded lifts /
    invoices / quotes / BOLs regroup under their coded master name and every score/forecast
    recomputes on the master customer. Re-uploadable: re-run any time to extend the mapping.
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    try:
        df = ingest.parse_file(content, file.filename or "name-map")
    except ingest.IngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if df is None or len(df.columns) < 2:
        raise HTTPException(status_code=400,
                            detail="A customer name map needs two columns: the raw account name "
                                   "and the coded (master) name.")

    raw_col, coded_col = _detect_name_map_columns(df)
    pairs = list(zip(df[raw_col].tolist(), df[coded_col].tolist()))

    with db.lock():
        con = _con()
        loaded = crosswalk.load_name_map(con, pairs, _now())
        applied = db.reapply_crosswalk(con)
        db.set_meta(con, "last_import_at", _now())  # bust scoring / demand / pricing caches
        db.log_hygiene_audit(con, _now_us(), schema.LIFTS, file.filename or "name-map", [
            {"step": "crosswalk_name_map", "rows_affected": loaded["loaded"],
             "detail": f"Loaded {loaded['loaded']} raw→coded name mapping(s) "
                       f"({loaded['masters']} master name(s)); confirmed, overrides fuzzy."},
            {"step": "crosswalk_reapply", "rows_affected": applied["total_remapped"],
             "detail": f"Re-resolved {applied['total_remapped']} existing row(s) to master ids."},
        ])
        db.log_import(con, _now(), schema.CUSTOMERS, file.filename or "name-map",
                      loaded["loaded"], "name_map")
        unmapped = db.unmapped_customers(con)
        state = _state(con)
        crosswalk_size = len(db.get_crosswalk(con))
        masters = db.crosswalk_master_count(con)

    return {
        "ok": True,
        "raw_column": raw_col,
        "coded_column": coded_col,
        "loaded": loaded["loaded"],
        "masters": loaded["masters"],
        "remapped": applied["remapped"],
        "total_remapped": applied["total_remapped"],
        "unmapped": unmapped,
        "n_unmapped": len(unmapped),
        "crosswalk_size": crosswalk_size,
        "crosswalk_masters": masters,
        **state,
    }


@router.get("/unmapped-customers")
def unmapped_customers_list():
    """Raw customer names not yet covered by the confirmed crosswalk (shown as-is, add them)."""
    with db.lock():
        con = _con()
        rows = db.unmapped_customers(con)
        return {
            "unmapped": rows,
            "n_unmapped": len(rows),
            "crosswalk_masters": db.crosswalk_master_count(con),
            "crosswalk_size": len(db.get_crosswalk(con)),
            "customers_total": db.row_count(con, schema.CUSTOMERS),
        }


@router.post("/product-map/upload")
async def product_map_upload(file: UploadFile = File(...)):
    """Upload a hand-built two-column Product Reference chart (Raw Product Code → Standardized
    Code). Loads every row as a CONFIRMED product-crosswalk entry, then RE-APPLIES across the
    whole store so already-loaded lifts/inventory/market/quotes/receipts/BOLs restate their
    product to the standardized code and every product-level metric recomputes on it.
    Re-uploadable any time to extend the chart."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    try:
        df = ingest.parse_file(content, file.filename or "product-map")
    except ingest.IngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if df is None or len(df.columns) < 2:
        raise HTTPException(status_code=400,
                            detail="A product reference chart needs two columns: the raw product "
                                   "code and the standardized code.")

    raw_col, std_col = _detect_product_map_columns(df)
    pairs = list(zip(df[raw_col].tolist(), df[std_col].tolist()))

    with db.lock():
        con = _con()
        loaded = crosswalk.load_product_map(con, pairs, _now())
        applied = db.reapply_product_crosswalk(con)
        db.set_meta(con, "last_import_at", _now())  # bust scoring / demand / pricing caches
        db.log_hygiene_audit(con, _now_us(), schema.LIFTS, file.filename or "product-map", [
            {"step": "product_map", "rows_affected": loaded["loaded"],
             "detail": f"Loaded {loaded['loaded']} raw→standard product mapping(s) "
                       f"({loaded['standards']} standardized code(s)); confirmed."},
            {"step": "product_reapply", "rows_affected": applied["total_remapped"],
             "detail": f"Standardized {applied['total_remapped']} existing row(s)' product code."},
        ])
        db.log_import(con, _now(), schema.LIFTS, file.filename or "product-map",
                      loaded["loaded"], "product_map")
        unmapped = db.unmapped_products(con)
        state = _state(con)
        standards = db.product_standard_count(con)

    return {
        "ok": True,
        "raw_column": raw_col,
        "standard_column": std_col,
        "loaded": loaded["loaded"],
        "standards": loaded["standards"],
        "remapped": applied["remapped"],
        "total_remapped": applied["total_remapped"],
        "unmapped": unmapped,
        "n_unmapped": len(unmapped),
        "product_standards": standards,
        **state,
    }


@router.get("/unmapped-products")
def unmapped_products_list():
    """Distinct product codes in lifts not standardized by the Product Reference chart yet."""
    with db.lock():
        con = _con()
        rows = db.unmapped_products(con)
        return {
            "unmapped": rows,
            "n_unmapped": len(rows),
            "product_standards": db.product_standard_count(con),
        }


@router.post("/validate")
def validate(req: ValidateRequest):
    up = ingest.get_upload(req.upload_id)
    if up is None:
        raise HTTPException(status_code=404, detail="Upload expired — please re-upload the file.")
    if req.table not in schema.IMPORTABLE_TABLES:
        raise HTTPException(status_code=400, detail=f"Unknown target table '{req.table}'.")

    base = ingest.validate(up.df, req.table, req.mapping)  # mapping validity + field stats
    opts = hygiene.HygieneOptions.from_dict(req.options)

    with db.lock():
        fixed, report, _audit = _coerce_fix(up, req.table, req.mapping, req.options)
        raw = _raw_by_target(up.df, fixed.index, req.mapping, req.table)
        rules = validation.run_rules(fixed, req.table, req.options, raw, _con())

    base["rules"] = rules["rules"]
    base["fixes_preview"] = report
    base["rule_errors"] = rules["n_errors"]
    base["rule_warnings"] = rules["n_warnings"]

    # Honest, reconciling counts: clean + quarantined + dropped == rows after hygiene fixes.
    # Failing rows are HELD for review by default (quarantine_failures); only when the user
    # opts out are they dropped — and we surface that count instead of hiding it (a 0/0 that
    # silently loses every row is exactly the confusion we're fixing).
    n_fixed = int(len(fixed))
    n_failing = int(rules["quarantine_count"])
    quarantine_on = bool((req.options or {}).get("quarantine_failures", True))
    base["rows_after_fixes"] = n_fixed
    base["quarantine_count"] = n_failing if quarantine_on else 0
    base["dropped_rows"] = 0 if quarantine_on else n_failing
    base["clean_rows"] = max(0, n_fixed - n_failing)

    # Preview BOL grouping on the rows that would actually be kept: how many lifts result.
    good = fixed.drop(index=rules["quarantine_index"]) if rules["quarantine_index"] else fixed
    grouped = hygiene.group_by_bol(good, req.table) if opts.group_bol else good
    base["lifts_after_grouping"] = int(len(grouped))
    base["corrections"] = _correction_count(rules)
    base["quarantine_reasons"] = _reason_breakdown(rules)
    return base


@router.post("/commit")
def commit(req: CommitRequest):
    up = ingest.get_upload(req.upload_id)
    if up is None:
        raise HTTPException(status_code=404, detail="Upload expired — please re-upload the file.")
    if req.table not in schema.IMPORTABLE_TABLES:
        raise HTTPException(status_code=400, detail=f"Unknown target table '{req.table}'.")
    if req.mode not in ("replace", "append"):
        raise HTTPException(status_code=400, detail="mode must be 'replace' or 'append'.")

    base = ingest.validate(up.df, req.table, req.mapping)
    if not base["can_commit"]:
        raise HTTPException(status_code=422, detail={"message": "Mapping is not valid.",
                                                     "errors": base["errors"]})

    opts = hygiene.HygieneOptions.from_dict(req.options)

    with db.lock():
        con = _con()
        fixed, report, audit = _coerce_fix(up, req.table, req.mapping, req.options)
        raw = _raw_by_target(up.df, fixed.index, req.mapping, req.table)
        rules = validation.run_rules(fixed, req.table, opts.to_dict(), raw, con)

        q_index = rules["quarantine_index"]
        quarantined = fixed.loc[q_index] if q_index else fixed.iloc[0:0]
        good = fixed.drop(index=q_index) if q_index else fixed
        clean_rows = int(len(good))               # rows that passed the rules (pre-grouping)

        # Collapse the compartment rows of each BOL into a single lift (sum gross + net). Runs on
        # the CLEAN rows only, so junk/heartbeat rows never land inside a group.
        if opts.group_bol:
            good = hygiene.group_by_bol(good, req.table, report, audit)

        if opts.dedupe_exact:
            good = hygiene.dedupe_exact(good, report, audit)

        if req.mode == "replace":
            db.truncate(con, req.table)
        rows_written = db.insert_df(con, req.table, good.reset_index(drop=True))

        # Route failing rows to quarantine (held for review) unless the user opted out.
        n_quarantined = 0
        n_dropped = 0
        if len(quarantined):
            if opts.quarantine_failures:
                n_quarantined = _quarantine_rows(con, req.table, up.filename, quarantined,
                                                  rules["quarantine_reasons"])
                audit.append({"step": "quarantine", "rows_affected": n_quarantined,
                              "detail": f"Held {n_quarantined} failing row(s) for review."})
            else:
                n_dropped = int(len(quarantined))
                audit.append({"step": "drop_invalid", "rows_affected": n_dropped,
                              "detail": f"Dropped {n_dropped} invalid row(s) (quarantine disabled)."})

        if req.table == schema.LIFTS:
            db.rebuild_customers_from_lifts(con, replace=(req.mode == "replace"))

        db.set_meta(con, "profile", "imported")
        db.set_meta(con, "last_import_at", _now())
        db.set_meta(con, "last_import_table", req.table)
        db.set_meta(con, "last_import_filename", up.filename)
        db.log_import(con, _now(), req.table, up.filename, rows_written, req.mode)
        db.log_hygiene_audit(con, _now_us(), req.table, up.filename, audit)

        if req.save_profile:
            db.save_import_profile(
                con, req.save_profile.strip(), req.table,
                json.dumps(req.mapping), json.dumps(list(up.df.columns)), _now(),
                json.dumps(opts.to_dict()))

        state = _state(con)

    return {
        "ok": True,
        "table": req.table,
        "mode": req.mode,
        "rows_written": rows_written,
        "rows_in_file": int(len(up.df)),
        "clean_rows": clean_rows,                 # passed the rules (compartment-level)
        "lifts_after_grouping": rows_written,     # after BOL grouping (== rows written)
        "corrections": _correction_count(rules),  # negative volumes kept & tagged
        "quarantined": n_quarantined,
        "dropped": n_dropped,
        "quarantine_reasons": _reason_breakdown(rules),
        "hygiene": report,
        "rules": rules["rules"],
        "saved_profile": req.save_profile or None,
        **state,
    }


def _quarantine_rows(con, table: str, filename: str, frame: pd.DataFrame,
                     reasons: dict) -> int:
    cols = [c for c in schema.column_names(table) if c in frame.columns]
    rows = []
    for idx, row in frame.iterrows():
        payload = {}
        for c in cols:
            v = row[c]
            if v is None or (isinstance(v, float) and pd.isna(v)):
                payload[c] = None
            elif isinstance(v, (pd.Timestamp, datetime)):
                payload[c] = str(v)
            else:
                payload[c] = v if not isinstance(v, float) else round(float(v), 4)
        rows.append({
            "id": uuid.uuid4().hex,
            "at": _now_us(),
            "target_table": table,
            "filename": filename,
            "reasons": json.dumps(reasons.get(idx, [])),
            "payload": json.dumps(payload, default=str),
        })
    return db.add_quarantine(con, rows)


@router.get("/quarantine")
def quarantine_list(table: str | None = None):
    with db.lock():
        rows = db.list_quarantine(_con(), table)
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "at": r["at"], "target_table": r["target_table"],
            "filename": r["filename"],
            "reasons": json.loads(r["reasons"] or "[]"),
            "payload": json.loads(r["payload"] or "{}"),
        })
    with db.lock():
        counts = db.quarantine_counts(_con())
    return {"rows": out, "counts": counts, "total": sum(counts.values())}


@router.post("/quarantine/discard")
def quarantine_discard(req: QuarantineRequest):
    with db.lock():
        if req.ids:
            n = db.delete_quarantine(_con(), req.ids)
        else:
            db.clear_quarantine(_con())
            n = -1
    return {"ok": True, "discarded": n}


@router.post("/quarantine/reimport")
def quarantine_reimport(req: QuarantineRequest):
    with db.lock():
        con = _con()
        rows = db.get_quarantine_rows(con, req.ids) if req.ids else db.list_quarantine(con)
        by_table: dict[str, list[dict]] = {}
        for r in rows:
            by_table.setdefault(r["target_table"], []).append(r)

        total_reimported, total_still = 0, 0
        per_table = {}
        for table, trows in by_table.items():
            reimported, still = _reimport_table(con, table, trows, req.edits)
            per_table[table] = {"reimported": reimported, "still_quarantined": still}
            total_reimported += reimported
            total_still += still
            if table == schema.LIFTS and reimported:
                db.rebuild_customers_from_lifts(con, replace=False)
        if total_reimported:
            db.set_meta(con, "last_import_at", _now())
        state = _state(con)

    return {"ok": True, "reimported": total_reimported, "still_quarantined": total_still,
            "by_table": per_table, **state}


def _reimport_table(con, table: str, rows: list[dict], edits: dict) -> tuple[int, int]:
    records, ids = [], []
    for r in rows:
        payload = json.loads(r["payload"] or "{}")
        payload.update(edits.get(r["id"], {}))
        records.append(payload)
        ids.append(r["id"])
    if not records:
        return 0, 0

    df = pd.DataFrame(records)
    raw_dates = {}
    types = schema.column_types(table)
    for col in list(df.columns):
        dt = types.get(col)
        if dt is None:
            df = df.drop(columns=[col])
            continue
        if dt in (schema.DType.DATE, schema.DType.TIMESTAMP):
            raw_dates[col] = df[col].copy()
        coerced, _, _ = ingest.coerce_column(df[col], dt.value)
        df[col] = coerced

    # Re-importing hand-fixed rows: respect the corrected values (don't recompute net).
    opts = hygiene.HygieneOptions(net_correction="off")
    fixed, _report, _audit = hygiene.apply_fixes(df, table, opts, con)
    raw = {t: s.reindex(fixed.index) for t, s in raw_dates.items()}
    rules = validation.run_rules(fixed, table, opts.to_dict(), raw, con)

    q_index = set(rules["quarantine_index"])
    good = fixed.drop(index=list(q_index)) if q_index else fixed
    db.insert_df(con, table, good.reset_index(drop=True))

    passed_ids = [ids[i] for i in good.index]
    db.delete_quarantine(con, passed_ids)
    return len(passed_ids), len(q_index)


@router.get("/data-health")
def data_health_report():
    with db.lock():
        return data_health.compute(_con())


@router.get("/audit")
def audit_log(limit: int = 100):
    with db.lock():
        return {"audit": db.list_hygiene_audit(_con(), limit)}


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
            "hygiene": json.loads(r["hygiene"]) if r.get("hygiene") else None,
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
            json.dumps(req.source_columns), _now(),
            json.dumps(req.hygiene) if req.hygiene else None)
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


def _append_entries(con, table: str, records: list[dict], filename: str,
                    resolve: bool = True) -> dict:
    """Append manually-entered rows through the SAME hygiene path as a file import.

    Coerce → apply fixes (incl. crosswalk resolution) → run rules → split quarantine →
    append clean rows → audit/log. Used by the rack-benchmark and quote quick-entry forms
    so direct entries are cleaned exactly like everything else.
    """
    if not records:
        return {"rows_written": 0, "quarantined": 0}
    df = pd.DataFrame(records)
    types = schema.column_types(table)
    raw_dates: dict[str, pd.Series] = {}
    for col in list(df.columns):
        dt = types.get(col)
        if dt is None:
            df = df.drop(columns=[col])
            continue
        if dt in (schema.DType.DATE, schema.DType.TIMESTAMP):
            raw_dates[col] = df[col].copy()
        coerced, _, _ = ingest.coerce_column(df[col], dt.value)
        df[col] = coerced

    opts = hygiene.HygieneOptions(net_correction="off", resolve_customers=resolve,
                                  dedupe_lifts_grain=False, quarantine_failures=True)
    fixed, report, audit = hygiene.apply_fixes(df, table, opts, con)
    raw = {t: s.reindex(fixed.index) for t, s in raw_dates.items()}
    rules = validation.run_rules(fixed, table, opts.to_dict(), raw, con)

    q_index = rules["quarantine_index"]
    quarantined = fixed.loc[q_index] if q_index else fixed.iloc[0:0]
    good = fixed.drop(index=q_index) if q_index else fixed
    good = hygiene.dedupe_exact(good, report, audit)

    rows_written = db.insert_df(con, table, good.reset_index(drop=True))
    n_quarantined = 0
    if len(quarantined):
        n_quarantined = _quarantine_rows(con, table, filename, quarantined,
                                         rules["quarantine_reasons"])
    db.set_meta(con, "last_import_at", _now())
    db.log_import(con, _now(), table, filename, rows_written, "append")
    db.log_hygiene_audit(con, _now_us(), table, filename, audit)
    return {"rows_written": rows_written, "quarantined": n_quarantined}


@router.post("/rack-benchmark")
def rack_benchmark(req: RackBenchmarkRequest):
    """Daily street/OPIS rack-benchmark entry — appends to the market price time series."""
    if not req.entries:
        raise HTTPException(status_code=400, detail="No rack-benchmark entries provided.")
    records = [{"price_date": e.price_date, "terminal": e.terminal, "product": e.product,
                "rack_benchmark": e.rack_benchmark} for e in req.entries]
    with db.lock():
        con = _con()
        res = _append_entries(con, schema.MARKET, records, "rack-benchmark (manual)", resolve=False)
        state = _state(con)
    return {"ok": True, **res, **state}


@router.post("/quote")
def quote(req: QuoteRequest):
    """Quote-logger quick entry — resolves the customer via the crosswalk and appends a quote."""
    if not req.entries:
        raise HTTPException(status_code=400, detail="No quotes provided.")
    records = []
    for e in req.entries:
        rec = {k: v for k, v in e.model_dump().items() if v is not None}
        records.append(rec)
    with db.lock():
        con = _con()
        res = _append_entries(con, schema.QUOTES, records, "quote log (manual)", resolve=True)
        state = _state(con)
    return {"ok": True, **res, **state}


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
