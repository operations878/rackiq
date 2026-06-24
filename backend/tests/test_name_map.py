"""Hand-built customer name-map: raw BOL account names → coded master names.

Exercises the full feature: upload a two-column map, load it as confirmed crosswalk entries,
re-apply across already-loaded lifts so variants collapse into one master customer shown by the
coded name, surface still-unmapped names, and confirm the deep VAR statistics ride along.
"""

from __future__ import annotations

import io

import pandas as pd


def _lifts_csv() -> bytes:
    """A lifts book whose customer column holds RAW consignee names (number-laden variants)."""
    rows = []
    # Three raw spellings of one real customer ("7 Oil") — weekly, steady ~8,400 gal.
    seven_variants = ["7 OIL CO INC.-NJ 991164", "7 OIL CO- NJ", "7 OIL CO/PEPSI- NJ"]
    base = pd.Timestamp("2024-01-01")
    for w in range(30):
        v = seven_variants[w % 3]
        rows.append((v, str(base + pd.Timedelta(weeks=w)), 8400 + (w % 4) * 120, "Linden", "ULSD"))
    # A separate customer with its own raw name.
    for w in range(26):
        rows.append(("HUDSON PETRO LLC 4471", str(base + pd.Timedelta(weeks=w)),
                     12000 + (w % 5) * 300, "Albany", "RBOB"))
    # A name we will deliberately NOT map (stays unmapped, shown as-is).
    for w in range(20):
        rows.append(("RANDO TRUCKING 88", str(base + pd.Timedelta(weeks=w)),
                     3000 + (w % 3) * 200, "Linden", "ULSD"))
    df = pd.DataFrame(rows, columns=["Consignee Name", "Lift Date", "Net Gallons",
                                     "Terminal", "Product"])
    return df.to_csv(index=False).encode()


def _name_map_csv() -> bytes:
    df = pd.DataFrame(
        {
            "Raw BOL Account Names": ["7 OIL CO INC.-NJ 991164", "7 OIL CO- NJ",
                                      "7 OIL CO/PEPSI- NJ", "HUDSON PETRO LLC 4471"],
            "Coded Account Names": ["7 Oil", "7 Oil", "7 Oil", "Hudson Petroleum"],
        }
    )
    return df.to_csv(index=False).encode()


def _commit_lifts(client):
    r = client.post("/api/studio/inspect",
                    files={"file": ("book.csv", io.BytesIO(_lifts_csv()), "text/csv")})
    assert r.status_code == 200, r.text
    upload_id = r.json()["upload_id"]
    mapping = {"Consignee Name": "customer_id", "Lift Date": "lift_datetime",
               "Net Gallons": "net_gallons", "Terminal": "terminal", "Product": "product"}
    opts = {"group_bol": False, "net_correction": "off", "resolve_customers": True}
    r = client.post("/api/studio/commit", json={
        "upload_id": upload_id, "table": "lifts", "mapping": mapping,
        "mode": "replace", "options": opts})
    assert r.status_code == 200, r.text
    return r.json()


def test_name_map_groups_and_renames(client):
    com = _commit_lifts(client)
    # Before any mapping: 5 distinct raw names → 5 customers, all shown by their raw id.
    assert com["summary"]["customers"] == 5

    un = client.get("/api/studio/unmapped-customers").json()
    assert un["n_unmapped"] == 5
    assert un["crosswalk_masters"] == 0

    # Upload the hand-built name map.
    r = client.post("/api/studio/crosswalk/upload-names",
                    files={"file": ("names.csv", io.BytesIO(_name_map_csv()), "text/csv")})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["ok"] is True
    assert res["raw_column"] == "Raw BOL Account Names"
    assert res["coded_column"] == "Coded Account Names"
    assert res["loaded"] == 4 and res["masters"] == 2
    # The three 7-Oil variants + Hudson's raw name all moved to their master id.
    assert res["total_remapped"] >= 30 + 26
    # 5 raw names → 3 customers (7 Oil, Hudson Petroleum, the unmapped RANDO).
    assert res["summary"]["customers"] == 3

    # "7 Oil" now exists as ONE customer, displayed by its coded name, with all variant lifts.
    scores = client.get("/api/scores?window=all").json()
    by_name = {c["name"]: c for c in scores["customers"]}
    assert "7 Oil" in by_name and "Hudson Petroleum" in by_name
    seven = by_name["7 Oil"]
    assert seven["n_lifts"] == 30                       # all three variants rolled up
    assert seven["customer_id"] == "7 Oil"             # master id is the coded name
    # Every mapped customer is displayed by its clean coded name (no raw account-number string).
    assert {c["name"] for c in scores["customers"]} == {"7 Oil", "Hudson Petroleum", "RANDO TRUCKING 88"}

    # The unmapped name is still present, shown as-is, and listed for the user to add.
    un2 = client.get("/api/studio/unmapped-customers").json()
    assert un2["n_unmapped"] == 1
    assert un2["unmapped"][0]["customer_id"] == "RANDO TRUCKING 88"
    assert un2["crosswalk_masters"] == 2


def test_name_map_deep_var_statistics(client):
    _commit_lifts(client)
    client.post("/api/studio/crosswalk/upload-names",
                files={"file": ("names.csv", io.BytesIO(_name_map_csv()), "text/csv")})
    detail = client.get("/api/scores/customer/7 Oil?window=all").json()
    v = detail["customer"]["var"]

    # The frozen score + grade are present...
    assert v["score"] is not None and v["grade"] in {"A", "B", "C", "D"}
    # ...and the transparency layer is fully populated.
    assert v["plain"] and "7 Oil" in v["plain"]
    assert v["descriptor"] in {"Very predictable", "Fairly predictable", "Somewhat erratic", "Erratic"}
    assert v["base_range"][0] <= v["base_level"] <= v["base_range"][1]
    assert v["variability_range"][0] <= v["base_range"][0] and v["base_range"][1] <= v["variability_range"][1]
    assert len(v["components"]) == 3
    assert {c["key"] for c in v["components"]} == {"in_band", "tightness", "excursion_control"}
    assert v["cadence"]["base_cadence_days"] is not None

    d = v["diagnostics"]
    assert d["forecastability"] is not None and 0 <= d["forecastability"] <= 100
    assert d["skill"]["predictability"] is not None
    assert d["trend_test"]["direction"] in {"rising", "falling", "flat"}
    assert d["base_ci"]["lo"] <= d["base_ci"]["base"] <= d["base_ci"]["hi"]
    assert "white_noise" in d["residuals"]
    assert v["steadiness"]["direction"] in {"improving", "deteriorating", "steady", "insufficient"}


def test_name_map_reupload_is_idempotent(client):
    _commit_lifts(client)
    client.post("/api/studio/crosswalk/upload-names",
                files={"file": ("names.csv", io.BytesIO(_name_map_csv()), "text/csv")})
    # Re-uploading the SAME map moves nothing further and keeps the same customer set.
    r = client.post("/api/studio/crosswalk/upload-names",
                    files={"file": ("names.csv", io.BytesIO(_name_map_csv()), "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["total_remapped"] == 0
    assert r.json()["summary"]["customers"] == 3
