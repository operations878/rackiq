"""Daily operating engine — regime-aware re-ranking and the nine actionable panels.

This is the brain behind **Blueprint C** (the Daily Operating Dashboard). It takes the
standing customer scores (``scoring.compute_scores``), applies the **V1 regime-multiplier
matrix** (``regime_config``) to each customer's Base Value to get today's **Regime-Adjusted
Score**, and assembles nine *ranked, actionable* worklists per terminal — lists, not charts.

Every row carries: an **action**, a one-line **why-now**, and an **expected impact**. The
output is also persisted to the ``daily_recommendations`` table (§14) so the morning worklist
is reproducible and auditable.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from . import db, scoring
from .regime_config import (normalize_regime, opposite_regime, regime_breakdown,
                            regime_label, regime_multiplier, regime_score)
from .scoring_config import DEFAULT_CONFIG, ScoringConfig

# §14 — the daily worklist store. Reproducible per run_date × terminal × regime.
DAILY_DDL = """
CREATE TABLE IF NOT EXISTS daily_recommendations (
    run_date VARCHAR, computed_at VARCHAR, terminal VARCHAR, regime VARCHAR,
    regime_label VARCHAR, panel VARCHAR, rank INTEGER,
    customer_id VARCHAR, customer_name VARCHAR, archetype VARCHAR,
    action VARCHAR, why_now VARCHAR, expected_impact VARCHAR, impact_value DOUBLE,
    base_value DOUBLE, regime_score DOUBLE
)
"""

# Panel registry — order matters (this is the on-screen order).
PANELS: list[tuple[str, str, str]] = [
    ("today_actions", "Today's Actions", "The highest-impact moves to make first."),
    ("customer_rankings", "Customer Rankings", "Base Value vs. today's Regime Score."),
    ("inventory_actions", "Inventory Actions", "Move length or protect supply for today's book."),
    ("pricing_opportunities", "Pricing Opportunities", "Where to push price or quote thin today."),
    ("credit_alerts", "Credit Alerts", "Exposure to watch before you allocate."),
    ("churn_alerts", "Churn Alerts", "Accounts fading or overdue — call before they're gone."),
    ("contract_candidates", "Contract Candidates", "Steady volume worth locking with a term deal."),
    ("discount_opportunities", "Discount Opportunities", "Where a targeted cut actually pays for itself."),
    ("strategic_accounts", "Strategic Accounts", "Protect and invest in the franchise."),
]
PANEL_LABEL = {k: label for k, label, _ in PANELS}


def ensure_tables(con) -> None:
    con.execute(DAILY_DDL)


# ---- formatting helpers ---------------------------------------------------------
def _gal(x: float | None) -> str:
    if not x:
        return "—"
    if abs(x) >= 1e6:
        return f"{x / 1e6:.2f}MM gal"
    if abs(x) >= 1e3:
        return f"{x / 1e3:.0f}k gal"
    return f"{x:,.0f} gal"


def _usd(x: float | None) -> str:
    if x is None:
        return "—"
    if abs(x) >= 1e6:
        return f"${x / 1e6:.2f}MM"
    if abs(x) >= 1e3:
        return f"${x / 1e3:.0f}k"
    return f"${x:,.0f}"


def _row(c: dict, regime: dict, action: str, why: str, impact: str, impact_value: float) -> dict:
    """Build one panel row from a scored-customer record."""
    arche = c["archetype"]["primary"]
    bv = c["base_value"]["score"]
    rs = regime_score(bv, arche, regime)
    return {
        "customer_id": c["customer_id"], "name": c["name"],
        "archetype": arche, "secondary_archetype": c["archetype"]["secondary"],
        "home_terminal": c["home_terminal"],
        "action": action, "why_now": why,
        "expected_impact": impact, "impact_value": round(float(impact_value), 1),
        "base_value": bv, "regime_score": rs,
        "regime_delta": round((rs - bv), 1) if rs is not None else None,
    }


def _exposure(c: dict) -> float:
    bv = c["base_value"]
    util = (c.get("facts") or {}).get("credit_utilization")
    if util is not None and bv["egp"]:
        return max(bv["egp"] * 0.12, util * bv["egp"])
    return max(1.0, bv["egp"] * 0.12)


# ---- the nine panels ------------------------------------------------------------
def _customer_rankings(cs: list[dict], regime: dict) -> list[dict]:
    rows = []
    for c in cs:
        bv = c["base_value"]["score"]
        rs = regime_score(bv, c["archetype"]["primary"], regime)
        delta = (rs - bv) if rs is not None else 0.0
        if delta >= 8:
            action, why = "Prioritize today", "Regime lifts this account well above its standing value."
        elif delta <= -8:
            action, why = "Deprioritize today", "Regime pushes this account below its standing value."
        elif rs is not None and rs >= 70:
            action, why = "Hold / service", "High standing value, neutral in today's regime."
        else:
            action, why = "Maintain", "Steady-state account in today's regime."
        rows.append(_row(c, regime, action, why,
                         f"Base {bv} → Regime {rs} ({'+' if delta >= 0 else ''}{round(delta, 1)})",
                         rs if rs is not None else bv))
    rows.sort(key=lambda r: (r["regime_score"] if r["regime_score"] is not None else -1), reverse=True)
    return rows


def _inventory_actions(cs: list[dict], regime: dict) -> list[dict]:
    inv = regime["inventory"]
    absorb = {"Surplus Absorber", "Price Shopper", "Flex Buyer", "Backup-Only", "Scarcity Buyer"}
    protect = {"Anchor Base-Load", "Strategic Platform", "Contract Candidate", "Premium Spot"}
    rows = []
    for c in cs:
        arche = c["archetype"]["primary"]
        ann = c["base_value"]["annual_gallons"]
        mv = c["monthly_volume"]
        if inv in ("long", "tank_constrained"):
            if arche in absorb:
                extra = mv * 0.5
                rows.append(_row(c, regime, "Call to take extra volume",
                                 f"{inv.replace('_', '-')} book — {arche} absorbs length at a clearing price.",
                                 f"~{_gal(extra)} placed", extra))
        elif inv == "tight":
            if arche in protect:
                rows.append(_row(c, regime, "Protect / ration allocation",
                                 f"Tight supply — secure the {arche} before discretionary buyers.",
                                 f"Protect {_gal(ann)}/yr", ann))
            elif arche in ("Price Shopper", "Backup-Only", "Surplus Absorber"):
                rows.append(_row(c, regime, "Hold off — surplus only",
                                 "Tight supply — don't burn scarce gallons on discretionary demand.",
                                 f"Free up {_gal(mv)}", mv))
        else:  # balanced / normal
            if arche in protect and (c["var"]["score"] or 0) >= 60:
                rows.append(_row(c, regime, "Keep serviced",
                                 "Balanced book — keep your steady base-load humming.",
                                 f"{_gal(ann)}/yr base", ann))
    rows.sort(key=lambda r: r["impact_value"], reverse=True)
    return rows


def _pricing_opportunities(cs: list[dict], regime: dict, cfg: ScoringConfig) -> list[dict]:
    mkt, inv = regime["market"], regime["inventory"]
    raise_set = {"Premium Spot", "Scarcity Buyer", "Weather-Triggered"}
    fill_set = {"Price Shopper", "Flex Buyer", "Surplus Absorber", "Backup-Only"}
    rows = []
    tight_or_rising = (inv == "tight") or (mkt in ("rising", "volatile"))
    long_or_falling = (inv in ("long", "tank_constrained")) or (mkt == "falling")
    for c in cs:
        arche = c["archetype"]["primary"]
        ann = c["base_value"]["annual_gallons"]
        if tight_or_rising and arche in raise_set:
            delta = 0.01  # +1¢/gal premium opportunity
            rows.append(_row(c, regime, "Hold / raise premium",
                             f"{arche} pays for availability in a {mkt}/{inv} regime.",
                             f"+{_usd(ann * delta)}/yr at +1¢", ann * delta))
        elif long_or_falling and arche in fill_set:
            extra = c["monthly_volume"] * 0.4
            mgn = (c.get("facts") or {}).get("gross_margin_per_gal_mean") or 0.04
            rows.append(_row(c, regime, "Quote thin to fill",
                             f"{arche} lifts on price — use a thin quote to drain length.",
                             f"~{_usd(extra * 12 * max(mgn, 0.0))}/yr incremental GP", extra * 12 * max(mgn, 0.0)))
    rows.sort(key=lambda r: r["impact_value"], reverse=True)
    return rows


def _credit_alerts(cs: list[dict], regime: dict) -> list[dict]:
    tight = regime["credit"] == "tight"
    rows = []
    for c in cs:
        facts = c.get("facts") or {}
        util = facts.get("credit_utilization")
        late = facts.get("late_rate")
        arche = c["archetype"]["primary"]
        flagged = (util is not None and util >= 0.80) or (late is not None and late >= 0.20) \
            or arche == "Credit Drag"
        if not flagged:
            continue
        exp = _exposure(c)
        bits = []
        if util is not None:
            bits.append(f"{util * 100:.0f}% credit used")
        if late is not None and late >= 0.15:
            bits.append(f"{late * 100:.0f}% late")
        if arche == "Credit Drag":
            bits.append("Credit-Drag archetype")
        why = ("Credit tight — " if tight else "") + (", ".join(bits) or "elevated exposure") + "."
        action = "Cap / shorten terms" if (tight or arche == "Credit Drag") else "Watch exposure"
        rows.append(_row(c, regime, action, why, f"{_usd(exp)} at risk", exp))
    rows.sort(key=lambda r: r["impact_value"], reverse=True)
    return rows


def _churn_alerts(cs: list[dict], regime: dict) -> list[dict]:
    rows = []
    for c in cs:
        churn = c["subscores"].get("churn_risk", {}).get("value") or 0.0
        gap = c["recency_gap"]
        trend = c["trend_pct"]
        if churn < 45 and gap <= 1.5 and trend > -15:
            continue
        ann = c["base_value"]["annual_gallons"]
        bits = []
        if gap > 1.5:
            bits.append(f"{gap:.1f}× past usual cadence")
        if trend <= -10:
            bits.append(f"volume {trend:.0f}%")
        if churn >= 45:
            bits.append(f"churn risk {churn:.0f}")
        rows.append(_row(c, regime, "Call before they're gone",
                         "Fading — " + (", ".join(bits) or "elevated churn risk") + ".",
                         f"{_gal(ann)}/yr at risk", ann))
    rows.sort(key=lambda r: r["impact_value"], reverse=True)
    return rows


def _contract_candidates(cs: list[dict], regime: dict) -> list[dict]:
    rows = []
    for c in cs:
        a = c["archetype"]
        is_cc = "Contract Candidate" in (a["primary"], a["secondary"])
        steady_growth = (c["var"]["score"] or 0) >= 62 and c["base_value"]["score"] < 65 and c["trend_pct"] >= -2
        if not (is_cc or steady_growth):
            continue
        ann = c["base_value"]["annual_gallons"]
        why = ("Classified Contract Candidate" if is_cc
               else f"Steady (VAR {c['var']['score']}) with headroom") + \
            (" — lock before scarcity." if regime["inventory"] == "tight" else " — lock the volume.")
        rows.append(_row(c, regime, "Offer a term deal", why, f"Lock ~{_gal(ann)}/yr", ann))
    rows.sort(key=lambda r: r["impact_value"], reverse=True)
    return rows


def _discount_opportunities(cs: list[dict], regime: dict) -> list[dict]:
    """Best targets for a volume-buying discount. Strict winners (efficiency ratio > 1) lead;
    otherwise rank by the discount-efficiency percentile so the panel always shows the most
    efficient accounts to test — never a perpetually-empty list when elasticity is thin."""
    cand = []
    for c in cs:
        de = c["subscores"].get("discount_efficiency", {})
        if not de.get("available"):
            continue
        ratio = de.get("ratio")
        value = de.get("value")  # percentile across the book
        if ratio is None and value is None:
            continue
        cand.append((c, ratio, value))
    strict = [(c, r, v) for c, r, v in cand if r is not None and r >= 1.0]
    pool = strict if strict else cand
    # rank: prefer the highest ratio when present, else the percentile sub-score
    pool.sort(key=lambda t: (t[1] if t[1] is not None else -1, t[2] or 0), reverse=True)
    rows = []
    for c, ratio, value in pool[:15]:
        ann = c["base_value"]["annual_gallons"]
        mgn = (c.get("facts") or {}).get("gross_margin_per_gal_mean") or 0.04
        if ratio is not None and ratio >= 1.0:
            inc = ann * 0.02 * max(mgn, 0.0) * (ratio - 1.0)
            why = f"Discount efficiency ×{ratio:.2f} — a 2¢ cut returns more GP than it gives up."
            impact = f"~{_usd(inc)}/yr net GP"
            rank_val = (value or 0) + 100  # strict winners outrank the relative pool
        else:
            why = (f"Most price-responsive in the book (efficiency pct {value:.0f})"
                   if value is not None else "Price-responsive account") + \
                " — best target to test a volume-buying discount."
            impact = f"~{_gal(ann * 0.04)} upside if it converts"
            rank_val = value or 0
        rows.append(_row(c, regime, "Test a targeted discount", why, impact, rank_val))
    return rows


def _strategic_accounts(cs: list[dict], regime: dict) -> list[dict]:
    rows = []
    for c in cs:
        arche = c["archetype"]["primary"]
        bv = c["base_value"]["score"]
        if not ((arche in ("Strategic Platform", "Anchor Base-Load") and bv >= 58) or bv >= 80):
            continue
        rfap = c["base_value"]["rfap"]
        why = f"{arche} — {_usd(rfap)}/yr risk-adjusted profit; this is the franchise."
        action = "Invest / deepen" if arche == "Strategic Platform" else "Protect & defend"
        rows.append(_row(c, regime, action, why, f"{_usd(rfap)}/yr RFAP", rfap))
    rows.sort(key=lambda r: r["impact_value"], reverse=True)
    return rows


def _today_actions(panels: dict[str, list[dict]], regime: dict) -> list[dict]:
    """Cross-panel digest: the highest-priority single move per customer, top of the stack."""
    # source priority weights (urgency); impact is normalized within its own panel
    weights = {"credit_alerts": 1.0, "churn_alerts": 0.95, "inventory_actions": 0.85,
               "pricing_opportunities": 0.8, "contract_candidates": 0.65,
               "discount_opportunities": 0.6, "strategic_accounts": 0.55}
    best: dict[str, dict] = {}
    for src, w in weights.items():
        rows = panels.get(src, [])
        if not rows:
            continue
        top = max(r["impact_value"] for r in rows) or 1.0
        for r in rows:
            norm = (r["impact_value"] / top) if top else 0.0
            priority = w * (0.5 + 0.5 * norm)
            cur = best.get(r["customer_id"])
            if cur is None or priority > cur["_priority"]:
                best[r["customer_id"]] = {**r, "_priority": priority, "source": PANEL_LABEL[src]}
    out = sorted(best.values(), key=lambda r: r["_priority"], reverse=True)[:12]
    for r in out:
        r.pop("_priority", None)
    return out


def build_panels(cs: list[dict], regime: dict, cfg: ScoringConfig) -> dict[str, list[dict]]:
    """Build all nine panels for one terminal's scored customers."""
    panels: dict[str, list[dict]] = {
        "customer_rankings": _customer_rankings(cs, regime),
        "inventory_actions": _inventory_actions(cs, regime),
        "pricing_opportunities": _pricing_opportunities(cs, regime, cfg),
        "credit_alerts": _credit_alerts(cs, regime),
        "churn_alerts": _churn_alerts(cs, regime),
        "contract_candidates": _contract_candidates(cs, regime),
        "discount_opportunities": _discount_opportunities(cs, regime),
        "strategic_accounts": _strategic_accounts(cs, regime),
    }
    panels["today_actions"] = _today_actions(panels, regime)
    return panels


# ---- orchestration --------------------------------------------------------------
def build_daily(con, regime: dict | None = None, terminal: str | None = None,
                window: str = "all", cfg: ScoringConfig | None = None,
                limit: int = 12) -> dict:
    """Compute the per-terminal daily operating dashboard for a regime.

    Returns the chosen terminal's nine panels plus the list of terminals and the regime
    config echo. Each panel is truncated to ``limit`` rows for the UI (full set is persisted).
    """
    cfg = cfg or DEFAULT_CONFIG
    regime = normalize_regime(regime)
    res = scoring.compute_scores(con, cfg, window)
    customers = res["customers"]
    terminals = sorted({c["home_terminal"] for c in customers if c["home_terminal"]})
    if terminal is None or (terminals and terminal not in terminals):
        terminal = terminals[0] if terminals else None

    scoped = [c for c in customers if (terminal is None or c["home_terminal"] == terminal)]
    panels_full = build_panels(scoped, regime, cfg)
    panels = [{
        "key": key, "label": label, "description": desc,
        "rows": panels_full.get(key, [])[:limit],
        "total": len(panels_full.get(key, [])),
    } for key, label, desc in PANELS]

    return {
        "as_of": res["as_of"], "window": window,
        "regime": regime, "regime_label": regime_label(regime),
        "terminal": terminal, "terminals": terminals,
        "n_customers": len(scoped), "availability": res["availability"],
        "panels": panels,
    }


def persist_daily(con, regime: dict | None = None, window: str = "all",
                  cfg: ScoringConfig | None = None) -> dict:
    """Recompute the full worklist for *every* terminal and write daily_recommendations (§14)."""
    cfg = cfg or DEFAULT_CONFIG
    regime = normalize_regime(regime)
    ensure_tables(con)
    res = scoring.compute_scores(con, cfg, window)
    customers = res["customers"]
    terminals = sorted({c["home_terminal"] for c in customers if c["home_terminal"]}) or [None]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run_date = str(date.today())
    rlabel = regime_label(regime)
    import json
    regime_json = json.dumps(regime)

    con.execute("DELETE FROM daily_recommendations WHERE run_date = ?", [run_date])
    written = 0
    for term in terminals:
        scoped = [c for c in customers if (term is None or c["home_terminal"] == term)]
        panels_full = build_panels(scoped, regime, cfg)
        for key, _label, _desc in PANELS:
            for rank, r in enumerate(panels_full.get(key, []), start=1):
                con.execute(
                    "INSERT INTO daily_recommendations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [run_date, now, term or "(all)", regime_json, rlabel, key, rank,
                     r["customer_id"], r["name"], r["archetype"], r["action"], r["why_now"],
                     r["expected_impact"], r["impact_value"], r["base_value"], r["regime_score"]])
                written += 1
    db.set_meta(con, "daily_computed_at", now)
    return {"ok": True, "run_date": run_date, "computed_at": now,
            "regime": regime, "terminals": [t for t in terminals if t], "rows_written": written}


def scorecards(con, regime: dict | None = None, terminal: str | None = None,
               window: str = "all", cfg: ScoringConfig | None = None) -> dict:
    """Per-customer blueprint scorecards with the regime-adjusted score and the flip-side line.

    Returns one representative-rich record per customer in scope (covering every archetype
    present), each with: sub-scores, Base Value, today's Regime-Adjusted Score, archetype(s),
    why-now, recommended action, posture (pricing/terms/allocation), expected impact, and the
    **flip side** — how the score & action change under the opposite inventory/market regime.
    """
    cfg = cfg or DEFAULT_CONFIG
    regime = normalize_regime(regime)
    flip = opposite_regime(regime)
    res = scoring.compute_scores(con, cfg, window)
    customers = res["customers"]
    terminals = sorted({c["home_terminal"] for c in customers if c["home_terminal"]})
    scoped = [c for c in customers if (terminal is None or c["home_terminal"] == terminal)]

    cards = []
    for c in scoped:
        arche = c["archetype"]["primary"]
        bv = c["base_value"]["score"]
        rs = regime_score(bv, arche, regime)
        fs = regime_score(bv, arche, flip)
        cards.append({
            "customer_id": c["customer_id"], "name": c["name"],
            "home_terminal": c["home_terminal"],
            "archetype": c["archetype"], "base_value": c["base_value"],
            "var": c["var"], "subscores": c["subscores"], "quadrant": c["quadrant"],
            "monthly_volume": c["monthly_volume"], "trend_pct": c["trend_pct"],
            "recency_gap": c["recency_gap"], "facts": c.get("facts"),
            "regime_score": rs, "regime_multiplier": round(regime_multiplier(arche, regime), 3),
            "regime_breakdown": {k: round(v, 3) for k, v in regime_breakdown(arche, regime).items()},
            "why_now": _scorecard_why(c, regime),
            "recommended_action": _scorecard_action(c, regime),
            "expected_impact": _scorecard_impact(c, regime),
            "flip": {
                "regime": flip, "regime_label": regime_label(flip),
                "regime_score": fs, "delta": round((fs - rs), 1) if (fs is not None and rs is not None) else None,
                "action": _scorecard_action(c, flip),
                "line": _flip_line(c, regime, flip, rs, fs),
            },
        })
    cards.sort(key=lambda x: (x["regime_score"] if x["regime_score"] is not None else -1), reverse=True)

    # one exemplar per archetype present (so the view "covers every archetype present")
    by_arche: dict[str, dict] = {}
    for card in cards:
        by_arche.setdefault(card["archetype"]["primary"], card)

    return {
        "as_of": res["as_of"], "window": window, "regime": regime,
        "regime_label": regime_label(regime), "flip_regime_label": regime_label(flip),
        "terminal": terminal, "terminals": terminals,
        "availability": res["availability"], "n": len(cards),
        "archetypes_present": sorted(by_arche.keys()),
        "exemplars": list(by_arche.values()), "cards": cards,
    }


def _scorecard_why(c: dict, regime: dict) -> str:
    arche = c["archetype"]["primary"]
    mult = regime_multiplier(arche, regime)
    direction = "lifts" if mult > 1.02 else "lowers" if mult < 0.98 else "holds"
    return (f"{regime_label(regime)} {direction} a {arche} (×{mult:.2f}). "
            f"VAR {c['var']['score']}, base value {c['base_value']['score']}, "
            f"recency {c['recency_gap']}× cadence, trend {c['trend_pct']:+.0f}%.")


def _scorecard_action(c: dict, regime: dict) -> str:
    arche = c["archetype"]["primary"]
    posture = c["archetype"].get("posture", {})
    inv, mkt = regime["inventory"], regime["market"]
    # regime overrides on top of the standing posture
    if arche in ("Surplus Absorber", "Price Shopper", "Backup-Only") and inv in ("long", "tank_constrained"):
        return "Call now to place surplus at a clearing price."
    if arche in ("Premium Spot", "Scarcity Buyer") and (inv == "tight" or mkt == "rising"):
        return "Hold the premium — they buy on availability, not price."
    if arche == "Credit Drag" and regime["credit"] == "tight":
        return "Cap exposure / shift to prepay before allocating."
    if arche == "Contract Candidate":
        return "Offer a term deal to lock the volume."
    return posture.get("pricing", "Maintain standing posture.")


def _scorecard_impact(c: dict, regime: dict) -> str:
    bv = c["base_value"]
    return f"{_usd(bv['rfap'])}/yr RFAP · {_gal(bv['annual_gallons'])}/yr"


def _flip_line(c: dict, regime: dict, flip: dict, rs: float | None, fs: float | None) -> str:
    if rs is None or fs is None:
        return "Insufficient history to flip."
    arrow = "↑" if fs > rs else "↓" if fs < rs else "→"
    return (f"Under {regime_label(flip)}: score {arrow} {fs} (from {rs}). "
            f"{_scorecard_action(c, flip)}")
