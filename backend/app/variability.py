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

from . import behavioral, calendar_days, db, schema
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

    # 2×2 thresholds. Frequency split is presence-based (frequent/daily vs sparse/intermittent);
    # size split is the size-consistency score.
    size_consistent_threshold: float = 55.0
    frequent_classes: tuple = ("daily", "frequent")

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


def _weather_residual_sizes(cl: pd.DataFrame, family: str | None) -> tuple[np.ndarray, bool]:
    """SEAM for weather-normalizing AXIS 2 on heating fuels.

    Returns (per-lift sizes used for the size axis, weather_adjusted?). Today it returns the RAW
    sizes (weather layer not merged) so nothing is silently smoothed; when the HDD/β layer lands this
    is where the residual (size − β·HDD) replaces the raw size for heating families, so cold-snap
    sizing is no longer misread as inconsistency. Non-heating families are never touched.
    """
    sizes = pd.to_numeric(cl["net_gallons"], errors="coerce").to_numpy(dtype=float)
    sizes = sizes[np.isfinite(sizes)]
    # weather layer not merged → raw, and we say so via the returned flag
    return sizes, False


# ---- the two-axis score per customer --------------------------------------------
def _axes_for_customer(cl: pd.DataFrame, prof_all: dict, family: str | None,
                       cfg: VariabilityConfig) -> dict:
    """Compute AXIS 1 (cadence) and AXIS 2 (size) from one customer's full lift history."""
    presence = prof_all.get("presence", {}) if prof_all else {}
    gap_cv = presence.get("gap_cv")
    active_dpw = presence.get("active_days_per_week")
    active_rate = presence.get("active_day_rate")
    freq_class = prof_all.get("frequency_class") if prof_all else None

    # ---- AXIS 1: cadence consistency ----
    if gap_cv is None:
        cadence = None                                  # too few active days to read a cadence
    else:
        regularity = _clamp(1.0 - gap_cv / cfg.cadence_gap_cv_ref)
        presence_factor = _clamp((active_dpw or 0.0) / cfg.presence_full_days_per_week)
        cadence = round(100.0 * (cfg.cadence_w_regularity * regularity
                                 + cfg.cadence_w_presence * presence_factor), 1)

    # ---- AXIS 2: size consistency (per-lift, active only; raw — weather seam) ----
    sizes, weather_adjusted = _weather_residual_sizes(cl, family)
    sstats = _size_stats(sizes)
    size_cv = sstats["cv"]
    if size_cv is None:
        size_score = None
    else:
        size_score = round(100.0 * _clamp(1.0 - size_cv / cfg.size_cv_ref), 1)

    # ---- 2×2 quadrant ----
    frequent = freq_class in cfg.frequent_classes
    consistent_size = (size_score is not None and size_score >= cfg.size_consistent_threshold)
    quadrant, quadrant_label, plan = _quadrant(frequent, consistent_size)

    overall = None
    if cadence is not None and size_score is not None:
        overall = round(cfg.overall_w_cadence * cadence + cfg.overall_w_size * size_score, 1)

    return {
        "cadence_consistency": cadence,
        "cadence_grade": grade(cadence, cfg),
        "size_consistency": size_score,
        "size_grade": grade(size_score, cfg),
        "overall_stability": overall,                   # SECONDARY roll-up only
        "overall_grade": grade(overall, cfg),
        "quadrant": quadrant,
        "quadrant_label": quadrant_label,
        "planning_note": plan,
        "cadence_inputs": {"gap_cv": gap_cv, "active_days_per_week": active_dpw,
                           "active_day_rate": active_rate, "frequency_class": freq_class,
                           "median_gap_working_days": presence.get("median_gap_days")},
        "size_inputs": sstats,
        "size_weather_adjusted": weather_adjusted,
        "weather_sensitive": (family in HEATING_FAMILIES) if family else False,
        "intermittent": bool(prof_all.get("intermittent")) if prof_all else False,
        "misleading_average": bool(prof_all.get("misleading_average")) if prof_all else False,
    }


_QUADRANTS = {
    (True, True): ("metronome", "Metronome",
                   "Fully plannable — forecast both timing AND quantity."),
    (True, False): ("daily_variable_size", "Shows up steadily, size unpredictable",
                    "Plan the presence (stage for them); can't size the lift."),
    (False, True): ("infrequent_identical", "Infrequent but identical",
                    "When they come it's a known number — plan the quantity, watch the timing."),
    (False, False): ("sporadic_bursty", "Sporadic / bursty",
                     "Genuinely hard to plan — honest low-confidence flag."),
}


def _quadrant(frequent: bool, consistent_size: bool):
    return _QUADRANTS[(frequent, consistent_size)]


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

    customers: list[dict] = []
    for cust, cl in lifts.groupby("customer_id"):
        cl = cl.sort_values("lift_datetime")
        n_lifts = int(len(cl))
        # dominant product family (drives the weather-sensitivity flag)
        fam = (cl["product"].mode().iloc[0] if cl["product"].notna().any() else None)
        terminal = (cl["terminal"].mode().iloc[0] if cl["terminal"].notna().any() else None)
        prof = behavioral.daily_profile(cl, scfg, as_of, name=names.get(cust), cal=cal, terminal=terminal)
        prof_all = (prof.get("windows", {}) or {}).get("all", {})
        n_active = int(prof_all.get("n_active_days", 0))
        sufficient = n_lifts >= cfg.min_lifts and n_active >= cfg.min_active_days

        row = {
            "customer_id": cust, "name": names.get(cust, cust),
            "n_lifts": n_lifts, "n_active_days": n_active,
            "total_net_gallons": round(float(cl["net_gallons"].sum()), 0),
            "dominant_product": fam, "home_terminal": terminal,
            "data_sufficient": sufficient,
            "behavior_label": prof.get("label"),
            "commitment": annotations.get(cust, {"available": False, "label": "no commitment data"}),
        }
        if sufficient:
            row.update(_axes_for_customer(cl, prof_all, fam, cfg))
        else:
            row.update({"cadence_consistency": None, "size_consistency": None,
                        "cadence_grade": None, "size_grade": None, "overall_stability": None,
                        "quadrant": "insufficient", "quadrant_label": "Not enough history",
                        "planning_note": "Too few lifts to read a pattern yet.",
                        "size_inputs": _size_stats(pd.to_numeric(cl["net_gallons"],
                                                                  errors="coerce").to_numpy(float)),
                        "cadence_inputs": {}, "weather_sensitive": fam in HEATING_FAMILIES if fam else False,
                        "intermittent": False, "misleading_average": False})
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
    }


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
                  "East Coast", "Century Star", "Bayside", "Chief", "Plymouth", "Cooper Oil"]


def validation_readout(con, cfg: VariabilityConfig | None = None) -> dict:
    """The real-book validation gate — both axes, blunt. Powers /api + the CLI report."""
    from . import dealbook
    cfg = cfg or DEFAULT_VAR_CONFIG
    res = compute_variability(con, cfg)
    if not res.get("available"):
        return {"available": False, "reason": res.get("reason")}
    by_id = {c["customer_id"]: c for c in res["customers"]}
    dist = res["distribution"]

    def spreads(h):
        # "spread" = real dispersion AND not bunched into one bin
        top_bin = max(h["hist"].values()) if h["hist"] else 0
        return {"std": h["std"], "range": [h["min"], h["max"]], "grades": h["grades"],
                "spreads": h["std"] >= 12 and top_bin < 0.7 * h["n"]}

    # (a) gut check on the 2×2
    gut = []
    for nm in GUTCHECK_NAMES:
        c = by_id.get(nm)
        if c:
            gut.append({"name": nm, "cadence": c.get("cadence_consistency"),
                        "cadence_grade": c.get("cadence_grade"), "size": c.get("size_consistency"),
                        "size_grade": c.get("size_grade"), "quadrant": c.get("quadrant_label"),
                        "n_lifts": c["n_lifts"], "commitment": c["commitment"].get("label")})

    # (b) prove the split: steady-cadence/variable-size vs sparse/consistent-size
    scored = [c for c in res["customers"] if c["data_sufficient"]
              and c.get("cadence_consistency") is not None and c.get("size_consistency") is not None]
    daily_lumpy = sorted([c for c in scored if c["cadence_consistency"] >= 60],
                         key=lambda c: c["size_consistency"])[:1]
    sparse_tight = sorted([c for c in scored if c["cadence_consistency"] < 45 and c["size_consistency"] >= 70],
                          key=lambda c: -c["size_consistency"])[:1]

    def thumb(c):
        return {"name": c["name"], "cadence": c["cadence_consistency"], "size": c["size_consistency"],
                "quadrant": c["quadrant_label"], "gap_cv": c["cadence_inputs"].get("gap_cv"),
                "size_cv": c["size_inputs"].get("cv"),
                "size_p10_p90": [c["size_inputs"].get("p10"), c["size_inputs"].get("p90")]}

    # (c) annotation sanity — are metronomes more often term-backed?
    metro = [c for c in scored if c["quadrant"] == "metronome"]
    metro_backed = [c for c in metro if (c["commitment"].get("term_backed_share") or 0) >= 0.3]
    # (d) conformance anomalies — committed ≥ actual. Split term (inferred-year) from forward (real
    # dates) so the cause is self-evident: a TERM-driven over-commit is a period-anchor artifact; a
    # FORWARD-driven one is a real signal (under-lift / basis / join miss) worth chasing.
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
                              "committed_window_term_gal": cw_term, "committed_window_forward_gal": cw_fwd,
                              "actual_window_gal": com.get("actual_window_gal"), "likely_cause": cause})

    return {
        "available": True,
        "as_of": res["as_of"],
        "axis1_cadence": spreads(dist["cadence_consistency"]),
        "axis2_size": spreads(dist["size_consistency"]),
        "axis1_hist": dist["cadence_consistency"]["hist"],
        "axis2_hist": dist["size_consistency"]["hist"],
        "quadrants": dist["quadrants"],
        "gut_check": gut,
        "split_proof": {"steady_cadence_variable_size": [thumb(c) for c in daily_lumpy],
                        "sparse_consistent_size": [thumb(c) for c in sparse_tight]},
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
