"""Validation rule engine — data-quality rules with row-level drill-down.

Runs over the *coerced + fixed* canonical frame (the exact data a commit would write) and
returns a structured result per rule: severity, count, a human message, and a sample of the
offending rows so the UI can drill down to what failed. Rules with ``action == "quarantine"``
contribute their offenders to a quarantine index; those rows are held for review instead of
being silently dropped.

This is deliberately separate from ``ingest.validate`` (which checks the *mapping*: required
fields mapped, no duplicate target). Mapping errors block a commit; data-rule failures route
rows to quarantine and never block the clean rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from . import schema

_ROW_SAMPLE_LIMIT = 50


def _src_row(idx) -> int:
    """Spreadsheet-style source row number (1 header + 1-based)."""
    try:
        return int(idx) + 2
    except (TypeError, ValueError):
        return -1


def _fmt(v) -> object:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (pd.Timestamp, datetime)):
        return str(v)
    if isinstance(v, float):
        return round(v, 4)
    return v


def _rows_for(df: pd.DataFrame, mask: pd.Series, cols: list[str]) -> list[dict]:
    cols = [c for c in cols if c in df.columns]
    sub = df[mask]
    out = []
    for idx, row in sub.head(_ROW_SAMPLE_LIMIT).iterrows():
        out.append({"row": _src_row(idx), "values": {c: _fmt(row[c]) for c in cols}})
    return out


def _rule(key, label, severity, mask, df, cols, action, message_on_fail) -> dict:
    count = int(mask.sum()) if mask is not None else 0
    return {
        "key": key,
        "label": label,
        "severity": severity,        # "error" | "warning" | "info"
        "action": action,            # "quarantine" | "fix" | "none"
        "passed": count == 0,
        "count": count,
        "message": (message_on_fail.format(n=count) if count else "OK"),
        "rows": _rows_for(df, mask, cols) if count else [],
    }


def run_rules(df: pd.DataFrame, table: str, options: dict | None,
              raw_by_target: dict[str, pd.Series] | None = None, con=None) -> dict:
    """Evaluate all data-quality rules against the fixed canonical frame ``df``."""
    options = options or {}
    raw_by_target = raw_by_target or {}
    n = int(len(df))
    rules: list[dict] = []
    quarantine_mask = pd.Series(False, index=df.index)
    reasons: dict[object, list[str]] = {}

    def falsemask():
        return pd.Series(False, index=df.index)

    def _quarantine(mask, key):
        nonlocal quarantine_mask
        quarantine_mask |= mask
        for idx in df.index[mask]:
            reasons.setdefault(idx, []).append(key)

    # 1) Required fields present (rows missing any required key cannot be stored).
    required = [c for c in schema.required_import_keys(table) if c in df.columns]
    if required:
        miss = df[required].isna().any(axis=1)
        r = _rule("required_present", "Required fields present", "error", miss, df,
                  required, "quarantine", "{n} row(s) are missing a required field.")
        rules.append(r)
        _quarantine(miss, "required_present")

    # 1b) Genuine EDI control / heartbeat rows: BOL number 0 AND gross 0 AND net 0 (often product
    #     code "ZZZ"). They carry no lift and ARE held as junk — the one quarantine we keep for
    #     "good wide BOL exports". A blank/zero BOL with a *real* volume is NOT junk (it stays).
    junk_specs = {
        schema.LIFTS: ("bol_number", "gross_gallons", "net_gallons"),
        schema.BOL: ("bol_number", "compartment_gross_gallons", "compartment_net_gallons"),
    }
    if table in junk_specs:
        bcol, gcol, ncol = junk_specs[table]
        # Only a BOL-bearing file can have BOL control rows; need net to judge "no volume".
        if bcol in df.columns and ncol in df.columns:
            def _zeroish(col):
                if col not in df.columns:
                    return pd.Series(True, index=df.index)
                return pd.to_numeric(df[col], errors="coerce").fillna(0) == 0
            def _bol_zero():
                if bcol not in df.columns:
                    return pd.Series(True, index=df.index)
                raw = df[bcol]
                blank = raw.map(lambda v: v is None
                                or (isinstance(v, float) and pd.isna(v))
                                or (isinstance(v, str) and v.strip() == ""))
                return blank | (pd.to_numeric(raw, errors="coerce").fillna(-1) == 0)
            junk_mask = _bol_zero() & _zeroish(gcol) & _zeroish(ncol)
            cols = [c for c in (bcol, gcol, ncol) if c in df.columns]
            r = _rule("edi_control_row", "EDI control / heartbeat rows", "warning", junk_mask,
                      df, cols, "quarantine",
                      "{n} EDI control row(s) (BOL 0, gross 0, net 0) held as junk.")
            rules.append(r)
            _quarantine(junk_mask, "edi_control_row")

    # 2) Dates parseable (non-blank in source but failed to coerce to a date).
    date_targets = [f.name for f in schema.CANONICAL_FIELDS
                    if f.table == table and f.dtype in (schema.DType.DATE, schema.DType.TIMESTAMP)]
    date_targets += [n_ for n_, dt in schema.STRUCTURAL_COLUMNS.get(table, [])
                     if dt in (schema.DType.DATE, schema.DType.TIMESTAMP)]
    unparse_mask = falsemask()
    for t in date_targets:
        if t in df.columns and t in raw_by_target:
            raw = raw_by_target[t].reindex(df.index)
            nonblank = raw.map(lambda v: not (v is None
                                              or (isinstance(v, float) and pd.isna(v))
                                              or (isinstance(v, str) and str(v).strip() == "")))
            unparse_mask |= (nonblank & df[t].isna())
    if date_targets:
        rules.append(_rule("dates_parseable", "Dates parseable", "warning", unparse_mask, df,
                           date_targets, "none",
                           "{n} date value(s) could not be parsed and became blank."))

    # 3) Dates in a sane window (catches typo'd years like 0202 / 2202).
    now = datetime.now()
    lo = pd.Timestamp(year=schema.MIN_REASONABLE_YEAR, month=1, day=1)
    hi = pd.Timestamp(now + timedelta(days=365 * schema.MAX_REASONABLE_YEAR_OFFSET))
    range_mask = falsemask()
    for t in date_targets:
        if t in df.columns:
            col = pd.to_datetime(df[t], errors="coerce")
            range_mask |= (col.notna() & ((col < lo) | (col > hi)))
    if date_targets:
        rules.append(_rule("dates_in_range", "Dates within a sane range", "warning", range_mask,
                           df, date_targets, "none",
                           "{n} date(s) fall outside %d–%d." % (schema.MIN_REASONABLE_YEAR, hi.year)))

    # 4) Negative volumes are legitimate reversals / corrections — KEEP them, never quarantine.
    #    They are tagged and listed for review (and they sum correctly under BOL grouping, so a
    #    reversal compartment nets out against the load it corrects).
    vol_fields = [f for f in schema.volume_fields_for_table(table) if f in df.columns]
    neg_mask = falsemask()
    for f in vol_fields:
        neg_mask |= (df[f].notna() & (df[f] < 0))
    if vol_fields:
        rules.append(_rule(
            "volume_corrections", "Negative volumes (corrections / reversals)", "info",
            neg_mask, df, vol_fields, "none",
            "{n} row(s) carry a negative volume — kept and tagged as a correction/reversal."))

    # 5) Numeric fields within sane bounds (likely unit mismatch / fat-finger).
    bound_fields = [f.name for f in schema.CANONICAL_FIELDS
                    if f.table == table and f.name in schema.FIELD_BOUNDS and f.name in df.columns]
    bounds_mask = falsemask()
    for f in bound_fields:
        lob, hib = schema.FIELD_BOUNDS[f]
        if f in schema.VOLUME_FIELDS:
            # A negative volume is a correction (own rule), not a unit mismatch — flag only highs.
            bounds_mask |= (df[f].notna() & (df[f] > hib))
        else:
            bounds_mask |= (df[f].notna() & ((df[f] < lob) | (df[f] > hib)))
    if bound_fields:
        rules.append(_rule("value_bounds", "Values within sane bounds", "warning", bounds_mask,
                           df, bound_fields, "none",
                           "{n} value(s) are outside the expected range (check units)."))

    # 6) Duplicate lifts (same customer + datetime + net gallons).
    if table == schema.LIFTS:
        grain = [c for c in ("customer_id", "lift_datetime", "net_gallons") if c in df.columns]
        dup_mask = falsemask()
        if len(grain) == 3 and n:
            dup_mask = df.duplicated(subset=grain, keep="first")
        quarantine_dupes = bool(options.get("dedupe_lifts_grain"))
        action = "quarantine" if quarantine_dupes else "none"
        sev = "warning"
        r = _rule("duplicate_lifts", "Duplicate lifts (same customer · time · gallons)", sev,
                  dup_mask, df, grain, action,
                  "{n} duplicate lift(s) detected on the customer · datetime · net-gallons grain.")
        rules.append(r)
        if quarantine_dupes:
            _quarantine(dup_mask, "duplicate_lifts")

    # 7) Price / cost sanity (selling below cost, or non-positive price).
    if table == schema.LIFTS and {"unit_price", "unit_cost"} <= set(df.columns):
        below = (df["unit_price"].notna() & df["unit_cost"].notna()
                 & (df["unit_price"] < df["unit_cost"]))
        rules.append(_rule("price_cost_sanity", "Price ≥ cost", "warning", below, df,
                           ["unit_price", "unit_cost", "net_gallons", "customer_id"], "none",
                           "{n} lift(s) priced below cost (negative margin)."))

    q_index = list(df.index[quarantine_mask])
    return {
        "rules": rules,
        "n_rows": n,
        "quarantine_index": q_index,
        "quarantine_reasons": {idx: reasons.get(idx, []) for idx in q_index},
        "quarantine_count": len(q_index),
        "n_errors": sum(1 for r in rules if r["severity"] == "error" and not r["passed"]),
        "n_warnings": sum(1 for r in rules if r["severity"] == "warning" and not r["passed"]),
    }
