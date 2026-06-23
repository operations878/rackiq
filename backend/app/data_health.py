"""Standing data-health report — overall quality score and drift alerts.

Reads the *committed* canonical store (not a pending upload) and produces a single quality
score with component breakdown, plus drift alerts that matter for a book fed regularly:
  - new / unmapped customer codes (possible un-merged variants of an existing master)
  - volumes out of the historical monthly pattern

Also surfaces the quarantine backlog, crosswalk size, and recent hygiene activity so the
"Data Health" page is a one-stop standing dashboard.
"""

from __future__ import annotations

import statistics

from . import capabilities, crosswalk, db, schema


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _completeness(con) -> tuple[float, dict]:
    n = db.row_count(con, schema.LIFTS)
    if not n:
        return 1.0, {"rows": 0}
    nn = db.nonnull_counts(con, schema.LIFTS)
    req = schema.required_field_names()
    req_cov = [nn.get(f, 0) / n for f in req if f in nn]
    score = sum(req_cov) / len(req_cov) if req_cov else 1.0
    return _clamp01(score), {"rows": n, "required_coverage": round(score, 4)}


def _validity(con) -> tuple[float, dict]:
    n = db.row_count(con, schema.LIFTS)
    if not n:
        return 1.0, {}
    lo, hi = schema.FIELD_BOUNDS["net_gallons"]
    bad = int(con.execute(
        "SELECT count(*) FROM lifts WHERE net_gallons IS NOT NULL AND "
        "(net_gallons < ? OR net_gallons > ?)", [lo, hi]).fetchone()[0])
    neg = int(con.execute(
        "SELECT count(*) FROM lifts WHERE net_gallons < 0").fetchone()[0])
    invalid = bad + neg
    return _clamp01(1.0 - invalid / n), {"out_of_bounds": bad, "negative": neg}


def _consistency(con) -> tuple[float, dict]:
    n = db.row_count(con, schema.LIFTS)
    if not n:
        return 1.0, {}
    distinct = int(con.execute(
        "SELECT count(*) FROM (SELECT DISTINCT customer_id, lift_datetime, net_gallons FROM lifts)"
    ).fetchone()[0])
    dupes = max(0, n - distinct)
    return _clamp01(1.0 - dupes / n), {"duplicate_lifts": dupes}


def _resolution(con) -> tuple[float, dict]:
    """Fraction of distinct customer codes that are 'known' (resolved or registered)."""
    codes = [r[0] for r in con.execute(
        "SELECT DISTINCT customer_id FROM lifts WHERE customer_id IS NOT NULL").fetchall()]
    if not codes:
        return 1.0, {"distinct_codes": 0}
    cw = db.get_crosswalk(con)
    masters = {v["master_id"] for v in cw.values() if v.get("status") == "confirmed"}
    known = set(cw.keys()) | masters
    resolved = sum(1 for c in codes if c in known)
    return _clamp01(resolved / len(codes)), {
        "distinct_codes": len(codes), "registered": resolved}


WEIGHTS = {"completeness": 0.30, "validity": 0.30, "consistency": 0.20, "resolution": 0.20}


def _drift_customers(con) -> list[dict]:
    """Customer codes not in the crosswalk; flag ones that look like an un-merged variant."""
    cw = db.get_crosswalk(con)
    masters = {v["master_id"]: v.get("master_name") for v in cw.values()
               if v.get("status") == "confirmed"}
    known = set(cw.keys()) | set(masters)
    codes = [r[0] for r in con.execute(
        "SELECT customer_id, count(*) c FROM lifts WHERE customer_id IS NOT NULL "
        "GROUP BY 1 ORDER BY c DESC").fetchall()]
    alerts: list[dict] = []
    for code in codes:
        if code in known:
            continue
        # Does this brand-new code resemble an existing master? (possible variant)
        best_m, best_s = None, 0.0
        for mid, mname in masters.items():
            s = max(crosswalk.similarity(code, mid),
                    crosswalk.similarity(code, mname or mid))
            if s > best_s:
                best_m, best_s = mid, s
        if best_m and best_s >= 0.78:
            alerts.append({"code": code, "kind": "possible_variant",
                           "near": best_m, "similarity": round(best_s, 3)})
        else:
            alerts.append({"code": code, "kind": "new_code"})
    return alerts


def _drift_volume(con) -> dict | None:
    rows = con.execute(
        "SELECT strftime(lift_datetime, '%Y-%m') m, sum(net_gallons) v "
        "FROM lifts WHERE lift_datetime IS NOT NULL GROUP BY 1 ORDER BY 1").fetchall()
    if len(rows) < 4:
        return None
    series = [(r[0], float(r[1] or 0.0)) for r in rows]
    *prior, last = series
    prior_vals = [v for _, v in prior]
    mean = statistics.fmean(prior_vals)
    sd = statistics.pstdev(prior_vals) if len(prior_vals) > 1 else 0.0
    last_month, last_val = last
    if sd <= 0:
        return None
    z = (last_val - mean) / sd
    if abs(z) < 2.0:
        return {"month": last_month, "value": round(last_val, 1), "mean": round(mean, 1),
                "z": round(z, 2), "alert": False}
    return {"month": last_month, "value": round(last_val, 1), "mean": round(mean, 1),
            "z": round(z, 2), "alert": True,
            "direction": "above" if z > 0 else "below"}


def _feeds(con) -> dict:
    """Running counts of the early data feeds (rack benchmark, quotes, receipts)."""
    counts = capabilities.feed_counts(con)

    def _grp(sql: str) -> dict:
        try:
            return {(r[0] or "—"): int(r[1]) for r in con.execute(sql).fetchall()}
        except Exception:  # noqa: BLE001
            return {}

    return {
        "rack_benchmark_days": counts["rack_benchmark_days"],
        "quotes": {
            "total": counts["quotes_logged"],
            "rejected": counts["quotes_rejected"],
            "by_outcome": _grp("SELECT lower(outcome), count(*) FROM quotes GROUP BY 1 ORDER BY 2 DESC"),
        },
        "receipts": {
            "rows": counts["receipt_rows"],
            "by_source": _grp("SELECT lower(receipt_source), count(*) FROM receipts GROUP BY 1 ORDER BY 2 DESC"),
        },
    }


def compute(con) -> dict:
    parts = {
        "completeness": _completeness(con),
        "validity": _validity(con),
        "consistency": _consistency(con),
        "resolution": _resolution(con),
    }
    components = []
    score = 0.0
    for key, (val, detail) in parts.items():
        score += WEIGHTS[key] * val
        components.append({"key": key, "score": round(val * 100, 1),
                           "weight": WEIGHTS[key], "detail": detail})

    cust_alerts = _drift_customers(con)
    vol = _drift_volume(con)
    q_counts = db.quarantine_counts(con)

    return {
        "score": round(score * 100, 1),
        "grade": _grade(score * 100),
        "components": components,
        "drift": {
            "customers": cust_alerts,
            "n_possible_variants": sum(1 for a in cust_alerts if a["kind"] == "possible_variant"),
            "n_new_codes": sum(1 for a in cust_alerts if a["kind"] == "new_code"),
            "volume": vol,
        },
        "quarantine": {"total": sum(q_counts.values()), "by_table": q_counts},
        "feeds": _feeds(con),
        "crosswalk": {"size": len(db.get_crosswalk(con)),
                      "masters": len({v["master_id"] for v in db.get_crosswalk(con).values()
                                      if v.get("status") == "confirmed"})},
        "recent_audit": db.list_hygiene_audit(con, limit=25),
        "profile": db.get_meta(con, "profile", "empty"),
    }


def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"
