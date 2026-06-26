"""Two-axis customer variability — the Phase-1 score.

A customer's predictability is **two independent things**, and conflating them into one grade was the
old failure (it bunched the whole book as "kinda variable" and told the desk nothing). This module
keeps them SEPARATE as the headline output:

  • AXIS 1 — CADENCE consistency: how predictably they *show up*, over WORKING days (zeros are data;
    reuse the calendar). Driven by the regularity of the working-day gap between lifts (every working
    day, or every Tuesday → high; erratic → low) plus how often they're present. A daily-like or
    strictly-periodic lifter scores high here.
  • AXIS 2 — SIZE consistency: when they DO lift, how alike each lift is — over ACTIVE lifts only
    (per-BOL net gallons, never diluted by silent days). Same ~N gallons every time → high; 500-to-
    8000 swings → low. This axis is reported RAW and on its own: a steady-cadence / variable-size
    customer reads "steady cadence, variable loads" — we never smooth real size swings away.

The two axis scores are the headline and are always shown independently. A frequency × size **2×2**
names the planning quadrant (metronome / shows-daily-size-unpredictable / infrequent-but-identical /
sporadic-bursty). A combined "overall" roll-up exists only as a secondary convenience.

The deal book ANNOTATES (commitment context) but never changes the axis scores. Weather-adjustment of
AXIS 2 for heating fuels is a built SEAM: raw size CV now, residual-after-β·HDD when the weather layer
lands. Nothing here is tuned to force separation — every reference scale is a stated, principled
constant and the validation reports the honest distribution.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from . import behavioral, calendar_days, db, schema, weather_model
from .dealbook import HEATING_FAMILIES
from .scoring_config import ScoringConfig


@dataclass(frozen=True)
class VariabilityConfig:
    # AXIS 1 — cadence consistency. gap_cv = 1.0 is the random/Poisson reference (zero regularity);
    # 0 is perfectly periodic. Presence is a gentler, secondary term (≥3 active working-days/week =
    # full presence credit). Regularity dominates so a strictly-periodic buyer scores high at ANY
    # frequency, and a frequent-but-erratic buyer cannot buy steadiness with frequency alone.
    cadence_w_regularity: float = 0.72
    cadence_w_presence: float = 0.28
    cadence_gap_cv_ref: float = 1.0
    presence_full_days_per_week: float = 3.0

    # AXIS 2 — size consistency. size_cv = 1.0 (std == mean) is the high-variance reference → 0.
    size_cv_ref: float = 1.0

    # ---- 2×2 quadrant cutoffs (Stage 2 FIX 2 — tunable, principled, NOT tuned to a book) ----
    # The TIMING axis is cadence REGULARITY (gap-CV based), not frequency: a perfectly regular lifter
    # earns ≥72 from the regularity term alone (cadence = 100·(0.72·regularity + 0.28·presence)), so a
    # cutoff of 60 calls any regular weekly/biweekly/monthly lifter "regular" and an irregular one not.
    cadence_regular_cutoff: float = 60.0
    # "Consistent size" ⇔ active-day size CV ≤ 0.35 (score = 100·(1 − CV) ⇒ a 65 cutoff). Loads within
    # ±35% of their typical size read consistent; a 2×+ swing reads variable.
    size_consistent_cutoff: float = 65.0

    # ---- confidence tier (Stage 2 FIX 3 — annotates trust, NEVER changes the quadrant) ----
    # From lift count + history span. Absolute (a thin account is low-confidence in any book): a
    # ~5,800-lift account is High, an ~88-lift account is Low. Low-confidence accounts STILL get a rec,
    # explicitly flagged "provisional — based on only N lifts"; they are never suppressed.
    conf_high_min_lifts: int = 200
    conf_high_min_span_days: int = 365
    conf_med_min_lifts: int = 100
    conf_med_min_span_days: int = 180

    # data sufficiency guard
    min_lifts: int = 6
    min_active_days: int = 3

    # grade bands (semantic, fixed — NOT data-derived). Applied to each axis independently.
    grade_a: float = 78.0
    grade_b: float = 58.0
    grade_c: float = 38.0

    # secondary roll-up weights (cadence-led; SECONDARY only)
    overall_w_cadence: float = 0.6
    overall_w_size: float = 0.4


DEFAULT_VAR_CONFIG = VariabilityConfig()


def grade(score: float | None, cfg: VariabilityConfig) -> str | None:
    if score is None:
        return None
    if score >= cfg.grade_a:
        return "A"
    if score >= cfg.grade_b:
        return "B"
    if score >= cfg.grade_c:
        return "C"
    return "D"


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# ---- per-lift size stats (AXIS 2 raw inputs) ------------------------------------
def _size_stats(net: np.ndarray) -> dict:
    net = net[np.isfinite(net)]
    if len(net) == 0:
        return {"n": 0, "mean": None, "median": None, "cv": None, "p10": None, "p50": None,
                "p90": None, "min": None, "max": None}
    mean = float(np.mean(net))
    sd = float(np.std(net, ddof=1)) if len(net) > 1 else 0.0
    cv = (sd / mean) if mean else None
    return {
        "n": int(len(net)), "mean": round(mean, 1), "median": round(float(np.median(net)), 1),
        "cv": round(cv, 3) if cv is not None else None,
        "p10": round(float(np.percentile(net, 10)), 1), "p50": round(float(np.percentile(net, 50)), 1),
        "p90": round(float(np.percentile(net, 90)), 1),
        "min": round(float(net.min()), 1), "max": round(float(net.max()), 1),
    }


def _weather_residual_sizes(cl: pd.DataFrame, family: str | None, terminal: str | None,
                            model: dict | None) -> tuple[np.ndarray, bool, dict]:
    """Weather-normalize AXIS 2 on heating fuels (Stage 1).

    Returns (per-lift sizes used for the size axis, weather_adjusted?, diagnostics). For a heating
    family (ULSHO/HO4) with a stable positive HDD→size β, the per-lift size becomes the residual
    after removing β·(HDD − HDD̄) — re-centred to keep the level — so a customer who only swings on
    cold snaps reads steady-underneath. The adjustment is kept ONLY when it lowers the size CV (it can
    never manufacture steadiness), and non-heating families are never touched. ``model`` is the
    precomputed :func:`weather_model.build_model` result (None ⇒ raw, as before).
    """
    if model is None:
        sizes = pd.to_numeric(cl["net_gallons"], errors="coerce").to_numpy(dtype=float)
        sizes = sizes[np.isfinite(sizes)]
        return sizes, False, {"weather_sensitive": family in HEATING_FAMILIES if family else False,
                              "reason": "weather model not built"}
    return weather_model.adjusted_sizes(cl, family, terminal, model)


# ---- the two-axis score per customer --------------------------------------------
def _size_score(size_cv: float | None, cfg: VariabilityConfig) -> float | None:
    if size_cv is None:
        return None
    return round(100.0 * _clamp(1.0 - size_cv / cfg.size_cv_ref), 1)


def _axes_for_customer(cl: pd.DataFrame, prof_all: dict, family: str | None,
                       terminal: str | None, model: dict | None,
                       cfg: VariabilityConfig) -> dict:
    """Compute AXIS 1 (cadence) and AXIS 2 (size) from one customer's full lift history."""
    presence = prof_all.get("presence", {}) if prof_all else {}
    gap_cv = presence.get("gap_cv")
    active_dpw = presence.get("active_days_per_week")
    active_rate = presence.get("active_day_rate")
    freq_class = prof_all.get("frequency_class") if prof_all else None

    # ---- AXIS 1: cadence consistency (regularity-dominated; the quadrant reads this SCORE) ----
    if gap_cv is None:
        cadence = None                                  # too few active days to read a cadence
    else:
        regularity = _clamp(1.0 - gap_cv / cfg.cadence_gap_cv_ref)
        presence_factor = _clamp((active_dpw or 0.0) / cfg.presence_full_days_per_week)
        cadence = round(100.0 * (cfg.cadence_w_regularity * regularity
                                 + cfg.cadence_w_presence * presence_factor), 1)

    # ---- AXIS 2: size consistency (per-lift, ACTIVE days only) ----
    # RAW size on the real per-lift gallons; then the Stage-1 weather residual REPLACES it for heating
    # fuels (so cold-snap sizing isn't misread as inconsistency). Both are reported.
    raw_sizes = pd.to_numeric(cl["net_gallons"], errors="coerce").to_numpy(float)
    raw_sizes = raw_sizes[np.isfinite(raw_sizes)]
    raw_stats = _size_stats(raw_sizes)
    size_raw = _size_score(raw_stats["cv"], cfg)

    sizes, weather_adjusted, wdiag = _weather_residual_sizes(cl, family, terminal, model)
    sstats = _size_stats(sizes) if weather_adjusted else raw_stats
    size_score = _size_score(sstats["cv"], cfg) if weather_adjusted else size_raw

    # ---- 2×2 quadrant — built on the two SCORES with tunable cutoffs (Stage 2 FIX 2) ----
    regular_timing = (cadence is not None and cadence >= cfg.cadence_regular_cutoff)
    consistent_size = (size_score is not None and size_score >= cfg.size_consistent_cutoff)
    quadrant, quadrant_label, plan = _quadrant(regular_timing, consistent_size)

    overall = None
    if cadence is not None and size_score is not None:
        overall = round(cfg.overall_w_cadence * cadence + cfg.overall_w_size * size_score, 1)

    return {
        "cadence_consistency": cadence,
        "cadence_grade": grade(cadence, cfg),
        "size_consistency": size_score,
        "size_consistency_raw": size_raw,               # before weather adjustment (heating fuels)
        "size_grade": grade(size_score, cfg),
        "overall_stability": overall,                   # SECONDARY roll-up only
        "overall_grade": grade(overall, cfg),
        "quadrant": quadrant,
        "quadrant_label": quadrant_label,
        "planning_note": plan,
        "regular_timing": regular_timing,
        "consistent_size": consistent_size,
        "cadence_inputs": {"gap_cv": gap_cv, "active_days_per_week": active_dpw,
                           "active_day_rate": active_rate, "frequency_class": freq_class,
                           "median_gap_working_days": presence.get("median_gap_days")},
        "size_inputs": sstats,
        "size_inputs_raw": raw_stats,
        "size_weather_adjusted": weather_adjusted,
        "weather_beta": wdiag.get("beta"),
        "weather_beta_source": wdiag.get("beta_source"),
        "weather_note": wdiag.get("reason"),
        "weather_sensitive": (family in HEATING_FAMILIES) if family else False,
        "intermittent": bool(prof_all.get("intermittent")) if prof_all else False,
        "misleading_average": bool(prof_all.get("misleading_average")) if prof_all else False,
    }


# The 2×2 keyed on (regular_timing?, consistent_size?). The TIMING axis is cadence REGULARITY (gap-CV
# based), NOT frequency — a regular weekly/biweekly lifter is regular at ANY frequency. Each quadrant
# carries the recommended CHANNEL (set by variability + confidence ONLY; margin never moves it).
_QUADRANTS = {
    (True, True): {
        "key": "metronome", "label": "Metronome",
        "plan": "Plannable on BOTH timing and size — the rack/term anchor.",
        "primary_channel": "RACK", "term_eligible": True,
        "channel_note": "Rack-priced; a strong TERM/contract candidate (commit the volume)."},
    (True, False): {
        "key": "predictable_timing", "label": "Predictable timing, variable size",
        "plan": "You can plan WHEN they lift, not how much — serve on rack, don't commit firm volume.",
        "primary_channel": "RACK", "term_eligible": False,
        "channel_note": "Capped rack — size swings, so spot the overage rather than term-committing it."},
    (False, True): {
        "key": "predictable_size", "label": "Predictable size, irregular timing",
        "plan": "You know the quantity, not the timing — rack-eligible, watch the calendar.",
        "primary_channel": "RACK", "term_eligible": True,
        "channel_note": "Rack-eligible (term on the known quantity); flag the irregular timing."},
    (False, False): {
        "key": "unpredictable", "label": "Unpredictable",
        "plan": "Hard to plan either way — price it on spot.",
        "primary_channel": "SPOT", "term_eligible": False,
        "channel_note": "Spot — neither timing nor size is plannable."},
}


def _quadrant(regular_timing: bool, consistent_size: bool):
    q = _QUADRANTS[(regular_timing, consistent_size)]
    return q["key"], q["label"], q["plan"]


def _quadrant_meta(quadrant_key: str) -> dict:
    for q in _QUADRANTS.values():
        if q["key"] == quadrant_key:
            return q
    return {"key": quadrant_key, "primary_channel": None, "term_eligible": False,
            "channel_note": "Not enough history to recommend a channel.", "label": quadrant_key}


# ---- commitment annotation (from the deal book; never changes the axes) ----------
def commitment_annotations(con) -> dict[str, dict]:
    """Per master customer: the commitment CONTEXT that annotates (not grades) the axes.

    Reconciled over the deal-months ∩ BOL-months window: committed (term+forward) vs actual lifted,
    plus spot presence and a requirements flag. Attaches ONLY to masters that resolve through the
    crosswalk bridge; everything else gets 'no commitment data' rather than a wrong annotation.
    """
    if db.deals_count(con) == 0 or db.row_count(con, schema.LIFTS) == 0:
        return {}
    rng = con.execute("SELECT date_trunc('month', min(lift_datetime)), "
                      "date_trunc('month', max(lift_datetime)) FROM lifts").fetchone()
    bol_min, bol_max = rng[0], rng[1]
    actual = con.execute(
        "SELECT customer_id, sum(net_gallons) gal FROM lifts GROUP BY 1").df()
    actual_by = {r.customer_id: float(r.gal or 0) for r in actual.itertuples()}

    deals = con.execute("""
        SELECT customer_master, customer_raw, source, commitment_type, month,
               committed_gallons, realized_gallons
        FROM deals WHERE customer_master IS NOT NULL""").df()
    out: dict[str, dict] = {}
    if deals.empty:
        return out
    for master, g in deals.groupby("customer_master"):
        in_win = g[(g["month"] >= pd.Timestamp(bol_min)) & (g["month"] <= pd.Timestamp(bol_max))]
        # split committed-in-window by source: forward-fixed has REAL deal dates (reliable); term is
        # a month-only schedule with an INFERRED year, so a term-driven committed≫actual anomaly is a
        # period-anchor artifact, not a real over-commitment — keep them separable for diagnosis.
        cw_forward = float(in_win[in_win["source"] == "forward_fixed"]["committed_gallons"].sum())
        cw_term = float(in_win[in_win["source"] == "term"]["committed_gallons"].sum())
        committed_window = cw_forward + cw_term
        committed_total = float(g[g["source"].isin(["term", "forward_fixed"])]["committed_gallons"].sum())
        spot_total = float(g[g["source"] == "spot"]["realized_gallons"].sum())
        has_term = bool((g["source"] == "term").any())
        has_forward = bool((g["source"] == "forward_fixed").any())
        has_spot = bool((g["source"] == "spot").any())
        requirements = bool((g["commitment_type"] == "requirements").any())
        actual = actual_by.get(master, 0.0)
        share = (committed_window / actual) if actual > 0 else None
        out[master] = {
            "available": True,
            "committed_window_gal": round(committed_window, 0),
            "committed_window_forward_gal": round(cw_forward, 0),    # real deal dates (reliable)
            "committed_window_term_gal": round(cw_term, 0),          # inferred-year (diagnose anomalies)
            "committed_total_gal": round(committed_total, 0),
            "spot_gal": round(spot_total, 0),
            "actual_window_gal": round(actual, 0),
            "term_backed_share": round(_clamp(share, 0, 1.5), 3) if share is not None else None,
            "has_term": has_term, "has_forward": has_forward, "has_spot": has_spot,
            "requirements": requirements,
            "label": _commit_label(share, has_term, has_forward, has_spot, requirements,
                                   committed_window, spot_total),
        }
    return out


def _commit_label(share, has_term, has_forward, has_spot, requirements, committed_window, spot) -> str:
    parts = []
    if requirements:
        parts.append("requirements contract")
    if share is not None and committed_window > 0:
        parts.append(f"{share:.0%} term/forward-backed")
    elif has_term or has_forward:
        parts.append("contracted (volume outside the lift window)")
    if has_spot and committed_window <= 0 and not (has_term or has_forward):
        parts.append("spot-only — opportunistic")
    elif has_spot:
        parts.append("also active in spot")
    return "; ".join(parts) if parts else "no commitment data"


# ---- confidence tier (FIX 3 — annotates trust, never changes the quadrant) -------
def confidence_tier(n_lifts: int, span_days: int, cfg: VariabilityConfig) -> dict:
    """High / Medium / Low from lift count + history span. Absolute boundaries (a thin account is
    low-confidence in ANY book). Low-confidence accounts STILL get a rec, flagged provisional."""
    if n_lifts >= cfg.conf_high_min_lifts and span_days >= cfg.conf_high_min_span_days:
        tier = "High"
    elif n_lifts >= cfg.conf_med_min_lifts and span_days >= cfg.conf_med_min_span_days:
        tier = "Medium"
    else:
        tier = "Low"
    months = round(span_days / 30.0, 1)
    return {"tier": tier, "n_lifts": n_lifts, "span_days": span_days,
            "provisional": tier == "Low",
            "reason": f"based on {n_lifts:,} lifts over ~{months} months",
            "flag": (f"provisional — based on only {n_lifts:,} lifts" if tier == "Low" else None)}


# ---- current channel from the deal book (the actuals) ----------------------------
def _current_channel(com: dict) -> dict:
    """What channel the customer is on TODAY, from the deal book (term/forward = contract, spot)."""
    if not com.get("available"):
        return {"channel": "unknown", "label": "no deal-book data", "known": False}
    has_commit = bool(com.get("has_term") or com.get("has_forward"))
    has_spot = bool(com.get("has_spot"))
    committed = float(com.get("committed_total_gal") or 0)
    spot = float(com.get("spot_gal") or 0)
    if has_commit and has_spot:
        lean = "contract-led" if committed >= spot else "spot-led"
        return {"channel": "mixed", "label": f"mixed ({lean})", "known": True}
    if has_commit:
        return {"channel": "contract", "label": "term/forward contract", "known": True}
    if has_spot:
        return {"channel": "spot", "label": "spot-only", "known": True}
    return {"channel": "unknown", "label": "no commitment data", "known": False}


# ---- the channel recommendation (set by variability + confidence ONLY) -----------
def channel_recommendation(quadrant_key: str, conf: dict, com: dict,
                           margin_info: dict | None) -> dict:
    """Recommend rack/term vs spot from the QUADRANT (+ confidence flag). Margin is attached as a
    ranking-only NOTE and can NEVER move the channel (FIX 4)."""
    meta = _quadrant_meta(quadrant_key)
    primary = meta.get("primary_channel")               # 'RACK' | 'SPOT' | None
    cur = _current_channel(com)

    # mismatch: recommended vs actual. Strong only for the clear cases; predictable_timing is soft.
    mismatch, strength, direction, reason = False, "none", None, None
    if primary and cur["known"]:
        if primary == "RACK" and cur["channel"] == "spot":
            mismatch = True
            strength = "strong" if quadrant_key in ("metronome", "predictable_size") else "soft"
            direction = "upgrade_to_rack"
            reason = ("Steady customer stuck on spot — upside in moving to rack/term."
                      if strength == "strong" else
                      "Regular timing but swingy size — rack-eligible (capped); review vs spot.")
        elif primary == "SPOT" and cur["channel"] in ("contract", "mixed"):
            mismatch = True
            strength = "strong" if cur["channel"] == "contract" else "soft"
            direction = "downgrade_to_spot"
            reason = "Irregular, variable account is term-committed — volume risk; spot is safer."
        elif primary == "RACK" and cur["channel"] == "mixed":
            strength, reason = "soft", "Rack-suited and partly contracted — room to deepen the term book."

    return {
        "recommended_channel": primary,
        "channel_label": _channel_label(meta),
        "term_eligible": meta.get("term_eligible", False),
        "channel_note": meta.get("channel_note"),
        "quadrant": quadrant_key, "quadrant_label": meta.get("label"),
        "confidence": conf["tier"], "provisional": conf["provisional"],
        "confidence_reason": conf["reason"], "confidence_flag": conf.get("flag"),
        "rationale": _rationale(meta, conf),
        "current_channel": cur["channel"], "current_channel_label": cur["label"],
        "current_channel_known": cur["known"],
        "mismatch": mismatch, "mismatch_strength": strength,
        "mismatch_direction": direction, "mismatch_reason": reason,
        "margin_rank": (margin_info or {}).get("rank_by_margin"),
        "margin_cents_gal": (margin_info or {}).get("book_cents_gal"),
        "margin_note": _margin_note(primary, margin_info),
    }


def _channel_label(meta: dict) -> str:
    primary = meta.get("primary_channel")
    if primary is None:
        return "—"
    if primary == "SPOT":
        return "Spot"
    if meta.get("term_eligible"):
        return "Rack / Term"
    return "Rack (capped)"


def _rationale(meta: dict, conf: dict) -> str:
    base = meta.get("plan", "")
    if conf["provisional"]:
        base += f" ({conf['flag']}.)"
    return base


def _margin_note(primary: str | None, margin_info: dict | None) -> str | None:
    """A human-judgment tension flag — RANKING ONLY. It never changes the channel.

    'pctile' is the customer's margin percentile across the book (1 = fattest). A rack-recommended
    account that earns thin, or a spot-recommended one that earns fat, is flagged for a human call.
    """
    if not margin_info or margin_info.get("pctile") is None:
        return None
    pct = margin_info["pctile"]
    cents = margin_info.get("book_cents_gal")
    if primary == "RACK" and pct <= 0.33:
        return (f"Steady, but margin is thin (~{cents}¢/gal, bottom third) — rack still fits; "
                "confirm the posted spread covers landed cost. [ranking note only]")
    if primary == "SPOT" and pct >= 0.67:
        return (f"Spot-suited, and it earns well (~{cents}¢/gal, top third) — keep it opportunistic, "
                "don't over-commit. [ranking note only]")
    if primary == "RACK" and pct >= 0.67:
        return (f"Steady AND high-margin (~{cents}¢/gal, top third) — protect it with a term deal. "
                "[ranking note only]")
    return None


def _margin_map(con) -> dict:
    """Per-master margin rank + ¢/gal + percentile, best-effort (empty if margin is unavailable).

    Read-only consumption of the Phase-2 margin layer for the RANKING note; margin never imports or
    alters variability and never moves a channel (one-way: variability → margin)."""
    try:
        from . import margin
        res = margin.compute_margin(con)
    except Exception:  # noqa: BLE001 — margin is an optional layer; its absence must not break scoring
        return {}
    if not res.get("available") or not res.get("customers"):
        return {}
    cust = res["customers"]
    n = len(cust)
    out = {}
    # percentile by book ¢/gal (1.0 = fattest margin in the book)
    ranked = sorted(cust, key=lambda c: (c.get("book_cents_gal") if c.get("book_cents_gal") is not None
                                         else -1e9))
    for i, c in enumerate(ranked):
        pct = (i + 1) / n if n else None
        out[c["customer_id"]] = {"rank_by_margin": c.get("rank_by_margin"),
                                 "rank_by_volume": c.get("rank_by_volume"),
                                 "book_cents_gal": c.get("book_cents_gal"), "pctile": pct}
    return out


# ---- the engine -----------------------------------------------------------------
def compute_variability(con, cfg: VariabilityConfig | None = None,
                        scfg: ScoringConfig | None = None) -> dict:
    """Two-axis variability for every master customer in the BOL book, + commitment annotation."""
    cfg = cfg or DEFAULT_VAR_CONFIG
    scfg = scfg or ScoringConfig()

    lifts = con.execute(
        "SELECT customer_id, lift_datetime, net_gallons, product, terminal FROM lifts "
        "WHERE customer_id IS NOT NULL AND lift_datetime IS NOT NULL AND net_gallons IS NOT NULL").df()
    if lifts.empty:
        return {"available": False, "customers": [], "n_customers": 0,
                "reason": "no BOL lifts loaded"}
    lifts["lift_datetime"] = pd.to_datetime(lifts["lift_datetime"], errors="coerce")
    as_of = lifts["lift_datetime"].max()
    cal, _rhythm = calendar_days.from_connection(con)
    annotations = commitment_annotations(con)
    names = {r.customer_id: r.name for r in
             con.execute("SELECT customer_id, name FROM customers").df().itertuples()}
    # Stage 1 weather model (built once; rewrites the heating-fuel size axis) + Phase-2 margin map
    # (read-only; the ranking note only — margin never moves a channel).
    model = weather_model.build_model(con)
    margins = _margin_map(con)

    customers: list[dict] = []
    for cust, cl in lifts.groupby("customer_id"):
        cl = cl.sort_values("lift_datetime")
        n_lifts = int(len(cl))
        span_days = int((cl["lift_datetime"].max() - cl["lift_datetime"].min()).days)
        # dominant product family (drives the weather-sensitivity flag)
        fam = (cl["product"].mode().iloc[0] if cl["product"].notna().any() else None)
        terminal = (cl["terminal"].mode().iloc[0] if cl["terminal"].notna().any() else None)
        prof = behavioral.daily_profile(cl, scfg, as_of, name=names.get(cust), cal=cal, terminal=terminal)
        prof_all = (prof.get("windows", {}) or {}).get("all", {})
        n_active = int(prof_all.get("n_active_days", 0))
        sufficient = n_lifts >= cfg.min_lifts and n_active >= cfg.min_active_days
        com = annotations.get(cust, {"available": False, "label": "no commitment data"})

        row = {
            "customer_id": cust, "name": names.get(cust, cust),
            "n_lifts": n_lifts, "n_active_days": n_active, "span_days": span_days,
            "total_net_gallons": round(float(cl["net_gallons"].sum()), 0),
            "dominant_product": fam, "home_terminal": terminal,
            "data_sufficient": sufficient,
            "behavior_label": prof.get("label"),
            "commitment": com,
        }
        conf = confidence_tier(n_lifts, span_days, cfg)
        row["confidence"] = conf
        if sufficient:
            row.update(_axes_for_customer(cl, prof_all, fam, terminal, model, cfg))
            row["channel"] = channel_recommendation(row["quadrant"], conf, com, margins.get(cust))
        else:
            row.update({"cadence_consistency": None, "size_consistency": None,
                        "size_consistency_raw": None, "cadence_grade": None, "size_grade": None,
                        "overall_stability": None,
                        "quadrant": "insufficient", "quadrant_label": "Not enough history",
                        "planning_note": "Too few lifts to read a pattern yet.",
                        "regular_timing": None, "consistent_size": None,
                        "size_inputs": _size_stats(pd.to_numeric(cl["net_gallons"],
                                                                  errors="coerce").to_numpy(float)),
                        "cadence_inputs": {}, "weather_sensitive": fam in HEATING_FAMILIES if fam else False,
                        "size_weather_adjusted": False, "weather_beta": None,
                        "intermittent": False, "misleading_average": False})
            row["channel"] = channel_recommendation("insufficient", conf, com, margins.get(cust))
        customers.append(row)

    customers.sort(key=lambda c: (-(c.get("cadence_consistency") or -1),
                                  -(c.get("size_consistency") or -1)))
    return {
        "available": True,
        "as_of": str(pd.Timestamp(as_of).date()),
        "window": "all",
        "n_customers": len(customers),
        "config": asdict(cfg),
        "customers": customers,
        "distribution": _distribution(customers, cfg),
        "coverage": _coverage(con, customers, annotations),
        "channel_summary": _channel_summary(customers),
        "mismatches": _mismatch_report(customers),
        "weather": {"available": model.get("available", False),
                    "n_adjusted": sum(1 for c in customers if c.get("size_weather_adjusted")),
                    "coverage": model.get("coverage", {}) and [
                        {"terminal": (None if t == "None" else t), **v}
                        for t, v in model.get("coverage", {}).items()]},
    }


def _channel_summary(customers: list[dict]) -> dict:
    """Distribution of recommended channels + how many are confidence-flagged provisional."""
    rec = {"RACK": 0, "SPOT": 0, "none": 0}
    by_conf = {"High": 0, "Medium": 0, "Low": 0}
    for c in customers:
        ch = c.get("channel") or {}
        rec[ch.get("recommended_channel") or "none"] = rec.get(ch.get("recommended_channel") or "none", 0) + 1
        by_conf[(c.get("confidence") or {}).get("tier", "Low")] += 1
    provisional = sum(1 for c in customers if (c.get("channel") or {}).get("provisional"))
    return {"recommended": rec, "by_confidence": by_conf, "n_provisional": provisional}


def _mismatch_report(customers: list[dict]) -> dict:
    """The headline deliverable: current vs recommended, top mismatches each direction with reasons."""
    def pick(c):
        ch = c["channel"]
        return {"name": c["name"], "customer_id": c["customer_id"], "n_lifts": c["n_lifts"],
                "quadrant": ch.get("quadrant_label"), "recommended": ch.get("recommended_channel"),
                "channel_label": ch.get("channel_label"), "current": ch.get("current_channel_label"),
                "strength": ch.get("mismatch_strength"), "reason": ch.get("mismatch_reason"),
                "confidence": ch.get("confidence"), "provisional": ch.get("provisional"),
                "volume": c.get("total_net_gallons"), "margin_note": ch.get("margin_note")}
    mism = [c for c in customers if (c.get("channel") or {}).get("mismatch")]
    up = [pick(c) for c in mism if c["channel"]["mismatch_direction"] == "upgrade_to_rack"]
    down = [pick(c) for c in mism if c["channel"]["mismatch_direction"] == "downgrade_to_spot"]
    # rank by strength then volume-at-stake
    sk = lambda r: (0 if r["strength"] == "strong" else 1, -(r["volume"] or 0))
    up.sort(key=sk)
    down.sort(key=sk)
    return {"n_mismatches": len(mism),
            "stuck_on_spot_should_be_rack": up,
            "committed_should_be_spot": down}


def _distribution(customers: list[dict], cfg: VariabilityConfig) -> dict:
    """Separate histograms for BOTH axes (the gate: each must spread on its own)."""
    def hist(key):
        vals = [c[key] for c in customers if c.get(key) is not None]
        bins = [0, 20, 40, 60, 80, 100.01]
        labels = ["0-20", "20-40", "40-60", "60-80", "80-100"]
        counts = {l: 0 for l in labels}
        for v in vals:
            for i in range(len(labels)):
                if bins[i] <= v < bins[i + 1]:
                    counts[labels[i]] += 1
                    break
        grades = {"A": 0, "B": 0, "C": 0, "D": 0}
        for v in vals:
            grades[grade(v, cfg)] += 1
        arr = np.array(vals) if vals else np.array([0.0])
        return {"n": len(vals), "hist": counts, "grades": grades,
                "min": round(float(arr.min()), 1), "p25": round(float(np.percentile(arr, 25)), 1),
                "median": round(float(np.median(arr)), 1), "p75": round(float(np.percentile(arr, 75)), 1),
                "max": round(float(arr.max()), 1), "std": round(float(np.std(arr)), 1)}
    quad = {}
    for c in customers:
        q = c.get("quadrant", "insufficient")
        quad[q] = quad.get(q, 0) + 1
    return {"cadence_consistency": hist("cadence_consistency"),
            "size_consistency": hist("size_consistency"),
            "quadrants": quad}


GUTCHECK_NAMES = ["Diesel Direct", "Taylor Oil", "Approved", "Summa", "Super Quality", "Rastall",
                  "East Coast", "Century Star", "Bayside", "Chief", "Plymouth", "Cooper Oil",
                  "Van Varick"]
# the four NEW quadrants, in the order they're walked end-to-end
QUADRANT_ORDER = ["metronome", "predictable_timing", "predictable_size", "unpredictable"]


def validation_readout(con, cfg: VariabilityConfig | None = None) -> dict:
    """The real-book validation gate — both axes + the rebuilt spot/rack rec, blunt. Powers /api + CLI."""
    from . import dealbook
    cfg = cfg or DEFAULT_VAR_CONFIG
    res = compute_variability(con, cfg)
    if not res.get("available"):
        return {"available": False, "reason": res.get("reason")}
    by_id = {c["customer_id"]: c for c in res["customers"]}
    dist = res["distribution"]
    scored = [c for c in res["customers"] if c["data_sufficient"]
              and c.get("cadence_consistency") is not None and c.get("size_consistency") is not None]

    def spreads(h):
        top_bin = max(h["hist"].values()) if h["hist"] else 0
        return {"std": h["std"], "range": [h["min"], h["max"]], "grades": h["grades"],
                "spreads": h["std"] >= 12 and top_bin < 0.7 * h["n"]}

    # (a) gut check — enriched with confidence + the rebuilt channel rec
    gut = []
    for nm in GUTCHECK_NAMES:
        c = by_id.get(nm)
        if c:
            ch = c.get("channel") or {}
            gut.append({"name": nm, "cadence": c.get("cadence_consistency"),
                        "size": c.get("size_consistency"), "size_raw": c.get("size_consistency_raw"),
                        "quadrant": c.get("quadrant_label"), "n_lifts": c["n_lifts"],
                        "confidence": ch.get("confidence"), "provisional": ch.get("provisional"),
                        "recommended_channel": ch.get("recommended_channel"),
                        "channel_label": ch.get("channel_label"),
                        "current_channel": ch.get("current_channel_label"),
                        "mismatch": ch.get("mismatch"), "commitment": c["commitment"].get("label")})

    # (b) the all-spot FIX proof: post-fix quadrant spread + verdict
    quad = dist["quadrants"]
    n_quad_pop = sum(1 for q in QUADRANT_ORDER if quad.get(q, 0) > 0)
    top_quad = max((quad.get(q, 0) for q in QUADRANT_ORDER), default=0)
    spot_share = quad.get("unpredictable", 0) / max(1, len(scored))
    quadrant_spread = {
        "counts": {q: quad.get(q, 0) for q in QUADRANT_ORDER},
        "n_quadrants_populated": n_quad_pop,
        "spreads_across_four": n_quad_pop >= 3 and top_quad < 0.85 * max(1, len(scored)),
        "not_all_spot": spot_share < 0.6,
        "spot_share": round(spot_share, 3),
        "verdict": ("spreads across the quadrants — no longer all-spot" if n_quad_pop >= 3
                    and spot_share < 0.6 else "still bunched — cutoffs need review"),
    }

    # (c) walk one named customer per quadrant end-to-end (lift count → confidence → axes vs cutoffs
    # → quadrant → channel → margin rank → mismatch). Pick the largest-volume exemplar of each.
    walk = []
    for q in QUADRANT_ORDER:
        ex = sorted([c for c in scored if c["quadrant"] == q],
                    key=lambda c: -(c["total_net_gallons"] or 0))[:1]
        for c in ex:
            ch = c["channel"]
            walk.append({
                "quadrant": q, "name": c["name"], "n_lifts": c["n_lifts"],
                "confidence": ch["confidence"], "provisional": ch["provisional"],
                "cadence": c["cadence_consistency"], "cadence_cutoff": cfg.cadence_regular_cutoff,
                "regular_timing": c["regular_timing"],
                "size": c["size_consistency"], "size_cutoff": cfg.size_consistent_cutoff,
                "consistent_size": c["consistent_size"],
                "size_weather_adjusted": c.get("size_weather_adjusted"),
                "recommended_channel": ch["recommended_channel"], "channel_label": ch["channel_label"],
                "margin_rank": ch.get("margin_rank"), "margin_note": ch.get("margin_note"),
                "current_channel": ch.get("current_channel_label"),
                "mismatch": ch.get("mismatch"), "mismatch_reason": ch.get("mismatch_reason"),
                "rationale": ch.get("rationale")})

    # (d) confidence tiers + an explicit low-confidence exemplar
    conf_dist = {"High": 0, "Medium": 0, "Low": 0}
    for c in res["customers"]:
        conf_dist[(c.get("confidence") or {}).get("tier", "Low")] += 1
    low_ex = sorted([c for c in scored if (c.get("confidence") or {}).get("tier") == "Low"],
                    key=lambda c: -c["n_lifts"])[:3]
    low_examples = [{"name": c["name"], "n_lifts": c["n_lifts"],
                     "quadrant": c["quadrant_label"],
                     "recommended_channel": c["channel"]["recommended_channel"],
                     "flag": c["channel"]["confidence_flag"]} for c in low_ex]

    # (e) AUDIT — margin is RANKING ONLY: confirm every recommended channel equals the quadrant's own
    # primary channel (i.e. margin never moved it). Structurally guaranteed; surfaced as a guard.
    flips = 0
    for c in scored:
        expected = _quadrant_meta(c["quadrant"]).get("primary_channel")
        if c["channel"].get("recommended_channel") != expected:
            flips += 1
    margin_audit = {"channel_set_by": "variability quadrant + confidence ONLY",
                    "margin_role": "ranking note only",
                    "channels_flipped_by_margin": flips,
                    "verdict": "margin never moved a channel call" if flips == 0
                    else f"WARNING: {flips} channels diverge from quadrant — investigate"}

    # (f) annotation sanity + conformance anomalies (kept)
    metro = [c for c in scored if c["quadrant"] == "metronome"]
    metro_backed = [c for c in metro if (c["commitment"].get("term_backed_share") or 0) >= 0.3]
    anomalies = []
    for c in res["customers"]:
        com = c["commitment"]
        if (com.get("term_backed_share") or 0) >= 1.0:
            cw_term = com.get("committed_window_term_gal") or 0
            cw_fwd = com.get("committed_window_forward_gal") or 0
            cause = ("term-year anchor (inferred; term is likely a different contract year)"
                     if cw_term > cw_fwd else "forward/real — under-lift, basis, or join miss to chase")
            anomalies.append({"name": c["name"], "term_backed_share": com.get("term_backed_share"),
                              "committed_window_gal": com.get("committed_window_gal"),
                              "actual_window_gal": com.get("actual_window_gal"), "likely_cause": cause})

    return {
        "available": True,
        "as_of": res["as_of"],
        "axis1_cadence": spreads(dist["cadence_consistency"]),
        "axis2_size": spreads(dist["size_consistency"]),
        "axis1_hist": dist["cadence_consistency"]["hist"],
        "axis2_hist": dist["size_consistency"]["hist"],
        "quadrants": dist["quadrants"],
        "quadrant_spread": quadrant_spread,
        "four_quadrant_walk": walk,
        "gut_check": gut,
        "confidence": {"distribution": conf_dist, "low_confidence_examples": low_examples},
        "channel_summary": res["channel_summary"],
        "mismatches": res["mismatches"],
        "margin_audit": margin_audit,
        "weather": res["weather"],
        "annotation_sanity": {"n_metronome": len(metro),
                              "n_metronome_term_backed": len(metro_backed),
                              "pct_metronome_term_backed": round(100 * len(metro_backed) / len(metro), 1) if metro else None},
        "conformance_anomalies": sorted(anomalies, key=lambda a: -(a["term_backed_share"] or 0))[:10],
        "coverage": res["coverage"],
        "bridge": dealbook.bridge_candidates(con),
    }


def _coverage(con, customers: list[dict], annotations: dict) -> dict:
    total_gal = sum(c["total_net_gallons"] for c in customers)
    scored = [c for c in customers if c["data_sufficient"]]
    scored_gal = sum(c["total_net_gallons"] for c in scored)
    annotated = [c for c in customers if c["commitment"].get("available")]
    annotated_gal = sum(c["total_net_gallons"] for c in annotated)
    return {
        "n_customers": len(customers),
        "n_scored": len(scored),
        "pct_customers_scored": round(100 * len(scored) / len(customers), 1) if customers else 0,
        "pct_volume_scored": round(100 * scored_gal / total_gal, 1) if total_gal else 0,
        "n_annotated": len(annotated),
        "pct_volume_annotated": round(100 * annotated_gal / total_gal, 1) if total_gal else 0,
    }
