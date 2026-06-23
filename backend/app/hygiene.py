"""Hygiene Studio pipeline — the cleaning seam between Data Studio and the canonical store.

Data Studio hands a mapped, type-coerced DataFrame to :func:`run_pipeline` *before* it is
written to a canonical table. This module is the single place where data-cleaning steps
live. The next phase ("Hygiene Studio") will grow this into a configurable, inspectable
pipeline (dedupe strategies, outlier handling, unit normalization, fuzzy entity
resolution, …); for now it performs a small set of safe, universally-correct passes and
returns a structured report of everything it did so the UI can show it.

The contract is intentionally stable so later phases can expand the internals without
touching callers:

    cleaned_df, report = run_pipeline(df, table, key_columns=[...])

``report`` is a list of ``{"step", "detail", "rows_affected"}`` dicts.
"""

from __future__ import annotations

import pandas as pd

from . import schema


def _trim_strings(df: pd.DataFrame, report: list[dict]) -> pd.DataFrame:
    """Strip leading/trailing whitespace from object (string) columns."""
    affected = 0
    for col in df.columns:
        if df[col].dtype == object:
            before = df[col]
            stripped = before.map(lambda v: v.strip() if isinstance(v, str) else v)
            # Treat emptied strings as missing so null-rate / coverage stay honest.
            stripped = stripped.map(lambda v: None if (isinstance(v, str) and v == "") else v)
            changed = (before.fillna("__na__") != stripped.fillna("__na__")).sum()
            if changed:
                affected += int(changed)
                df[col] = stripped
    if affected:
        report.append({
            "step": "trim_whitespace",
            "detail": "Trimmed surrounding whitespace and blanked empty strings.",
            "rows_affected": affected,
        })
    return df


def _drop_empty_rows(df: pd.DataFrame, report: list[dict]) -> pd.DataFrame:
    """Drop rows that are entirely null across every mapped column."""
    before = len(df)
    df = df.dropna(how="all").reset_index(drop=True)
    dropped = before - len(df)
    if dropped:
        report.append({
            "step": "drop_empty_rows",
            "detail": "Removed rows with no values in any mapped column.",
            "rows_affected": dropped,
        })
    return df


def _drop_missing_required(df: pd.DataFrame, table: str, report: list[dict]) -> pd.DataFrame:
    """Drop rows missing any required key for the target table (cannot be stored)."""
    required = [c for c in schema.required_import_keys(table) if c in df.columns]
    if not required:
        return df
    before = len(df)
    df = df.dropna(subset=required).reset_index(drop=True)
    dropped = before - len(df)
    if dropped:
        report.append({
            "step": "drop_missing_required",
            "detail": f"Removed rows missing a required key ({', '.join(required)}).",
            "rows_affected": dropped,
        })
    return df


def _dedupe(df: pd.DataFrame, key_columns: list[str] | None, report: list[dict]) -> pd.DataFrame:
    """Drop duplicate rows (keep first).

    Conservative-by-default: when no key columns are given, only *exact* duplicates
    (identical across every mapped column) are removed, which can never collapse distinct
    records. Grain-aware deduplication is a job for the forthcoming Hygiene Studio.
    """
    keys = [c for c in (key_columns or []) if c in df.columns] or list(df.columns)
    if not keys:
        return df
    before = len(df)
    df = df.drop_duplicates(subset=keys, keep="first").reset_index(drop=True)
    dropped = before - len(df)
    if dropped:
        scope = "identical across all mapped columns" if keys == list(df.columns) \
            else f"sharing the same {', '.join(keys)}"
        report.append({
            "step": "dedupe",
            "detail": f"Dropped duplicate rows ({scope}).",
            "rows_affected": dropped,
        })
    return df


def run_pipeline(
    df: pd.DataFrame,
    table: str,
    key_columns: list[str] | None = None,
) -> tuple[pd.DataFrame, list[dict]]:
    """Clean a mapped DataFrame on its way into a canonical table.

    Returns the cleaned frame and a report of the steps applied. Steps are conservative
    and lossless-by-default; the forthcoming Hygiene Studio will make them configurable.
    ``key_columns`` may pin deduplication to a grain key; by default only exact-duplicate
    rows are removed.
    """
    report: list[dict] = []
    df = df.copy()
    df = _trim_strings(df, report)
    df = _drop_empty_rows(df, report)
    df = _drop_missing_required(df, table, report)
    df = _dedupe(df, key_columns, report)
    if not report:
        report.append({
            "step": "noop",
            "detail": "Data was already clean — no changes made.",
            "rows_affected": 0,
        })
    return df, report
