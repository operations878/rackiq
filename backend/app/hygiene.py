"""Data Hygiene Studio — the configurable cleaning pipeline between mapping and the store.

Commit hands a mapped, type-coerced DataFrame here *before* it is written. Every step is
opt-in via :class:`HygieneOptions`, conservative by default, and reports both a human-facing
line (for the wizard) and a structured audit entry (persisted to the ``hygiene_audit`` log).
Nothing destructive happens without the user's chosen options.

Steps (in order):
  1. trim_whitespace        — strip surrounding whitespace, blank emptied strings
  2. drop_empty_rows        — remove rows with no values at all
  3. standardize_units      — barrels → gallons (×42) on volume fields
  4. fill_defaults          — fill missing terminal/product from a chosen default
  5. net_60_correction      — ASTM D1250-style gross→net(60°F) volume correction
  6. resolve_customers      — apply the Customer Master crosswalk (variant → master id)

Deduplication and quarantine routing are orchestrated by the caller (``api/studio.py``)
*after* fixes, using the validation rule engine, so failing rows are held — never dropped.

Backward-compatible ``run_pipeline(df, table)`` is preserved for the conservative default.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import pandas as pd

from . import schema


# ---- Options --------------------------------------------------------------------
@dataclass
class HygieneOptions:
    trim_whitespace: bool = True
    drop_empty_rows: bool = True
    standardize_units: bool = False
    source_unit: str = "gallons"            # "gallons" | "barrels"
    fill_defaults: bool = False
    default_terminal: str | None = None
    default_product: str | None = None
    net_correction: str = "auto"            # "auto" | "factor" | "gross" | "off"
    net_factor: float | None = None
    resolve_customers: bool = True
    dedupe_exact: bool = True
    dedupe_lifts_grain: bool = False
    quarantine_failures: bool = True

    @classmethod
    def from_dict(cls, d: dict | None) -> "HygieneOptions":
        d = d or {}
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> dict:
        return asdict(self)


# ---- ASTM D1250-style volume correction factor (gross → net at 60°F) ------------
# Generalized products coefficients (K0, K1) for the thermal-expansion coefficient
# alpha60 = (K0 + K1*rho60) / rho60^2, with density rho60 in kg/m^3 at 60°F.
_K = {
    "gasoline":   (346.4228, 0.4388),   # RBOB / motor gasolines
    "distillate": (186.9696, 0.4862),   # ULSD / ULSHO / fuel oils
    "crude":      (341.0957, 0.0),      # fallback
}
_WATER_60F = 999.016  # kg/m^3


def _product_group(product: str | None, api: float | None) -> str:
    p = (product or "").upper()
    if "RBOB" in p or "GAS" in p or "MOGAS" in p:
        return "gasoline"
    if any(t in p for t in ("ULSD", "ULSHO", "DIESEL", "DISTILLATE", "HEAT", "FUEL OIL", "KERO")):
        return "distillate"
    if api is not None and not math.isnan(api):
        return "gasoline" if api >= 45 else "distillate"
    return "crude"


def vcf(api: float, temp_f: float, product: str | None = None) -> float:
    """ASTM D1250-style volume correction factor at observed temperature ``temp_f``."""
    if api is None or temp_f is None or math.isnan(api) or math.isnan(temp_f):
        return 1.0
    sg60 = 141.5 / (131.5 + api)
    rho60 = sg60 * _WATER_60F
    if rho60 <= 0:
        return 1.0
    k0, k1 = _K[_product_group(product, api)]
    alpha60 = (k0 + k1 * rho60) / (rho60 * rho60)
    dt = temp_f - 60.0
    return math.exp(-alpha60 * dt * (1.0 + 0.8 * alpha60 * dt))


def _net_from_row(gross, temp, api, product) -> float | None:
    if gross is None or (isinstance(gross, float) and math.isnan(gross)):
        return None
    return float(gross) * vcf(api, temp, product)


# ---- Individual steps -----------------------------------------------------------
def _emit(report, audit, step, detail, n):
    report.append({"step": step, "detail": detail, "rows_affected": int(n)})
    audit.append({"step": step, "detail": detail, "rows_affected": int(n)})


def _trim(df, report, audit):
    affected = 0
    for col in df.columns:
        if df[col].dtype == object or pd.api.types.is_string_dtype(df[col].dtype):
            before = df[col]
            stripped = before.map(lambda v: v.strip() if isinstance(v, str) else v)
            stripped = stripped.map(lambda v: None if (isinstance(v, str) and v == "") else v)
            changed = (before.fillna("__na__") != stripped.fillna("__na__")).sum()
            if changed:
                affected += int(changed)
                df[col] = stripped
    if affected:
        _emit(report, audit, "trim_whitespace",
              "Trimmed surrounding whitespace and blanked empty strings.", affected)
    return df


def _drop_empty(df, report, audit):
    before = len(df)
    df = df.dropna(how="all")
    dropped = before - len(df)
    if dropped:
        _emit(report, audit, "drop_empty_rows", "Removed fully-blank rows.", dropped)
    return df


def _standardize_units(df, table, opts, report, audit):
    if not opts.standardize_units or opts.source_unit != "barrels":
        return df
    vol_cols = [c for c in schema.volume_fields_for_table(table) if c in df.columns]
    affected = 0
    for c in vol_cols:
        col = pd.to_numeric(df[c], errors="coerce")
        affected = max(affected, int(col.notna().sum()))
        df[c] = col * schema.GALLONS_PER_BARREL
    if vol_cols:
        _emit(report, audit, "standardize_units",
              f"Converted barrels → gallons (×{schema.GALLONS_PER_BARREL:g}) on "
              f"{', '.join(vol_cols)}.", affected)
    return df


def _fill_defaults(df, table, opts, report, audit):
    if not opts.fill_defaults:
        return df
    defaults = {"terminal": opts.default_terminal, "product": opts.default_product}
    for field in schema.DEFAULTABLE_FIELDS.get(table, []):
        val = defaults.get(field)
        if val and field in df.columns:
            mask = df[field].isna()
            n = int(mask.sum())
            if n:
                df.loc[mask, field] = val
                _emit(report, audit, "fill_defaults",
                      f"Filled {n} missing '{field}' with default '{val}'.", n)
        elif val and field not in df.columns:
            df[field] = val
            _emit(report, audit, "fill_defaults",
                  f"Set '{field}' to default '{val}' for all rows.", len(df))
    return df


def _net_correction(df, table, opts, report, audit):
    if table != schema.LIFTS or opts.net_correction == "off":
        return df
    if "gross_gallons" not in df.columns:
        _emit(report, audit, "net_60_correction",
              "Skipped — gross_gallons not provided; nothing to correct.", 0)
        return df

    has_temp = "observed_temp" in df.columns
    has_api = "api_gravity" in df.columns
    # net_gallons must be float so we can write corrected values into it (pandas 2.x is strict).
    if "net_gallons" not in df.columns:
        df["net_gallons"] = float("nan")
    else:
        df["net_gallons"] = pd.to_numeric(df["net_gallons"], errors="coerce").astype("float64")

    gross = pd.to_numeric(df["gross_gallons"], errors="coerce")
    temp = pd.to_numeric(df["observed_temp"], errors="coerce") if has_temp else pd.Series(float("nan"), index=df.index)
    api = pd.to_numeric(df["api_gravity"], errors="coerce") if has_api else pd.Series(float("nan"), index=df.index)
    product = df["product"] if "product" in df.columns else pd.Series([None] * len(df), index=df.index)

    full = gross.notna() & temp.notna() & api.notna()
    corrected = 0
    if opts.net_correction == "auto":
        # Authoritative D1250 correction wherever all three inputs exist.
        for idx in df.index[full]:
            df.at[idx, "net_gallons"] = round(
                _net_from_row(gross[idx], temp[idx], api[idx], product[idx]), 1)
        corrected = int(full.sum())
        # Rows lacking temp/api: if net missing, fall back to gross (factor 1.0).
        fallback = (~full) & gross.notna() & df["net_gallons"].isna()
        nfb = int(fallback.sum())
        df.loc[fallback, "net_gallons"] = gross[fallback].round(1)
        detail = (f"Computed ASTM D1250 net(60°F) for {corrected} row(s)"
                  + (f"; used gross as net for {nfb} row(s) lacking temperature/API." if nfb else "."))
        _emit(report, audit, "net_60_correction", detail, corrected + nfb)
    elif opts.net_correction == "factor":
        factor = float(opts.net_factor or 1.0)
        mask = gross.notna()
        df.loc[mask, "net_gallons"] = (gross[mask] * factor).round(1)
        corrected = int(mask.sum())
        _emit(report, audit, "net_60_correction",
              f"Applied flat correction factor {factor:g} to gross for {corrected} row(s).", corrected)
    elif opts.net_correction == "gross":
        mask = gross.notna() & df["net_gallons"].isna()
        df.loc[mask, "net_gallons"] = gross[mask].round(1)
        n = int(mask.sum())
        _emit(report, audit, "net_60_correction",
              f"Proceeded on gross (net = gross) for {n} row(s) with no net provided.", n)
    return df


def _resolve_customers(df, table, opts, con, report, audit):
    if not opts.resolve_customers or con is None:
        return df
    key_col = schema.customer_key_column(table)
    if not key_col or key_col not in df.columns:
        return df
    from . import crosswalk
    df, n_remapped, rewrites = crosswalk.apply_to_frame(df, key_col, con)
    if n_remapped:
        sample = "; ".join(f"{r['from']}→{r['to']}" for r in rewrites[:4])
        more = "" if len(rewrites) <= 4 else f" (+{len(rewrites) - 4} more)"
        _emit(report, audit, "resolve_customers",
              f"Resolved {n_remapped} row(s) to master ids via the customer crosswalk "
              f"[{sample}{more}].", n_remapped)
    return df


# ---- Public API -----------------------------------------------------------------
def apply_fixes(df: pd.DataFrame, table: str, options: HygieneOptions | dict | None = None,
                con=None) -> tuple[pd.DataFrame, list[dict], list[dict]]:
    """Apply the approved, non-routing fixes. Returns (df, report, audit)."""
    opts = options if isinstance(options, HygieneOptions) else HygieneOptions.from_dict(options)
    report: list[dict] = []
    audit: list[dict] = []
    df = df.copy()
    if opts.trim_whitespace:
        df = _trim(df, report, audit)
    if opts.drop_empty_rows:
        df = _drop_empty(df, report, audit)
    df = _standardize_units(df, table, opts, report, audit)
    df = _fill_defaults(df, table, opts, report, audit)
    df = _net_correction(df, table, opts, report, audit)
    df = _resolve_customers(df, table, opts, con, report, audit)
    return df, report, audit


def dedupe_exact(df: pd.DataFrame, report: list[dict], audit: list[dict]) -> pd.DataFrame:
    """Drop rows identical across every mapped column (lossless)."""
    before = len(df)
    df = df.drop_duplicates(keep="first")
    dropped = before - len(df)
    if dropped:
        _emit(report, audit, "dedupe_exact",
              "Dropped rows identical across every mapped column.", dropped)
    return df


def run_pipeline(df: pd.DataFrame, table: str,
                 key_columns: list[str] | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """Conservative default pipeline (trim, drop-empty, drop-missing-required, exact dedupe).

    Preserved for callers/tests that want the lossless default without the full options set.
    """
    report: list[dict] = []
    audit: list[dict] = []
    df = df.copy()
    df = _trim(df, report, audit)
    df = _drop_empty(df, report, audit)
    required = [c for c in schema.required_import_keys(table) if c in df.columns]
    if required:
        before = len(df)
        df = df.dropna(subset=required)
        dropped = before - len(df)
        if dropped:
            _emit(report, audit, "drop_missing_required",
                  f"Removed rows missing a required key ({', '.join(required)}).", dropped)
    df = dedupe_exact(df, report, audit)
    df = df.reset_index(drop=True)
    if not report:
        report.append({"step": "noop", "detail": "Data was already clean — no changes made.",
                       "rows_affected": 0})
    return df, report
