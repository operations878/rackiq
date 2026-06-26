"""Tests for deal-book ingestion: product families, identity normalization, parsers, idempotent
upsert, and the deal-book → BOL-master crosswalk bridge."""

from __future__ import annotations

import datetime as dt
import os

import pandas as pd
import pytest

from app import dealbook, db

SAMPLE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sample_data", "deals")


# ---- product family normalization ----
@pytest.mark.parametrize("raw,fam", [
    ("ULSD 15MV2 B5", "ULSD"),
    ("ULTRA LSD 2 15 PPM", "ULSD"),
    ("ULSD #2 15 MV", "ULSD"),                          # #2 with a diesel token → ULSD, NOT heating oil
    ("HO BLEND DYED (ULSHO) (B) (C)", "ULSHO"),         # dyed heating oil is still heating oil
    ("B20 HO BLEND DYED (ULSHO) (B) (C)", "ULSHO"),     # blend number does not change the family
    ("DYED ULTRA LOW SULFUR HEATING OIL", "ULSHO"),
    ("2 OIL", "ULSHO"),                                  # bare #2 / 2-oil (no diesel token) → heating oil
    ("ULSD 15MV2 DYED B5", "DYED"),                      # dyed clear diesel
    ("FUEL OIL #4 0.15% (B)", "HO4"),
    ("B-5 Renewable Diesel 15 ppm Clear", "RD"),
    ("BIO DIESEL 99.9", "RD"),
    ("ZZZ", "OTHER"),
])
def test_product_family(raw, fam):
    assert dealbook.product_family(raw) == fam


def test_blend_number_is_product_not_identity():
    # "GEC 10" / "GEC 20" are GEC at B10/B20 — identity must collapse to GEC
    assert dealbook.base_customer_identity("GEC 10") == "GEC"
    assert dealbook.base_customer_identity("GEC 20") == "GEC"
    assert dealbook.base_customer_identity("Summa") == "Summa"
    assert dealbook.base_customer_identity("Century Star") == "Century Star"


def test_deal_key_stable_and_sensitive():
    k1 = dealbook.deal_key("spot", "Summa", "ULSD", None, dt.date(2025, 10, 1), dt.date(2025, 10, 16))
    k2 = dealbook.deal_key("spot", "Summa", "ULSD", None, dt.date(2025, 10, 1), dt.date(2025, 10, 16))
    k3 = dealbook.deal_key("spot", "Summa", "ULSD", None, dt.date(2025, 11, 1), dt.date(2025, 10, 16))
    assert k1 == k2 and k1 != k3


# ---- parsers against the bundled sample files (skip if the operator hasn't dropped them in) ----
def _have(name):
    return os.path.exists(os.path.join(SAMPLE_DIR, name))


@pytest.mark.skipif(not _have("deals_summary.xlsx"), reason="term sample not present")
def test_parse_term():
    rows = dealbook.parse_term(os.path.join(SAMPLE_DIR, "deals_summary.xlsx"))
    assert rows and all(r["source"] == "term" for r in rows)
    assert all(r["committed_gallons"] is None or r["committed_gallons"] > 0 for r in rows)
    assert {r["customer_raw"] for r in rows}                     # has customers
    assert any(r["price_type"] == "basis" for r in rows)


@pytest.mark.skipif(not _have("forward_fixed_price_sales.xlsx"), reason="forward sample not present")
def test_parse_forward_excludes_orphans():
    rows = dealbook.parse_forward_fixed(os.path.join(SAMPLE_DIR, "forward_fixed_price_sales.xlsx"))
    assert rows and all(r["source"] == "forward_fixed" for r in rows)
    # orphan rows (no customer) are excluded → every row is attributed
    assert all(r["customer_raw"] and r["customer_raw"] != "Approved" for r in rows)
    assert all(r["deal_date"] is None or isinstance(r["deal_date"], dt.date) for r in rows)


@pytest.mark.skipif(not _have("wholesale_spot_deal_report.xlsx"), reason="spot sample not present")
def test_parse_spot_has_months_and_realized():
    rows = dealbook.parse_spot(os.path.join(SAMPLE_DIR, "wholesale_spot_deal_report.xlsx"))
    assert rows and all(r["source"] == "spot" for r in rows)
    assert all(r["realized_gallons"] and r["realized_gallons"] > 0 for r in rows)
    assert sum(1 for r in rows if r["month"] is not None) == len(rows)   # the date-column fix


# ---- idempotent upsert ----
def _rows(source, custs):
    out = []
    for cust, month, gal in custs:
        out.append({
            "source": source, "customer_raw": cust, "product_raw": "ULSD", "product_family": "ULSD",
            "terminal": None, "month": month, "committed_gallons": gal if source != "spot" else None,
            "realized_gallons": gal if source == "spot" else None, "price": 2.0,
            "price_type": "realized" if source == "spot" else "fixed", "commitment_type": "firm",
            "volume_basis": "net", "deal_date": None, "representative": None,
        })
    for r in out:
        r["deal_key"] = dealbook.deal_key(r["source"], r["customer_raw"], r["product_family"],
                                          r["terminal"], r["month"], r["deal_date"])
    return out


def test_upsert_is_idempotent(con):
    rows = _rows("spot", [("Summa", dt.date(2025, 10, 1), 1000.0),
                          ("Rastall", dt.date(2025, 10, 1), 2000.0)])
    db.upsert_deals(con, rows, "spot", "spot.xlsx", "now")
    db.upsert_deals(con, rows, "spot", "spot.xlsx", "now")          # re-run same month
    assert db.deals_count(con, "spot") == 2                          # no double-count
    total = con.execute("SELECT sum(realized_gallons) FROM deals").fetchone()[0]
    assert total == 3000.0


def test_upsert_scope_replace(con):
    db.upsert_deals(con, _rows("spot", [("Summa", dt.date(2025, 10, 1), 1000.0)]), "spot", "f", "now")
    # re-upload the same month with a corrected number → replaces, not appends
    db.upsert_deals(con, _rows("spot", [("Summa", dt.date(2025, 10, 1), 1500.0)]), "spot", "f", "now")
    assert db.deals_count(con, "spot") == 1
    assert con.execute("SELECT realized_gallons FROM deals").fetchone()[0] == 1500.0


# ---- the crosswalk bridge ----
def test_bridge_proposes_not_auto_merges(con):
    # a BOL master "Taylor Oil" exists; the deal book says "Taylor" → should be a CANDIDATE, not mapped
    db.insert_df(con, "lifts", pd.DataFrame({
        "customer_id": ["Taylor Oil"] * 6,
        "lift_datetime": pd.date_range("2024-01-01", periods=6, freq="7D"),
        "net_gallons": [5000.0] * 6}))
    db.upsert_deals(con, _rows("forward_fixed", [("Taylor", dt.date(2024, 1, 1), 9000.0)]),
                    "forward_fixed", "f", "now")
    db.resolve_deal_masters(con)
    bridge = dealbook.bridge_candidates(con)
    cand_names = {c["customer_raw"] for c in bridge["candidates"]}
    assert "Taylor" in cand_names                                   # proposed, not silently merged
    assert bridge["n_mapped"] == 0
    # confirming the bridge attaches the master
    dealbook.confirm_bridge(con, [("Taylor", "Taylor Oil")], "now")
    assert con.execute("SELECT customer_master FROM deals").fetchone()[0] == "Taylor Oil"
