"""Tests for Phase-2 price/cost ingestion (pricegrid.py).

Format-aware parsers proven against synthetic fixtures that reproduce each documented quirk:
the Matrix PRODUCT+CUSTOMER concat keys, the per-terminal multi-row headers, the Benchmarks named
differentials, and the Trips report (barrels→gallons, the "mb" magnitude heuristic, $/gal logistics
legs, and the all-in-vs-logistics-only cargo-flat sanity gate). Plus idempotent upsert + crosswalk
resolution of grid names.
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile

from openpyxl import Workbook

from app import db, pricegrid
from app.margin_config import DEFAULT_CONFIG


def _xlsx(sheets: dict[str, list[list]]) -> str:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for r in rows:
            ws.append(r)
    path = tempfile.mktemp(suffix=".xlsx")
    wb.save(path)
    return path


D = [dt.datetime(2025, 1, 6), dt.datetime(2025, 1, 7), dt.datetime(2025, 1, 8)]


# ---- Matrix concat-key splitting -------------------------------------------------
def test_split_matrix_key():
    assert pricegrid.split_matrix_key("ULSHO4416 Oil Corp") == ("ULSHO", "4416 Oil Corp")
    assert pricegrid.split_matrix_key("ULSHO24 Hour") == ("ULSHO", "24 Hour")
    assert pricegrid.split_matrix_key("RBOBSumma") == ("RBOB", "Summa")
    assert pricegrid.split_matrix_key("B10 ULSHOBronx Co") == ("B10 ULSHO", "Bronx Co")
    # a key with no known product prefix is flagged, never guessed
    assert pricegrid.split_matrix_key("XYZWeird Co") == (None, None)


def test_parse_matrix_flags_ambiguous():
    rows = [
        [None, D[0], D[1], D[2]],
        ["ULSHO4416 Oil Corp", 2.51, 2.52, 2.53],
        ["ULSD24 Hour", 2.80, 2.81, None],
        ["XYZNoProduct", 2.10, 2.11, 2.12],         # ambiguous → flagged, not parsed
    ]
    parsed, ambiguous = pricegrid.parse_matrix(rows, DEFAULT_CONFIG)
    assert "XYZNoProduct" in ambiguous
    customers = {r["customer_raw"] for r in parsed}
    assert customers == {"4416 Oil Corp", "24 Hour"}
    fams = {r["product_family"] for r in parsed}
    assert fams == {"ULSHO", "ULSD"}
    # values are SELL prices in $/gal and only positive cells become rows
    assert all(2.0 < r["sell_price"] < 3.0 for r in parsed)
    assert sum(1 for r in parsed if r["customer_raw"] == "24 Hour") == 2   # the None cell is skipped


# ---- per-terminal/product sheet --------------------------------------------------
def test_parse_sheet_name():
    assert pricegrid.parse_sheet_name("B10 ULSHO Bronx") == ("ULSHO", "B10", "Bronx")
    assert pricegrid.parse_sheet_name("Baltimore ULSD") == ("ULSD", None, "Baltimore")
    assert pricegrid.parse_sheet_name("Newark B5 ULSD") == ("ULSD", "B5", "Newark")


def test_parse_terminal_sheet_multirow_header():
    rows = [
        [None, 1, 2, 3],                            # weekday-number row (ignored)
        ["Customer", D[0], D[1], D[2]],             # real header
        ["Summa", 2.90, 2.91, 2.92],
        ["Rastall", 2.88, None, 2.89],
        ["Average", 2.89, 2.90, 2.90],              # a non-customer summary row → skipped
    ]
    parsed = pricegrid.parse_terminal_sheet(rows, "Newark B5 ULSD", DEFAULT_CONFIG)
    custs = {r["customer_raw"] for r in parsed}
    assert custs == {"Summa", "Rastall"}
    assert all(r["terminal"] == "Newark" and r["product_family"] == "ULSD" for r in parsed)
    assert all(r["blend"] == "B5" and r["source"] == "terminal_sheet" for r in parsed)


# ---- Benchmarks ------------------------------------------------------------------
def test_parse_benchmarks():
    rows = [
        ["Benchmark", "B10", "B20"],
        ["DD", 0.02, 0.03],
        ["ASHBY", 0.05, 0.06],
    ]
    diffs = pricegrid.parse_benchmarks(rows)
    by = {(d["name"], d["blend"]): d["value"] for d in diffs}
    assert by[("DD", "B10")] == 0.02
    assert by[("ASHBY", "B20")] == 0.06


# ---- Trips report: units + cargo-flat sanity gate --------------------------------
def _trips_rows():
    return [
        ["Discharge Terminal", "Product Code", "Discharge ETA", "Product Vol",
         "Barge Cost Per Gallon", "Inspector Cost Per Gallon", "Operational Cost Per Gallon",
         "Gain/Loss Cost Per Gallon", "Estimated Trip Value", "Pricing Type", "Fixed Differential",
         "Discharge Final / VEF"],
        # vol 84 "mb" → 84,000 bbl → 3,528,000 gal; ETV/gal = 2.50 (passes band) → all_in
        ["Newark", "ULSD", dt.datetime(2025, 1, 5), 84,
         0.020, 0.003, 0.004, 0.001, 2.50 * 84_000 * 42, "Fixed Diff", 0.05, 1.001],
        # ETV/gal = 0.05 (a differential, not a flat) → fails band → logistics_only
        ["Newark", "ULSD", dt.datetime(2025, 2, 5), 84,
         0.018, 0.003, 0.004, 0.001, 0.05 * 84_000 * 42, "Monthly Average", None, 0.999],
    ]


def test_parse_trips_units_and_cargo_flat():
    path = _xlsx({"Trips": _trips_rows()})
    rows = pricegrid.parse_trips(path, DEFAULT_CONFIG)
    os.unlink(path)
    assert len(rows) == 2
    a, b = rows
    # mb heuristic: 84 → 84,000 bbl → 3,528,000 gal
    assert a["vol_unit"] == "mb" and a["volume_bbl"] == 84_000
    assert abs(a["volume_gal"] - 3_528_000) < 1
    # logistics = sum of the four $/gal legs
    assert abs(a["logistics_cost"] - 0.028) < 1e-9
    # all-in landed = ETV/gal (2.50) + logistics (0.028)
    assert a["cost_basis"] == "all_in" and abs(a["all_in_landed"] - 2.528) < 1e-6
    assert a["product_family"] == "ULSD" and a["fixed_differential"] == 0.05
    # the second barge's ETV is a differential, not a flat → cargo gap flagged
    assert b["cost_basis"] == "logistics_only" and b["all_in_landed"] is None


def test_mb_heuristic_large_value_is_raw_barrels():
    vol, unit = pricegrid._resolve_volume(84_000, DEFAULT_CONFIG)
    assert unit == "bbl" and vol == 84_000
    vol, unit = pricegrid._resolve_volume(84, DEFAULT_CONFIG)
    assert unit == "mb" and vol == 84_000


# ---- store: idempotent upsert + crosswalk resolution -----------------------------
def test_price_grid_upsert_idempotent(con):
    pricegrid.ensure_tables(con)
    path = _xlsx({
        "Matrix": [[None, D[0], D[1], D[2]], ["ULSDSumma", 2.80, 2.81, 2.82]],
        "Newark ULSD": [[None, 1, 2, 3], ["Customer", D[0], D[1], D[2]], ["Summa", 2.90, 2.91, 2.92]],
    })
    pricegrid.load_price_grid_file(con, path, "now")
    n1 = pricegrid.store_counts(con)["price_grid_rows"]
    pricegrid.load_price_grid_file(con, path, "now")          # re-upload same file
    os.unlink(path)
    n2 = pricegrid.store_counts(con)["price_grid_rows"]
    assert n1 == n2 and n1 == 6                                # 3 matrix + 3 terminal, no double-count


def test_trips_upsert_and_resolution(con):
    pricegrid.ensure_tables(con)
    path = _xlsx({"Trips": _trips_rows()})
    res = pricegrid.load_trips_file(con, path, "now")
    os.unlink(path)
    assert res["trips_written"] == 2 and res["all_in_trips"] == 1


def test_grid_customer_resolves_to_master(con):
    pricegrid.ensure_tables(con)
    # a confirmed crosswalk maps the raw grid name → coded master
    db.upsert_crosswalk_entries(con, [{"variant_key": "Summa", "master_id": "SUMMA CORP",
                                       "master_name": "SUMMA CORP", "confidence": 1.0,
                                       "status": "confirmed", "source": "name_map", "updated_at": "now"}])
    path = _xlsx({"Newark ULSD": [[None, 1], ["Customer", D[0]], ["Summa", 2.90]]})
    res = pricegrid.load_price_grid_file(con, path, "now")
    os.unlink(path)
    assert res["masters_resolved"] == 1
    master = con.execute("SELECT customer_master FROM price_grid").fetchone()[0]
    assert master == "SUMMA CORP"
    assert pricegrid.unmapped_grid_customers(con) == []
