"""/api/profile/* — the CONVERGENCE layer.

This module adds NO new analytics. It is a pure fan-out / assembly: it reads the existing engine
outputs (two-axis variability + channel + confidence + commitment, the Phase-2 margin layer, the
deal book, the working-day book status) and routes them into ONE view per real-world unit — a
customer, a terminal — plus the orienting home screen. It also writes the plain-English SYNTHESIS
the operator reads first (a prescriptive one-breath verdict templated from the real facet values)
and the DOLLAR joins that make the closed loop visible (winnable gallons × the existing margin
¢/gal). Every number already exists in another engine; this layer only joins, names, and phrases.

The opportunity facet is the REAL Phase-6 modeled missing-volume engine (peak ≈ wallet), pulled per
master from ``opportunity.compute_opportunity`` and labelled MODELED everywhere it surfaces. The
terminal view reads the REAL Phase-7 position / days-of-cover engine (``/api/position``) directly.
Both degrade honestly to "not enough data" — never a zero that reads as a real number — when an
engine returns empty.

If you find yourself adding a calculation here, it belongs in an engine, not in this view layer.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException

from .. import db, margin, pricegrid, schema, variability, weather_hdd

router = APIRouter(prefix="/api/profile")

# Default operator identity (overridable via the `company_name` meta key). The book is the
# Soundview wholesale-fuel book per the project brief.
_DEFAULT_COMPANY = "Soundview Energy"
_STALE_DAYS = 90  # a customer silent this long is excluded from "winnable" volume (stale exclusion)

_CACHE: dict = {}


def _con():
    return db.get_shared_connection()


def _sig(con) -> tuple:
    """Bust when lifts / deals / prices / the day change — same signals the engines cache on."""
    pricegrid.ensure_tables(con)
    counts = pricegrid.store_counts(con)
    return (db.row_count(con, schema.LIFTS), db.deals_count(con),
            counts["price_grid_rows"], counts["landed_cost_trips"],
            str(db.get_meta(con, "last_import_at")), str(db.get_meta(con, "last_deal_import_at")),
            str(db.get_meta(con, "last_price_import_at")), str(db.get_meta(con, "last_weather_import_at")),
            str(date.today()))


def _bundle(con) -> dict:
    """The joined book — computed once per data signature and shared across every profile route."""
    sig = _sig(con)
    if _CACHE.get("sig") != sig:
        _CACHE.clear()
        _CACHE["sig"] = sig
        _CACHE["data"] = _build_bundle(con)
    return _CACHE["data"]


# =================================================================================
# Assembly
# =================================================================================
def _last_lifts(con) -> dict[str, str]:
    """Per-master last lift date (drives the stale exclusion). Cheap SQL on the resolved book."""
    if db.row_count(con, schema.LIFTS) == 0:
        return {}
    rows = con.execute(
        "SELECT customer_id, max(lift_datetime) AS last FROM lifts "
        "WHERE lift_datetime IS NOT NULL GROUP BY 1").df()
    return {r.customer_id: (str(r.last)[:10] if r.last is not None else None)
            for r in rows.itertuples()}


def _margin_by_customer(con) -> tuple[dict, dict]:
    """(per-customer margin row, full margin payload) — best-effort; empty when margin is locked."""
    try:
        res = margin.compute_margin(con)
    except Exception:  # noqa: BLE001 — margin is an optional layer; never break the view
        return {}, {"available": False}
    if not res.get("available"):
        return {}, res
    by = {c["customer_id"]: c for c in res.get("customers", [])}
    return by, res


def _opportunity_facets(con) -> tuple[dict, bool]:
    """{master_id: the REAL Phase-6 modeled facet} + an availability flag.

    The facet is a superset of the old interim opportunity shape (winnable gal/$ + winnability +
    shrunk-vs-under-served + the spot/rack tag + a MODELED headline), so the view reads it directly.
    Best-effort: an empty ({}, False) when the engine is locked or the book is too thin — the tile
    then degrades honestly to "not enough data" rather than fabricating a zero.
    """
    try:
        from .. import opportunity
        res = opportunity.compute_opportunity(con)
    except Exception:  # noqa: BLE001 — opportunity is an optional layer; never break the view
        return {}, False
    if not res.get("available"):
        return {}, False
    return ({c["customer_id"]: c.get("facet") for c in res.get("customers", []) if c.get("facet")},
            True)


def _build_bundle(con) -> dict:
    var = variability.compute_variability(con)
    margin_by, margin_full = _margin_by_customer(con)
    opp_facets, opp_available = _opportunity_facets(con)
    last_lifts = _last_lifts(con)
    as_of = var.get("as_of")
    as_of_d = _parse_date(as_of)

    n_margin = len(margin_by)
    customers: list[dict] = []
    for c in (var.get("customers") or []):
        cid = c["customer_id"]
        m = margin_by.get(cid)
        last = last_lifts.get(cid)
        stale = _is_stale(last, as_of_d)
        customers.append(_assemble_customer(c, m, last, stale, n_margin, opp_facets.get(cid)))

    return {
        "available": bool(var.get("available")),
        "as_of": as_of,
        "margin_available": bool(margin_full.get("available")),
        "margin_full": margin_full,
        "opportunity_available": opp_available,
        "customers": customers,
        "by_id": {c["customer_id"]: c for c in customers},
        "mismatches": var.get("mismatches") or {},
        "channel_summary": var.get("channel_summary") or {},
    }


def _assemble_customer(c: dict, m: dict | None, last_lift: str | None, stale: bool,
                       n_margin: int = 0, opp_facet: dict | None = None) -> dict:
    """Join one customer's facets from every engine into a single record (the spine of the list)."""
    ch = c.get("channel") or {}
    conf = c.get("confidence") or {}
    com = c.get("commitment") or {}

    margin_facet = None
    margin_pctile = None
    if m:
        rank = m.get("rank_by_margin")
        if rank and n_margin:
            margin_pctile = round((n_margin - rank + 1) / n_margin, 3)  # 1.0 = fattest in the book
        margin_facet = {
            "available": True,
            "book_cents_gal": m.get("book_cents_gal"),
            "repl_cents_gal": m.get("repl_cents_gal"),
            "book_margin_dollars": m.get("book_margin_dollars"),
            "rank_by_margin": rank,
            "rank_by_volume": m.get("rank_by_volume"),
            "rank_delta": m.get("rank_delta"),
            "gallons": m.get("gallons"),
            "pctile": margin_pctile,
        }
    margin_cents = (margin_facet or {}).get("book_cents_gal") if margin_facet else ch.get("margin_cents_gal")

    opp = _opportunity(opp_facet)

    rec = {
        "customer_id": c["customer_id"],
        "name": c.get("name"),
        "n_lifts": c.get("n_lifts"),
        "span_days": c.get("span_days"),
        "total_net_gallons": c.get("total_net_gallons"),
        "primary_terminal": c.get("home_terminal"),
        "top_product": c.get("dominant_product"),
        "data_sufficient": c.get("data_sufficient"),
        "last_lift": last_lift,
        "stale": stale,
        # confidence (prominent on the identity header; annotates every rec, never suppresses one)
        "confidence_tier": conf.get("tier"),
        "confidence_provisional": conf.get("provisional"),
        "confidence_reason": conf.get("reason"),
        "confidence_flag": conf.get("flag"),
        # steadiness facet (+ the inputs behind it, for "why am I seeing this number")
        "quadrant": c.get("quadrant"),
        "quadrant_label": c.get("quadrant_label"),
        "planning_note": c.get("planning_note"),
        "cadence_consistency": c.get("cadence_consistency"),
        "size_consistency": c.get("size_consistency"),
        "size_consistency_raw": c.get("size_consistency_raw"),
        "cadence_inputs": c.get("cadence_inputs"),
        "size_inputs": c.get("size_inputs"),
        "behavior_label": c.get("behavior_label"),
        "weather_sensitive": c.get("weather_sensitive"),
        "size_weather_adjusted": c.get("size_weather_adjusted"),
        "weather_beta": c.get("weather_beta"),
        "weather_beta_source": c.get("weather_beta_source"),
        # channel facet
        "recommended_channel": ch.get("recommended_channel"),
        "channel_label": ch.get("channel_label"),
        "current_channel_label": ch.get("current_channel_label"),
        "current_channel_known": ch.get("current_channel_known"),
        "mismatch": ch.get("mismatch"),
        "mismatch_strength": ch.get("mismatch_strength"),
        "mismatch_direction": ch.get("mismatch_direction"),
        "mismatch_reason": ch.get("mismatch_reason"),
        "term_eligible": ch.get("term_eligible"),
        "margin_note": ch.get("margin_note"),
        # margin facet (BOOK = inventory-cost basis; replacement = latest barge)
        "margin": margin_facet,
        "margin_cents_gal": margin_cents,
        "margin_pctile": margin_pctile,
        # opportunity facet (REAL Phase-6 modeled missing-volume — see _opportunity)
        "winnable_gal_per_yr": opp.get("winnable_gal_per_yr") or 0,
        "opportunity": opp,
        # commitment context
        "commitment_label": com.get("label"),
        "commitment_available": bool(com.get("available")),
    }
    rec.update(_verdict(rec))  # the prescriptive read — reasons over every facet above
    return rec


_OPP_PREMISE = ("MODELED: a customer's weather-normalized peak with us ≈ their whole wallet "
                "(peak ≈ wallet) — an estimate of opportunity, not measured demand.")


def _opportunity(facet: dict | None) -> dict:
    """The REAL Phase-6 modeled missing-volume facet for one master, or an honest empty read.

    ``opportunity.compute_opportunity`` already shapes the facet as a drop-in superset of the old
    interim opportunity tile: winnable gal/$, the gap, a 0–100 winnability with the shrunk-vs-
    under-served split, the reused spot/rack tag, and a MODELED plain-English headline. We pass it
    straight through. When the engine returned nothing for this customer (locked layer or too thin a
    book) we return ``available: False`` with a plain reason so the tile reads "not enough data" —
    never a fabricated zero that looks like measured demand.
    """
    if facet:
        return facet
    return {"available": False, "modeled": True, "source": "modeled_peak_demand",
            "kind": "unknown", "winnable_gal_per_yr": 0, "winnable_dollars_per_yr": None,
            "gap_gal_per_yr": 0, "winnability": None, "winnability_flag": "insufficient",
            "chase_channel": None,
            "note": "Not enough history yet to model a demand gap for this account.",
            "premise": _OPP_PREMISE, "interim_note": _OPP_PREMISE, "caveat": _OPP_PREMISE}


# =================================================================================
# Prescriptive synthesis — a desk colleague's one-breath verdict, templated from the
# ACTUAL facet values (NOT new computation). Dark facets are omitted, never "unknown".
# =================================================================================
_QUAD_READ = {
    "metronome": "steady daily lifter you can plan around",
    "predictable_timing": "shows up on a dependable cadence but the load size swings",
    "predictable_size": "lifts a consistent load but on irregular timing",
    "unpredictable": "erratic — irregular timing and variable loads",
    "insufficient": "too new to read a pattern yet",
}
_ACTIONS = {
    "CALL": "CALL — chase the modeled upside",
    "DE_RISK": "DE-RISK — move off the firm commitment",
    "FIX_PRICING": "FIX PRICING — steady but underpriced",
    "WATCH": "WATCH — possible churn",
    "PROTECT": "PROTECT with a term deal",
    "LEAVE": "LEAVE as-is — on the right channel",
    "REVIEW": "REVIEW — not enough history to call it",
}


def _verdict(cust: dict) -> dict:
    """Return {action, action_label, headline, summary} — the spine sentence + a one-word action.

    Reasons over EVERY facet (steadiness · margin · channel · the REAL modeled opportunity), picks
    the single most useful next move, and phrases it like a desk colleague's one-breath take. Dark
    facets are omitted, never spelled out as "unknown"; the modeled upside is always labelled as a
    modeled estimate, and a low-winnability account reads "looks shrunk, not winnable" rather than
    dangling a tempting gallons number.
    """
    quad = cust.get("quadrant") or "insufficient"
    rec = cust.get("recommended_channel")
    opp = cust.get("opportunity") or {}
    tier = cust.get("confidence_tier")
    cents = cust.get("margin_cents_gal")
    pct = cust.get("margin_pctile")
    steady = quad in ("metronome", "predictable_size")

    win_gal = opp.get("winnable_gal_per_yr") or 0
    is_win = bool(opp.get("available") and opp.get("kind") == "win" and win_gal > 0)
    is_shrunk = bool(opp.get("available") and opp.get("kind") == "shrunk")
    channel_risk = bool(cust.get("mismatch") and cust.get("mismatch_direction") == "downgrade_to_spot")

    # ---- pick the action (priority order; first to fire wins the headline) ----
    if is_win:
        action = "CALL"
    elif channel_risk:
        action = "DE_RISK"
    elif cust.get("stale") or is_shrunk:
        action = "WATCH"
    elif steady and pct is not None and pct <= 0.34 and rec == "RACK":
        action = "FIX_PRICING"
    elif steady and rec == "RACK" and not cust.get("mismatch"):
        action = "PROTECT" if (pct is None or pct >= 0.5) else "LEAVE"
    elif quad == "insufficient":
        action = "REVIEW"
    else:
        action = "LEAVE"

    # ---- build the headline clauses from real values, omitting dark facets ----
    clauses = [_QUAD_READ.get(quad, _QUAD_READ["insufficient"])]
    if cust.get("weather_sensitive"):
        clauses[0] += " (weather-driven)"

    if cents is not None:
        if pct is not None and pct >= 0.75:
            clauses.append(f"top-quartile margin (~{_g(cents)}¢/gal)")
        elif pct is not None and pct <= 0.34:
            clauses.append(f"thin margin (~{_g(cents)}¢/gal)")
        else:
            clauses.append(f"fair margin (~{_g(cents)}¢/gal)")

    if cust.get("current_channel_known"):
        if cust.get("mismatch") and cust.get("mismatch_direction") == "upgrade_to_rack":
            clauses.append("on spot but behaves like rack")
        elif channel_risk:
            clauses.append("over-committed for how it buys")
        elif rec:
            clauses.append("on the right channel")
    elif rec:
        clauses.append("rack/term-suited" if rec == "RACK" else "spot-suited")

    # ---- the closing clause + the action verb, phrased per action ----
    win_d = opp.get("winnable_dollars_per_yr")
    if action == "CALL":
        money = f" (≈ ${_compact(win_d)}/yr)" if win_d else ""
        chase = opp.get("chase_channel")
        clauses.append(f"~{_compact(win_gal)} gal/yr modeled upside{money}")
        tail = f"CALL — chase it{f' via {chase}' if chase else ''}"
    elif action == "DE_RISK":
        tail = "DE-RISK — move off the firm commitment"
    elif action == "WATCH" and is_shrunk:
        clauses.append("buying less lately, big days are old")
        tail = "WATCH — looks shrunk, not winnable"
    elif action == "WATCH":
        tail = "WATCH — gone quiet, possible churn"
    elif action == "FIX_PRICING":
        tail = "FIX PRICING — steady but underpriced"
    elif action == "PROTECT":
        tail = "PROTECT with a term deal"
    elif action == "REVIEW":
        tail = "REVIEW — not enough history to call it"
    else:
        tail = "LEAVE as-is — on the right channel"

    name = cust.get("name") or cust.get("customer_id")
    headline = f"{name} — " + ", ".join(clauses) + f" — {tail}."
    if tier == "Low":
        headline += f" Provisional — only {_int(cust.get('n_lifts'))} lifts."
    return {"action": action, "action_label": _ACTIONS.get(action, tail), "headline": headline,
            "summary": headline}


# =================================================================================
# Data-source connectivity (the "Your data — N of M connected" panel) — with what each
# dark source COSTS, so nobody acts on an estimate thinking it is contract truth.
# =================================================================================
def _sources(con) -> dict:
    pricegrid.ensure_tables(con)
    counts = pricegrid.store_counts(con)
    n_lifts = db.row_count(con, schema.LIFTS)
    n_deals = db.deals_count(con)
    rng = con.execute(
        "SELECT min(lift_datetime), max(lift_datetime) FROM lifts").fetchone() if n_lifts else (None, None)
    bol_through = str(rng[1])[:10] if rng and rng[1] is not None else None

    try:
        unmapped = len(db.unmapped_customers(con))
        n_customers = db.row_count(con, schema.CUSTOMERS)
        mapped_rate = (None if not n_customers else round(100 * (n_customers - unmapped) / n_customers, 0))
    except Exception:  # noqa: BLE001
        mapped_rate = None

    bridge_rate = None
    try:
        from .. import dealbook
        if n_deals:
            br = dealbook.bridge_candidates(con)
            # the bridge already reports a percent (0–100), not a fraction
            bridge_rate = round(min(100.0, float(br.get("match_rate_by_committed_volume") or 0)), 0)
    except Exception:  # noqa: BLE001
        bridge_rate = None

    n_hdd = int(weather_hdd.store_counts(con).get("hdd_observations", 0))

    sources = [
        {"key": "bols", "label": "Lift book (BOLs)", "connected": n_lifts > 0, "count": n_lifts,
         "unit": "lifts", "through": bol_through, "match_rate": mapped_rate, "match_label": "named",
         "unlocks": "the whole book — customers, steadiness and demand",
         "cost_when_dark": "Without lifts there is no book at all.",
         "upload_route": "studio", "primary": True},
        {"key": "deals", "label": "Deal book (term / forward / spot)", "connected": n_deals > 0,
         "count": n_deals, "unit": "deal rows", "match_rate": bridge_rate,
         "match_label": "of committed volume bridges",
         "unlocks": "channel fit (spot vs rack/term), mismatches and commitment context",
         "cost_when_dark": "Channel calls and the winnable worklist are off, and margin is estimated "
                           "from lift prices — not your contract terms.",
         "upload_action": "deals"},
        {"key": "prices", "label": "Price grid (sell side)", "connected": counts["price_grid_rows"] > 0,
         "count": counts["price_grid_rows"], "unit": "price rows",
         "unlocks": "margin in ¢/gal and the value ranking",
         "cost_when_dark": "Margin ¢/gal is estimated from lift invoice prices, not your sell grid.",
         "upload_action": "prices"},
        {"key": "trips", "label": "Barge trips (landed cost)", "connected": counts["landed_cost_trips"] > 0,
         "count": counts["landed_cost_trips"], "unit": "trip legs",
         "unlocks": "landed cost, true margin, and the barge-nomination cost (the cure)",
         "cost_when_dark": "Cost is the lift cost, not the barge running cost — and there is no "
                           "barge-nomination cure figure on the terminal view.",
         "upload_action": "trips"},
        {"key": "weather", "label": "Weather (HDD)", "connected": n_hdd > 0, "count": n_hdd,
         "unit": "HDD days",
         "unlocks": "weather-adjusted steadiness for heating-fuel customers",
         "cost_when_dark": "Heating-fuel steadiness isn't cold-snap-adjusted, so winter swings can "
                           "read as inconsistency.",
         "upload_action": "weather"},
    ]
    n_connected = sum(1 for s in sources if s["connected"])
    return {"sources": sources, "n_connected": n_connected, "n_total": len(sources)}


def _freshness(con) -> dict:
    """The book is only as fresh as the last upload (until a live sync exists) — surface it."""
    keys = ["last_import_at", "last_deal_import_at", "last_price_import_at", "last_weather_import_at"]
    stamps = [s for s in (db.get_meta(con, k) for k in keys) if s]
    latest = max(stamps) if stamps else None
    return {"last_upload_at": latest,
            "note": "Everything is as fresh as your last upload — there is no live sync yet."}


# =================================================================================
# Routes
# =================================================================================
@router.get("/home")
def home():
    with db.lock():
        con = _con()
        b = _bundle(con)
        srcs = _sources(con)
        company = db.get_meta(con, "company_name") or _DEFAULT_COMPANY

        customers = b["customers"]
        mismatches = b["mismatches"]
        deals_on = bool(srcs["sources"][1]["connected"])
        opp_on = bool(b["opportunity_available"])
        n_active = sum(1 for c in customers if c.get("data_sufficient"))
        n_mismatch = int(mismatches.get("n_mismatches") or 0)
        win = [c for c in customers if c["opportunity"].get("kind") == "win"]
        winnable = round(sum((c["opportunity"].get("winnable_gal_per_yr") or 0) for c in win), 0)
        winnable_d = round(sum((c["opportunity"].get("winnable_dollars_per_yr") or 0) for c in win), 0)

        tiles = [
            {"key": "customers", "label": "Customers you can read", "value": n_active,
             "unit": "accounts", "sub": "with enough history to plan around",
             "route": "customers", "tone": "neutral"},
            {"key": "mismatches", "label": "Channel mismatches", "value": n_mismatch,
             "unit": "accounts", "sub": "on the wrong channel for how they buy",
             "route": "opportunity", "tone": "amber" if n_mismatch else "neutral",
             "available": deals_on,
             "unavailable_note": "Connect the deal book to compare channels"},
            {"key": "winnable", "label": "Winnable volume", "value": winnable,
             "unit": "gal/yr", "format": "gal",
             "sub": (f"≈ ${_compact(winnable_d)}/yr at current margin · MODELED" if winnable_d else
                     "modeled missing volume you could win back"),
             "route": "opportunity", "tone": "emerald" if winnable else "neutral",
             "available": opp_on, "modeled": True,
             "unavailable_note": "Needs more lift history to model demand"},
        ]
        return {
            "company": company, "data_through": b["as_of"], "available": b["available"],
            "tiles": tiles, "margin_available": b["margin_available"],
            "opportunity_available": opp_on,
            "sources": srcs["sources"], "n_connected": srcs["n_connected"], "n_total": srcs["n_total"],
            "freshness": _freshness(con), "doorways": _doorways(),
        }


def _doorways() -> list[dict]:
    return [
        {"key": "plan", "question": "Who can I plan around?",
         "answer": "Your customers, ranked by how readable they are.", "route": "customers"},
        {"key": "tight", "question": "Where am I tight?",
         "answer": "Each terminal's demand, cover and risk.", "route": "terminals"},
        {"key": "sell", "question": "Who should I sell more to?",
         "answer": "Steady accounts on spot — volume to win back.", "route": "opportunity"},
        {"key": "mean", "question": "What does this mean?",
         "answer": "Every term defined in plain English.", "route": "glossary"},
    ]


_LIST_FIELDS = (
    "customer_id", "name", "n_lifts", "span_days", "total_net_gallons", "primary_terminal",
    "top_product", "data_sufficient", "stale", "last_lift", "confidence_tier",
    "confidence_provisional", "confidence_flag", "quadrant", "quadrant_label", "planning_note",
    "cadence_consistency", "size_consistency", "behavior_label", "weather_sensitive",
    "recommended_channel", "channel_label", "current_channel_label", "current_channel_known",
    "mismatch", "mismatch_strength", "mismatch_direction", "margin_note", "margin_cents_gal",
    "margin_pctile", "winnable_gal_per_yr", "commitment_label", "action", "action_label", "headline",
)


@router.get("/customers")
def customers():
    with db.lock():
        b = _bundle(_con())
        out = []
        for c in b["customers"]:
            row = {k: c.get(k) for k in _LIST_FIELDS}
            mf = c.get("margin")
            opp = c["opportunity"]
            row["margin_dollars"] = (mf or {}).get("book_margin_dollars")
            row["rank_by_margin"] = (mf or {}).get("rank_by_margin")
            row["opportunity_kind"] = opp.get("kind")
            row["opportunity_available"] = bool(opp.get("available"))
            row["winnable_dollars_per_yr"] = opp.get("winnable_dollars_per_yr")
            row["winnability"] = opp.get("winnability")
            row["winnability_flag"] = opp.get("winnability_flag")
            row["chase_channel"] = opp.get("chase_channel")
            row["gap_gal_per_yr"] = opp.get("gap_gal_per_yr")
            row["opportunity_note"] = opp.get("note")
            out.append(row)
        return {
            "available": b["available"], "as_of": b["as_of"], "n_customers": len(out),
            "margin_available": b["margin_available"],
            "opportunity_available": b["opportunity_available"],
            "deals_available": any(c.get("current_channel_known") for c in b["customers"]),
            "customers": out,
        }


@router.get("/customer/{customer_id}")
def customer(customer_id: str):
    with db.lock():
        con = _con()
        b = _bundle(con)
        c = b["by_id"].get(customer_id)
        if c is None:
            raise HTTPException(status_code=404, detail=f"customer '{customer_id}' not found")
        c = dict(c)
        c["product_mix"] = _product_mix(con, customer_id)
        return {
            "available": b["available"], "as_of": b["as_of"],
            "margin_available": b["margin_available"], "customer": c,
        }


def _product_mix(con, customer_id: str) -> list[dict]:
    """The customer's volume split by product (for the per-product drill-down under margin). Volume
    only — per-product margin is not computed by the engine, so we never imply it."""
    try:
        rows = con.execute(
            "SELECT product, sum(net_gallons) AS gal, count(*) AS lifts FROM lifts "
            "WHERE customer_id = ? AND product IS NOT NULL GROUP BY 1 ORDER BY 2 DESC", [customer_id]).df()
        total = float(rows["gal"].sum()) or 1.0
        return [{"product": r.product, "gallons": round(float(r.gal), 0),
                 "lifts": int(r.lifts), "share": round(float(r.gal) / total, 3)}
                for r in rows.itertuples()]
    except Exception:  # noqa: BLE001
        return []


@router.get("/terminals")
def terminals():
    """Per-terminal rollups assembled from the deal book + the joined customer book (cheap).

    The terminal DETAIL page composes the existing /hedging, /demand and /margin/gap endpoints for
    the live demand band, days-of-cover and barge-nomination cure; this list is the orientation."""
    with db.lock():
        con = _con()
        b = _bundle(con)
        n_lifts = db.row_count(con, schema.LIFTS)
        if n_lifts == 0:
            return {"available": False, "terminals": []}

        committed_by, spot_by = {}, {}
        if db.deals_count(con):
            d = con.execute("""
                SELECT terminal, source, sum(committed_gallons) AS cg, sum(realized_gallons) AS rg
                FROM deals WHERE terminal IS NOT NULL GROUP BY 1, 2""").df()
            for r in d.itertuples():
                t = r.terminal
                if r.source in ("term", "forward_fixed"):
                    committed_by[t] = committed_by.get(t, 0) + float(r.cg or 0)
                elif r.source == "spot":
                    spot_by[t] = spot_by.get(t, 0) + float(r.rg or 0)

        vol = con.execute("""
            SELECT terminal, sum(net_gallons) AS gal, count(*) AS lifts,
                   count(DISTINCT customer_id) AS customers
            FROM lifts WHERE terminal IS NOT NULL GROUP BY 1 ORDER BY 2 DESC""").df()

        # winnable / at-risk per terminal, carried over from the joined book (the action overlay).
        # winnable = the REAL modeled missing-volume; at-risk = the over-committed-erratic CHANNEL
        # signal (annualized lift volume on a firm commitment that buys like spot).
        win_by, risk_by = {}, {}
        for c in b["customers"]:
            t = c.get("primary_terminal")
            if not t:
                continue
            opp = c["opportunity"]
            if opp.get("kind") == "win":
                win_by[t] = win_by.get(t, 0) + (opp.get("winnable_gal_per_yr") or 0)
            if c.get("mismatch") and c.get("mismatch_direction") == "downgrade_to_spot":
                per_yr = (float(c.get("total_net_gallons") or 0)
                          / max(int(c.get("span_days") or 0), 1)) * 365.0
                risk_by[t] = risk_by.get(t, 0) + per_yr

        inv_connected = _inventory_connected(con)
        terms = []
        for r in vol.itertuples():
            t = r.terminal
            terms.append({
                "terminal": t, "total_net_gallons": round(float(r.gal or 0), 0),
                "lifts": int(r.lifts), "customers": int(r.customers),
                "committed_gallons": round(committed_by.get(t, 0), 0),
                "spot_gallons": round(spot_by.get(t, 0), 0),
                "winnable_gal_per_yr": round(win_by.get(t, 0), 0),
                "at_risk_gal_per_yr": round(risk_by.get(t, 0), 0),
                "has_deals": (t in committed_by) or (t in spot_by),
            })
        return {
            "available": True, "as_of": b["as_of"], "inventory_connected": inv_connected,
            "deals_available": bool(committed_by or spot_by),
            "margin_available": b["margin_available"], "terminals": terms,
        }


# =================================================================================
# helpers
# =================================================================================
def _inventory_connected(con) -> bool:
    try:
        from .. import capabilities
        caps = capabilities.compute_capabilities(con)
        feat = next((f for f in caps.get("features", [])
                     if f.get("key") == "inventory_days_of_supply"), None)
        return bool(feat and feat.get("enabled"))
    except Exception:  # noqa: BLE001
        return False


def _parse_date(s: str | None):
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except Exception:  # noqa: BLE001
        return None


def _is_stale(last_lift: str | None, as_of: date | None) -> bool:
    d = _parse_date(last_lift)
    if d is None or as_of is None:
        return False
    return (as_of - d).days > _STALE_DAYS


def _int(x) -> str:
    try:
        return f"{int(x):,}"
    except Exception:  # noqa: BLE001
        return str(x)


def _g(x) -> str:
    """A number with no false precision: 0.985 -> '0.99', 8.4 -> '8.4', 12.0 -> '12'."""
    if x is None:
        return "—"
    if abs(x) < 1:
        return f"{x:.2f}".rstrip("0").rstrip(".")
    return f"{x:.1f}".rstrip("0").rstrip(".")


def _compact(x) -> str:
    """Compact magnitude for the headline: 1_100_000 -> '1.1M', 6300 -> '6.3k', 320 -> '320'."""
    if x is None:
        return "—"
    a = abs(x)
    if a >= 1e6:
        return f"{x / 1e6:.1f}M"
    if a >= 1e3:
        return f"{x / 1e3:.0f}k" if a >= 1e4 else f"{x / 1e3:.1f}k"
    return f"{round(x)}"
