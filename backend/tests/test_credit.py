"""Tests for the Credit & Account-Risk module (P9).

Covers the AR capability gate, the per-customer credit score (DSO / days-late / % late /
exposure / trend → 0–100, higher = safer, percentile-ranked), the VAR × credit account-risk
map, conversion targeting (spot→ratable / grow-me / revenue-at-risk), and the API flow. The
synthetic ``full`` book seeds chronically-late payers, so danger-quadrant accounts and
conversion targets are expected to surface.
"""

from __future__ import annotations

from app import capabilities, credit, generator


def _feature(caps: dict, key: str) -> dict:
    return next(f for f in caps["features"] if f["key"] == key)


def _full(con, seed=42, n=40, months=21):
    generator.generate(generator.GenConfig(seed=seed, n_customers=n, months=months, profile="full"), con)
    return con


# ---- Capability gate ------------------------------------------------------------
def test_credit_gated_then_unlocked(con):
    generator.generate(generator.GenConfig(seed=1, n_customers=12, months=10, profile="lite"), con)
    f = _feature(capabilities.compute_capabilities(con), "credit_account_risk")
    assert f["enabled"] is False and f["status"] == "locked"
    for req in ("invoice_date", "due_date", "paid_date", "invoice_amount", "credit_limit"):
        assert req in f["missing_fields"]

    _full(con, seed=1, n=14, months=14)
    f = _feature(capabilities.compute_capabilities(con), "credit_account_risk")
    assert f["enabled"] is True and f["status"] == "enabled"


def test_engine_locked_payload_without_ar(con):
    generator.generate(generator.GenConfig(seed=2, n_customers=12, months=12, profile="lite"), con)
    r = credit.compute_credit(con)
    assert r["available"] is False
    assert "invoice_date" in r["missing_fields"]
    assert "Feed me" in r["reason"]


# ---- (1) Credit risk score ------------------------------------------------------
def test_credit_score_shape_and_bounds(con):
    _full(con)
    r = credit.compute_credit(con, window="all")
    assert r["available"] and r["n_customers"] > 0
    scores = [c["credit"]["score"] for c in r["customers"] if c["credit"]["score"] is not None]
    assert scores and all(0 <= s <= 100 for s in scores)
    # percentile-ranked across the book ⇒ spans a wide range, not a constant
    assert max(scores) - min(scores) > 40
    for c in r["customers"]:
        cr = c["credit"]
        assert cr["grade"] in ("A", "B", "C", "D", None)
        assert cr["n_invoices"] >= 1
        if cr["utilization"] is not None:
            assert cr["utilization"] >= 0


def test_late_payers_score_lower_than_prompt_payers(con):
    _full(con)
    r = credit.compute_credit(con, window="all")
    # rank by raw safety inputs: high pct_late / days_late should land below the median score
    by_late = sorted((c for c in r["customers"] if c["credit"]["pct_late"] is not None),
                     key=lambda c: c["credit"]["pct_late"], reverse=True)
    assert by_late, "expected some customers with a late ratio"
    worst = by_late[0]
    best = min((c for c in r["customers"] if c["credit"]["score"] is not None),
               key=lambda c: c["credit"]["pct_late"] if c["credit"]["pct_late"] is not None else 1)
    assert worst["credit"]["score"] <= best["credit"]["score"]


# ---- (2) Account-risk map -------------------------------------------------------
def test_account_risk_quadrants_populate(con):
    _full(con)
    r = credit.compute_credit(con, window="all")
    counts = r["quadrant_counts"]
    assert sum(counts.values()) > 0
    # Anchor and Danger are the diagonal we care about; at least one should land in Danger
    assert counts.get("Danger", 0) >= 1, "expected erratic + slow-pay danger accounts"
    quads = {c["quadrant"] for c in r["customers"] if c["quadrant"]}
    assert "Anchor" in quads


# ---- (3) Conversion targeting ---------------------------------------------------
def test_conversion_targets_respect_profile(con):
    _full(con)
    r = credit.compute_credit(con, window="all")
    targets = r["conversion_targets"]
    assert targets, "expected at least one spot→ratable conversion target"
    # ranked by conversion_score desc
    sc = [t["conversion_score"] for t in targets]
    assert sc == sorted(sc, reverse=True)
    cfg = r["config"]
    for t in targets:
        assert t["credit_score"] >= cfg["conv_credit_floor"]   # credit gate honored
        assert t["var_score"] < cfg["conv_var_ceiling"]        # already-steady excluded
        assert t["rationale"]


def test_grow_me_and_revenue_at_risk(con):
    _full(con)
    r = credit.compute_credit(con, window="all")
    for g in r["grow_me"]:
        assert g["trend_pct"] >= r["config"]["grow_min_trend_pct"]
        assert g["credit_score"] >= r["config"]["grow_credit_floor"]
    for v in r["revenue_at_risk"]:
        assert v["trend_pct"] <= -r["config"]["rar_min_fade_pct"]
        assert v["volume_at_risk"] >= 0
    # revenue-at-risk ranked by volume at risk desc
    var = [v["volume_at_risk"] for v in r["revenue_at_risk"]]
    assert var == sorted(var, reverse=True)


# ---- Persistence ----------------------------------------------------------------
def test_recompute_persists_and_survives(con):
    _full(con)
    out = credit.recompute_and_persist(con)
    assert out["ok"] and out["windows"]["all"] > 0
    n = con.execute("SELECT count(*) FROM customer_credit WHERE score_window='all'").fetchone()[0]
    assert n == out["windows"]["all"]


# ---- API ------------------------------------------------------------------------
def test_credit_api_flow(client):
    client.post("/api/studio/load-demo", json={"profile": "full"})
    r = client.get("/api/credit?window=all").json()
    assert r["available"] and r["n_customers"] > 0
    assert r["customers"][0]["credit_score"] is not None
    assert "Anchor" in r["quadrant_order"]
    assert r["conversion_targets"]

    cfg = client.get("/api/credit/config").json()
    assert "conv_credit_floor" in cfg["config"]

    cid = r["customers"][0]["customer_id"]
    drill = client.get(f"/api/credit/customer/{cid}?window=all").json()
    assert drill["customer"]["customer_id"] == cid
    assert "components" in drill["customer"]["credit"]

    rec = client.post("/api/credit/recompute", json={}).json()
    assert rec["ok"]

    assert client.get("/api/credit?window=decade").status_code == 400


def test_credit_api_locked_on_core(client):
    client.post("/api/studio/load-demo", json={"profile": "core"})
    r = client.get("/api/credit").json()
    assert r["available"] is False
    assert "invoice_date" in r["missing_fields"]
