"""Credit & Account-Risk engine (P9) — financial risk, the account-risk map, conversion targeting.

Gated on the AR fields (``invoice_date``, ``due_date``, ``paid_date``, ``invoice_amount``,
``credit_limit``). Reads the **resolved** customer master (ids already rewritten to master id at
commit) and the **VAR score from P3** (via :mod:`scoring`) so the two risk axes — supply
variability (VAR) and financial reliability (credit) — line up per customer.

Layout:
  Part 1  Per-customer credit facts: DSO, average days late, % invoices late, open exposure vs
          credit_limit, and a worsening/improving trend → a CREDIT RISK SCORE (0–100, higher =
          safer), percentile-ranked across the book like the VAR sub-scores.
  Part 2  Account-risk map: the 2×2 of VAR (x) × credit score (y) with Anchor / Watch / Danger.
  Part 3  Conversion targeting: spot→ratable term candidates, plus Grow-me and Revenue-at-risk.

Every weight/threshold lives in :class:`credit_config.CreditConfig`. Live-computed over the
shared connection with a data-signature cache in the API layer; ``customer_credit`` is a derived
cache (created by :func:`ensure_tables`, NOT ``init_db``) so it survives demo reload / reset.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.stats import rankdata

from . import db, schema, scoring
from .credit_config import QUADRANTS, CreditConfig
from .credit_config import DEFAULT_CONFIG as CREDIT_DEFAULT
from .credit_config import grade as credit_grade
from .scoring_config import DEFAULT_CONFIG as SCORING_DEFAULT
from .scoring_config import WINDOWS, ScoringConfig

# The AR fields the whole module is gated on.
REQUIRED_AR_FIELDS = ("invoice_date", "due_date", "paid_date", "invoice_amount", "credit_limit")


# ---- Persistence (derived cache; recomputed from canonical data) ----------------
CREDIT_DDL = [
    """CREATE TABLE IF NOT EXISTS customer_credit (
        customer_id VARCHAR, score_window VARCHAR, computed_at VARCHAR, name VARCHAR,
        credit_score DOUBLE, credit_grade VARCHAR, safety_absolute DOUBLE,
        dso_days DOUBLE, avg_days_late DOUBLE, pct_late DOUBLE,
        open_exposure DOUBLE, credit_limit DOUBLE, utilization DOUBLE, trend_days_late DOUBLE,
        var_score DOUBLE, quadrant VARCHAR, n_invoices INTEGER, n_open INTEGER,
        detail VARCHAR,
        PRIMARY KEY (customer_id, score_window)
    )""",
]


def ensure_tables(con) -> None:
    for ddl in CREDIT_DDL:
        con.execute(ddl)


# ---- Tiny helpers ---------------------------------------------------------------
def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _pct_rank(values: dict) -> dict:
    """Percentile rank (0–100) of each non-null value across the book; None stays None."""
    items = [(k, v) for k, v in values.items()
             if v is not None and not (isinstance(v, float) and math.isnan(v))]
    out = {k: None for k in values}
    if not items:
        return out
    ks = [k for k, _ in items]
    vs = np.array([float(v) for _, v in items])
    if len(vs) == 1:
        out[ks[0]] = 50.0
        return out
    r = rankdata(vs, method="average")
    pct = (r - 0.5) / len(vs) * 100.0
    for k, p in zip(ks, pct):
        out[k] = round(float(p), 1)
    return out


def _ar_available(con) -> tuple[bool, list[str]]:
    """The capability gate: which of the required AR fields have ≥1 non-null value."""
    if not db.row_count(con, schema.INVOICES):
        return False, list(REQUIRED_AR_FIELDS)
    nn = db.nonnull_counts(con, schema.INVOICES)
    missing = [f for f in REQUIRED_AR_FIELDS if nn.get(f, 0) <= 0]
    return (not missing), missing


# ---- Part 1: per-customer credit facts ------------------------------------------
def _credit_facts(iv: pd.DataFrame, as_of: pd.Timestamp, cfg: CreditConfig) -> dict | None:
    """DSO, average days late, % late, exposure/utilization, and a pay-behavior trend.

    Lateness counts both *paid-late* invoices (paid_date past due) and *currently overdue
    open* invoices (still unpaid past their due date, measured to ``as_of``). Invoices that are
    open but not yet due are neither on-time nor late and are excluded from the late ratio.
    """
    iv = iv.copy()
    for c in ("invoice_date", "due_date", "paid_date"):
        iv[c] = pd.to_datetime(iv[c], errors="coerce")
    iv = iv.dropna(subset=["invoice_date"])
    if not len(iv):
        return None

    terms = None
    if iv["due_date"].notna().any():
        terms = float((iv["due_date"] - iv["invoice_date"]).dt.days.dropna().mean())

    paid = iv.dropna(subset=["paid_date"])
    open_inv = iv[iv["paid_date"].isna()]

    # DSO ~ average collection period (invoice → paid) over paid invoices.
    dso = round(float((paid["paid_date"] - paid["invoice_date"]).dt.days.mean()), 1) if len(paid) else None

    # Lateness per "settled-or-overdue" bill.
    rows = []  # (invoice_date, days_late, is_late)
    if iv["due_date"].notna().any():
        for r in paid.itertuples(index=False):
            if pd.isna(r.due_date):
                continue
            dl = (r.paid_date - r.due_date).days
            rows.append((r.invoice_date, max(0, dl), dl > 0))
        for r in open_inv.itertuples(index=False):
            if pd.isna(r.due_date) or pd.isna(as_of) or as_of <= r.due_date:
                continue  # not yet overdue ⇒ no verdict
            dl = (as_of - r.due_date).days
            rows.append((r.invoice_date, dl, True))

    avg_days_late = pct_late = trend_days_late = None
    if rows:
        considered = pd.DataFrame(rows, columns=["invoice_date", "days_late", "is_late"])
        avg_days_late = round(float(considered["days_late"].mean()), 1)
        pct_late = round(float(considered["is_late"].mean()), 3)
        if len(considered) >= 6:
            considered = considered.sort_values("invoice_date")
            half = len(considered) // 2
            early = considered.iloc[:half]["days_late"].mean()
            recent = considered.iloc[half:]["days_late"].mean()
            trend_days_late = round(float(recent - early), 1)

    credit_limit = float(iv["credit_limit"].dropna().median()) if iv["credit_limit"].notna().any() else None
    open_exposure = round(float(open_inv["invoice_amount"].dropna().sum()), 2)
    utilization = round(open_exposure / credit_limit, 3) if credit_limit else None

    # ---- raw safety (0–1, higher = safer): 1 − weighted penalty ----
    util_pen = _clamp((utilization or 0.0) / cfg.utilization_norm)
    late_pen = pct_late if pct_late is not None else 0.0
    dayslate_pen = _clamp((avg_days_late or 0.0) / cfg.days_late_norm)
    dso_excess = max(0.0, (dso - terms)) if (dso is not None and terms is not None) else 0.0
    dso_pen = _clamp(dso_excess / cfg.dso_excess_norm)
    trend_pen = _clamp(max(0.0, trend_days_late or 0.0) / cfg.trend_norm)
    penalty = (cfg.cr_w_pct_late * late_pen + cfg.cr_w_avg_days_late * dayslate_pen
               + cfg.cr_w_utilization * util_pen + cfg.cr_w_dso_excess * dso_pen
               + cfg.cr_w_trend * trend_pen)
    safety_raw = _clamp(1.0 - penalty)

    return {
        "n_invoices": int(len(iv)), "n_paid": int(len(paid)), "n_open": int(len(open_inv)),
        "payment_terms_days": round(terms, 1) if terms is not None else None,
        "dso_days": dso, "avg_days_late": avg_days_late, "pct_late": pct_late,
        "open_exposure": open_exposure, "credit_limit": round(credit_limit, 0) if credit_limit else None,
        "utilization": utilization, "trend_days_late": trend_days_late,
        "safety_raw": safety_raw,
        "penalty_components": {
            "pct_late": round(late_pen, 3), "avg_days_late": round(dayslate_pen, 3),
            "utilization": round(util_pen, 3), "dso_excess": round(dso_pen, 3),
            "trend": round(trend_pen, 3)},
    }


def _window_cutoff(as_of, window: str):
    if window == "all" or as_of is None:
        return None
    return as_of - pd.Timedelta(days=int(window))


# ---- Orchestration --------------------------------------------------------------
def compute_credit(con, ccfg: CreditConfig | None = None, scfg: ScoringConfig | None = None,
                   window: str = "all") -> dict:
    """Full Credit & Account-Risk payload for one window (gated, percentile-ranked)."""
    ccfg = ccfg or CREDIT_DEFAULT
    scfg = scfg or SCORING_DEFAULT
    if window not in WINDOWS:
        window = "all"

    ok, missing = _ar_available(con)
    if not ok:
        return {
            "available": False, "window": window,
            "missing_fields": missing,
            "reason": ("Feed me " + ", ".join(missing) + " — credit & account-risk needs the AR "
                       "ledger (invoice/due/paid dates, amount, credit limit)."),
            "config": ccfg.to_dict(),
        }

    # VAR + behavioral context from P3 (resolved master ids, same window).
    sc = scoring.compute_scores(con, scfg, window)
    score_by_id = {c["customer_id"]: c for c in sc.get("customers", [])}
    as_of = pd.to_datetime(sc["as_of"]) if sc.get("as_of") else None

    invoices = con.execute(
        "SELECT customer_id, invoice_date, due_date, paid_date, invoice_amount, credit_limit "
        "FROM invoices WHERE customer_id IS NOT NULL").df()
    invoices["invoice_date"] = pd.to_datetime(invoices["invoice_date"], errors="coerce")
    # "today" for overdue-open lateness = the latest dated event in the AR ledger.
    ledger_max = pd.to_datetime(pd.concat([
        pd.to_datetime(invoices["invoice_date"], errors="coerce"),
        pd.to_datetime(invoices["due_date"], errors="coerce"),
        pd.to_datetime(invoices["paid_date"], errors="coerce"),
    ]).dropna()).max() if len(invoices) else None
    as_of = max([d for d in (as_of, ledger_max) if d is not None], default=None)

    customers = con.execute("SELECT customer_id, name, home_terminal FROM customers").df()
    name_by_id = dict(zip(customers["customer_id"], customers["name"])) if len(customers) else {}
    home_by_id = dict(zip(customers["customer_id"], customers["home_terminal"])) if len(customers) else {}

    cutoff = _window_cutoff(as_of, window)
    iw = invoices if cutoff is None else invoices[invoices["invoice_date"] >= cutoff]

    # ---- pass 1: per-customer facts + raw safety ----
    facts: dict[str, dict] = {}
    for cid, grp in iw.groupby("customer_id"):
        f = _credit_facts(grp, as_of, ccfg)
        if f is not None:
            facts[cid] = f
    if not facts:
        return {"available": True, "window": window, "as_of": str(as_of.date()) if as_of is not None else None,
                "config": ccfg.to_dict(), "n_customers": 0, "customers": [],
                "network": {}, "conversion_targets": [], "grow_me": [], "revenue_at_risk": [],
                "quadrant_counts": {}}

    credit_score = _pct_rank({cid: f["safety_raw"] for cid, f in facts.items()})

    # axis cuts for the account-risk map
    var_vals = [score_by_id.get(cid, {}).get("var", {}).get("score") for cid in facts]
    var_vals = [v for v in var_vals if v is not None]
    cr_vals = [v for v in credit_score.values() if v is not None]
    if ccfg.quadrant_split == "fixed":
        var_cut, cr_cut = ccfg.var_fixed_cut, ccfg.credit_fixed_cut
    else:
        var_cut = float(np.median(var_vals)) if var_vals else 50.0
        cr_cut = float(np.median(cr_vals)) if cr_vals else 50.0

    # ---- pass 2: assemble per-customer rows + quadrant ----
    out_customers = []
    quadrant_counts = {q: 0 for q in set(QUADRANTS.values())}
    for cid, f in facts.items():
        sc_c = score_by_id.get(cid, {})
        var_block = sc_c.get("var", {}) or {}
        var_score = var_block.get("score")
        cs = credit_score[cid]
        quad = None
        if var_score is not None and cs is not None:
            vx = "hi" if var_score >= var_cut else "lo"
            cy = "hi" if cs >= cr_cut else "lo"
            quad = QUADRANTS[(vx, cy)]
            quadrant_counts[quad] = quadrant_counts.get(quad, 0) + 1

        subs = sc_c.get("subscores", {}) or {}
        elasticity = subs.get("price_sensitivity", {}) or {}
        out_customers.append({
            "customer_id": cid, "name": name_by_id.get(cid, cid),
            "home_terminal": home_by_id.get(cid), "window": window,
            "credit": {
                "score": cs, "grade": credit_grade(cs, ccfg),
                "safety_absolute": round(f["safety_raw"] * 100.0, 1),
                "dso_days": f["dso_days"], "avg_days_late": f["avg_days_late"],
                "pct_late": f["pct_late"], "open_exposure": f["open_exposure"],
                "credit_limit": f["credit_limit"], "utilization": f["utilization"],
                "trend_days_late": f["trend_days_late"],
                "payment_terms_days": f["payment_terms_days"],
                "n_invoices": f["n_invoices"], "n_open": f["n_open"],
                "components": f["penalty_components"],
                "explanation": _credit_explanation(f, cs)},
            "var_score": var_score, "var_grade": var_block.get("grade"),
            "quadrant": quad,
            "total_net_gallons": sc_c.get("total_net_gallons"),
            "monthly_volume": sc_c.get("monthly_volume"),
            "trend_pct": sc_c.get("trend_pct"),
            "base_value": (sc_c.get("base_value") or {}).get("score"),
            "archetype": (sc_c.get("archetype") or {}).get("primary"),
            "price_sensitivity": elasticity.get("value"),
            "price_sensitivity_available": bool(elasticity.get("available")),
            "churn_risk": (subs.get("churn_risk", {}) or {}).get("value"),
        })

    out_customers.sort(key=lambda c: (c["credit"]["score"] if c["credit"]["score"] is not None else -1),
                       reverse=True)

    network = _network(out_customers, var_cut, cr_cut)
    conv = _conversion_targets(out_customers, ccfg)
    grow = _grow_me(out_customers, ccfg)
    rar = _revenue_at_risk(out_customers, ccfg)

    return {
        "available": True, "window": window,
        "as_of": str(as_of.date()) if as_of is not None else None,
        "config": ccfg.to_dict(), "n_customers": len(out_customers),
        "axis_cuts": {"var": round(var_cut, 1), "credit": round(cr_cut, 1)},
        "elasticity_available": bool(sc.get("availability", {}).get("price_elasticity", {}).get("available")),
        "customers": out_customers,
        "quadrant_counts": quadrant_counts,
        "network": network,
        "conversion_targets": conv,
        "grow_me": grow,
        "revenue_at_risk": rar,
    }


def _credit_explanation(f: dict, score) -> str:
    if score is None:
        return "No VAR pairing yet — credit score computed from AR only."
    bits = []
    if f["dso_days"] is not None:
        bits.append(f"DSO {f['dso_days']:.0f}d" + (f" vs {f['payment_terms_days']:.0f}d terms"
                    if f["payment_terms_days"] is not None else ""))
    if f["pct_late"] is not None:
        bits.append(f"{f['pct_late']:.0%} of bills late")
    if f["avg_days_late"] is not None:
        bits.append(f"avg {f['avg_days_late']:.0f}d late")
    if f["utilization"] is not None:
        bits.append(f"{f['utilization']:.0%} of credit limit drawn (${f['open_exposure']:,.0f} open)")
    if f["trend_days_late"] is not None and abs(f["trend_days_late"]) >= 2:
        bits.append(("slowing " if f["trend_days_late"] > 0 else "improving ")
                    + f"{abs(f['trend_days_late']):.0f}d")
    return f"Credit score {score} (book percentile of payment safety): " + "; ".join(bits) + "."


# ---- Part 2/3 derived blocks ----------------------------------------------------
def _network(rows: list[dict], var_cut: float, cr_cut: float) -> dict:
    n = len(rows)
    open_total = sum((r["credit"]["open_exposure"] or 0.0) for r in rows)
    danger = [r for r in rows if r["quadrant"] == "Danger"]
    danger_exposure = sum((r["credit"]["open_exposure"] or 0.0) for r in danger)
    scored = [r["credit"]["score"] for r in rows if r["credit"]["score"] is not None]
    over_limit = [r for r in rows if (r["credit"]["utilization"] or 0.0) > 1.0]
    return {
        "n_customers": n,
        "open_exposure_total": round(open_total, 0),
        "median_credit_score": round(float(np.median(scored)), 1) if scored else None,
        "n_danger": len(danger),
        "danger_open_exposure": round(danger_exposure, 0),
        "n_over_limit": len(over_limit),
        "var_cut": round(var_cut, 1), "credit_cut": round(cr_cut, 1),
    }


def _conversion_targets(rows: list[dict], cfg: CreditConfig) -> list[dict]:
    """Rank spot→ratable term candidates: high volume + erratic (low VAR) + elastic + OK credit."""
    vol_pct = _pct_rank({r["customer_id"]: r["total_net_gallons"] for r in rows})
    out = []
    for r in rows:
        var = r["var_score"]
        cs = r["credit"]["score"]
        vpct = vol_pct.get(r["customer_id"])
        if var is None or cs is None or vpct is None:
            continue
        if cs < cfg.conv_credit_floor:        # credit gate: don't deepen exposure to slow payers
            continue
        if var >= cfg.conv_var_ceiling:        # already steady — nothing to convert
            continue
        if vpct < cfg.conv_min_volume_pct:     # too small to be worth a term conversation
            continue
        elastic = r["price_sensitivity"]
        elastic_term = elastic if (elastic is not None and r["price_sensitivity_available"]) else 50.0
        conv = (cfg.conv_w_volume * vpct + cfg.conv_w_erratic * (100.0 - var)
                + cfg.conv_w_elastic * elastic_term)
        out.append({
            "customer_id": r["customer_id"], "name": r["name"], "home_terminal": r["home_terminal"],
            "conversion_score": round(conv, 1), "var_score": var, "credit_score": cs,
            "credit_grade": r["credit"]["grade"], "volume_pct": vpct,
            "price_sensitivity": elastic if r["price_sensitivity_available"] else None,
            "monthly_volume": r["monthly_volume"], "archetype": r["archetype"],
            "rationale": _conv_rationale(r, vpct, elastic if r["price_sensitivity_available"] else None),
        })
    out.sort(key=lambda x: x["conversion_score"], reverse=True)
    return out


def _conv_rationale(r: dict, vpct, elastic) -> str:
    vol = f"{(r['monthly_volume'] or 0):,.0f} gal/mo (vol p{vpct:.0f})"
    erratic = f"erratic supply (VAR {r['var_score']})"
    el = (f"price-elastic (elasticity p{elastic:.0f})" if elastic is not None
          else "price response still collecting")
    credit = f"credit OK ({r['credit']['grade']})"
    return f"High volume but {erratic}; {el}; {credit} — lock {vol} on a ratable term deal."


def _grow_me(rows: list[dict], cfg: CreditConfig) -> list[dict]:
    """Steady, growing, good-credit accounts worth leaning into."""
    out = []
    for r in rows:
        var = r["var_score"]
        cs = r["credit"]["score"]
        trend = r["trend_pct"]
        if var is None or cs is None or trend is None:
            continue
        if trend < cfg.grow_min_trend_pct or cs < cfg.grow_credit_floor:
            continue
        score = (cfg.grow_w_var * var + cfg.grow_w_trend * _clamp(trend, 0, 50) / 50.0 * 100.0
                 + cfg.grow_w_credit * cs)
        out.append({
            "customer_id": r["customer_id"], "name": r["name"], "home_terminal": r["home_terminal"],
            "grow_score": round(score, 1), "var_score": var, "credit_score": cs,
            "credit_grade": r["credit"]["grade"], "trend_pct": trend,
            "monthly_volume": r["monthly_volume"], "archetype": r["archetype"],
            "rationale": (f"Steady (VAR {var}), growing +{trend:.0f}%, pays well "
                          f"({r['credit']['grade']}) — expand allocation / cross-sell."),
        })
    out.sort(key=lambda x: x["grow_score"], reverse=True)
    return out


def _revenue_at_risk(rows: list[dict], cfg: CreditConfig) -> list[dict]:
    """Good accounts that are fading — rank by annualized volume at risk."""
    out = []
    for r in rows:
        trend = r["trend_pct"]
        bv = r["base_value"]
        if trend is None or trend > -cfg.rar_min_fade_pct:
            continue
        if bv is not None and bv < cfg.rar_min_base_value:
            continue
        fade = abs(trend) / 100.0
        annual = (r["monthly_volume"] or 0.0) * 12.0
        at_risk = annual * fade
        out.append({
            "customer_id": r["customer_id"], "name": r["name"], "home_terminal": r["home_terminal"],
            "volume_at_risk": round(at_risk, 0), "trend_pct": trend,
            "base_value": bv, "var_score": r["var_score"], "credit_score": r["credit"]["score"],
            "credit_grade": r["credit"]["grade"], "churn_risk": r["churn_risk"],
            "archetype": r["archetype"], "monthly_volume": r["monthly_volume"],
            "rationale": (f"Good account (base value {bv if bv is not None else '—'}) fading "
                          f"{trend:.0f}% — ~{at_risk:,.0f} gal/yr at risk; call before it slips."),
        })
    out.sort(key=lambda x: x["volume_at_risk"], reverse=True)
    return out


# ---- Persistence ----------------------------------------------------------------
def recompute_and_persist(con, ccfg: CreditConfig | None = None,
                          scfg: ScoringConfig | None = None) -> dict:
    """Recompute every window and write the customer_credit derived cache."""
    ccfg = ccfg or CREDIT_DEFAULT
    scfg = scfg or SCORING_DEFAULT
    ensure_tables(con)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute("DELETE FROM customer_credit")
    summary = {}
    for window in WINDOWS:
        res = compute_credit(con, ccfg, scfg, window)
        if not res.get("available"):
            summary[window] = 0
            continue
        summary[window] = res["n_customers"]
        for c in res["customers"]:
            cr = c["credit"]
            con.execute(
                "INSERT INTO customer_credit VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [c["customer_id"], window, now, c["name"],
                 cr["score"], cr["grade"], cr["safety_absolute"], cr["dso_days"],
                 cr["avg_days_late"], cr["pct_late"], cr["open_exposure"], cr["credit_limit"],
                 cr["utilization"], cr["trend_days_late"], c["var_score"], c["quadrant"],
                 cr["n_invoices"], cr["n_open"],
                 json.dumps({"credit": cr, "trend_pct": c["trend_pct"],
                             "base_value": c["base_value"], "archetype": c["archetype"]})])
    db.set_meta(con, "credit_computed_at", now)
    return {"ok": True, "computed_at": now, "windows": summary}
