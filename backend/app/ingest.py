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
                    "account", "acct", "account number", "buyer", "client", "bill to",
                    # BOL/EDI exports name the buyer the "consignee" (Consignee Number is the id).
                    "consignee", "consignee number", "consignee no", "consignee num",
                    "consignee #", "consignee id"],
    "lift_datetime": ["lift date", "lift time", "date", "datetime", "timestamp",
                      "load date", "ship date", "delivery date", "transaction date",
                      "bol date", "pickup date", "lift dt"],
    "net_gallons": ["net gallons", "net gal", "net", "net qty", "net quantity",
                    "net volume", "gallons", "volume", "qty", "quantity", "net gals",
                    "net amount"],
    "terminal": ["terminal", "term", "rack", "location", "origin", "supply point",
                 "loading terminal", "branch", "terminal name"],
    "product": ["product", "grade", "fuel", "item", "material", "prod", "product code",
                "commodity", "product name"],
    "gross_gallons": ["gross gallons", "gross gal", "gross", "gross qty", "gross volume",
                      "gross gals", "observed gallons", "gross amount"],
    "observed_temp": ["temp", "temperature", "observed temp", "obs temp", "deg f",
                      "product temp", "load temp"],
    "api_gravity": ["api", "api gravity", "gravity", "api grav", "deg api", "gravity api"],
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
    "rack_benchmark": ["rack benchmark", "opis rack", "opis", "street rack", "posted rack",
                       "rack price", "benchmark rack", "opis benchmark", "street price",
                       "posting", "rack"],
    # --- quotes (elasticity training set) ---
    "quote_time": ["quote time", "quote date", "quote datetime", "quoted at", "quote dt",
                   "date quoted", "time", "timestamp", "date"],
    "quoted_price": ["quoted price", "quote price", "our price", "offer price", "quote",
                     "price quoted", "quoted", "ask", "offer"],
    "market_price_at_quote": ["market price at quote", "market at quote", "reference price",
                              "benchmark at quote", "rack at quote", "market price", "screen price"],
    "inventory_state": ["inventory state", "inventory posture", "inv state", "position state",
                        "stock state", "inventory status"],
    "capacity_state": ["capacity state", "capacity posture", "logistics state", "capacity status"],
    "competitor_context": ["competitor context", "competition", "competitor", "comp context",
                           "competitive context", "rival"],
    "outcome": ["outcome", "result", "status", "decision", "won lost", "accept reject",
                "disposition", "quote result"],
    "time_to_decision": ["time to decision", "decision time", "response time", "ttd",
                         "latency", "minutes to decide"],
    "final_gallons": ["final gallons", "final gal", "lifted gallons", "actual gallons",
                      "delivered gallons", "won gallons", "final volume"],
    # --- receipts (receipt detail / P8) ---
    "receipt_datetime": ["receipt date", "receipt time", "received date", "receipt datetime",
                         "arrival date", "delivery date", "receipt dt", "landed date"],
    "receipt_source": ["source", "receipt source", "mode", "transport", "carrier mode",
                       "inbound mode", "delivery mode", "supply mode"],
    "receipt_gross_gallons": ["gross gallons", "gross gal", "gross received", "gross volume",
                              "receipt gross", "gross"],
    "receipt_net_gallons": ["net gallons", "net gal", "net received", "net volume",
                            "receipt net", "net"],
    "measurement_basis": ["measurement basis", "measure basis", "gauge basis", "meter",
                          "measurement", "basis of measure", "metering"],
    "bl_vs_received_variance": ["bl vs received", "bl variance", "bol variance", "loss gain",
                                "bill of lading variance", "received variance", "transit loss"],
    # --- bol_compartments (raw compartment rows; reconciliation groups by BOL) ---
    # Compartment value fields use only compartment-prefixed aliases so they don't out-rank a
    # plain lifts file; the bare "net/gross/temp" headers still map via token-overlap similarity.
    "bol_number": ["bol", "bol number", "bol no", "bill of lading", "b/l", "bl number",
                   "load number", "ticket number", "load ticket", "bol id"],
    "bol_datetime": ["bol date", "bol time", "load date", "loading date", "ship date",
                     "bol datetime", "load datetime"],
    "meter_id": ["meter", "meter id", "lane", "loading lane", "load arm", "rack meter",
                 "meter no", "loading arm"],
    "compartment_id": ["compartment", "compartment id", "comp", "comp id", "compartment no",
                       "compartment number"],
    "compartment_gross_gallons": ["compartment gross", "gross loaded", "observed gallons",
                                  "gross load"],
    "compartment_net_gallons": ["compartment net", "billed net", "metered net", "ticket net",
                                "net loaded"],
    "compartment_temp": ["compartment temp", "load temp", "loading temp"],
    "compartment_api": ["compartment api", "load api"],
    "compartment_unit_cost": ["compartment cost", "load cost"],
}

_SUGGEST_THRESHOLD = 0.52          # required keys: map generously (we want the core 3 found)
_OPTIONAL_SUGGEST_THRESHOLD = 0.72  # optional fields: only when confident, so a loose header
#                                     ("Rack Driver ID") never auto-fills a numeric field with junk


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
    required = set(schema.required_import_keys(table))
    # Rank all (header, target) pairs above threshold, then assign greedily. Required keys use
    # a generous threshold (they must be found); optional fields use a stricter one so a weakly
    # similar header isn't auto-mapped into a canonical field it doesn't belong in.
    pairs = []
    for h in headers:
        for t in targets:
            s = score_header(h, t)
            thr = _SUGGEST_THRESHOLD if t in required else _OPTIONAL_SUGGEST_THRESHOLD
            if s >= thr:
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
    """Build the full inspect payload the wizard renders (columns + suggestions + targets).

    Each column carries the full profiling scorecard (type, null %, distinct count, min/max,
    samples, outliers, and quality flags) so problems are visible before any mapping.
    """
    from . import profiling  # lazy import (profiling imports helpers from this module)

    headers = list(df.columns)
    profile = profiling.profile_frame(df)
    columns = profile["columns"]

    suggested_table = infer_table(headers)
    suggestions_by_table = {t: suggest_for_table(headers, t) for t in schema.IMPORTABLE_TABLES}
    targets_by_table = {t: schema.import_targets(t) for t in schema.IMPORTABLE_TABLES}

    return {
        "filename": filename,
        "n_rows": int(len(df)),
        "n_columns": int(len(headers)),
        "columns": columns,
        "profile": {"score": profile["score"], "n_flagged_columns": profile["n_flagged_columns"],
                    "n_warnings": profile["n_warnings"]},
        "suggested_table": suggested_table,
        "suggestions_by_table": suggestions_by_table,
        "targets_by_table": targets_by_table,
        "table_labels": schema.TABLE_LABELS,
        "required_keys": {t: schema.required_import_keys(t) for t in schema.IMPORTABLE_TABLES},
    }


# ---- Coercion -------------------------------------------------------------------
# Textual stand-ins for "missing" that should coerce to NULL (a blank) rather than be
# counted as parse errors. Exported books routinely type empty cells as a dash, "N/A", or
# leave an Excel error cell behind; treating these as blanks is what lets a column with a
# handful of junk cells still import cleanly instead of inflating the parse-error count and
# (worse) tipping every row into quarantine.
_NULL_TOKENS = {
    "", "-", "--", "---", "—", "–", "n/a", "n/a.", "na", "n.a.", "#n/a",
    "null", "none", "nil", "nan", "tbd", "tba", "?", "??", "unknown", "unk",
    "#ref!", "#value!", "#name?", "#div/0!", "#null!", "#num!", ".", "..",
}

_PARSE_SAMPLE_LIMIT = 6


def _is_empty(v: object) -> bool:
    """True for a genuinely empty cell (None / NaN / whitespace-only)."""
    return (v is None
            or (isinstance(v, float) and pd.isna(v))
            or (isinstance(v, str) and v.strip() == ""))


def _is_missing(v: object) -> bool:
    """True for an empty cell OR a textual missing-value token (``N/A``, ``-`` …)."""
    if _is_empty(v):
        return True
    return isinstance(v, str) and v.strip().lower() in _NULL_TOKENS


def _fail_samples(series: pd.Series, mask: pd.Series, k: int = _PARSE_SAMPLE_LIMIT) -> list[str]:
    """A few distinct raw values that failed to coerce — for user-facing diagnostics."""
    out: list[str] = []
    seen: set[str] = set()
    for v in series[mask]:
        s = str(v).strip()
        if s and s.lower() != "nan" and s not in seen:
            seen.add(s)
            out.append(s)
        if len(out) >= k:
            break
    return out


def _clean_numeric(series: pd.Series) -> pd.Series:
    """Strip the common, recoverable numeric decorations before parsing.

    Handles thousands separators, currency / percent signs, accounting-style negatives
    ``(123)``, a Unicode minus, and the Excel text-number leading apostrophe.
    """
    def _conv(v):
        if not isinstance(v, str):
            return v
        s = v.strip().replace("−", "-")         # Unicode minus → ASCII hyphen
        neg = s.startswith("(") and s.endswith(")")  # accounting negative, e.g. (1,234)
        if neg:
            s = s[1:-1]
        s = s.lstrip("'")                            # Excel text-number leading apostrophe
        s = re.sub(r"[,$%\s]", "", s)               # thousands / currency / percent / spaces
        if neg and s and not s.startswith("-"):
            s = "-" + s
        return s
    cleaned = series.map(_conv)
    return pd.to_numeric(cleaned, errors="coerce")


# Excel stores dates as a serial number of days since its 1899-12-30 epoch (e.g. 45474 ==
# 2024-07-01). A date column exported to CSV/text — or an unformatted xlsx cell — often carries
# that raw serial instead of a formatted date; pandas would (wrongly) read it as epoch-nanoseconds
# or fail. We convert serials in a plausible modern band. This runs ONLY inside date coercion, so
# a numeric NON-date column (a customer number like 42023, a dollar amount) is never touched.
_EXCEL_EPOCH = pd.Timestamp("1899-12-30")
_SERIAL_MIN, _SERIAL_MAX = 20000.0, 80000.0   # ~1954-10 .. ~2119 — wider than any real ship date
_SERIAL_RE = re.compile(r"^\d{4,6}(\.0+)?$")


def _excel_serial(v: object) -> "pd.Timestamp | None":
    """If ``v`` is an Excel date serial in the plausible band, the date it denotes, else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and not (isinstance(v, float) and pd.isna(v)):
        x = float(v)
    elif isinstance(v, str) and _SERIAL_RE.match(v.strip()):
        x = float(v.strip())
    else:
        return None
    if _SERIAL_MIN <= x <= _SERIAL_MAX:
        return _EXCEL_EPOCH + pd.to_timedelta(x, unit="D")
    return None


def _parse_mixed_dates(series: pd.Series) -> pd.Series:
    """Parse a column of possibly-mixed date formats, salvaging serials and day-first values.

    Order: parse natively (handles real datetimes + ISO/US strings), override any Excel serial
    numbers with their true date, then retry whatever is still unparsed day-first (so
    "13/02/2024" and "02/13/2024" can coexist in one column).
    """
    import warnings

    def _to_dt(s, **kw):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                return pd.to_datetime(s, errors="coerce", format="mixed", **kw)
            except (ValueError, TypeError):
                return pd.to_datetime(s, errors="coerce", **kw)

    non_blank = series.map(lambda v: not (v is None
                                          or (isinstance(v, float) and pd.isna(v))
                                          or (isinstance(v, str) and v.strip() == "")))
    out = _to_dt(series)
    # Excel serials: override whatever the native parse made of a serial number.
    serial = series.map(_excel_serial)
    has_serial = serial.notna()
    if has_serial.any():
        out = out.copy()
        out.loc[has_serial] = serial[has_serial]
    remaining = non_blank & out.isna()
    if remaining.any():
        out.loc[remaining] = _to_dt(series[remaining], dayfirst=True)
    return out


def coerce_column(series: pd.Series, dtype: str) -> tuple[pd.Series, int, list[str]]:
    """Coerce a source column to a canonical dtype.

    Returns ``(coerced, parse_error_count, failing_samples)``. A *parse error* is a value
    that carried real content in the source but could not be coerced; textual missing-value
    tokens (``N/A``, ``-``, Excel error cells …) are treated as blanks (→ NULL), never as
    parse errors, so a few junk cells don't quarantine an otherwise-good column.
    """
    if dtype in ("DOUBLE", "INTEGER"):
        non_blank = ~series.map(_is_missing)
        out = _clean_numeric(series)
        err = non_blank & out.isna()
    elif dtype in ("TIMESTAMP", "DATE"):
        non_blank = ~series.map(_is_missing)
        out = _parse_mixed_dates(series)
        err = non_blank & out.isna()
    else:  # VARCHAR — only genuinely-empty cells become NULL; content is preserved verbatim.
        out = series.map(lambda v: None if _is_empty(v) else str(v).strip())
        err = pd.Series(False, index=series.index)
    n_err = int(err.sum())
    samples = _fail_samples(series, err) if n_err else []
    return out, n_err, samples


def build_mapped_frame(
    df: pd.DataFrame, table: str, mapping: dict[str, str]
) -> tuple[pd.DataFrame, dict[str, int], dict[str, list[str]]]:
    """Apply a {source_col: target} mapping, coercing each target to its declared type.

    Returns the canonical-named frame, a per-target parse-error count, and per-target samples
    of the values that failed to coerce (for diagnostics). Unmapped source columns are
    dropped; targets not in ``mapping`` are simply absent (left NULL on insert).
    """
    types = schema.column_types(table)
    out = pd.DataFrame(index=df.index)
    parse_errors: dict[str, int] = {}
    parse_samples: dict[str, list[str]] = {}
    for src, target in mapping.items():
        if not target or src not in df.columns:
            continue
        dt = types.get(target)
        coerced, errors, samples = coerce_column(df[src], dt.value if dt else "VARCHAR")
        out[target] = coerced
        parse_errors[target] = errors
        if samples:
            parse_samples[target] = samples
    return out, parse_errors, parse_samples


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

    mapped, parse_errors, parse_samples = build_mapped_frame(df, table, mapping)
    n_rows = int(len(mapped))

    # Per-field null rates (post-coercion), parse errors, and sample failing values.
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
            "parse_error_samples": parse_samples.get(target, []),
            "all_null": bool(n_rows and nulls == n_rows),
        })

    # Status of every required key: mapped? and (if mapped) did every value land null?
    # This is what lets the UI say *why* "required fields present" fails — an unmapped key
    # vs. a key whose source column is blank/unparseable — instead of a bare ∅.
    required_status = []
    for r in required:
        is_mapped = r in mapped_targets
        is_all_null = bool(is_mapped and r in mapped.columns and n_rows and mapped[r].isna().all())
        required_status.append({"field": r, "mapped": is_mapped, "all_null": is_all_null})

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
            ex = fr.get("parse_error_samples") or []
            ex_txt = f" (e.g. {', '.join(repr(x) for x in ex[:3])})" if ex else ""
            warnings.append(
                f"{fr['parse_errors']} value(s) in '{fr['source']}' could not be parsed as "
                f"{schema.column_types(table).get(fr['target']).value} and will become null{ex_txt}.")
    for rs in required_status:
        if rs["all_null"]:
            warnings.append(
                f"Required field '{rs['field']}' is mapped but every value is blank or "
                f"unparseable — those rows can't be stored until it's remapped or filled.")
    if duplicate_rows:
        warnings.append(f"{duplicate_rows} exact-duplicate row(s) will be removed by the "
                        "hygiene pipeline.")
    if droppable:
        warnings.append(f"{droppable} row(s) are missing a required key and will be held "
                        "for review (quarantined) rather than dropped.")

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
        "required_status": required_status,
        "warnings": warnings,
        "errors": errors,
        "can_commit": len(errors) == 0,
    }
