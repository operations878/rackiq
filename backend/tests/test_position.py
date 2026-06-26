"""Tests for the Phase-7 position / days-of-cover engine + the Trips supply parser.

Validated on SYNTHETIC data (the real Trips .xls is local-only / gitignored). The sanity checks the
brief calls for, proven here:
  • barrels→gallons (×42) is applied EXACTLY ONCE (in the parser; the engine never re-multiplies);
  • the running net flow TIES OUT on a hand-checked terminal×product (proxy = in−out; gauge = anchor
    + inbound_since − outbound_since);
  • days-of-cover is counted in WORKING days (weekends/holidays handled by the Phase-1 calendar);
  • the gauge-vs-proxy mode is honestly labeled and the "nominate a barge" cure ties out in barrels.
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
import warnings

import duckdb
import pandas as pd
import pytest
from openpyxl import Workbook

from app import barges, db, generator, hedging

warnings.filterwarnings("ignore")

AS_OF = pd.Timestamp("2026-05-29")   # a Friday — the constructed books' last data date


def _con():
    c = duckdb.connect(":memory:")
    db.init_db(c)
    return c


def _xlsx(rows: list[list], sheet: str = "Trips") -> str:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for r in rows:
        ws.append(r)
    path = tempfile.mktemp(suffix=".xlsx")
    wb.save(path)
    return path


def _trips_rows():
    return [
        ["Discharge Terminal", "Product Code", "Discharge ETA", "Product Vol",
         "Barge Cost Per Gallon", "Inspector Cost Per Gallon", "Operational Cost Per Gallon",
         "Gain/Loss Cost Per Gallon", "Estimated Trip Value", "Pricing Type", "Fixed Differential",
         "Discharge Final / VEF"],
        # 84 "mb" → 84,000 bbl → 3,528,000 gal; VEF 1.001 → delivered slightly over nominal
        ["Newark", "ULSD", dt.datetime(2025, 1, 5), 84,
         0.020, 0.003, 0.004, 0.001, 2.50 * 84_000 * 42, "Fixed Diff", 0.05, 1.001],
        # 25 "mb" → 25,000 bbl → 1,050,000 gal; VEF 0.994 → delivered under nominal (transit loss)
        ["Newark", "ULSHO", dt.datetime(2025, 2, 5), 25,
         0.018, 0.003, 0.004, 0.001, None, "Monthly Average", None, 0.994],
        # 60,000 raw barrels (> mb threshold) → bbl; no VEF → nominal basis
        ["Baltimore", "RBOB", dt.datetime(2025, 2, 7), 60_000,
         0.020, 0.0, 0.0, 0.0, None, None, None, None],
    ]


def _insert_lifts(con, cid, dates, gal, terminal="Linden", product="ULSD"):
    rows = [(cid, pd.Timestamp(d).to_pydatetime(), float(gal), terminal, product) for d in dates]
    con.executemany("INSERT INTO lifts (customer_id, lift_datetime, net_gallons, terminal, product) "
                    "VALUES (?,?,?,?,?)", rows)


def _weekdays(start, end):
    return [d for d in pd.date_range(start, end, freq="D") if d.weekday() <= 4]


# =====================================================================================
# 1) INBOUND parser — units (×42 once), mb heuristic, VEF basis, idempotent, survive-reset
# =====================================================================================
def test_parser_barrels_to_gallons_once_and_vef_basis():
    path = _xlsx(_trips_rows())
    parsed = barges.parse_trips_supply(path)
    os.unlink(path)
    rows = {(r["terminal"], r["product_family"]): r for r in parsed["rows"]}
    assert len(rows) == 3

    a = rows[("Newark", "ULSD")]
    assert a["vol_unit"] == "mb" and a["volume_bbl"] == 84_000          # mb heuristic
    assert abs(a["nominal_gallons"] - 84_000 * 42) < 1                  # ×42 exactly
    assert a["volume_basis"] == "vef_adjusted" and a["vef"] == 1.001
    assert abs(a["delivered_gallons"] - 84_000 * 42 * 1.001) < 1        # VEF applied to delivered
    assert a["transit_gain_loss_gallons"] > 0                           # +gain at VEF>1
    assert a["landed_cost_cpg"] == pytest.approx(2.8)                   # (0.028 $/gal)×100 ¢/gal

    b = rows[("Newark", "ULSHO")]
    assert b["volume_basis"] == "vef_adjusted" and b["transit_gain_loss_gallons"] < 0  # loss at VEF<1

    c = rows[("Baltimore", "GAS")]                                      # RBOB → GAS family
    assert c["vol_unit"] == "bbl" and c["vef"] is None
    assert c["volume_basis"] == "nominal" and abs(c["delivered_gallons"] - 60_000 * 42) < 1

    conv = parsed["conversion"]
    assert conv["discharges"] == 3 and conv["factor"] == 42.0
    assert abs(conv["total_nominal_gallons"] - conv["total_barrels"] * 42) < 1  # once, over all rows


def test_parser_ignores_implausible_vef():
    rows = [_trips_rows()[0],
            ["Newark", "ULSD", dt.datetime(2025, 1, 5), 10, 0.02, 0.0, 0.0, 0.0, None, None, None, 4.2]]
    path = _xlsx(rows)
    parsed = barges.parse_trips_supply(path)
    os.unlink(path)
    r = parsed["rows"][0]
    assert r["vef"] is None and r["volume_basis"] == "nominal"          # 4.2 is not a VEF → ignored
    assert abs(r["delivered_gallons"] - 10_000 * 42) < 1


def test_barge_store_idempotent_and_survives_reset(con):
    path = _xlsx(_trips_rows())
    n1 = barges.load_trips_supply_file(con, path, "now")["discharges_written"]
    c1 = barges.store_counts(con)["barge_discharges"]
    barges.load_trips_supply_file(con, path, "now")                     # re-upload same file
    os.unlink(path)
    c2 = barges.store_counts(con)["barge_discharges"]
    assert n1 == 3 and c1 == 3 and c2 == 3                              # no double-count

    db.reset_data(con)                                                  # demo/reset
    assert barges.store_counts(con)["barge_discharges"] == 3            # survives like deals/landed_costs


# =====================================================================================
# 2) POSITION — proxy tie-out (cumulative in − out) on a hand-checked terminal×product
# =====================================================================================
def test_proxy_position_ties_out():
    con = _con()
    days = _weekdays("2026-04-01", AS_OF)
    _insert_lifts(con, "C1", days, 10_000.0, "Linden", "ULSD")
    total_out = len(days) * 10_000.0
    con.executemany("INSERT INTO receipts (receipt_datetime, terminal, product, receipt_source, "
                    "receipt_net_gallons) VALUES (?,?,?,?,?)",
                    [(pd.Timestamp("2026-04-10").to_pydatetime(), "Linden", "ULSD", "marine", 200_000.0),
                     (pd.Timestamp("2026-05-01").to_pydatetime(), "Linden", "ULSD", "marine", 200_000.0)])
    res = hedging.compute_position(con, today="2026-06-01")
    r = next(x for x in res["positions"] if (x["terminal"], x["product"]) == ("Linden", "ULSD"))
    assert r["mode"] == "proxy" and res["inbound"]["source"] == "receipts"
    assert r["position_gallons"] == pytest.approx(400_000.0 - total_out, abs=1.0)   # in − out
    assert "flow delta" in r["proxy_note"]


# =====================================================================================
# 3) POSITION — gauge-anchored tie-out with roll-forward (gauge OLDER than as_of)
# =====================================================================================
def test_gauge_position_rolls_forward_and_ties_out():
    con = _con()
    # lifts every weekday Apr–May; a verified gauge mid-stream; a receipt after the gauge.
    days = _weekdays("2026-04-01", AS_OF)
    _insert_lifts(con, "C1", days, 5_000.0, "Linden", "ULSD")
    gauge_date = pd.Timestamp("2026-05-15")
    con.execute("INSERT INTO inventory_snapshots (snapshot_datetime, terminal, product, "
                "physical_inventory, tank_capacity, min_heel, receipts) VALUES (?,?,?,?,?,?,?)",
                [pd.Timestamp("2026-05-15 23:59").to_pydatetime(), "Linden", "ULSD",
                 300_000.0, 2_000_000.0, 100_000.0, 0.0])
    con.execute("INSERT INTO receipts (receipt_datetime, terminal, product, receipt_source, "
                "receipt_net_gallons) VALUES (?,?,?,?,?)",
                [pd.Timestamp("2026-05-20").to_pydatetime(), "Linden", "ULSD", "marine", 150_000.0])
    res = hedging.compute_position(con, today="2026-06-01")
    r = next(x for x in res["positions"] if (x["terminal"], x["product"]) == ("Linden", "ULSD"))
    assert r["mode"] == "gauge"
    # outbound strictly AFTER the gauge date, up to as_of (= last lift, a Friday)
    out_after = sum(5_000.0 for d in days if d > gauge_date)
    expected = 300_000.0 + 150_000.0 - out_after
    assert r["position_gallons"] == pytest.approx(expected, abs=1.0)
    assert r["anchor"]["level"] == 300_000.0 and r["anchor"]["outbound_since"] == pytest.approx(out_after)


# =====================================================================================
# 4) DAYS-OF-COVER is in WORKING days (weekends excluded from the denominator)
# =====================================================================================
def test_cover_is_in_working_days():
    con = _con()
    days = _weekdays("2026-03-01", AS_OF)            # weekday-only lifts (no Saturday activity)
    _insert_lifts(con, "C1", days, 8_000.0, "Linden", "ULSD")
    con.execute("INSERT INTO inventory_snapshots (snapshot_datetime, terminal, product, "
                "physical_inventory, tank_capacity, min_heel, receipts) VALUES (?,?,?,?,?,?,?)",
                [pd.Timestamp("2026-05-29 23:59").to_pydatetime(), "Linden", "ULSD",
                 400_000.0, 5_000_000.0, 100_000.0, 0.0])
    res = hedging.compute_position(con, today="2026-06-01")
    r = next(x for x in res["positions"] if (x["terminal"], x["product"]) == ("Linden", "ULSD"))
    win = r["cover_window"]
    # the working-day denominator is well below the 45 calendar-day lookback (weekends excluded)
    assert win["working_days"] < win["lookback_days"]
    # cover is internally consistent: position / (outbound_in_window / working_days)
    burn = r["burn_gallons_per_working_day"]
    assert burn == pytest.approx(win["outbound_gallons"] / win["working_days"], rel=1e-6)
    assert r["days_of_cover"] == pytest.approx(400_000.0 / burn, abs=0.01)   # cover rounded to 2 dp
    # run-out date is never a Sunday (a non-working day)
    assert pd.Timestamp(r["run_out_date"]).weekday() != 6


# =====================================================================================
# 5) CURE — short cover fires a barrel nomination that ties out in barrels
# =====================================================================================
def test_cure_fires_when_short_and_ties_out_in_barrels():
    con = _con()
    days = _weekdays("2026-03-01", AS_OF)
    _insert_lifts(con, "C1", days, 20_000.0, "Linden", "ULSD")
    con.execute("INSERT INTO inventory_snapshots (snapshot_datetime, terminal, product, "
                "physical_inventory, tank_capacity, min_heel, receipts) VALUES (?,?,?,?,?,?,?)",
                [pd.Timestamp("2026-05-29 23:59").to_pydatetime(), "Linden", "ULSD",
                 60_000.0, 5_000_000.0, 100_000.0, 0.0])   # ~3 working days of cover → short
    res = hedging.compute_position(con, today="2026-06-01")
    r = next(x for x in res["positions"] if (x["terminal"], x["product"]) == ("Linden", "ULSD"))
    assert r["status"] == "short" and r["cure"]["short"] is True
    burn = r["burn_gallons_per_working_day"]
    target = 10.0 * burn                                   # target_cover_working_days default = 10
    assert r["cure"]["gallons_short"] == pytest.approx(max(0.0, target - 60_000.0), abs=1.0)
    # the nomination is the shortfall expressed in BARRELS (÷42)
    assert r["cure"]["implied_barge_bbl"] == pytest.approx(r["cure"]["gallons_short"] / 42.0, abs=1.0)
    assert "nominate" in r["facet"]["sentence"] and "bbl" in r["facet"]["sentence"]


# =====================================================================================
# 6) INBOUND source priority: barge_discharges > receipts > inventory_snapshots.receipts
# =====================================================================================
def test_inbound_source_priority_prefers_barges():
    con = _con()
    days = _weekdays("2026-04-01", AS_OF)
    _insert_lifts(con, "C1", days, 4_000.0, "Newark", "ULSD")
    con.execute("INSERT INTO receipts (receipt_datetime, terminal, product, receipt_source, "
                "receipt_net_gallons) VALUES (?,?,?,?,?)",
                [pd.Timestamp("2026-05-01").to_pydatetime(), "Newark", "ULSD", "marine", 100_000.0])
    path = _xlsx(_trips_rows())                             # Newark ULSD barge present
    barges.load_trips_supply_file(con, path, "now")
    os.unlink(path)
    cells, src, label = hedging._inbound_flows(con)
    assert src == "trips_barges"                            # barges win over receipts
    assert ("Newark", "ULSD") in cells


# =====================================================================================
# 7) End-to-end on the generated full synthetic book
# =====================================================================================
def test_end_to_end_on_full_synthetic_book():
    con = _con()
    generator.generate(generator.GenConfig(seed=42, profile="full", months=12), con)
    res = hedging.compute_position(con)
    assert res["availability"]["available"] is True
    assert res["inbound"]["source"] == "receipts"          # no Trips file in synthetic → canonical receipts
    assert res["summary"]["n_cells"] > 0
    # synthetic carries daily physical gauges → every cell anchors on a verified level
    assert res["summary"]["gauge_cells"] == res["summary"]["n_cells"]
    # products are normalized families (RBOB → GAS), terminals come from the book
    assert set(res["products"]).issubset({"GAS", "ULSD", "ULSHO", "DYED", "HO4", "RD", "OTHER"})
    for r in res["positions"]:
        assert r["mode"] in ("gauge", "proxy")
        assert r["facet"]["sentence"]                        # every cell self-describes
        if r["days_of_cover"] is not None:
            assert r["days_of_cover"] >= 0


def test_lite_profile_unavailable_without_terminal_product():
    con = _con()
    generator.generate(generator.GenConfig(seed=1, profile="core", months=6), con)  # no terminal/product
    res = hedging.compute_position(con)
    assert res["availability"]["available"] is False
    assert res["positions"] == []


# =====================================================================================
# 8) API flow
# =====================================================================================
def test_api_flow(client):
    client.post("/api/studio/load-demo", json={"profile": "full"})
    p = client.get("/api/position").json()
    assert p["availability"]["available"] is True
    assert p["summary"]["n_cells"] > 0
    assert p["facets"] and "sentence" in p["facets"][0]
    # terminal filter narrows the cells
    t0 = p["terminals"][0]
    pt = client.get("/api/position", params={"terminal": t0}).json()
    assert all(r["terminal"] == t0 for r in pt["positions"])
    assert client.get("/api/position/config").json()["config"]["target_cover_working_days"] == 10.0


def test_api_upload_trips_supply(client):
    path = _xlsx(_trips_rows())
    with open(path, "rb") as fh:
        r = client.post("/api/position/upload",
                        files={"file": ("trips_report.xlsx", fh,
                                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    os.unlink(path)
    body = r.json()
    assert body["discharges_written"] == 3
    assert body["conversion"]["factor"] == 42.0 and body["conversion"]["discharges"] == 3
    assert body["stores"]["barge_discharges"] == 3
    # the engine now prefers the uploaded barge supply
    summary = client.get("/api/position/summary").json()
    assert summary["inbound_source"] == "trips_barges"
