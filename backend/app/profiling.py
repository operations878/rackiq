"""Column profiling — the data-quality scorecard shown the instant a file is uploaded.

For every source column we report type, null %, distinct count, min/max, sample values,
outlier counts, and a set of human-readable quality flags (high-null, mixed-type, constant,
negatives, possible unit mismatch, …). This is intentionally storage- and mapping-agnostic:
it profiles the *raw* uploaded frame so the user sees problems before mapping or committing.
"""

from __future__ import annotations

import pandas as pd

from .ingest import _clean_numeric, _dtype_guess, _looks_datelike, _looks_numeric, _null_rate, _sample_values


def _nonblank(series: pd.Series) -> pd.Series:
    return series.map(lambda v: not (v is None
                                     or (isinstance(v, float) and pd.isna(v))
                                     or (isinstance(v, str) and str(v).strip() == "")))


def _numeric_outliers(nums: pd.Series) -> tuple[int, list[float]]:
    """Count IQR-fence outliers; return (count, [low_fence, high_fence])."""
    clean = nums.dropna()
    if len(clean) < 8:
        return 0, []
    q1, q3 = float(clean.quantile(0.25)), float(clean.quantile(0.75))
    iqr = q3 - q1
    if iqr <= 0:
        return 0, []
    lo, hi = q1 - 3.0 * iqr, q3 + 3.0 * iqr
    n = int(((clean < lo) | (clean > hi)).sum())
    return n, [round(lo, 4), round(hi, 4)]


def profile_column(series: pd.Series) -> dict:
    n = int(len(series))
    nonblank_mask = _nonblank(series)
    nonblank = series[nonblank_mask]
    dtype = _dtype_guess(series)
    distinct = int(nonblank.map(lambda v: str(v).strip()).nunique()) if len(nonblank) else 0
    null_rate = _null_rate(series)

    out: dict = {
        "name": str(series.name),
        "dtype_guess": dtype,
        "null_rate": null_rate,
        "distinct": distinct,
        "n_total": n,
        "n_nonblank": int(nonblank_mask.sum()),
        "samples": _sample_values(series),
        "min": None,
        "max": None,
        "outliers": 0,
        "flags": [],
    }
    flags: list[dict] = out["flags"]

    # Numeric stats + mixed-type + outlier detection.
    if dtype == "number":
        nums = _clean_numeric(series)
        parsed = nums.notna() & nonblank_mask
        mixed = int((nonblank_mask & nums.isna()).sum())
        if parsed.any():
            out["min"] = round(float(nums[parsed].min()), 4)
            out["max"] = round(float(nums[parsed].max()), 4)
            n_out, fences = _numeric_outliers(nums)
            out["outliers"] = n_out
            if fences:
                out["fences"] = fences
            if int((nums[parsed] < 0).sum()) > 0:
                flags.append({"level": "info", "code": "negatives",
                              "message": "Contains negative values."})
            if n_out:
                flags.append({"level": "info", "code": "outliers",
                              "message": f"{n_out} value(s) far outside the typical range."})
        if mixed:
            flags.append({"level": "warn", "code": "mixed_type",
                          "message": f"{mixed} value(s) are not numeric."})
    elif dtype == "date":
        dts = pd.to_datetime(series, errors="coerce")
        parsed = dts.notna()
        unparsed = int((nonblank_mask & dts.isna()).sum())
        if parsed.any():
            out["min"] = str(pd.Timestamp(dts[parsed].min()).date())
            out["max"] = str(pd.Timestamp(dts[parsed].max()).date())
        if unparsed:
            flags.append({"level": "warn", "code": "unparsed_dates",
                          "message": f"{unparsed} value(s) are not recognizable dates."})
    else:  # text
        lengths = nonblank.map(lambda v: len(str(v).strip()))
        if len(lengths):
            out["min"] = int(lengths.min())
            out["max"] = int(lengths.max())
        # whitespace that trimming would change
        ws = int(nonblank.map(lambda v: isinstance(v, str) and v != v.strip()).sum())
        if ws:
            flags.append({"level": "info", "code": "whitespace",
                          "message": f"{ws} value(s) have leading/trailing whitespace."})

    # Cross-type flags.
    if null_rate >= 0.5:
        flags.append({"level": "warn", "code": "high_null",
                      "message": f"{round(null_rate * 100)}% of values are blank."})
    elif null_rate >= 0.1:
        flags.append({"level": "info", "code": "some_null",
                      "message": f"{round(null_rate * 100)}% of values are blank."})
    if out["n_nonblank"] and distinct <= 1:
        flags.append({"level": "info", "code": "constant",
                      "message": "Every populated value is identical."})

    out["quality"] = round(_column_quality(out), 4)
    return out


def _column_quality(p: dict) -> float:
    """A 0..1 per-column quality contribution penalizing blanks and warnings."""
    q = 1.0 - min(1.0, p["null_rate"])
    for f in p["flags"]:
        if f["level"] == "warn":
            q -= 0.25
        elif f["code"] in ("outliers", "negatives"):
            q -= 0.05
    return max(0.0, q)


def profile_frame(df: pd.DataFrame) -> dict:
    """Profile every column; return the scorecard payload (+ an overall 0..100 score)."""
    columns = [profile_column(df[c]) for c in df.columns]
    score = round(100.0 * (sum(c["quality"] for c in columns) / len(columns)), 1) if columns else 0.0
    n_warn = sum(1 for c in columns for f in c["flags"] if f["level"] == "warn")
    return {
        "n_rows": int(len(df)),
        "n_columns": int(len(df.columns)),
        "score": score,
        "columns": columns,
        "n_flagged_columns": sum(1 for c in columns if c["flags"]),
        "n_warnings": n_warn,
    }
