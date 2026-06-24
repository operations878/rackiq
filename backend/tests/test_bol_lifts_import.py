"""Wide EDI/BOL export → lifts: required-field gating, serial dates, corrections, BOL grouping.

A real BOL export is a wide file (dozens of mostly-blank admin columns), several compartment
rows share one BOL number, dates may be Excel serials, and reversals show as negative volumes.
These tests pin the behaviour the validator must have: keep the good data, group compartments
into lifts, and quarantine only genuine junk (a BOL-0 / no-volume control row).
"""

from __future__ import annotations

import io

import pandas as pd

from app import hygiene, ingest, schema


# ---- Unit: Excel serial-date parsing -------------------------------------------
def test_excel_serial_dates_parse_alongside_text():
    s = pd.Series(["2024-07-01", "45474", "07/03/2024", "", "not a date"])
    out, n_err, samples = ingest.coerce_column(s, "TIMESTAMP")
    assert str(out.iloc[0].date()) == "2024-07-01"
    assert str(out.iloc[1].date()) == "2024-07-01"     # 45474 == 2024-07-01 (Excel serial)
    assert str(out.iloc[2].date()) == "2024-07-03"
    assert pd.isna(out.iloc[3])                          # blank stays blank
    assert n_err == 1 and samples == ["not a date"]      # only real junk is a parse error


# ---- Unit: BOL grouping ---------------------------------------------------------
def test_group_by_bol_sums_compartments():
    df = pd.DataFrame({
        "bol_number": ["B1", "B1", "B2", None],
        "customer_id": ["100", "100", "200", "300"],
        "lift_datetime": pd.to_datetime(["2024-07-01"] * 4),
        "net_gallons": [3000.0, 2000.0, 4200.0, 1000.0],
        "gross_gallons": [3010.0, 2005.0, 4210.0, 1005.0],
    })
    out = hygiene.group_by_bol(df, schema.LIFTS, hygiene.HygieneOptions())
    assert len(out) == 3                                 # B1 (2 rows) collapses to one lift
    b1 = out[out["bol_number"] == "B1"].iloc[0]
    assert b1["net_gallons"] == 5000.0 and b1["gross_gallons"] == 5015.0
    # the no-BOL row survives as its own standalone lift
    assert (out["customer_id"] == "300").sum() == 1


# ---- Wide BOL export fixture ----------------------------------------------------
def _wide_bol_csv() -> bytes:
    """A wide BOL export: dozens of (mostly blank) admin columns + compartment rows."""
    rows = [
        # BOL,    Consignee#, Consignee Name,    Ship Date,    Net,    Gross,  Term,     Prod
        ("700001", "100", "Acme Fuel Co",   "2024-07-01", "3,000", "3010", "Linden", "ULSD"),
        ("700001", "100", "Acme Fuel Co",   "2024-07-01", "2,000", "2005", "Linden", "ULSD"),
        ("700002", "200", "Bayside Energy", "07/03/2024", "4,200", "4210", "Albany", "RBOB"),
        ("700003", "100", "Acme Fuel Co",   "45478",      "1,500", "1505", "Linden", "ULSD"),
        ("700003", "100", "Acme Fuel Co",   "45478",      "1,500", "1505", "Linden", "ULSD"),
        ("700004", "300", "  Coastal Oil ", "2024-07-10", "-250",  "-250", "Linden", "ULSD"),
        ("0",      "999", "CONTROL",        "2024-07-01", "0",     "0",    "",       "ZZZ"),
    ]
    cols = ["BOL Number", "Consignee Number", "Consignee Name", "Ship Date",
            "Net Amount", "Gross Amount", "Terminal", "Product"]
    df = pd.DataFrame(rows, columns=cols)
    # Pad with a pile of blank/admin EDI columns — they must never quarantine a row.
    for c in ["Carrier SCAC", "Trailer", "Seal Number", "PO Number", "Freight Terms",
              "Tax Code", "EDI Trace", "Origin Code", "Pay Terms", "Misc 1", "Misc 2"]:
        df[c] = ""
    return df.to_csv(index=False).encode()


def test_wide_bol_imports_as_grouped_lifts(client):
    client.post("/api/studio/reset", json={})
    ins = client.post("/api/studio/inspect", files={
        "file": ("bol_export.csv", io.BytesIO(_wide_bol_csv()), "text/csv")}).json()

    # 1) inference + auto-map the required three (+ BOL grouping key) by header name
    assert ins["suggested_table"] == "lifts"
    mapping = {c: s["target"] for c, s in ins["suggestions_by_table"]["lifts"].items()}
    assert mapping.get("Consignee Number") == "customer_id"
    assert mapping.get("Ship Date") == "lift_datetime"
    assert mapping.get("Net Amount") == "net_gallons"
    assert mapping.get("BOL Number") == "bol_number"

    # 2) validate preview: most rows kept, only the control row held
    v = client.post("/api/studio/validate", json={
        "upload_id": ins["upload_id"], "table": "lifts", "mapping": mapping,
        "options": {"net_correction": "off"}}).json()
    assert v["can_commit"] is True
    assert v["quarantine_count"] == 1
    assert v["quarantine_breakdown"] == {"edi_control_row": 1}
    assert v["kept_lifts"] == 4                            # 6 good compartment rows → 4 lifts
    assert v["corrections"] == 1                           # the -250 reversal

    # 3) commit
    com = client.post("/api/studio/commit", json={
        "upload_id": ins["upload_id"], "table": "lifts", "mapping": mapping,
        "mode": "replace", "options": {"net_correction": "off"}}).json()
    assert com["rows_in_file"] == 7
    assert com["kept_lifts"] == 4 and com["rows_written"] == 4
    assert com["quarantined"] == 1
    assert com["quarantine_breakdown"] == {"edi_control_row": 1}
    assert com["corrections"] == 1
    # net summed per BOL across compartments and across the book (5000 + 4200 + 3000 - 250)
    assert com["summary"]["total_net_gallons"] == 11950.0
    # the large majority of rows flowed through (6 of 7 kept, 1 junk held)
    assert com["quarantined"] / com["rows_in_file"] < 0.2
