"""End-to-end API test of the Data Hygiene Studio wizard flow."""

from __future__ import annotations

import io

import pandas as pd


def _dirty_lifts_csv() -> bytes:
    """A tiny in-memory dirty lifts file: name variants, a bad date, a negative, a dupe."""
    rows = [
        # Customer,                 Lift Date,             Net Gallons, Terminal, Product, Gross, Temp, API, Price, Cost
        ("RIVERSIDE FUEL",          "2024-01-01 08:00:00", 5000, "Linden", "ULSD", 5010, 62, 36, 2.55, 2.50),
        ("Riverside Fuel Dist",     "2024-01-05 09:00:00", 4200, "Linden", "ULSD", 4210, 61, 36, 2.56, 2.50),
        ("riverside fuel ",         "2024-01-09 10:00:00", 3800, "Linden", "ULSD", 3810, 63, 36, 2.54, 2.50),
        ("Hudson Petroleum",        "2024-01-02 08:00:00", 9000, "Albany", "RBOB", 9020, 60, 60, 2.23, 2.20),
        ("Hudson Petroleum",        "not a date",          7000, "Albany", "RBOB", 7010, 60, 60, 2.24, 2.20),
        ("Beacon Energy",          "2024-01-03 08:00:00", -250, "Linden", "ULSD", -250, 62, 36, 2.55, 2.50),
        ("Hudson Petroleum",        "2024-01-02 08:00:00", 9000, "Albany", "RBOB", 9020, 60, 60, 2.23, 2.20),  # dup
    ]
    df = pd.DataFrame(rows, columns=["Customer", "Lift Date", "Net Gallons", "Terminal",
                                     "Product", "Gross Gallons", "Temp (F)", "API Gravity",
                                     "Sell Price", "Unit Cost"])
    return df.to_csv(index=False).encode()


def test_full_hygiene_flow(client):
    # 1) inspect → profiling + auto-mapping
    r = client.post("/api/studio/inspect",
                    files={"file": ("dirty.csv", io.BytesIO(_dirty_lifts_csv()), "text/csv")})
    assert r.status_code == 200, r.text
    ins = r.json()
    assert ins["suggested_table"] == "lifts"
    assert "score" in ins["profile"]
    upload_id = ins["upload_id"]
    mapping = {c: s["target"] for c, s in ins["suggestions_by_table"]["lifts"].items()}
    assert mapping["Customer"] == "customer_id"

    # 2) propose merge groups → Riverside variants cluster, Hudson stays separate
    r = client.post("/api/studio/crosswalk/propose",
                    json={"upload_id": upload_id, "table": "lifts", "mapping": mapping})
    assert r.status_code == 200, r.text
    prop = r.json()
    assert prop["n_groups"] >= 1
    riverside = max(prop["groups"], key=lambda g: len(g["members"]))
    member_keys = [m["key"] for m in riverside["members"]]
    assert any("iverside" in k.lower() or "RIVERSIDE" in k for k in member_keys)
    assert riverside["confidence"] >= 0.8

    # 3) confirm
    r = client.post("/api/studio/crosswalk/confirm", json={
        "groups": [{"master_id": riverside["master_id"], "master_name": "Riverside Fuel",
                    "members": member_keys}], "rejected_keys": []})
    assert r.status_code == 200, r.text
    assert r.json()["crosswalk_size"] >= 2

    opts = {"net_correction": "auto", "resolve_customers": True,
            "quarantine_failures": True, "dedupe_lifts_grain": True}

    # 4) validate → rules + quarantine preview
    r = client.post("/api/studio/validate", json={
        "upload_id": upload_id, "table": "lifts", "mapping": mapping, "options": opts})
    assert r.status_code == 200, r.text
    val = r.json()
    assert val["can_commit"] is True
    keyed = {x["key"]: x for x in val["rules"]}
    assert keyed["volume_corrections"]["count"] >= 1   # negative is a correction, not quarantined
    assert keyed["volume_corrections"]["action"] == "none"
    assert val["quarantine_count"] >= 2                # the bad-date row + the duplicate

    # 5) commit → clean rows written, bad rows quarantined, customers merged
    r = client.post("/api/studio/commit", json={
        "upload_id": upload_id, "table": "lifts", "mapping": mapping,
        "mode": "replace", "options": opts, "save_profile": "test-profile"})
    assert r.status_code == 200, r.text
    com = r.json()
    assert com["quarantined"] >= 2
    assert com["rows_written"] >= 1
    # Riverside's 3 variants collapse to one master → fewer customers than raw names
    assert com["summary"]["customers"] <= 3

    # 6) data-health standing report
    r = client.get("/api/studio/data-health")
    assert r.status_code == 200, r.text
    health = r.json()
    assert 0 <= health["score"] <= 100
    assert health["quarantine"]["total"] >= 2

    # 7) quarantine list + reimport a hand-fixed row
    r = client.get("/api/studio/quarantine")
    assert r.status_code == 200, r.text
    q = r.json()
    assert q["total"] >= 2
    # The unparseable-date row is held for a *required* field — fix the date and re-import it.
    bad = next((row for row in q["rows"] if "required_present" in row["reasons"]), None)
    assert bad is not None
    edit = {"lift_datetime": "2024-01-06 08:00:00"}
    r = client.post("/api/studio/quarantine/reimport",
                    json={"ids": [bad["id"]], "edits": {bad["id"]: edit}})
    assert r.status_code == 200, r.text
    assert r.json()["reimported"] == 1

    # 8) saved cleaning profile persisted with hygiene options
    r = client.get("/api/studio/profiles")
    prof = next(p for p in r.json()["profiles"] if p["name"] == "test-profile")
    assert prof["hygiene"] is not None and prof["hygiene"]["net_correction"] == "auto"
