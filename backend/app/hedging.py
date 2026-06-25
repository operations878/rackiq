"""Operational demand-hedging engine — how much product to stage, per terminal, each morning.

This is **physical / operational** hedging: staging product against *demand surprise*, NOT financial
price hedging. Built directly on the **working-day calendar** (``calendar_days``) so every day-count —
the staging horizon, "days since last lift", cadence, overdue-ness — is in real operating days (a
customer silent Fri–Sun is NOT three working days overdue). It reuses the existing per-customer
machinery: the forward **forecast** (today-anchored expected demand + its own backtest error), the
daily **behavioral** profile (presence/size split, intermittency, typical load), the **VAR** lane
(steadiness) and the working-day **cadence/recency** — all from :func:`scoring.compute_scores`.

For one terminal, over a configurable horizon (default the next 3 and 5 **working** days) it answers:

  1. **Expected demand** — sum each customer's forward expectation (attributed to the terminal by
     their volume mix) into a terminal total with an honest **P10/P50/P90** band from combined
     out-of-sample error **accounting for customer correlation** (cold snaps lift many distillate
     accounts together → variances don't just add). Split the reliable **FLOOR** (steady-customer
     volume) from the volatile **UPSIDE**.
  2. **Behavior-aware dynamic buffer** (the heart) — safety stock sized by *who* is at the terminal.
     A statistical ``band_buffer`` (z·σ at the service level) plus a ``coil_buffer``: for each
     **bursty / intermittent** customer, the share of their typical load that their **overdue-ness**
     (working days silent ÷ their working-day cadence) says is "coiled" and due to land now. A burst
     buyer past their cadence RAISES the buffer; a recently-lifted one adds ~nothing.
  3. **Risk concentration** — rank customers by contribution to demand **variability** (not volume):
     who *makes the buffer necessary*. Flag any single customer whose one load could exceed the buffer.
  4. **Morning readout** — one plain-language paragraph per terminal.
  5. **Operational customer view** — per customer, the staging-relevant facts.

**Honesty:** demand + recommended **target** staging are always computed; if inventory / tank
capacity isn't loaded, that's stated (target staging, not days-of-cover) — inventory is never faked.

Every threshold lives in :class:`HedgingConfig`. Results resolve per **master** customer (ids are
already master at commit).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import calendar_days, db, demand, scoring
from .scoring_config import DEFAULT_CONFIG as SCORING_DEFAULT
from .scoring_config import WINDOWS


# ---- Configuration (every weight / threshold a parameter) -----------------------
@dataclass(frozen=True)
class HedgingConfig:
    horizons: tuple = (3, 5)               # staging horizons, in WORKING days
    default_service_level: float = 0.90    # cover this share of demand outcomes
    band_z: float = 1.2816                 # P10/P90 band half-width (10th–90th under a normal approx)
    service_level_min: float = 0.50
    service_level_max: float = 0.999

    rel_sigma_default: float = 0.40        # relative forecast σ when a customer can't be backtested
    rel_sigma_cap: float = 1.25            # cap a wild account's σ so the band stays sane
    sigma_floor_gallons: float = 250.0     # absolute per-customer σ floor

    # Customer correlation (cold snaps co-move distillate; variances don't just add).
    corr_same_product: float = 0.30        # two accounts on the same product
    corr_cross_product: float = 0.08       # two accounts on different products
    corr_weather: float = 0.55             # two weather-sensitive accounts (co-move on snaps)
    weather_subscore_min: float = 60.0     # weather-sensitivity percentile ≥ this ⇒ "weather-linked"
    corr_cap: float = 0.95

    # Coil (overdue-burst) buffer. Overdue-ness is measured against the data's own timeframe
    # (anchored to the book's last data date), so a uniformly stale book doesn't make everyone look
    # overdue; on fresh data the data date ≈ today.
    coil_start: float = 1.0                # overdue ratio (silent ÷ cadence) where the coil ramps in — "past their gap"
    coil_full: float = 2.0                 # overdue ratio where it reaches a full typical load
    coil_overdue_flag: float = 1.0         # overdue ≥ this ⇒ "overdue" badge
    coil_min_load_gallons: float = 1000.0  # ignore trivially small coil contributions

    # Classification (which behavioral classes drive the buffer vs. the floor).
    bursty_frequencies: tuple = ("occasional", "rare")
    steady_frequencies: tuple = ("daily", "frequent")

    watch_list_size: int = 8               # how many risk drivers to surface
    single_lift_flag_ratio: float = 1.0    # flag if a customer's one load ≥ this × the buffer

    def to_dict(self) -> dict:
        return asdict(self)

    def with_overrides(self, overrides: dict | None) -> "HedgingConfig":
        if not overrides:
            return self
        known = set(self.__dataclass_fields__)  # type: ignore[attr-defined]
        return replace(self, **{k: v for k, v in overrides.items() if k in known})


DEFAULT_CONFIG = HedgingConfig()


# ---- Formatting -----------------------------------------------------------------
def _gal(x) -> str:
    if x is None:
        return "—"
    if abs(x) >= 1e6:
        return f"{x / 1e6:.2f}MM"
    if abs(x) >= 1e4:
        return f"{round(x / 1e3)}k"
    return f"{round(float(x)):,}"


def _plural(n, word: str) -> str:
    try:
        return word if int(round(n)) == 1 else word + "s"
    except (TypeError, ValueError):
        return word + "s"


def _ramp(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 1.0 if x >= hi else 0.0
    return float(max(0.0, min(1.0, (x - lo) / (hi - lo))))


# ---- Per-customer staging record ------------------------------------------------
def _terminal_share(c: dict, terminal: str | None) -> float:
    """The customer's share of volume at this terminal (1.0 if no terminal scoping)."""
    if terminal is None:
        return 1.0
    tm = (c.get("facts") or {}).get("terminal_mix") or {}
    return float(tm.get(terminal, 0.0))


def _typical_load(c: dict) -> float:
    """Their typical load size when they DO lift (presence-aware median, with fallbacks)."""
    beh = c.get("behavior") or {}
    win = (beh.get("windows") or {}).get(beh.get("primary_window")) or {}
    size = win.get("size_when_present") or {}
    for v in (size.get("median"), (c.get("facts") or {}).get("order_size_median"),
              (c.get("facts") or {}).get("order_size_mean")):
        if v:
            return float(v)
    return 0.0


def _build_customer(c: dict, terminal: str | None, cal, as_of_ts, wd_in_week: float,
                    wd_per_month: float, last_lift, hcfg: HedgingConfig) -> dict | None:
    """One customer's terminal-attributed staging facts (horizon-independent). Overdue-ness is
    measured against the book's last data date (``as_of_ts``) so a uniformly stale book doesn't make
    everyone look overdue; on fresh data the data date ≈ today."""
    share = _terminal_share(c, terminal)
    if share <= 1e-9:
        return None

    beh = c.get("behavior") or {}
    var = c.get("var") or {}
    facts = c.get("facts") or {}
    fc = c.get("forecast") or {}

    # ---- expected demand as a per-working-day rate at this terminal ----
    rate = None
    rel = hcfg.rel_sigma_default
    if fc.get("available"):
        h7 = next((h for h in fc.get("horizons", []) if h["days"] == 7), None)
        if h7 and wd_in_week > 0:
            rate = (float(h7["expected"]) * share) / wd_in_week
        rel = min(float(fc.get("rel_sigma") or hcfg.rel_sigma_default), hcfg.rel_sigma_cap)
    if rate is None:
        mv = float(facts.get("monthly_volume") or 0.0)
        rate = (mv * share) / wd_per_month if wd_per_month > 0 else 0.0

    typical = _typical_load(c)

    # ---- working-days since last lift (anchored to TODAY) and overdue-ness ----
    cad = var.get("base_cadence_days")
    if not cad:
        cad = (beh.get("windows") or {}).get(beh.get("primary_window"), {}).get("presence", {}).get("median_gap_days")
    days_silent = cal.working_days_between(last_lift, as_of_ts, terminal) if last_lift is not None else None
    overdue = (days_silent / cad) if (cad and cad > 0 and days_silent is not None) else None

    # ---- classification ----
    freq = beh.get("frequency_class")
    intermittent = bool(beh.get("intermittent"))
    is_bursty = bool(intermittent or freq in hcfg.bursty_frequencies)
    is_steady = bool((not is_bursty) and freq in hcfg.steady_frequencies)
    weather_v = ((c.get("subscores") or {}).get("weather_sensitivity") or {}).get("value")
    weather_linked = bool((weather_v is not None and weather_v >= hcfg.weather_subscore_min)
                          or c.get("archetype_true") == "weather_distillate")
    mix = facts.get("product_mix") or {}
    dom_product = max(mix, key=mix.get) if mix else "(unknown)"

    return {
        "customer_id": c["customer_id"], "name": c.get("name", c["customer_id"]),
        "terminal_share": round(share, 4),
        "daily_rate": max(0.0, rate), "rel_sigma": rel, "typical_load": typical,
        "var_score": var.get("score"), "var_grade": var.get("grade"),
        "behavior_label": beh.get("label"), "frequency_class": freq,
        "intermittent": intermittent, "misleading_severity": beh.get("misleading_severity"),
        "is_bursty": is_bursty, "is_steady": is_steady,
        "weather_linked": weather_linked, "dom_product": dom_product,
        "cadence_working_days": round(float(cad), 2) if cad else None,
        "working_days_since_last": round(float(days_silent), 1) if days_silent is not None else None,
        "overdue_ratio": round(float(overdue), 2) if overdue is not None else None,
        "overdue": bool(overdue is not None and overdue >= hcfg.coil_overdue_flag and is_bursty),
        "slowing": bool(fc.get("slowing")),
    }


# ---- Terminal aggregation for one horizon ---------------------------------------
def _correlation_sigma(recs: list[dict], sig: np.ndarray, hcfg: HedgingConfig) -> float:
    """Terminal σ with customer correlation: √(σ·R·σ). Same-product / weather-linked pairs co-move,
    so the band is honestly wider than independence (Σσ²) would give."""
    n = len(recs)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sig[0])
    prod = np.array([r["dom_product"] for r in recs], dtype=object)
    wx = np.array([r["weather_linked"] for r in recs], dtype=bool)
    same = prod[:, None] == prod[None, :]
    R = np.where(same, hcfg.corr_same_product, hcfg.corr_cross_product).astype(float)
    wpair = wx[:, None] & wx[None, :]
    R = np.maximum(R, np.where(wpair, hcfg.corr_weather, 0.0))
    R = np.minimum(R, hcfg.corr_cap)
    np.fill_diagonal(R, 1.0)
    var = float(sig @ R @ sig)
    return math.sqrt(max(0.0, var))


def _z_for(sl: float, hcfg: HedgingConfig) -> float:
    from scipy.stats import norm
    sl = max(hcfg.service_level_min, min(hcfg.service_level_max, sl))
    return float(norm.ppf(sl))


def _horizon_block(recs: list[dict], H: int, cal, today, terminal: str | None,
                   sl: float, hcfg: HedgingConfig) -> dict:
    """Expected band + floor/upside + behavior-aware buffer + readout for one working-day horizon."""
    exp = np.array([r["daily_rate"] * H for r in recs], dtype=float)
    sig = np.array([max(e * r["rel_sigma"], hcfg.sigma_floor_gallons) if e > 0 else 0.0
                    for e, r in zip(exp, recs)], dtype=float)
    p50 = float(exp.sum())
    sigma_terminal = _correlation_sigma(recs, sig, hcfg)
    z10 = hcfg.band_z
    p10 = max(0.0, p50 - z10 * sigma_terminal)
    p90 = p50 + z10 * sigma_terminal

    # floor (steady customers' expected) vs volatile upside
    floor = float(sum(e for e, r in zip(exp, recs) if r["is_steady"]))
    upside_expected = max(0.0, p50 - floor)

    # ---- behavior-aware dynamic buffer ----
    z_sl = _z_for(sl, hcfg)
    band_buffer = z_sl * sigma_terminal
    coil_items = []
    coil_total = 0.0
    for r in recs:
        if not r["is_bursty"] or r["overdue_ratio"] is None:
            continue
        ramp = _ramp(r["overdue_ratio"], hcfg.coil_start, hcfg.coil_full)
        coil = r["typical_load"] * r["terminal_share"] * ramp
        if coil >= hcfg.coil_min_load_gallons:
            coil_total += coil
            coil_items.append({"customer_id": r["customer_id"], "name": r["name"],
                               "coil_gallons": round(coil, 0), "overdue_ratio": r["overdue_ratio"],
                               "working_days_since_last": r["working_days_since_last"],
                               "cadence_working_days": r["cadence_working_days"],
                               "typical_load": round(r["typical_load"], 0),
                               "behavior_label": r["behavior_label"]})
    coil_items.sort(key=lambda x: x["coil_gallons"], reverse=True)
    buffer = band_buffer + coil_total
    staging = p50 + buffer
    sl_pct = round(max(hcfg.service_level_min, min(hcfg.service_level_max, sl)) * 100)
    by_date = str(cal.add_working_days(today, H, terminal).date())
    elevated = bool(coil_total > 0.15 * max(band_buffer, 1.0))

    # ---- plain-language morning readout ----
    drivers = ", ".join(x["name"] for x in coil_items[:3]) if coil_items else None
    readout = (f"{terminal or 'Network'} — next {H} working days: expect ~{_gal(p50)} "
               f"(likely {_gal(p10)}–{_gal(p90)}). Stage ~{_gal(staging)} "
               f"to hold a {sl_pct}% service level.")
    if drivers:
        nd = len(coil_items)
        singular = nd == 1
        readout += (f" Buffer elevated: {drivers}{' and others' if nd > 3 else ''} "
                    f"{'is' if singular else 'are'} overdue and "
                    f"{'drives' if singular else 'drive'} most upside risk.")
    if floor > 0:
        readout += f" Steady base provides ~{_gal(floor)} reliable floor."

    return {
        "horizon_working_days": H, "by_date": by_date,
        "expected": round(p50, 0), "p10": round(p10, 0), "p50": round(p50, 0), "p90": round(p90, 0),
        "sigma": round(sigma_terminal, 0),
        "floor": round(floor, 0), "upside": round(upside_expected, 0),
        "floor_share": round(floor / p50, 3) if p50 else None,
        "service_level": sl_pct, "z": round(z_sl, 3),
        "band_buffer": round(band_buffer, 0), "coil_buffer": round(coil_total, 0),
        "buffer": round(buffer, 0), "recommended_staging": round(staging, 0),
        "buffer_elevated": elevated, "overdue_drivers": coil_items,
        "readout": readout,
    }


# ---- Risk concentration ---------------------------------------------------------
def _risk_concentration(recs: list[dict], H: int, hcfg: HedgingConfig, buffer: float) -> list[dict]:
    """Rank customers by contribution to demand VARIABILITY (variance share), not volume — who makes
    the buffer necessary. Flags a customer whose single load could exceed the buffer."""
    sig2 = []
    for r in recs:
        e = r["daily_rate"] * H
        s = max(e * r["rel_sigma"], hcfg.sigma_floor_gallons) if e > 0 else 0.0
        sig2.append(s * s)
    total = float(sum(sig2)) or 1.0
    out = []
    for r, s2 in zip(recs, sig2):
        one_load = r["typical_load"] * r["terminal_share"]
        out.append({
            "customer_id": r["customer_id"], "name": r["name"],
            "variability_share": round(s2 / total, 4),
            "expected": round(r["daily_rate"] * H, 0),
            "typical_load": round(r["typical_load"], 0),
            "behavior_label": r["behavior_label"], "var_grade": r["var_grade"],
            "overdue": r["overdue"], "overdue_ratio": r["overdue_ratio"],
            "is_bursty": r["is_bursty"],
            "single_lift_exceeds_buffer": bool(buffer > 0 and one_load >= hcfg.single_lift_flag_ratio * buffer),
        })
    out.sort(key=lambda x: x["variability_share"], reverse=True)
    return out


# ---- Orchestration --------------------------------------------------------------
def _terminals(con) -> list[str]:
    try:
        return [r[0] for r in con.execute(
            "SELECT DISTINCT terminal FROM lifts WHERE terminal IS NOT NULL ORDER BY 1").fetchall()]
    except Exception:  # noqa: BLE001
        return []


def _last_lifts(con) -> dict:
    try:
        df = con.execute(
            "SELECT customer_id, max(lift_datetime) AS last FROM lifts "
            "WHERE customer_id IS NOT NULL GROUP BY 1").df()
    except Exception:  # noqa: BLE001
        return {}
    df["last"] = pd.to_datetime(df["last"], errors="coerce")
    return dict(zip(df["customer_id"], df["last"]))


def compute_hedging(con, terminal: str | None = None, window: str = "all",
                    service_level: float | None = None, scfg=None, hcfg: HedgingConfig | None = None,
                    today=None, score_res: dict | None = None, cal=None,
                    last_by_id: dict | None = None) -> dict:
    """Full operational hedging readout for one terminal. ``score_res`` / ``cal`` / ``last_by_id`` may
    be passed in to share work across an all-terminals sweep."""
    hcfg = hcfg or DEFAULT_CONFIG
    scfg = scfg or SCORING_DEFAULT
    if window not in WINDOWS:
        window = "all"
    sl = hcfg.default_service_level if service_level is None else float(service_level)

    terminals = _terminals(con)
    if terminals and (terminal is None or terminal not in terminals):
        terminal = terminals[0]
    if not terminals:
        terminal = None

    if cal is None:
        cal, _rhythm = calendar_days.from_connection(con, calendar_days.DEFAULT_CONFIG)
    else:
        _rhythm = None
    if score_res is None:
        score_res = scoring.compute_scores(con, scfg, window, today=today)
    if last_by_id is None:
        last_by_id = _last_lifts(con)

    anchor = score_res.get("forecast_anchor")
    today_ts = pd.Timestamp(anchor) if anchor else pd.Timestamp(datetime.now()).normalize()
    as_of_str = score_res.get("as_of")
    as_of_ts = pd.Timestamp(as_of_str) if as_of_str else today_ts   # overdue measured in-book
    wd_in_week = cal.window_working_days(today_ts, today_ts + pd.Timedelta(days=7), terminal) or 5.0
    wd_per_month = cal.working_week_length(terminal) * 52.0 / 12.0

    rhythm = _rhythm if _rhythm is not None else None
    sat_weight = cal.sat_weight(terminal)

    availability = {
        "demand": {"available": bool(score_res.get("customers")),
                   "reason": "Per-customer forward demand from the scoring engine."
                   if score_res.get("customers") else "No scored customers in scope."},
    }

    recs = []
    for c in score_res.get("customers", []):
        r = _build_customer(c, terminal, cal, as_of_ts, wd_in_week, wd_per_month,
                            last_by_id.get(c["customer_id"]), hcfg)
        if r is not None:
            recs.append(r)

    inv = demand._latest_inventory(con, terminal, None)
    inv_connected = inv is not None

    base = {
        "terminal": terminal, "terminals": terminals, "window": window, "windows": WINDOWS,
        "as_of": score_res.get("as_of"), "forecast_anchor": anchor,
        "data_lag_days": score_res.get("data_lag_days"), "recency_note": score_res.get("recency_note"),
        "service_level": round(max(hcfg.service_level_min, min(hcfg.service_level_max, sl)) * 100),
        "saturday_weight": round(sat_weight, 3),
        "config": hcfg.to_dict(), "availability": availability,
        "inventory_connected": inv_connected, "inventory": inv,
        "n_customers": len(recs),
    }

    if not recs:
        return {**base, "horizons": [], "primary_horizon": None, "customers": [],
                "watch_list": [], "readout": f"No customers with demand at {terminal or 'this terminal'}."}

    horizons = [_horizon_block(recs, int(H), cal, today_ts, terminal, sl, hcfg) for H in hcfg.horizons]
    primary = horizons[0]
    watch = _risk_concentration(recs, primary["horizon_working_days"], hcfg, primary["buffer"])

    # operational customer view (horizon-independent facts + the primary-horizon expectation)
    risk_by_id = {w["customer_id"]: w for w in watch}
    cust_view = []
    for r in recs:
        w = risk_by_id.get(r["customer_id"], {})
        cust_view.append({
            "customer_id": r["customer_id"], "name": r["name"],
            "behavior_label": r["behavior_label"], "frequency_class": r["frequency_class"],
            "var_score": r["var_score"], "var_grade": r["var_grade"],
            "cadence_working_days": r["cadence_working_days"],
            "working_days_since_last": r["working_days_since_last"],
            "overdue_ratio": r["overdue_ratio"], "overdue": r["overdue"], "slowing": r["slowing"],
            "typical_load": round(r["typical_load"], 0),
            "terminal_share": r["terminal_share"],
            "expected_primary_horizon": round(r["daily_rate"] * primary["horizon_working_days"], 0),
            "is_bursty": r["is_bursty"], "is_steady": r["is_steady"],
            "intermittent": r["intermittent"], "misleading_severity": r["misleading_severity"],
            "variability_share": w.get("variability_share"),
            "single_lift_exceeds_buffer": w.get("single_lift_exceeds_buffer", False),
        })
    cust_view.sort(key=lambda x: (x["variability_share"] or 0.0), reverse=True)

    inv_note = None
    if not inv_connected:
        inv_note = ("On-hand inventory isn't connected for this terminal — showing TARGET staging "
                    "(expected demand + buffer), not days-of-cover. Load inventory_snapshot + "
                    "tank_capacity to compare against what's in the tanks.")

    return {
        **base,
        "horizons": horizons, "primary_horizon": primary["horizon_working_days"],
        "readout": primary["readout"], "inventory_note": inv_note,
        "watch_list": watch[:hcfg.watch_list_size],
        "customers": cust_view,
    }


def all_terminals(con, window: str = "all", service_level: float | None = None,
                  scfg=None, hcfg: HedgingConfig | None = None, today=None) -> dict:
    """Hedging readout for every terminal (shares the scoring/calendar work across terminals)."""
    hcfg = hcfg or DEFAULT_CONFIG
    scfg = scfg or SCORING_DEFAULT
    cal, _ = calendar_days.from_connection(con, calendar_days.DEFAULT_CONFIG)
    score_res = scoring.compute_scores(con, scfg, window, today=today)
    last_by_id = _last_lifts(con)
    terminals = _terminals(con) or [None]
    out = []
    for t in terminals:
        out.append(compute_hedging(con, t, window, service_level, scfg, hcfg, today,
                                   score_res=score_res, cal=cal, last_by_id=last_by_id))
    return {"window": window, "as_of": score_res.get("as_of"),
            "forecast_anchor": score_res.get("forecast_anchor"),
            "terminals": [t for t in terminals if t], "readouts": out}
