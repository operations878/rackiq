"""Ingestion engine for Data Studio — parse, fuzzy-map, validate, and coerce uploads.

This module is deliberately storage-agnostic: it turns an uploaded file into a parsed
DataFrame, suggests a column mapping by fuzzy header match, and validates/coerces a
proposed mapping. Writing to DuckDB and recomputing capabilities is the caller's job
(see ``api/studio.py``), which keeps this layer pure and easy to reason about.
"""

from __future__ import annotations

import difflib
import io
import re
import time
import uuid
from dataclasses import dataclass, field

import pandas as pd

from . import schema

# ---- Upload cache ---------------------------------------------------------------
# Parsed uploads are stashed in-process so the wizard's inspect -> validate -> commit
# steps don't have to re-transmit the file. Bounded to the most recent uploads.
_MAX_UPLOADS = 24


@dataclass
class _Upload:
    df: pd.DataFrame
    filename: str
    created_at: float = field(default_factory=time.time)


_UPLOADS: dict[str, _Upload] = {}


def _evict_if_needed() -> None:
    while len(_UPLOADS) > _MAX_UPLOADS:
        oldest = min(_UPLOADS, key=lambda k: _UPLOADS[k].created_at)
        _UPLOADS.pop(oldest, None)


def stash_upload(df: pd.DataFrame, filename: str) -> str:
    upload_id = uuid.uuid4().hex
    _UPLOADS[upload_id] = _Upload(df=df, filename=filename)
    _evict_if_needed()
    return upload_id


def get_upload(upload_id: str) -> _Upload | None:
    return _UPLOADS.get(upload_id)


# ---- File parsing ---------------------------------------------------------------
class IngestError(Exception):
    """Raised for user-facing ingestion problems (bad file, unreadable, etc.)."""


def parse_file(content: bytes, filename: str) -> pd.DataFrame:
    """Parse raw bytes (CSV or Excel) into a DataFrame with original headers as strings."""
    name = (filename or "").lower()
    try:
        if name.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(content), sheet_name=0, dtype=object)
        elif name.endswith((".csv", ".txt", ".tsv")):
            sep = "\t" if name.endswith(".tsv") else None
            df = pd.read_csv(io.BytesIO(content), dtype=object, sep=sep,
                             engine="python", skipinitialspace=True)
        else:
            # Fall back to CSV sniffing for unknown extensions.
            df = pd.read_csv(io.BytesIO(content), dtype=object, sep=None,
                             engine="python", skipinitialspace=True)
    except Exception as exc:  # noqa: BLE001 — surface a clean message to the UI
        raise IngestError(f"Could not parse '{filename}': {exc}") from exc

    if df.shape[1] == 0:
        raise IngestError("The file has no columns.")
    df.columns = [str(c).strip() for c in df.columns]
    return df


# ---- Fuzzy header matching ------------------------------------------------------
# Hand-curated synonyms per canonical/structural target. The matcher also uses string
# similarity and token overlap, so this list only needs the non-obvious aliases.
SYNONYMS: dict[str, list[str]] = {
    "customer_id": ["customer", "cust", "customer id", "customer number", "cust no",
                    "account", "acct", "account number", "buyer", "client", "bill to"],
    "lift_datetime": ["lift date", "lift time", "date", "datetime", "timestamp",
                      "load date", "ship date", "delivery date", "transaction date",
                      "bol date", "pickup date", "lift dt"],
    "net_gallons": ["net gallons", "net gal", "net", "net qty", "net quantity",
                    "net volume", "gallons", "volume", "qty", "quantity", "net gals"],
    "terminal": ["terminal", "term", "rack", "location", "origin", "supply point",
                 "loading terminal", "branch"],
    "product": ["product", "grade", "fuel", "item", "material", "prod", "product code",
                "commodity"],
    "gross_gallons": ["gross gallons", "gross gal", "gross", "gross qty", "gross volume",
                      "gross gals", "observed gallons"],
    "observed_temp": ["temp", "temperature", "observed temp", "obs temp", "deg f",
                      "product temp", "load temp"],
    "api_gravity": ["api", "api gravity", "gravity", "api grav", "deg api"],
    "unit_price": ["price", "unit price", "sell price", "sale price", "rack price",
                   "price per gallon", "ppg", "selling price", "invoice price"],
    "unit_cost": ["cost", "unit cost", "cogs", "acquisition cost", "laid in cost",
                  "cost per gallon", "supply cost", "replacement cost"],
    "invoice_date": ["invoice date", "inv date", "bill date", "billed date", "invoice dt"],
    "due_date": ["due date", "due", "payment due", "net due", "terms date", "due dt"],
    "paid_date": ["paid date", "paid", "payment date", "date paid", "settled date",
                  "cleared date", "paid dt"],
    "invoice_amount": ["invoice amount", "amount", "invoice total", "amt", "total",
                       "balance", "invoice amt", "net amount", "amount due"],
    "credit_limit": ["credit limit", "credit", "limit", "credit line", "cl"],
    "snapshot_datetime": ["snapshot date", "snapshot", "as of date", "as of",
                          "inventory date", "reading date", "gauge date", "snapshot dt"],
    "tank_id": ["tank", "tank id", "tank no", "tank number", "tank name"],
    "tank_capacity": ["tank capacity", "capacity", "shell capacity", "tank cap",
                      "max capacity", "working capacity"],
    "min_heel": ["min heel", "heel", "minimum heel", "dead stock", "unpumpable"],
    "inventory_snapshot": ["inventory", "book inventory", "inventory snapshot", "book",
                           "on hand", "inventory book", "book stock", "closing inventory"],
    "physical_inventory": ["physical inventory", "physical", "gauged", "measured inventory",
                           "actual inventory", "physical stock", "gauge"],
    "receipts": ["receipts", "received", "deliveries in", "barge receipts",
                 "pipeline receipts", "receipt volume", "inbound"],
    "price_date": ["price date", "date", "as of", "market date", "quote date",
                   "pricing date", "trade date"],
    "market_price": ["market price", "benchmark", "platts", "opis", "rack benchmark",
                     "spot price", "reference price", "screen price"],
    "nyh_basis": ["nyh basis", "basis", "ny harbor basis", "differential", "diff",
                  "harbor basis"],
    "street_rack": ["street rack", "posted rack", "rack price", "posting", "street price",
                    "posted price", "street"],
    "committed_buys": ["committed buys", "buys", "long", "committed long", "buy position",
                       "purchases committed", "committed purchases"],
    "committed_sells": ["committed sells", "sells", "short", "committed short",
                        "sell position", "sales committed", "committed sales"],
}

_SUGGEST_THRESHOLD = 0.52


def _norm(s: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    if a in b or b in a:
        ratio = max(ratio, 0.86)
    at, bt = set(a.split()), set(b.split())
    if at and bt:
        jacc = len(at & bt) / len(at | bt)
        ratio = max(ratio, 0.55 * jacc + 0.45 * ratio)
    return ratio


def score_header(header: str, target: str) -> float:
    """Best similarity between a source header and a target field (incl. its synonyms)."""
    h = _norm(header)
    best = _similarity(h, _norm(target))
    for syn in SYNONYMS.get(target, []):
        best = max(best, _similarity(h, _norm(syn)))
        if best >= 0.999:
            break
    return round(best, 3)


def suggest_for_table(headers: list[str], table: str) -> dict[str, dict]:
    """Best target (within ``table``) for each header, resolved greedily by score.

    A target is assigned to at most one header (the highest-scoring), so two columns are
    never auto-mapped to the same canonical field.
    """
    targets = [t["name"] for t in schema.import_targets(table)]
    # Rank all (header, target) pairs above threshold, then assign greedily.
    pairs = []
    for h in headers:
        for t in targets:
            s = score_header(h, t)
            if s >= _SUGGEST_THRESHOLD:
                pairs.append((s, h, t))
    pairs.sort(reverse=True)
    used_headers: set[str] = set()
    used_targets: set[str] = set()
    out: dict[str, dict] = {}
    for s, h, t in pairs:
        if h in used_headers or t in used_targets:
            continue
        out[h] = {"target": t, "confidence": s}
        used_headers.add(h)
        used_targets.add(t)
    return out


def infer_table(headers: list[str]) -> str:
    """Pick the canonical table whose targets best explain the file's headers."""
    best_table, best_score = schema.LIFTS, -1.0
    for table in schema.IMPORTABLE_TABLES:
        sugg = suggest_for_table(headers, table)
        # Reward both the strength and the count of confident matches.
        score = sum(v["confidence"] for v in sugg.values())
        # Bonus for covering the table's required keys (a strong signal of file type).
        req = schema.required_import_keys(table)
        covered = sum(1 for k in req if k in {v["target"] for v in sugg.values()})
        score += covered * 0.75
        if score > best_score:
            best_table, best_score = table, score
    return best_table


# ---- Column inspection ----------------------------------------------------------
def _sample_values(series: pd.Series, k: int = 5) -> list[str]:
    vals = []
    for v in series:
        if v is None:
            continue
        if isinstance(v, float) and pd.isna(v):
            continue
        sv = str(v).strip()
        if sv and sv.lower() != "nan":
            vals.append(sv)
        if len(vals) >= k:
            break
    return vals


def _null_rate(series: pd.Series) -> float:
    n = len(series)
    if n == 0:
        return 0.0
    blank = series.map(lambda v: v is None
                       or (isinstance(v, float) and pd.isna(v))
                       or (isinstance(v, str) and v.strip() == "")).sum()
    return round(float(blank) / n, 4)


def _dtype_guess(series: pd.Series) -> str:
    sample = [v for v in series if v is not None and not (isinstance(v, float) and pd.isna(v))][:50]
    sample = [str(v).strip() for v in sample if str(v).strip() != ""]
    if not sample:
        return "empty"
    num = sum(1 for v in sample if _looks_numeric(v))
    if num / len(sample) >= 0.85:
        return "number"
    dt = sum(1 for v in sample if _looks_datelike(v))
    if dt / len(sample) >= 0.7:
        return "date"
    return "text"


_NUM_RE = re.compile(r"^[\-\+]?\$?\s*[\d,]*\.?\d+%?$")


def _looks_numeric(v: str) -> bool:
    return bool(_NUM_RE.match(v.replace(" ", "")))


def _looks_datelike(v: str) -> bool:
    if re.search(r"\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}", v):
        return True
    return bool(re.search(r"\d{4}-\d{2}-\d{2}", v))


def inspect(df: pd.DataFrame, filename: str) -> dict:
    """Build the full inspect payload the wizard renders (columns + suggestions + targets)."""
    headers = list(df.columns)
    columns = [{
        "name": h,
        "samples": _sample_values(df[h]),
        "null_rate": _null_rate(df[h]),
        "dtype_guess": _dtype_guess(df[h]),
    } for h in headers]

    suggested_table = infer_table(headers)
    suggestions_by_table = {t: suggest_for_table(headers, t) for t in schema.IMPORTABLE_TABLES}
    targets_by_table = {t: schema.import_targets(t) for t in schema.IMPORTABLE_TABLES}

    return {
        "filename": filename,
        "n_rows": int(len(df)),
        "n_columns": int(len(headers)),
        "columns": columns,
        "suggested_table": suggested_table,
        "suggestions_by_table": suggestions_by_table,
        "targets_by_table": targets_by_table,
        "table_labels": schema.TABLE_LABELS,
        "required_keys": {t: schema.required_import_keys(t) for t in schema.IMPORTABLE_TABLES},
    }


# ---- Coercion -------------------------------------------------------------------
def _clean_numeric(series: pd.Series) -> pd.Series:
    cleaned = series.map(lambda v: re.sub(r"[,$%\s]", "", v) if isinstance(v, str) else v)
    return pd.to_numeric(cleaned, errors="coerce")


def coerce_column(series: pd.Series, dtype: str) -> tuple[pd.Series, int]:
    """Coerce a source column to a canonical dtype; return (coerced, parse_error_count).

    A parse error is a value that was non-blank in the source but failed to coerce.
    """
    non_blank = series.map(lambda v: not (v is None
                                          or (isinstance(v, float) and pd.isna(v))
                                          or (isinstance(v, str) and v.strip() == "")))
    if dtype in ("DOUBLE", "INTEGER"):
        out = _clean_numeric(series)
        errors = int((non_blank & out.isna()).sum())
    elif dtype in ("TIMESTAMP", "DATE"):
        out = pd.to_datetime(series, errors="coerce")
        errors = int((non_blank & out.isna()).sum())
    else:  # VARCHAR
        out = series.map(lambda v: None if (v is None or (isinstance(v, float) and pd.isna(v)))
                         else str(v).strip())
        errors = 0
    return out, errors


def build_mapped_frame(
    df: pd.DataFrame, table: str, mapping: dict[str, str]
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Apply a {source_col: target} mapping, coercing each target to its declared type.

    Returns the canonical-named frame and a per-target parse-error count. Unmapped source
    columns are dropped; targets not in ``mapping`` are simply absent (left NULL on insert).
    """
    types = schema.column_types(table)
    out = pd.DataFrame(index=df.index)
    parse_errors: dict[str, int] = {}
    for src, target in mapping.items():
        if not target or src not in df.columns:
            continue
        dt = types.get(target)
        coerced, errors = coerce_column(df[src], dt.value if dt else "VARCHAR")
        out[target] = coerced
        parse_errors[target] = errors
    return out, parse_errors


# ---- Validation -----------------------------------------------------------------
def validate(df: pd.DataFrame, table: str, mapping: dict[str, str]) -> dict:
    """Produce the pre-commit validation preview for a proposed mapping."""
    # Keep only real targets for this table; ignore stray keys.
    valid_targets = {t["name"] for t in schema.import_targets(table)}
    mapping = {s: t for s, t in mapping.items() if t in valid_targets}

    required = schema.required_import_keys(table)
    mapped_targets = set(mapping.values())
    missing_required = [r for r in required if r not in mapped_targets]

    # Duplicate target assignments (two source cols → one field) are a hard error.
    target_counts: dict[str, list[str]] = {}
    for src, target in mapping.items():
        target_counts.setdefault(target, []).append(src)
    duplicate_targets = {t: srcs for t, srcs in target_counts.items() if len(srcs) > 1}

    mapped, parse_errors = build_mapped_frame(df, table, mapping)
    n_rows = int(len(mapped))

    # Per-field null rates (post-coercion) and parse errors.
    fields_report = []
    for src, target in mapping.items():
        if target not in mapped.columns:
            continue
        col = mapped[target]
        nulls = int(col.isna().sum())
        fields_report.append({
            "source": src,
            "target": target,
            "null_rate": round(nulls / n_rows, 4) if n_rows else 0.0,
            "parse_errors": parse_errors.get(target, 0),
        })

    # Date range over the table's primary time column (if mapped).
    time_col = schema.PRIMARY_TIME_COLUMN.get(table)
    date_range = {"start": None, "end": None, "column": time_col}
    if time_col and time_col in mapped.columns:
        ser = mapped[time_col].dropna()
        if len(ser):
            date_range["start"] = str(pd.Timestamp(ser.min()).date())
            date_range["end"] = str(pd.Timestamp(ser.max()).date())

    # Duplicate rows = exact duplicates across every mapped column (lossless to drop).
    duplicate_rows = int(mapped.duplicated(keep="first").sum()) if n_rows else 0

    # Rows that will be dropped because a required key is null.
    key_cols = [k for k in required if k in mapped.columns]
    droppable = 0
    if key_cols and n_rows:
        droppable = int(mapped[key_cols].isna().any(axis=1).sum())

    total_parse_errors = sum(parse_errors.values())

    warnings: list[str] = []
    for fr in fields_report:
        if fr["parse_errors"]:
            warnings.append(
                f"{fr['parse_errors']} value(s) in '{fr['source']}' could not be parsed as "
                f"{schema.column_types(table).get(fr['target']).value} and will become null.")
    if duplicate_rows:
        warnings.append(f"{duplicate_rows} exact-duplicate row(s) will be removed by the "
                        "hygiene pipeline.")
    if droppable:
        warnings.append(f"{droppable} row(s) are missing a required key and will be dropped.")

    errors: list[str] = []
    for r in missing_required:
        errors.append(f"Required field '{r}' is not mapped.")
    for t, srcs in duplicate_targets.items():
        errors.append(f"'{t}' is mapped from multiple columns ({', '.join(srcs)}).")
    if n_rows == 0:
        errors.append("The file has no rows to import.")

    importable_rows = max(0, n_rows - droppable - duplicate_rows)

    return {
        "table": table,
        "table_label": schema.TABLE_LABELS.get(table, table),
        "n_rows": n_rows,
        "importable_rows": importable_rows,
        "date_range": date_range,
        "duplicate_rows": duplicate_rows,
        "droppable_rows": droppable,
        "total_parse_errors": total_parse_errors,
        "fields": fields_report,
        "missing_required": missing_required,
        "warnings": warnings,
        "errors": errors,
        "can_commit": len(errors) == 0,
    }
