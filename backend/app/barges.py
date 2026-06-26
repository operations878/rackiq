"""Inbound barge-supply ingestion (Phase 7) — the Trips report → the ``barge_discharges`` store.

The Trips report is a messy operator workbook listing every barge **discharge** into a terminal.
``pricegrid.parse_trips`` already reads it for *landed cost* (the margin layer); this module reads the
SAME file for the *supply / position* side: how many delivered gallons landed where, when. The two
are deliberately separate concerns landing in separate stores (cost vs. volume), so Phase-7 position
and Phase-2 margin never fight over one table.

THE #1 UNIT TRAP (and the whole reason this is a dedicated parser): **Trips volumes are in BARRELS.**
Gallons are canonical everywhere downstream (lifts, receipts, inventory are all gallons). So the
barrels→gallons ``×42`` conversion happens **exactly once**, right here in :func:`parse_trips_supply`
(``nominal_gallons = volume_bbl × GALLONS_PER_BARREL``), is asserted, and is reported in the load
summary. The position engine reads ``delivered_gallons`` (already gallons) and NEVER re-multiplies.

Per discharge we capture: terminal, product (→ family), discharge/ETA date, barrels (→ gallons),
the **VEF** (vessel experience factor) + the derived **transit gain/loss**, and the landed cost
¢/gal as *metadata only*. The actual **delivered** gallons applies the VEF to the nominal cargo and
the row records WHICH volume basis was used (``nominal`` | ``vef_adjusted``), so nothing is presented
as more certain than it is.

The store is idempotent (upsert on a stable ``discharge_key``), survives reset/demo (it is uploaded
real data, created by :func:`ensure_tables`, NOT in ``schema.ALL_TABLES``) exactly like ``deals`` /
``landed_costs``, and is re-uploadable through Data Studio (``POST /api/position/upload``).
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import asdict, dataclass, replace

import pandas as pd

from . import dealbook, pricegrid, schema
from .ingest import _norm
from .margin_config import DEFAULT_CONFIG as _MARGIN_DEFAULT

logger = logging.getLogger(__name__)

# One US barrel = 42 US gallons — the single conversion this module is responsible for.
GALLONS_PER_BARREL = schema.GALLONS_PER_BARREL


@dataclass(frozen=True)
class BargeConfig:
    """Parser knobs for the Trips supply read (units + the VEF plausibility band)."""
    mb_threshold_bbl: float = _MARGIN_DEFAULT.mb_threshold_bbl   # < this ⇒ "mb" (thousand-bbl ×1000)
    gallons_per_barrel: float = GALLONS_PER_BARREL
    # A VEF is a near-1.0 multiplier (ship-vs-shore). Outside this band it is almost certainly a
    # mis-mapped column, so we ignore it for the delivered volume and fall back to nominal.
    vef_min: float = 0.95
    vef_max: float = 1.05

    def to_dict(self) -> dict:
        return asdict(self)

    def with_overrides(self, overrides: dict | None) -> "BargeConfig":
        if not overrides:
            return self
        known = set(self.__dataclass_fields__)  # type: ignore[attr-defined]
        return replace(self, **{k: v for k, v in overrides.items() if k in known})


DEFAULT_CONFIG = BargeConfig()


# ---- store (created on demand; survives reset/demo like the deal book / landed_costs) ----
BARGE_DISCHARGES_DDL = """CREATE TABLE IF NOT EXISTS barge_discharges (
    discharge_key VARCHAR PRIMARY KEY,
    terminal VARCHAR,
    product_family VARCHAR,
    product_raw VARCHAR,
    discharge_date DATE,
    volume_bbl DOUBLE,                 -- nominal cargo barrels (as read from the Trips report)
    vol_unit VARCHAR,                  -- 'bbl' | 'mb' (the magnitude heuristic's decision)
    nominal_gallons DOUBLE,            -- volume_bbl × 42  (the ×42 happens ONCE, in parse_trips_supply)
    vef DOUBLE,                        -- vessel experience factor (NULL if absent / implausible)
    transit_gain_loss_gallons DOUBLE,  -- signed delivered − nominal (NULL when nominal basis)
    delivered_gallons DOUBLE,          -- actual delivered gallons (VEF-adjusted) — the canonical supply figure
    volume_basis VARCHAR,              -- 'nominal' | 'vef_adjusted' (which volume we trusted)
    landed_cost_cpg DOUBLE,            -- metadata only: sum of the $/gal logistics legs, in ¢/gal
    pricing_type VARCHAR,
    source_file VARCHAR,
    imported_at VARCHAR
)"""

_COLS = ["discharge_key", "terminal", "product_family", "product_raw", "discharge_date",
         "volume_bbl", "vol_unit", "nominal_gallons", "vef", "transit_gain_loss_gallons",
         "delivered_gallons", "volume_basis", "landed_cost_cpg", "pricing_type",
         "source_file", "imported_at"]


def ensure_tables(con) -> None:
    con.execute(BARGE_DISCHARGES_DDL)


# ---- format-aware parse (reuses pricegrid's Trips column matcher + mb heuristic) --------
def _delivered(nominal_gal: float | None, vef: float | None,
               cfg: BargeConfig) -> tuple[float | None, float | None, str]:
    """Apply VEF / transit gain-loss to the nominal cargo → (delivered_gallons, transit_gl, basis).

    VEF is the standard ship-vs-shore multiplier; ``delivered = nominal × VEF`` and the transit
    gain/loss is the derived ``delivered − nominal``. A VEF outside the plausibility band is ignored
    (basis stays ``nominal``) so a mis-mapped column never silently distorts the delivered volume.
    """
    if nominal_gal is None:
        return None, None, "nominal"
    if vef is not None and cfg.vef_min <= vef <= cfg.vef_max:
        delivered = nominal_gal * vef
        return round(delivered, 1), round(delivered - nominal_gal, 1), "vef_adjusted"
    return round(nominal_gal, 1), None, "nominal"


def _landed_cost_cpg(barge, inspector, operational, gainloss) -> float | None:
    legs = [x for x in (barge, inspector, operational, gainloss) if x is not None]
    return round(sum(legs) * 100.0, 4) if legs else None   # $/gal → ¢/gal (metadata only)


def parse_trips_supply(path: str, cfg: BargeConfig = DEFAULT_CONFIG) -> dict:
    """Parse the Trips report into inbound barge-discharge rows (one per discharge).

    Returns ``{"rows": [...], "conversion": {...}, "sheet": name}``. The ``conversion`` block is the
    auditable proof the barrels→gallons ``×42`` ran exactly once over every parsed discharge.
    """
    # Pick the sheet whose header matches the most Trips targets AND carries a volume column.
    best_sheet, best_rows, best_cols, best_score = None, None, {}, -1
    for sn in pricegrid._sheet_names(path):
        rows = pricegrid._sheet_rows(path, sn)
        if not rows:
            continue
        for r in rows[:30]:
            cols = pricegrid._match_trip_columns(r)
            score = len(cols) + (5 if "volume_bbl" in cols else 0)
            if score > best_score and "volume_bbl" in cols and (
                    "discharge_date" in cols or "terminal" in cols):
                best_sheet, best_rows, best_cols, best_score = sn, rows, cols, score
    if best_rows is None:
        return {"rows": [], "conversion": {"discharges": 0, "factor": cfg.gallons_per_barrel},
                "sheet": None}

    # Locate the header row index (the first row that yields the chosen column map).
    hdr_i = next((i for i, r in enumerate(best_rows[:30])
                  if pricegrid._match_trip_columns(r) == best_cols), 0)

    out: list[dict] = []
    bbl_seen = gal_made = 0.0
    n_conv = 0
    for r in best_rows[hdr_i + 1:]:
        def g(key):
            j = best_cols.get(key)
            return r[j] if j is not None and j < len(r) else None

        vol_raw = dealbook._to_num(g("volume_bbl"))
        vol_bbl, vol_unit = pricegrid._resolve_volume(vol_raw, _MARGIN_DEFAULT)
        if vol_bbl is None or vol_bbl <= 0:
            continue  # a row with no cargo volume is not a discharge (header echo / control row)

        # ---- the ONE barrels→gallons conversion (×42), asserted ----
        nominal_gal = vol_bbl * cfg.gallons_per_barrel
        assert math.isclose(nominal_gal, vol_bbl * 42.0, rel_tol=1e-9), "barrels→gallons must be ×42"
        bbl_seen += vol_bbl
        gal_made += nominal_gal
        n_conv += 1

        vef = dealbook._to_num(g("vef"))
        delivered, transit_gl, basis = _delivered(nominal_gal, vef, cfg)
        terminal = (str(g("terminal")).strip() if g("terminal") is not None else None) or None
        product_raw = (str(g("product_raw")).strip() if g("product_raw") is not None else None) or None
        out.append({
            "terminal": terminal,
            "product_family": dealbook.product_family(product_raw) if product_raw else "OTHER",
            "product_raw": product_raw,
            "discharge_date": dealbook._to_date(g("discharge_date")),
            "volume_bbl": round(vol_bbl, 2), "vol_unit": vol_unit,
            "nominal_gallons": round(nominal_gal, 1),
            "vef": vef if (vef is not None and cfg.vef_min <= vef <= cfg.vef_max) else None,
            "transit_gain_loss_gallons": transit_gl,
            "delivered_gallons": delivered, "volume_basis": basis,
            "landed_cost_cpg": _landed_cost_cpg(
                dealbook._to_num(g("barge_cost")), dealbook._to_num(g("inspector_cost")),
                dealbook._to_num(g("operational_cost")), dealbook._to_num(g("gainloss_cost"))),
            "pricing_type": (str(g("pricing_type")).strip() if g("pricing_type") is not None else None),
        })

    conv = {"discharges": n_conv, "factor": cfg.gallons_per_barrel,
            "total_barrels": round(bbl_seen, 1), "total_nominal_gallons": round(gal_made, 1),
            "note": f"barrels→gallons ×{cfg.gallons_per_barrel:g} applied once to {n_conv} discharges"}
    return {"rows": out, "conversion": conv, "sheet": best_sheet}


# ---- stable key + idempotent upsert (delete-then-insert on the PK) ----------------------
def discharge_key(terminal, fam, d, vol_bbl, vef) -> str:
    parts = [(_norm(terminal) if terminal else ""), (fam or "").lower(),
             d.isoformat() if d else "", f"{round(vol_bbl or 0)}", f"{round(vef or 0, 4)}"]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]


def _col_type(col: str) -> str:
    if col == "discharge_date":
        return "DATE"
    if col in ("volume_bbl", "nominal_gallons", "vef", "transit_gain_loss_gallons",
               "delivered_gallons", "landed_cost_cpg"):
        return "DOUBLE"
    return "VARCHAR"


def upsert_barge_discharges(con, rows: list[dict], source_file: str, now: str) -> int:
    if not rows:
        return 0
    keyed: dict[str, dict] = {}
    for r in rows:
        r = dict(r)
        r["discharge_key"] = discharge_key(r.get("terminal"), r.get("product_family"),
                                           r.get("discharge_date"), r.get("volume_bbl"), r.get("vef"))
        r["source_file"] = source_file
        r["imported_at"] = now
        keyed[r["discharge_key"]] = r          # within-file: last write wins for a repeated key
    deduped = list(keyed.values())
    keys = list(keyed.keys())
    ph = ", ".join("?" for _ in keys)
    con.execute(f"DELETE FROM barge_discharges WHERE discharge_key IN ({ph})", keys)
    df = pd.DataFrame(deduped)
    for c in _COLS:
        if c not in df.columns:
            df[c] = None
    df = df[_COLS]
    con.register("_barge_ins", df)
    try:
        sel = ", ".join(f'CAST("{c}" AS {_col_type(c)}) AS "{c}"' for c in _COLS)
        con.execute(f"INSERT INTO barge_discharges ({', '.join(_COLS)}) SELECT {sel} FROM _barge_ins")
    finally:
        con.unregister("_barge_ins")
    return len(deduped)


# ---- reads -----------------------------------------------------------------------------
def read_barge_discharges(con) -> pd.DataFrame:
    ensure_tables(con)
    return con.execute(f"SELECT {', '.join(_COLS)} FROM barge_discharges").df()


def store_counts(con) -> dict:
    ensure_tables(con)
    n = int(con.execute("SELECT count(*) FROM barge_discharges").fetchone()[0])
    gal = con.execute("SELECT coalesce(sum(delivered_gallons), 0) FROM barge_discharges").fetchone()[0]
    terms = int(con.execute(
        "SELECT count(DISTINCT terminal) FROM barge_discharges WHERE terminal IS NOT NULL").fetchone()[0])
    return {"barge_discharges": n, "delivered_gallons": round(float(gal or 0.0), 1),
            "terminals": terms}


# ---- workbook-level parse + idempotent load --------------------------------------------
def load_trips_supply_file(con, path: str, now: str, cfg: BargeConfig = DEFAULT_CONFIG) -> dict:
    """Parse + idempotently upsert one Trips report into ``barge_discharges``."""
    ensure_tables(con)
    parsed = parse_trips_supply(path, cfg)
    fname = path.split("/")[-1]
    n = upsert_barge_discharges(con, parsed["rows"], fname, now)
    conv = parsed["conversion"]
    logger.info("barges: %s — %s", fname, conv.get("note", ""))
    vef_rows = sum(1 for r in parsed["rows"] if r.get("volume_basis") == "vef_adjusted")
    return {"discharges_written": n, "filename": fname, "sheet": parsed["sheet"],
            "conversion": conv, "vef_adjusted": vef_rows,
            "nominal_only": len(parsed["rows"]) - vef_rows}


def _is_trips_file(name: str) -> bool:
    return "trip" in name.lower()


def load_barges_dir(con, directory: str, now: str, cfg: BargeConfig = DEFAULT_CONFIG) -> dict:
    """One-shot: load every Trips report found in a directory into ``barge_discharges``."""
    import glob
    import os
    ensure_tables(con)
    loaded = []
    for p in sorted(glob.glob(os.path.join(directory, "*"))):
        base = os.path.basename(p)
        if _is_trips_file(base) and base.lower().endswith((".xls", ".xlsx", ".csv")):
            loaded.append(load_trips_supply_file(con, p, now, cfg))
    return {"loaded": loaded, "stores": store_counts(con)}
