"""Product Reference chart: raw product descriptions → standardized codes.

Mirrors the customer name-map. Upload a two-column chart, load it as the confirmed source of
truth, re-apply across already-loaded lifts so many raw descriptions collapse to one standardized
code, surface still-unmapped codes, resolve at commit when the chart is loaded first, and stay
idempotent on re-upload.
"""

from __future__ import annotations

import io

import pandas as pd

RAWS = ["ULTRA LSD 2 15 PPM (B) (C)", "APPROVED ULSD 2 15 PPM (B) (W) (C)",
        "B10 HO BLEND DYED (ULSHO) (B) (C)", "SOME UNLISTED PRODUCT 7"]


def _lifts_csv() -> bytes:
    rows = []
    base = pd.Timestamp("2024-01-01")
    for i, raw in enumerate(RAWS):
        for w in range(12):
            rows.append((f"Cust {i}", str(base + pd.Timedelta(weeks=w)), 5000 + w * 100, "Linden", raw))
    return pd.DataFrame(rows, columns=["Customer", "Lift Date", "Net Gallons", "Terminal", "Product"]).to_csv(index=False).encode()


def _product_map_csv() -> bytes:
    # Two raw ULSD spellings collapse to ULSD; the heating-oil blend to B10 ULSHO. The 4th raw
    # product is deliberately left out (stays raw, shows as unmapped).
    return pd.DataFrame({
        "Raw Product Code": RAWS[0:3],
        "Standardized Product Code": ["ULSD", "ULSD", "B10 ULSHO"],
    }).to_csv(index=False).encode()


def _commit_lifts(client, options=None):
    r = client.post("/api/studio/inspect",
                    files={"file": ("book.csv", io.BytesIO(_lifts_csv()), "text/csv")})
    assert r.status_code == 200, r.text
    mapping = {"Customer": "customer_id", "Lift Date": "lift_datetime",
               "Net Gallons": "net_gallons", "Terminal": "terminal", "Product": "product"}
    opts = {"group_bol": False, "net_correction": "off"}
    opts.update(options or {})
    r = client.post("/api/studio/commit", json={
        "upload_id": r.json()["upload_id"], "table": "lifts", "mapping": mapping,
        "mode": "replace", "options": opts})
    assert r.status_code == 200, r.text
    return r.json()


def _products(client):
    return set(client.get("/api/summary").json()["products"])


def test_product_map_standardizes_loaded_lifts(client):
    _commit_lifts(client)
    assert _products(client) == set(RAWS)                       # raw, pre-chart

    r = client.post("/api/studio/product-map/upload",
                    files={"file": ("prod.csv", io.BytesIO(_product_map_csv()), "text/csv")})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["ok"] and res["raw_column"] == "Raw Product Code"
    assert res["standard_column"] == "Standardized Product Code"
    assert res["loaded"] == 3 and res["standards"] == 2
    assert res["total_remapped"] == 36                          # all 3 listed raws × 12 rows

    # The two ULSD spellings + the heating-oil blend now read as standardized codes.
    assert _products(client) == {"ULSD", "B10 ULSHO", "SOME UNLISTED PRODUCT 7"}

    # The unlisted product stays raw and is surfaced for the user to add.
    assert res["n_unmapped"] == 1
    assert res["unmapped"][0]["product"] == "SOME UNLISTED PRODUCT 7"


def test_product_map_resolves_at_commit_when_loaded_first(client):
    # Chart first, lifts second → products standardize at commit time (no re-apply needed).
    client.post("/api/studio/product-map/upload",
                files={"file": ("prod.csv", io.BytesIO(_product_map_csv()), "text/csv")})
    _commit_lifts(client)
    assert _products(client) == {"ULSD", "B10 ULSHO", "SOME UNLISTED PRODUCT 7"}


def test_product_map_reupload_is_idempotent(client):
    _commit_lifts(client)
    client.post("/api/studio/product-map/upload",
                files={"file": ("prod.csv", io.BytesIO(_product_map_csv()), "text/csv")})
    r = client.post("/api/studio/product-map/upload",
                    files={"file": ("prod.csv", io.BytesIO(_product_map_csv()), "text/csv")})
    assert r.status_code == 200
    assert r.json()["total_remapped"] == 0                      # already standardized
    assert _products(client) == {"ULSD", "B10 ULSHO", "SOME UNLISTED PRODUCT 7"}
