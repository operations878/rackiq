"""HDD / weather ingestion — the Heating Degree Day book as a re-uploadable Data Studio source.

Source: ``4__Demand_Scenarios_Forecaster_NY-_B10.xlsx``, sheet **"HDD'S"** — observed Heating Degree
Days (LaGuardia / LGA) with climatological **Normal / 5-yr / 10-yr** baselines and a **"BX HO SOLD"**
demand series. The sheet has a messy multi-row header, so this parser finds the header row and the
date / year axis **EMPIRICALLY** (not by fixed offsets) and **self-reports** exactly what it mapped,
so the operator can see whether it read the file correctly.

Two layouts are supported, detected from the header:
  • **TIDY** — one column of real dates, named ``HDD`` / ``Normal`` / ``5-Yr`` / ``10-Yr`` /
    ``BX HO SOLD`` columns; one observation per row.
  • **YEAR-MATRIX** — columns are YEARS (``2019``, ``2020`` …) holding HDD, with a month/day date
    axis; each (row, year-column) is melted into one dated HDD observation. Baseline columns carry
    per row-date.

Two idempotent stores (created here, so they **survive** reset/demo exactly like ``deals`` /
``price_grid`` — they are uploaded real data, not in ``schema.ALL_TABLES``):
  • ``weather_hdd``         station × day → hdd (+ ``tmean``, ``hdd_normal``, ``hdd_5yr``, ``hdd_10yr``)
  • ``hdd_demand_anchor``   station × month → ``ho_sold``  (the BX HO SOLD anchor used to sanity-check
    the BOL-derived HDD→demand β before it is trusted).

HDD ≡ ``max(0, 65 − mean_temp)``. When the file already carries HDD we ingest it directly; when a
mean-temp column is also present we VERIFY the identity and report any rows that disagree (never
silently overwrite).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re

import pandas as pd

from . import dealbook
from .pricegrid import _sheet_names, _sheet_rows

HDD_BASE_F = 65.0   # the conventional heating-degree-day base temperature

# ---- stores (survive reset/demo) -------------------------------------------------
WEATHER_HDD_DDL = """CREATE TABLE IF NOT EXISTS weather_hdd (
    hdd_key VARCHAR PRIMARY KEY,
    station VARCHAR,
    day DATE,
    hdd DOUBLE,
    tmean DOUBLE,
    hdd_normal DOUBLE,
    hdd_5yr DOUBLE,
    hdd_10yr DOUBLE,
    source_file VARCHAR,
    imported_at VARCHAR
)"""

HDD_ANCHOR_DDL = """CREATE TABLE IF NOT EXISTS hdd_demand_anchor (
    anchor_key VARCHAR PRIMARY KEY,
    station VARCHAR,
    month DATE,
    ho_sold DOUBLE,
    hdd_month DOUBLE,           -- summed HDD over the month (paired with ho_sold for the β anchor)
    source_file VARCHAR,
    imported_at VARCHAR
)"""

_HDD_COLS = ["hdd_key", "station", "day", "hdd", "tmean", "hdd_normal", "hdd_5yr", "hdd_10yr",
             "source_file", "imported_at"]
_ANCHOR_COLS = ["anchor_key", "station", "month", "ho_sold", "hdd_month", "source_file", "imported_at"]


def ensure_tables(con) -> None:
    con.execute(WEATHER_HDD_DDL)
    con.execute(HDD_ANCHOR_DDL)


# ---- station inference -----------------------------------------------------------
# A title/sheet/filename mentioning a city resolves to its airport code (the HDD station).
_STATION_HINTS = {
    "laguardia": "LGA", "la guardia": "LGA", "lga": "LGA", "bronx": "LGA", "nyc": "LGA",
    "new york": "LGA", "brooklyn": "LGA", "queens": "LGA",
    "newark": "EWR", "ewr": "EWR",
    "baltimore": "BWI", "bwi": "BWI",
    "philadelphia": "PHL", "phl": "PHL", "philly": "PHL",
    "boston": "BOS", "bos": "BOS",
}
DEFAULT_STATION = "LGA"   # the file is the LaGuardia HDD book


def infer_station(*hints: object) -> str:
    blob = " ".join(str(h) for h in hints if h is not None).lower()
    for key, code in _STATION_HINTS.items():
        if key in blob:
            return code
    return DEFAULT_STATION


# ---- sheet / token detection -----------------------------------------------------
def find_hdd_sheet(names: list[str]) -> str | None:
    """The HDD sheet: name contains 'hdd' (tolerant of HDD'S / HDDS / HDD's / Hdd)."""
    for n in names:
        if "hdd" in re.sub(r"[^a-z]", "", str(n).lower()):
            return n
    return None


_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def _is_year(v: object) -> int | None:
    s = re.sub(r"[^\d]", "", str(v).strip()) if v is not None else ""
    if _YEAR_RE.match(s):
        return int(s)
    # a datetime header cell that is a Jan-1 year-marker also reads as a year column
    if isinstance(v, (dt.datetime, dt.date)) and getattr(v, "month", None) == 1 and v.day == 1:
        return v.year
    return None


def _norm_hdr(v: object) -> str:
    return re.sub(r"\s+", " ", str(v).strip().lower()) if v is not None else ""


def _classify_header(cell: object) -> str | None:
    """Classify a header cell into a known role (None = not a recognized label)."""
    h = _norm_hdr(cell)
    if not h:
        return None
    if "ho sold" in h or "bx ho" in h or re.search(r"\bho\b.*sold|sold.*\bho\b", h) or h in ("sold",):
        return "ho_sold"
    if "normal" in h:
        return "hdd_normal"
    if re.search(r"\b10\b", h) and re.search(r"yr|year|avg|average", h):
        return "hdd_10yr"
    if re.search(r"\b5\b", h) and re.search(r"yr|year|avg|average", h):
        return "hdd_5yr"
    if re.search(r"mean|avg temp|average temp|\btemp\b|temperature", h):
        return "tmean"
    if h == "hdd" or "heating degree" in h or re.search(r"\bhdd\b", h):
        return "hdd"
    return None


def _to_date(v: object) -> dt.date | None:
    d = dealbook._to_date(v)
    if d is not None:
        return d
    # textual dates / month-day tokens
    s = str(v).strip() if v is not None else ""
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%b %d %Y", "%d-%b-%y", "%d-%b-%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        ts = pd.to_datetime(s, errors="coerce")
        return ts.date() if pd.notna(ts) else None
    except Exception:  # noqa: BLE001
        return None


def _month_day(v: object) -> tuple[int, int] | None:
    """A month/day with no reliable year (the year-matrix date axis), e.g. 'Jul 1', '7/1'."""
    d = _to_date(v)
    if d is not None:
        return (d.month, d.day)
    s = str(v).strip().lower() if v is not None else ""
    m = re.match(r"([a-z]{3,9})[ \-/](\d{1,2})$", s)
    months = {mo[:3]: i for i, mo in enumerate(
        ["january", "february", "march", "april", "may", "june", "july", "august",
         "september", "october", "november", "december"], start=1)}
    if m and m.group(1)[:3] in months:
        return (months[m.group(1)[:3]], int(m.group(2)))
    m2 = re.match(r"(\d{1,2})[/\-](\d{1,2})$", s)
    if m2:
        return (int(m2.group(1)), int(m2.group(2)))
    return None


def _num(v: object) -> float | None:
    return dealbook._to_num(v)


# ---- the parser ------------------------------------------------------------------
def parse_hdd_workbook(path: str) -> dict:
    """Parse the HDD workbook → tidy observations + the HO-SOLD anchor + diagnostics.

    Returns ``{"observations": [...], "anchor": [...], "diagnostics": {...}, "station": code}``.
    Empirical: finds the header row (most recognized labels + year columns) and the date axis (the
    column with the most date-parseable cells), then melts a year-matrix or reads tidy rows.
    """
    names = _sheet_names(path)
    sheet = find_hdd_sheet(names) or (names[0] if names else None)
    if sheet is None:
        return {"observations": [], "anchor": [], "station": DEFAULT_STATION,
                "diagnostics": {"error": "no sheet found"}}
    rows = _sheet_rows(path, sheet)
    station = infer_station(path, sheet, *(rows[0] if rows else []),
                            *(rows[1] if len(rows) > 1 else []))

    # 1) find the header row: maximize recognized role-labels + year columns
    best_i, best_score, best_roles, best_years = None, -1, {}, {}
    for i, r in enumerate(rows[:40]):
        roles = {j: _classify_header(c) for j, c in enumerate(r)}
        roles = {j: v for j, v in roles.items() if v}
        years = {j: y for j, (c) in enumerate(r) if (y := _is_year(c)) is not None}
        score = len(roles) + len(years)
        if score > best_score:
            best_i, best_score, best_roles, best_years = i, score, roles, years
    if best_i is None or best_score <= 0:
        return {"observations": [], "anchor": [], "station": station,
                "diagnostics": {"error": "no header row with HDD/year labels found", "sheet": sheet}}

    hdr_i, roles, years = best_i, best_roles, best_years

    # 2) the date axis = the column (not a year/role col) with the most date-parseable cells below hdr
    role_cols = set(roles) | set(years)
    date_col, date_hits = None, 0
    for j in range(max((len(r) for r in rows), default=0)):
        if j in role_cols:
            continue
        hits = sum(1 for r in rows[hdr_i + 1:hdr_i + 400]
                   if j < len(r) and (_to_date(r[j]) is not None or _month_day(r[j]) is not None))
        if hits > date_hits:
            date_col, date_hits = j, hits
    if date_col is None or date_hits < 3:
        return {"observations": [], "anchor": [], "station": station,
                "diagnostics": {"error": "no date axis found", "sheet": sheet, "header_row": hdr_i}}

    matrix_mode = len(years) >= 2
    observations: list[dict] = []
    anchor_rows: list[dict] = []
    hdd_by_month: dict[dt.date, float] = {}
    ho_by_month: dict[dt.date, float] = {}
    mismatches = 0
    checked = 0

    for r in rows[hdr_i + 1:]:
        if date_col >= len(r):
            continue
        raw_date = r[date_col]
        full_date = _to_date(raw_date)
        md = _month_day(raw_date)
        if full_date is None and md is None:
            continue

        def role_val(role):
            j = next((jj for jj, rr in roles.items() if rr == role), None)
            return _num(r[j]) if j is not None and j < len(r) else None

        normal, h5, h10, tmean, ho = (role_val("hdd_normal"), role_val("hdd_5yr"),
                                      role_val("hdd_10yr"), role_val("tmean"), role_val("ho_sold"))

        if matrix_mode and md is not None:
            mo, da = md
            for j, yr in years.items():
                if j >= len(r):
                    continue
                hv = _num(r[j])
                if hv is None:
                    continue
                try:
                    d = dt.date(yr, mo, da)
                except ValueError:
                    continue
                observations.append(_obs(station, d, hv, tmean, normal, h5, h10))
                key = dt.date(d.year, d.month, 1)
                hdd_by_month[key] = hdd_by_month.get(key, 0.0) + hv
        elif full_date is not None:
            hv = role_val("hdd")
            if hv is None and tmean is not None:
                hv = max(0.0, HDD_BASE_F - tmean)
            if hv is None:
                continue
            if tmean is not None:
                checked += 1
                if abs(hv - max(0.0, HDD_BASE_F - tmean)) > 1.0:
                    mismatches += 1
            observations.append(_obs(station, full_date, hv, tmean, normal, h5, h10))
            key = dt.date(full_date.year, full_date.month, 1)
            hdd_by_month[key] = hdd_by_month.get(key, 0.0) + hv
            if ho is not None:
                ho_by_month[key] = ho_by_month.get(key, 0.0) + ho

    # the anchor: monthly HO SOLD paired with monthly HDD (only where HO SOLD is date-alignable)
    for m, ho in sorted(ho_by_month.items()):
        anchor_rows.append({"station": station, "month": m, "ho_sold": round(ho, 1),
                            "hdd_month": round(hdd_by_month.get(m, 0.0), 1)})

    ho_present = any(rr == "ho_sold" for rr in roles.values())
    diagnostics = {
        "sheet": sheet, "header_row": hdr_i, "mode": "year_matrix" if matrix_mode else "tidy",
        "date_column": date_col, "year_columns": sorted(years.values()) if years else [],
        "roles_mapped": sorted(set(roles.values())),
        "n_observations": len(observations), "n_anchor_months": len(anchor_rows),
        "identity_checked": checked, "identity_mismatches": mismatches,
        "ho_sold_present": ho_present,
        "ho_sold_alignable": bool(anchor_rows),
        "ho_sold_note": (None if anchor_rows or not ho_present else
                         "HO SOLD column found but not date-alignable in this layout "
                         "(year-matrix); provide a dated HO SOLD column to enable the β anchor."),
    }
    return {"observations": observations, "anchor": anchor_rows,
            "station": station, "diagnostics": diagnostics}


def _obs(station, d, hdd, tmean, normal, h5, h10) -> dict:
    return {"station": station, "day": d, "hdd": round(float(hdd), 2),
            "tmean": round(float(tmean), 2) if tmean is not None else None,
            "hdd_normal": round(float(normal), 2) if normal is not None else None,
            "hdd_5yr": round(float(h5), 2) if h5 is not None else None,
            "hdd_10yr": round(float(h10), 2) if h10 is not None else None}


# ---- idempotent upserts ----------------------------------------------------------
def _hdd_key(station, d) -> str:
    return hashlib.sha1(f"{(station or '').lower()}|{d.isoformat() if d else ''}".encode()).hexdigest()[:20]


def _anchor_key(station, m) -> str:
    return hashlib.sha1(f"{(station or '').lower()}|{m.isoformat() if m else ''}".encode()).hexdigest()[:20]


def _upsert(con, table: str, cols: list[str], rows: list[dict], key: str, date_cols: set) -> int:
    if not rows:
        return 0
    agg = {r[key]: r for r in rows}        # within-file: last write wins
    deduped = list(agg.values())
    keys = [r[key] for r in deduped]
    ph = ", ".join("?" for _ in keys)
    con.execute(f"DELETE FROM {table} WHERE {key} IN ({ph})", keys)
    df = pd.DataFrame(deduped)
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[cols]
    con.register("_hdd_ins", df)
    try:
        sel = ", ".join(
            f'CAST("{c}" AS {("DATE" if c in date_cols else ("DOUBLE" if c in _NUMERIC else "VARCHAR"))}) AS "{c}"'
            for c in cols)
        con.execute(f"INSERT INTO {table} ({', '.join(cols)}) SELECT {sel} FROM _hdd_ins")
    finally:
        con.unregister("_hdd_ins")
    return len(deduped)


_NUMERIC = {"hdd", "tmean", "hdd_normal", "hdd_5yr", "hdd_10yr", "ho_sold", "hdd_month"}


def upsert_observations(con, rows: list[dict], source_file: str, now: str) -> int:
    ensure_tables(con)
    for r in rows:
        r["hdd_key"] = _hdd_key(r["station"], r["day"])
        r["source_file"] = source_file
        r["imported_at"] = now
    return _upsert(con, "weather_hdd", _HDD_COLS, rows, "hdd_key", {"day"})


def upsert_anchor(con, rows: list[dict], source_file: str, now: str) -> int:
    ensure_tables(con)
    for r in rows:
        r["anchor_key"] = _anchor_key(r["station"], r["month"])
        r["source_file"] = source_file
        r["imported_at"] = now
    return _upsert(con, "hdd_demand_anchor", _ANCHOR_COLS, rows, "anchor_key", {"month"})


def load_hdd_file(con, path: str, now: str) -> dict:
    """Parse + idempotently upsert an HDD workbook (observations + anchor). Re-runnable."""
    ensure_tables(con)
    parsed = parse_hdd_workbook(path)
    fname = path.split("/")[-1]
    n_obs = upsert_observations(con, parsed["observations"], fname, now)
    n_anchor = upsert_anchor(con, parsed["anchor"], fname, now)
    return {"observations_written": n_obs, "anchor_months_written": n_anchor,
            "station": parsed["station"], "diagnostics": parsed["diagnostics"], "filename": fname}


# ---- reads -----------------------------------------------------------------------
def read_hdd(con, station: str | None = None) -> pd.DataFrame:
    ensure_tables(con)
    if station:
        return con.execute("SELECT station, day, hdd, tmean, hdd_normal, hdd_5yr, hdd_10yr "
                           "FROM weather_hdd WHERE station = ? ORDER BY day", [station]).df()
    return con.execute("SELECT station, day, hdd, tmean, hdd_normal, hdd_5yr, hdd_10yr "
                       "FROM weather_hdd ORDER BY station, day").df()


def read_anchor(con, station: str | None = None) -> pd.DataFrame:
    ensure_tables(con)
    if station:
        return con.execute("SELECT station, month, ho_sold, hdd_month FROM hdd_demand_anchor "
                           "WHERE station = ? ORDER BY month", [station]).df()
    return con.execute("SELECT station, month, ho_sold, hdd_month FROM hdd_demand_anchor "
                       "ORDER BY station, month").df()


def stations(con) -> list[str]:
    ensure_tables(con)
    return [r[0] for r in con.execute(
        "SELECT DISTINCT station FROM weather_hdd ORDER BY station").fetchall()]


def store_counts(con) -> dict:
    ensure_tables(con)
    obs = int(con.execute("SELECT count(*) FROM weather_hdd").fetchone()[0])
    rng = con.execute("SELECT min(day), max(day) FROM weather_hdd").fetchone()
    return {
        "hdd_observations": obs,
        "stations": stations(con),
        "day_min": str(rng[0]) if rng and rng[0] else None,
        "day_max": str(rng[1]) if rng and rng[1] else None,
        "anchor_months": int(con.execute("SELECT count(*) FROM hdd_demand_anchor").fetchone()[0]),
    }
