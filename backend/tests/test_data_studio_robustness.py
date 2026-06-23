"""Data Studio robustness: forgiving coercion, honest counts, partial-feed imports.

These cover the "use everything we can / quarantine as little as possible" behaviour:
textual missing-value tokens and decorated numbers no longer inflate parse errors, the
clean/quarantined/dropped counts reconcile, and a partial BOL feed (no terminal/tank_id)
imports instead of being held wholesale.
"""

from __future__ import annotations

import io

import pandas as pd

from app import ingest, schema


# ---- Coercion: missing-value tokens are blanks, not parse errors ----------------
def test_numeric_missing_tokens_are_blank_not_errors():
    s = pd.Series(["1000", "N/A", "-", "", "2,500", "TBD", "#REF!"])
    out, n_err, samples = ingest.coerce_column(s, "DOUBLE")
    assert out.iloc[0] == 1000.0
    assert out.iloc[4] == 2500.0                      # thousands separator recovered
    # every textual missing token coerced to NULL …
    assert [bool(pd.isna(v)) for v in out] == [False, True, True, True, False, True, True]
    # … and NONE of them counted as a parse error
    assert n_err == 0
    assert samples == []


def test_numeric_recovers_decorated_values():
    s = pd.Series(["$1,000.50", "(1,234.50)", "−5", "12.5%", "'42"])
    out, n_err, _ = ingest.coerce_column(s, "DOUBLE")
    assert n_err == 0
    assert out.iloc[0] == 1000.50
    assert out.iloc[1] == -1234.50                    # accounting-style negative
    assert out.iloc[2] == -5.0                        # unicode minus
    assert out.iloc[3] == 12.5                         # percent sign stripped
    assert out.iloc[4] == 42.0                         # Excel text-number apostrophe


def test_numeric_real_failures_are_reported_with_samples():
    s = pd.Series(["100", "banana", "200", "12ish", "banana"])
    out, n_err, samples = ingest.coerce_column(s, "DOUBLE")
    assert n_err == 3                                  # two "banana" rows + one "12ish"
    assert samples == ["banana", "12ish"]             # samples de-duplicate
    assert "banana" in samples and "12ish" in samples
    assert out.iloc[0] == 100.0 and out.iloc[2] == 200.0


def test_date_missing_tokens_are_blank_not_errors():
    s = pd.Series(["2024-01-01", "N/A", "-", "not a date"])
    out, n_err, samples = ingest.coerce_column(s, "TIMESTAMP")
    assert n_err == 1                                  # only "not a date" is a true failure
    assert samples == ["not a date"]
    assert not pd.isna(out.iloc[0]) and pd.isna(out.iloc[1])


def test_build_mapped_frame_returns_samples():
    df = pd.DataFrame({"Vol": ["10", "junk", "20"], "Cust": ["A", "B", "C"]})
    out, errors, samples = ingest.build_mapped_frame(
        df, schema.LIFTS, {"Vol": "net_gallons", "Cust": "customer_id"})
    assert errors["net_gallons"] == 1
    assert samples["net_gallons"] == ["junk"]
    assert "customer_id" not in samples               # VARCHAR never errors


# ---- ingest.validate: required-field status + all-null detection ----------------
def test_validate_reports_required_status_and_all_null():
    df = pd.DataFrame({
        "BOL": ["820001", "820002"],
        "When": ["2024-07-01", "2024-07-02"],
        "Net": ["N/A", "-"],                          # mapped but every value missing
    })
    mapping = {"BOL": "bol_number", "When": "bol_datetime", "Net": "compartment_net_gallons"}
    res = ingest.validate(df, schema.BOL, mapping)
    status = {r["field"]: r for r in res["required_status"]}
    assert status["bol_number"]["mapped"] and not status["bol_number"]["all_null"]
    assert status["compartment_net_gallons"]["all_null"] is True
    # an all-null required key is surfaced as a warning, never inflated parse errors
    assert res["total_parse_errors"] == 0
    assert any("every value is blank" in w for w in res["warnings"])


# ---- Relaxed BOL keys: a partial feed is committable ----------------------------
def test_partial_bol_feed_can_commit_without_terminal_or_tank():
    df = pd.DataFrame({
        "BOL": ["820001", "820002"],
        "When": ["2024-07-01", "2024-07-02"],
        "Net": ["5,000", "4,200"],
    })
    mapping = {"BOL": "bol_number", "When": "bol_datetime", "Net": "compartment_net_gallons"}
    res = ingest.validate(df, schema.BOL, mapping)
    assert res["can_commit"] is True                  # terminal/product/tank_id no longer required
    assert res["missing_required"] == []
    assert res["total_parse_errors"] == 0             # comma volumes recovered


# ---- API: honest, reconciling counts + partial-feed import ----------------------
def _bol_csv() -> bytes:
    df = pd.DataFrame(
        [("820001", "2024-07-01", "5,000", "5010"),
         ("820001", "2024-07-01", "-", "3000"),       # net missing → held, not a parse error
         ("820002", "2024-07-02", "4,200", "4210")],
        columns=["BOL Number", "BOL Date", "Compartment Net", "Compartment Gross"])
    return df.to_csv(index=False).encode()


def _inspect_bol(client) -> str:
    r = client.post("/api/studio/inspect",
                    files={"file": ("bol.csv", io.BytesIO(_bol_csv()), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()["upload_id"]


_BOL_MAPPING = {"BOL Number": "bol_number", "BOL Date": "bol_datetime",
                "Compartment Net": "compartment_net_gallons",
                "Compartment Gross": "compartment_gross_gallons"}


def test_validate_counts_reconcile_quarantine_on(client):
    upload_id = _inspect_bol(client)
    r = client.post("/api/studio/validate", json={
        "upload_id": upload_id, "table": "bol_compartments", "mapping": _BOL_MAPPING,
        "options": {"quarantine_failures": True}})
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["can_commit"] is True
    assert v["total_parse_errors"] == 0               # "-" is missing, commas recovered
    assert v["clean_rows"] == 2 and v["quarantine_count"] == 1 and v["dropped_rows"] == 0
    # clean + quarantined + dropped reconcile to the post-hygiene row count
    assert v["clean_rows"] + v["quarantine_count"] + v["dropped_rows"] == v["rows_after_fixes"]


def test_validate_dropped_surfaced_when_quarantine_off(client):
    upload_id = _inspect_bol(client)
    r = client.post("/api/studio/validate", json={
        "upload_id": upload_id, "table": "bol_compartments", "mapping": _BOL_MAPPING,
        "options": {"quarantine_failures": False}})
    v = r.json()
    # the failing row is reported as DROPPED rather than silently vanishing into 0/0
    assert v["quarantine_count"] == 0 and v["dropped_rows"] == 1 and v["clean_rows"] == 2


def test_partial_bol_commit_uses_everything(client):
    upload_id = _inspect_bol(client)
    r = client.post("/api/studio/commit", json={
        "upload_id": upload_id, "table": "bol_compartments", "mapping": _BOL_MAPPING,
        "mode": "replace", "options": {"quarantine_failures": True}})
    assert r.status_code == 200, r.text
    com = r.json()
    assert com["rows_written"] == 2                   # both comma volumes stored
    assert com["quarantined"] == 1                    # only the row with no net is held
