"""Wide BOL / EDI export ingestion — required-only gating, grouping, corrections, junk.

These lock in the fix for the real-world failure where a wide EDI BOL export was almost
entirely quarantined. The only required fields for a valid lift are customer id (the consignee
account NAME when present, so the raw→coded crosswalk can resolve it), ship date, and net
gallons; every other column — including the dozens of optional admin/EDI columns — must NEVER
quarantine a row on its own. Compartment rows sharing a BOL number are grouped (gross & net
summed) into one lift; negatives are kept as corrections; and only genuine control rows
(BOL 0 / gross 0 / net 0) are held as junk.
"""

from __future__ import annotations

import io

import pandas as pd

from app import hygiene, ingest, schema, validation


# ---- Column mapping: auto-match the required three, ignore admin noise ----------
def test_consignee_name_maps_to_customer_id_and_infers_lifts():
    headers = ["Submission Type", "User ID", "Consignee Number", "Consignee Name", "Ship Date",
               "BOL Number", "Terminal Name", "Product Name", "Gross Amount", "Net Amount",
               "Temperature", "Gravity (API)", "Destination County", "Rack Driver ID"]
    assert ingest.infer_table(headers) == schema.LIFTS
    sugg = ingest.suggest_for_table(headers, schema.LIFTS)
    inv = {v["target"]: h for h, v in sugg.items()}
    # The customer key is the consignee NAME (so the raw→coded name crosswalk resolves it and the
    # UI shows names, never a bare account number) — and NOT the EDI submitter's "User ID".
    assert inv["customer_id"] == "Consignee Name"
    # The bare account number is left for the user / internal-only — not auto-keyed as the customer.
    assert sugg.get("Consignee Number", {}).get("target") != "customer_id"
    assert inv["lift_datetime"] == "Ship Date"
    assert inv["net_gallons"] == "Net Amount"
    assert inv["gross_gallons"] == "Gross Amount"
    assert inv["bol_number"] == "BOL Number"
    # Loose admin headers are NOT auto-mapped into numeric canonical fields.
    mapped_targets = {v["target"] for v in sugg.values()}
    assert "User ID" not in sugg
    assert not any(h in ("Destination County", "Rack Driver ID") for h in sugg)
    assert "unit_price" not in mapped_targets and "unit_cost" not in mapped_targets


# ---- Date parsing: Excel serials + scoped to the ship-date column only ----------
def test_excel_serial_dates_parse_and_numeric_ids_are_untouched():
    # 45474 == 2024-07-01; native dates, int serials and string serials all parse.
    out, n_err, _ = ingest.coerce_column(
        pd.Series(["2024-07-01", 45474, "45475", "07/03/2024"]), "TIMESTAMP")
    assert n_err == 0
    assert [str(v)[:10] for v in out] == ["2024-07-01", "2024-07-01", "2024-07-02", "2024-07-03"]
    # A customer number that *looks* like a serial (42023) is a VARCHAR id, never a date.
    cust, _, _ = ingest.coerce_column(pd.Series([42023, 991122]), "VARCHAR")
    assert list(cust) == ["42023", "991122"]


def test_date_rules_only_run_on_the_ship_date_column(con):
    # Consignee Number (42023) and Net Amount are numeric — they must not be date-validated.
    df = pd.DataFrame({
        "bol_number": ["B1", "B2"],
        "customer_id": ["42023", "991122"],
        "lift_datetime": pd.to_datetime(["2024-07-01", "2024-07-02"]),
        "net_gallons": [2680.0, 702.0],
    })
    rules = validation.run_rules(df, schema.LIFTS, {}, {}, con)
    by = {r["key"]: r for r in rules["rules"]}
    assert by["dates_parseable"]["count"] == 0
    assert by["dates_in_range"]["count"] == 0
    assert rules["quarantine_count"] == 0


# ---- BOL grouping: compartments of one load sum into one lift -------------------
def test_group_by_bol_sums_compartments_and_passes_through_unkeyed():
    df = pd.DataFrame({
        "bol_number": ["B1", "B1", "B2", None, "0"],
        "customer_id": ["A", "A", "A", "C", "D"],
        "lift_datetime": pd.to_datetime(["2024-07-01"] * 5),
        "net_gallons": [2680.0, 746.0, 6223.0, 50.0, 0.0],
        "gross_gallons": [2700.0, 752.0, 6283.0, 51.0, 0.0],
        "terminal": ["BALT", "BALT", "SPRAGUE", "X", "Y"],
    })
    out = hygiene.group_by_bol(df, schema.LIFTS, [], [])
    assert len(out) == 4                                   # B1, B2, the None row, the "0" row
    b1 = out[out["bol_number"] == "B1"].iloc[0]
    assert b1["net_gallons"] == 3426.0 and b1["gross_gallons"] == 3452.0
    # rows without a real BOL number stay individual lifts (not merged with each other)
    assert int(out["bol_number"].isna().sum()) == 1
    assert int((out["bol_number"] == "0").sum()) == 1


def test_group_by_bol_reversal_nets_out_within_a_load():
    df = pd.DataFrame({
        "bol_number": ["B9", "B9"],
        "customer_id": ["A", "A"],
        "lift_datetime": pd.to_datetime(["2024-07-02", "2024-07-02"]),
        "net_gallons": [702.0, -99.0],                     # reversal compartment
        "gross_gallons": [707.0, -100.0],
    })
    out = hygiene.group_by_bol(df, schema.LIFTS, [], [])
    assert len(out) == 1
    assert out.iloc[0]["net_gallons"] == 603.0 and out.iloc[0]["gross_gallons"] == 607.0


# ---- Required-only gating: optionals & negatives never quarantine ---------------
def test_blank_optional_columns_never_quarantine(con):
    df = pd.DataFrame({
        "bol_number": ["B1", "B2"],
        "customer_id": ["42023", "991122"],
        "lift_datetime": pd.to_datetime(["2024-07-01", "2024-07-02"]),
        "net_gallons": [100.0, 200.0],
        # every optional field entirely blank — must NOT hold the rows under them
        "gross_gallons": [None, None], "terminal": [None, None], "product": [None, None],
        "observed_temp": [None, None], "api_gravity": [None, None],
        "unit_price": [None, None], "unit_cost": [None, None],
    })
    rules = validation.run_rules(df, schema.LIFTS, {}, {}, con)
    assert rules["quarantine_count"] == 0


def test_negative_volumes_are_corrections_not_quarantined(con):
    df = pd.DataFrame({
        "bol_number": ["B1", "B1"],
        "customer_id": ["A", "A"],
        "lift_datetime": pd.to_datetime(["2024-07-01", "2024-07-01"]),
        "net_gallons": [702.0, -99.0],
        "gross_gallons": [707.0, -100.0],
    })
    rules = validation.run_rules(df, schema.LIFTS, {}, {}, con)
    by = {r["key"]: r for r in rules["rules"]}
    assert by["volume_corrections"]["count"] == 1
    assert by["volume_corrections"]["action"] == "none"
    assert by["volume_corrections"]["rows"]                # listed for review
    assert rules["quarantine_count"] == 0


def test_only_value_above_bound_flagged_not_negative(con):
    df = pd.DataFrame({
        "bol_number": ["B1", "B2"],
        "customer_id": ["A", "B"],
        "lift_datetime": pd.to_datetime(["2024-07-01", "2024-07-02"]),
        "net_gallons": [-50.0, 9_000_000.0],               # a correction + a real unit error
    })
    rules = validation.run_rules(df, schema.LIFTS, {}, {}, con)
    by = {r["key"]: r for r in rules["rules"]}
    assert by["value_bounds"]["count"] == 1                # only the 9M row (negative is a correction)
    assert by["volume_corrections"]["count"] == 1


# ---- Genuine junk: EDI control / heartbeat rows are still held ------------------
def test_edi_control_rows_quarantined_but_zero_bol_with_volume_kept(con):
    df = pd.DataFrame({
        "bol_number": ["821006", "0", "0"],
        "customer_id": ["A", "X", "B"],
        "lift_datetime": pd.to_datetime(["2024-07-01", "2024-07-02", "2024-07-03"]),
        "net_gallons": [2680.0, 0.0, 500.0],
        "gross_gallons": [2700.0, 0.0, 510.0],
    })
    rules = validation.run_rules(df, schema.LIFTS, {}, {}, con)
    by = {r["key"]: r for r in rules["rules"]}
    assert by["edi_control_row"]["count"] == 1             # only BOL 0 / gross 0 / net 0
    assert rules["quarantine_count"] == 1
    held = rules["quarantine_index"][0]
    assert rules["quarantine_reasons"][held] == ["edi_control_row"]


# ---- End-to-end: a wide BOL export keeps the large majority of rows -------------
def _wide_bol_csv() -> bytes:
    cols = ["Submission Type", "User ID", "Consignee Number", "Consignee Name", "Ship Date",
            "BOL Number", "Terminal Name", "Product Name", "Gross Amount", "Net Amount",
            "Temperature", "Gravity (API)", "Destination County", "Rack Driver ID"]
    rows = [
        ("ADD", "u1", 42023, "DIESEL DIRECT- MD", "2024-07-01", 821006, "BALTIMORE", "ULSD", 2700, 2680, None, None, "", ""),
        ("ADD", "u1", 42023, "DIESEL DIRECT- MD", "2024-07-01", 821006, "BALTIMORE", "ULSD", 752, 746, None, None, "", ""),
        ("ADD", "u1", 42023, "DIESEL DIRECT- MD", "2024-07-01", 821016, "BALTIMORE", "ULSD", 2581, 2562, None, None, "", ""),
        ("ADD", "u1", 42023, "DIESEL DIRECT- MD", "2024-07-01", 821016, "BALTIMORE", "ULSD", 770, 764, None, None, "", ""),
        ("ADD", "u1", 42023, "DIESEL DIRECT- MD", "2024-07-01", 821016, "BALTIMORE", "ULSD", 800, 794, None, None, "", ""),
        ("ADD", "u2", 991122, "TAYLOR FUEL - NJ", "2024-07-02", 219889, "PENNSAUKEN", "ULSD", 707, 702, None, None, "", ""),
        ("ADD", "u2", 991122, "TAYLOR FUEL - NJ", "2024-07-02", 219889, "PENNSAUKEN", "ULSD", -100, -99, None, None, "", ""),
        ("CTL", "u9", 0, "", "2024-07-02", 0, "", "ZZZ", 0, 0, None, None, "", ""),
        ("ADD", "u3", 668288, "APPROVED OIL", "2024-07-03", 336046, "SPRAGUE", "ULSD", 6283, 6223, None, None, "", ""),
    ]
    return pd.DataFrame(rows, columns=cols).to_csv(index=False).encode()


def test_wide_bol_commit_keeps_majority_and_groups(client):
    ins = client.post("/api/studio/inspect", files={
        "file": ("bol.csv", io.BytesIO(_wide_bol_csv()), "text/csv")}).json()
    assert ins["suggested_table"] == "lifts"
    mapping = {c: s["target"] for c, s in ins["suggestions_by_table"]["lifts"].items()}
    # Customer key auto-resolves to the consignee NAME (not the bare account number).
    assert mapping["Consignee Name"] == "customer_id"
    assert mapping.get("Consignee Number") != "customer_id"

    com = client.post("/api/studio/commit", json={
        "upload_id": ins["upload_id"], "table": "lifts", "mapping": mapping,
        "mode": "replace", "options": {"quarantine_failures": True}}).json()
    assert com["rows_in_file"] == 9
    assert com["clean_rows"] == 8                           # 8 compartment rows pass (1 junk held)
    assert com["lifts_after_grouping"] == 4                 # grouped into 4 BOLs/lifts
    assert com["rows_written"] == 4
    assert com["corrections"] == 1                          # the reversal compartment, kept
    assert com["quarantined"] == 1
    # The one held row is the EDI control row — now keyed on the consignee NAME, its blank name
    # also trips required-present, so it is held for BOTH reasons (still a single quarantined row).
    assert com["quarantine_reasons"] == {"edi_control_row": 1, "required_present": 1}
    # the large majority of rows flow through — only the genuine control row is held
    assert com["clean_rows"] / com["rows_in_file"] >= 0.8
    # grouped totals are correct, incl. the reversal netting out within its BOL
    assert abs(com["summary"]["total_net_gallons"] - (3426 + 4120 + 603 + 6223)) < 0.5


# ---- The real flow: a file with BOTH a number and a name keys on the NAME, and the -------
#      raw→coded crosswalk then shows clean coded names everywhere (never a bare number).
def _wide_bol_number_and_name_csv() -> bytes:
    cols = ["Consignee Number", "Consignee Name", "BOL Number", "Ship Date", "Terminal",
            "Product", "Gross Amount", "Net Amount", "Rack Driver ID"]
    # Two raw spellings of Riverside (same account number) + Hudson — multi-compartment loads.
    variants = [("88231", "RIVERSIDE FUEL CO-NJ"), ("88231", "Riverside Fuel Dist- NJ"),
                ("4471", "HUDSON PETRO LLC")]
    rows, bol, base = [], 700000, pd.Timestamp("2024-01-01")
    for num, name in variants:
        for w in range(10):
            bol += 1
            d = (base + pd.Timedelta(weeks=w)).strftime("%Y-%m-%d")
            for _ in range(2):                                  # two compartments share one BOL
                rows.append((num, name, bol, d, "Linden", "ULSD", 2010, 2000, ""))
    return pd.DataFrame(rows, columns=cols).to_csv(index=False).encode()


def test_wide_bol_keys_on_name_then_crosswalk_shows_coded_names(client):
    ins = client.post("/api/studio/inspect", files={
        "file": ("bol.csv", io.BytesIO(_wide_bol_number_and_name_csv()), "text/csv")}).json()
    mapping = {c: s["target"] for c, s in ins["suggestions_by_table"]["lifts"].items()}
    # Auto-map keys the customer on the NAME (so the name crosswalk resolves), NOT the number.
    assert mapping["Consignee Name"] == "customer_id"
    assert mapping.get("Consignee Number") != "customer_id"

    com = client.post("/api/studio/commit", json={
        "upload_id": ins["upload_id"], "table": "lifts", "mapping": mapping,
        "mode": "replace", "options": {"group_bol": True, "net_correction": "off"}}).json()
    assert com["rows_written"] < com["clean_rows"]              # compartments grouped into lifts
    assert com["summary"]["customers"] == 3                     # three raw names, pre-crosswalk

    # Upload the hand-built raw→coded map; the two Riverside spellings collapse into one master.
    nm = pd.DataFrame({"Raw BOL Account Names": ["RIVERSIDE FUEL CO-NJ", "Riverside Fuel Dist- NJ",
                                                 "HUDSON PETRO LLC"],
                       "Coded Account Names": ["Riverside Fuel", "Riverside Fuel", "Hudson Petroleum"]})
    client.post("/api/studio/crosswalk/upload-names",
                files={"file": ("names.csv", io.BytesIO(nm.to_csv(index=False).encode()), "text/csv")})

    names = {c["name"] for c in client.get("/api/scores?window=all").json()["customers"]}
    assert names == {"Riverside Fuel", "Hudson Petroleum"}      # shown by clean coded names
    # The consignee NUMBER never surfaces as a customer label.
    assert not any(str(n).replace(".", "", 1).isdigit() for n in names)
