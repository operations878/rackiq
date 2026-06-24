"""Unit tests for the Data Hygiene Studio: profiling, VCF, crosswalk, validation, fixes."""

from __future__ import annotations

import pandas as pd

from app import crosswalk, data_health, generator, hygiene, profiling, schema, validation


# ---- ASTM D1250 net-60 correction ----------------------------------------------
def test_vcf_monotonic_and_unity_at_60():
    assert abs(hygiene.vcf(36.0, 60.0, "ULSD") - 1.0) < 1e-9
    assert hygiene.vcf(36.0, 90.0, "ULSD") < 1.0      # hot → shrink
    assert hygiene.vcf(36.0, 30.0, "ULSD") > 1.0      # cold → expand
    # gasoline expands faster than distillate for the same ΔT
    assert hygiene.vcf(60.0, 90.0, "RBOB") < hygiene.vcf(36.0, 90.0, "ULSD")


def test_net_correction_recomputes_from_gross(con):
    df = pd.DataFrame({
        "customer_id": [" C1 ", "C2"],
        "lift_datetime": pd.to_datetime(["2024-01-01", "2024-01-02"]),
        "net_gallons": [None, None],
        "gross_gallons": [1000.0, 2000.0],
        "observed_temp": [90.0, 90.0],
        "api_gravity": [36.0, 36.0],
        "product": ["ULSD", "ULSD"],
    })
    out, report, _audit = hygiene.apply_fixes(
        df, schema.LIFTS, hygiene.HygieneOptions(net_correction="auto", resolve_customers=False), con)
    assert out["net_gallons"].iloc[0] < 1000.0          # corrected down (hot)
    assert out["customer_id"].iloc[0] == "C1"           # trimmed
    assert any(s["step"] == "net_60_correction" for s in report)


def test_unit_standardization_barrels_to_gallons(con):
    df = pd.DataFrame({
        "customer_id": ["C1"], "lift_datetime": pd.to_datetime(["2024-01-01"]),
        "net_gallons": [100.0], "gross_gallons": [100.0],
    })
    opts = hygiene.HygieneOptions(standardize_units=True, source_unit="barrels",
                                  net_correction="off", resolve_customers=False)
    out, _r, _a = hygiene.apply_fixes(df, schema.LIFTS, opts, con)
    assert out["net_gallons"].iloc[0] == 100.0 * schema.GALLONS_PER_BARREL


# ---- Profiling ------------------------------------------------------------------
def test_profiling_flags_negatives_and_bad_dates():
    df = pd.DataFrame({
        "Net Gallons": ["1000", "2000", "-50", "1500", "3000"],
        "Lift Date": ["2024-01-01", "13/02/2024", "2024-03-05", "not a date", "2024-04-01"],
    })
    prof = profiling.profile_frame(df)
    ng = next(c for c in prof["columns"] if c["name"] == "Net Gallons")
    ld = next(c for c in prof["columns"] if c["name"] == "Lift Date")
    assert ng["dtype_guess"] == "number"
    assert any(f["code"] == "negatives" for f in ng["flags"])
    assert any(f["code"] == "unparsed_dates" for f in ld["flags"])
    assert 0 <= prof["score"] <= 100


# ---- Customer Master crosswalk (de-duplication) ---------------------------------
def test_crosswalk_clusters_confirms_and_applies(con):
    counts = {"RIVERSIDE FUEL": 10, "Riverside Fuel Dist": 5,
              "riverside fuel": 3, "Hudson Petroleum": 8}
    res = crosswalk.propose(con, counts, names=None, threshold=0.84)
    assert res["n_groups"] == 1
    group = res["groups"][0]
    members = [m["key"] for m in group["members"]]
    assert "Hudson Petroleum" not in members          # distinct entity not merged
    assert group["confidence"] >= 0.84

    crosswalk.confirm_groups(con, [{"master_id": "RIVERSIDE FUEL",
                                    "master_name": "Riverside Fuel", "members": members}], [], "now")
    frame = pd.DataFrame({"customer_id": ["riverside fuel", "Riverside Fuel Dist", "Hudson Petroleum"]})
    out, n, rewrites = crosswalk.apply_to_frame(frame, "customer_id", con)
    assert (out["customer_id"] == "RIVERSIDE FUEL").sum() == 2
    assert n == 2 and len(rewrites) == 2


def test_crosswalk_reject_suppresses_proposal(con):
    counts = {"ACME FUEL": 5, "Acme Fuel Co": 5}
    crosswalk.confirm_groups(con, [], rejected_keys=["ACME FUEL", "Acme Fuel Co"], now="now")
    res = crosswalk.propose(con, counts, names=None)
    assert res["n_groups"] == 0                        # rejected keys are not re-proposed


# ---- Validation rule engine -----------------------------------------------------
def test_validation_holds_required_and_dupes_but_keeps_negatives(con):
    df = pd.DataFrame({
        "customer_id": ["A", "B", "A", None],
        "lift_datetime": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-01", "2024-01-03"]),
        "net_gallons": [100.0, -5.0, 100.0, 50.0],
    })
    rules = validation.run_rules(df, schema.LIFTS, {"dedupe_lifts_grain": True}, {}, con)
    by = {r["key"]: r for r in rules["rules"]}
    assert by["required_present"]["count"] == 1
    # A negative volume is a correction/reversal — flagged for review, NOT quarantined.
    assert by["volume_corrections"]["count"] == 1
    assert by["volume_corrections"]["action"] == "none"
    assert by["duplicate_lifts"]["count"] == 1
    assert rules["quarantine_count"] == 2              # required + dupe only (negative kept)
    assert "volume_corrections" not in {r for rs in rules["quarantine_reasons"].values() for r in rs}
    assert by["required_present"]["rows"]              # drill-down rows present


# ---- Standing data-health -------------------------------------------------------
def test_data_health_on_generated_book(con):
    generator.generate(generator.GenConfig(seed=3, n_customers=10, months=8, profile="full"), con)
    health = data_health.compute(con)
    assert 0 <= health["score"] <= 100
    assert health["grade"] in {"A", "B", "C", "D", "F"}
    assert {c["key"] for c in health["components"]} == {
        "completeness", "validity", "consistency", "resolution"}
