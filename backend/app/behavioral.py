"""Daily, presence-aware behavioral profile — the layer that fixes "the average hides the pattern".

The problem this solves (the design test):
  • "Taylor"        lifts ~39k, 39k, 51k, 28k across a week — steady, present most days (~39k avg).
  • "Super Quality" lifts 0, 0, 60k, 0, 50k — SAME weekly total, but silent most days then spikes.
    A naive ~22k "average daily volume" is meaningless — they never lift 22k; they lift 0 or a
    large load.

The fix is to **split PRESENCE from SIZE** and compute both at **daily** resolution:

  1. PRESENCE / FREQUENCY — over ALL calendar days in the window, **zeros included**: active-day
     rate, median gap between lifts, longest silent stretch, lifts/active-days per week. Zeros are
     DATA, not skipped.
  2. SIZE-WHEN-PRESENT — over ACTIVE days only: mean / median / mode (bucketed) / min / max / range
     / std / CV / P10·P50·P90 — the real size of a load when they actually buy.
  3. NAIVE ALL-DAYS — mean & median over every day (incl. zeros). When the all-days **median is 0
     but the mean is > 0** the account is **intermittent** and that daily average is **misleading**;
     we flag it loudly.

Each customer is then classified on two axes — **FREQUENCY** (daily / frequent / occasional / rare)
× **SIZE-CONSISTENCY** (tight / variable / erratic), refined by a timing-regularity tiebreaker — into
a plain label (Steady Daily · Steady Intermittent · Erratic Frequent · Sporadic/Bursty …) with a
plain-English read.

This ENRICHES the VAR lane: it is a layer ON TOP of the frozen VAR score and the forecasting engine,
never a replacement. Everything resolves per **master customer** (ids are already rewritten to master
at commit) at **daily** resolution using real ship dates. Every threshold is a
:class:`scoring_config.ScoringConfig` parameter. The module is self-contained (no ``scoring`` import)
to keep the module graph acyclic — ``scoring`` calls it.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .scoring_config import ScoringConfig

# Plain-language descriptions of each behavioral label (used by the UI + the headline read).
LABEL_BLURB: dict[str, str] = {
    "Steady Daily": "Lifts almost every day in consistent loads — plan around them as baseload.",
    "Variable Daily": "Lifts almost every day, but the load size jumps around.",
    "Erratic Daily": "Lifts most days, but volumes are all over the place.",
    "Steady Frequent": "Lifts several days a week in consistent loads — a dependable base.",
    "Variable Frequent": "Lifts several days a week, with swingy load sizes.",
    "Erratic Frequent": "Lifts often, but the volumes are erratic and hard to plan.",
    "Steady Intermittent": "Buys in predictable bursts — quiet between, but the rhythm is regular.",
    "Sporadic/Bursty": "Silent most days, then occasional large loads at irregular times — a buffer-risk burst buyer.",
    "Rare but Regular": "Lifts rarely, but on a clockwork rhythm and a consistent size.",
    "New / Sparse": "Too few buying days yet to read a daily pattern.",
}

# Frequency / size labels for the 2×2 behavioral map axes.
FREQUENCY_ORDER = ["daily", "frequent", "occasional", "rare"]
SIZE_ORDER = ["tight", "variable", "erratic", "unknown"]


# ---- tiny formatting helpers (kept local so the module imports nothing from scoring) ----
def _fmt_gal(x) -> str:
    if x is None:
        return "—"
    if abs(x) >= 1e6:
        return f"{x / 1e6:.1f}MM"
    if abs(x) >= 1e4:
        return f"{round(x / 1e3)}k"
    return f"{round(float(x)):,}"


def _plural(n, word: str) -> str:
    try:
        return word if int(round(n)) == 1 else word + "s"
    except (TypeError, ValueError):
        return word + "s"


def _fmt_every(gap_days) -> str:
    """A natural 'every N days / week / ~N weeks' phrase from a median inter-active-day gap."""
    if gap_days is None:
        return "irregularly"
    d = round(float(gap_days))
    if d <= 1:
        return "every day"
    if d <= 10:
        return f"every ~{d} days"
    if d <= 45:
        wk = round(d / 7.0)
        return f"about every {wk} {_plural(wk, 'week')}"
    mo = round(d / 30.0)
    return f"about every {mo} {_plural(mo, 'month')}"


# ---- descriptive statistics -----------------------------------------------------
def _nice_width(span: float) -> float | None:
    """A 1 / 2 / 2.5 / 5 × 10ᵏ bucket width giving ~8 buckets across the span (for the mode)."""
    if span <= 0:
        return None
    raw = span / 8.0
    mag = 10.0 ** math.floor(math.log10(raw)) if raw > 0 else 1.0
    for m in (1.0, 2.0, 2.5, 5.0, 10.0):
        if m * mag >= raw:
            return m * mag
    return 10.0 * mag


def _mode_bucket(vals: np.ndarray, cfg: ScoringConfig) -> dict | None:
    """The densest size bucket (the 'typical load' in plain terms) — bucketed, since exact gallons
    rarely repeat. Width is adaptive (∝ spread) with a configurable floor."""
    vals = np.asarray(vals, dtype=float)
    vals = vals[~np.isnan(vals)]
    if not len(vals):
        return None
    span = float(vals.max() - vals.min())
    width = max(_nice_width(span) or cfg.behavior_size_bucket_gallons, cfg.behavior_size_bucket_gallons)
    edges = np.floor(vals / width).astype(int)
    uniq, counts = np.unique(edges, return_counts=True)
    i = int(np.argmax(counts))
    lo = float(uniq[i]) * width
    return {"lo": round(lo, 1), "hi": round(lo + width, 1), "center": round(lo + width / 2, 1),
            "count": int(counts[i]), "width": round(width, 1)}


def _describe(vals: np.ndarray, cfg: ScoringConfig) -> dict | None:
    """Full descriptive stats: mean / median / mode(bucketed) / min / max / range / std / CV /
    P10·P50·P90 for a 1-D array."""
    vals = np.asarray(vals, dtype=float)
    vals = vals[~np.isnan(vals)]
    if not len(vals):
        return None
    mean = float(vals.mean())
    med = float(np.median(vals))
    sd = float(vals.std(ddof=1)) if len(vals) >= 2 else 0.0
    cv = (sd / mean) if mean > 0 else None
    p10, p50, p90 = (float(np.percentile(vals, q)) for q in (10, 50, 90))
    return {
        "n": int(len(vals)), "mean": round(mean, 1), "median": round(med, 1),
        "min": round(float(vals.min()), 1), "max": round(float(vals.max()), 1),
        "range": round(float(vals.max() - vals.min()), 1),
        "std": round(sd, 1), "cv": round(cv, 3) if cv is not None else None,
        "p10": round(p10, 1), "p50": round(p50, 1), "p90": round(p90, 1),
        "mode": _mode_bucket(vals, cfg),
    }


def _robust_gap_cv(gaps: np.ndarray) -> float | None:
    """MAD-based CV of inter-active-day gaps (timing regularity); robust to a single long silence."""
    gaps = np.asarray(gaps, dtype=float)
    gaps = gaps[~np.isnan(gaps)]
    if len(gaps) < 2:
        return None
    med = float(np.median(gaps))
    if med <= 0:
        m = float(gaps.mean())
        return float(gaps.std() / m) if m > 0 else None
    mad = float(np.median(np.abs(gaps - med)))
    cv = 1.4826 * mad / med
    if cv <= 0 and gaps.std() > 0:  # all gaps equal under MAD but some spread → use plain CV
        cv = float(gaps.std() / med)
    return cv


def _longest_run(mask: np.ndarray) -> int:
    """Longest run of consecutive True values (the longest silent stretch when mask = inactive)."""
    best = run = 0
    for v in np.asarray(mask, dtype=bool):
        run = run + 1 if v else 0
        best = max(best, run)
    return int(best)


# ---- classification -------------------------------------------------------------
def _frequency_class(active_rate: float, cfg: ScoringConfig) -> str:
    if active_rate >= cfg.behavior_freq_daily:
        return "daily"
    if active_rate >= cfg.behavior_freq_frequent:
        return "frequent"
    if active_rate >= cfg.behavior_freq_occasional:
        return "occasional"
    return "rare"


def _size_class(size_cv: float | None, cfg: ScoringConfig) -> str:
    if size_cv is None:
        return "unknown"
    if size_cv <= cfg.behavior_size_tight_cv:
        return "tight"
    if size_cv <= cfg.behavior_size_variable_cv:
        return "variable"
    return "erratic"


def _timing_class(gap_cv: float | None, cfg: ScoringConfig) -> str:
    if gap_cv is None:
        return "unknown"
    return "regular" if gap_cv <= cfg.behavior_regular_gap_cv else "irregular"


def _label(freq: str, size: str, timing: str, intermittent: bool, n_active: int,
           cfg: ScoringConfig) -> str:
    """Map (frequency × size-consistency), refined by timing regularity, to a plain headline label.

    The headline axes are FREQUENCY × SIZE-CONSISTENCY; timing regularity only disambiguates the
    intermittent quadrant (predictable bursts → "Steady Intermittent" vs unpredictable →
    "Sporadic/Bursty"), since with the same frequency+size two accounts still differ on whether
    their bursts are clockwork or random.
    """
    if n_active < cfg.behavior_min_active_days:
        return "Sporadic/Bursty" if intermittent else "New / Sparse"
    if freq == "daily":
        return {"tight": "Steady Daily", "variable": "Variable Daily",
                "erratic": "Erratic Daily"}.get(size, "Steady Daily")
    if freq == "frequent":
        return {"tight": "Steady Frequent", "variable": "Variable Frequent",
                "erratic": "Erratic Frequent"}.get(size, "Steady Frequent")
    if freq == "occasional":
        if timing == "regular" and size != "erratic":
            return "Steady Intermittent"
        return "Sporadic/Bursty"
    # rare
    if size == "tight" and timing == "regular":
        return "Rare but Regular"
    return "Sporadic/Bursty"


# ---- one window's stats ---------------------------------------------------------
def _window_stats(net_arr: np.ndarray, cnt_arr: np.ndarray, idx: pd.DatetimeIndex,
                  window: str, cfg: ScoringConfig) -> dict:
    """All presence + size + naive-all-days stats and the classification for ONE calendar window.

    ``net_arr`` / ``cnt_arr`` are the per-calendar-day net-gallons and lift-count over ``idx`` (zeros
    on silent days included). Presence is read over ALL days; size-when-present over ACTIVE days only.
    """
    n_days = len(idx)
    active = cnt_arr > 0
    n_active = int(active.sum())
    n_lifts = int(cnt_arr.sum())
    active_rate = (n_active / n_days) if n_days else 0.0
    weeks = (n_days / 7.0) if n_days else 0.0

    # ---- presence / frequency (zeros are data) ----
    active_pos = np.flatnonzero(active)
    gaps = np.diff(active_pos).astype(float) if len(active_pos) >= 2 else np.array([])
    median_gap = float(np.median(gaps)) if len(gaps) else None
    gap_cv = _robust_gap_cv(gaps) if len(gaps) >= 2 else None
    longest_silent = _longest_run(~active)
    presence = {
        "active_day_rate": round(active_rate, 4),
        "n_active_days": n_active, "n_days": n_days,
        "median_gap_days": round(median_gap, 1) if median_gap is not None else None,
        "gap_cv": round(gap_cv, 3) if gap_cv is not None else None,
        "longest_silent_days": int(longest_silent),
        "lifts_per_week": round(n_lifts / weeks, 2) if weeks else None,
        "active_days_per_week": round(n_active / weeks, 2) if weeks else None,
    }

    # ---- size-when-present (active days only) ----
    size = _describe(net_arr[active], cfg) if n_active else None
    size_cv = size["cv"] if size else None

    # ---- naive all-days (incl. zeros) + the misleading-average detector ----
    # The literal spec: an all-days MEDIAN of 0 with an all-days MEAN > 0 means most days are silent
    # yet they do buy — so the naive daily average is a smear of "they never lift that". This is true
    # for any sub-half-the-days buyer; the SEVERITY below scales how dangerous treating them as a
    # daily rate is (a once-a-month marine parcel is far more misleading than an every-3-days ratable).
    all_days = _describe(net_arr, cfg)
    intermittent = bool(
        all_days is not None and n_days >= cfg.behavior_intermittent_min_days and n_active >= 1
        and all_days["median"] <= 0 < all_days["mean"])

    # ---- classification ----
    freq_class = _frequency_class(active_rate, cfg)
    enough = n_active >= cfg.behavior_min_active_days
    size_class = _size_class(size_cv, cfg) if enough else "unknown"
    timing_class = _timing_class(gap_cv, cfg) if (enough and gap_cv is not None) else "unknown"
    label = _label(freq_class, size_class, timing_class, intermittent, n_active, cfg)
    # high severity = a genuine buffer-risk burst buyer (silent most days, then a big load); moderate
    # = a chunky-but-frequent buyer whose daily average still smears. Drives whether the loud
    # "their X/day average is misleading" callout fires.
    misleading_severity = (("high" if freq_class in ("occasional", "rare") else "moderate")
                           if intermittent else None)

    # ---- daily bars for the chart (capped from the end so 'all' stays drawable) ----
    b0 = max(0, n_days - cfg.behavior_max_bar_days)
    bars = [{"date": str(pd.Timestamp(idx[i]).date()), "gallons": round(float(net_arr[i]), 1),
             "lifts": int(cnt_arr[i])} for i in range(b0, n_days)]

    return {
        "window": window, "n_days": n_days, "n_lifts": n_lifts, "n_active_days": n_active,
        "presence": presence, "size_when_present": size, "all_days": all_days,
        "intermittent": intermittent, "misleading_average": intermittent,
        "misleading_severity": misleading_severity,
        "frequency_class": freq_class, "size_class": size_class, "timing_class": timing_class,
        "label": label, "label_blurb": LABEL_BLURB.get(label, ""), "bars": bars,
    }


# ---- plain-English headline -----------------------------------------------------
def _presence_phrase(s: dict) -> str:
    pres = s["presence"]
    fc = s["frequency_class"]
    adpw = pres["active_days_per_week"] or 0.0
    if fc == "daily":
        return "lifts almost every day"
    if fc == "frequent":
        return f"lifts ~{max(1, round(adpw))} days a week"
    if fc == "occasional":
        if adpw >= 1.0:
            return f"lifts ~{max(1, round(adpw))} {_plural(round(adpw), 'day')} a week"
        return f"lifts {_fmt_every(pres['median_gap_days'])}"
    return f"lifts only {_fmt_every(pres['median_gap_days'])}"


def _size_phrase(s: dict) -> str:
    size = s["size_when_present"]
    if not size:
        return "in varying amounts"
    when = " when they do" if s["frequency_class"] in ("occasional", "rare") else " a load"
    med = size["median"]
    sd = size["std"] or 0.0
    if s["size_class"] == "tight" or (sd and med and sd / med <= 0.35):
        return f"~{_fmt_gal(med)}{when} (±{_fmt_gal(sd)})" if sd else f"~{_fmt_gal(med)}{when}"
    # variable / erratic: a range reads more honestly than mean ± σ
    return f"anywhere {_fmt_gal(size['p10'])}–{_fmt_gal(size['p90'])}{when}"


def _consistency_phrase(s: dict) -> str:
    return {"tight": "very consistent", "variable": "size swings a fair bit",
            "erratic": "wildly varying sizes", "unknown": ""}.get(s["size_class"], "")


def _closing_phrase(label: str) -> str:
    return {
        "Steady Daily": "Plan around them as baseload.",
        "Steady Frequent": "A dependable base you can plan around.",
        "Steady Intermittent": "Predictable bursts — pre-stage for their rhythm.",
        "Sporadic/Bursty": "A buffer-risk burst buyer — keep headroom, don't plan a daily rate.",
        "Rare but Regular": "Rare but clockwork — easy to anticipate.",
        "Erratic Frequent": "Frequent but choppy — watch the swings.",
        "Variable Frequent": "Frequent, but size your buffer for the swings.",
        "Variable Daily": "Daily, but size varies — buffer accordingly.",
        "Erratic Daily": "Daily but volatile — hold extra headroom.",
        "New / Sparse": "Too new to plan around yet.",
    }.get(label, "")


def _headline(name: str | None, s: dict, cfg: ScoringConfig) -> str:
    """One non-technical sentence an ops person reads and immediately 'gets', with the
    misleading-average callout fired loudly for an intermittent account."""
    nm = name or "This account"
    label = s["label"]
    if s["n_active_days"] < 1:
        return f"{nm} — no lifts in this window."
    parts = [f"{nm} — {label}:", _presence_phrase(s) + ",", _size_phrase(s)]
    cons = _consistency_phrase(s)
    sentence = " ".join(parts)
    if cons:
        sentence += f", {cons}"
    sentence += "."
    sev = s.get("misleading_severity")
    avg = (s["all_days"] or {}).get("mean")
    typ = (s["size_when_present"] or {}).get("median")
    if sev == "high":
        sentence += (f" Their {_fmt_gal(avg)}/day average is misleading — they never lift that; "
                     f"they lift 0 most days, then ~{_fmt_gal(typ)} when they buy.")
    elif sev == "moderate":
        sentence += (f" (Their ~{_fmt_gal(avg)}/day average smears a ~{_fmt_gal(typ)} load — "
                     f"they buy in chunks, not a daily trickle.)")
    close = _closing_phrase(label)
    if close:
        sentence += " " + close
    return sentence


def _presence_lane(s: dict) -> dict:
    """The PRESENCE-AWARE restatement of the lane: an intermittent customer's 'lane' is their
    active-day size + their frequency, NOT a smeared daily average. This reframes the VAR lane in
    presence terms (it does not change the VAR score)."""
    size = s["size_when_present"]
    pres = s["presence"]
    fc = s["frequency_class"]
    typical = size["median"] if size else None
    lo = size["p10"] if size else None
    hi = size["p90"] if size else None
    if fc in ("daily", "frequent"):
        freq_phrase = "almost every day" if fc == "daily" else f"~{max(1, round(pres['active_days_per_week'] or 1))} days/week"
    elif fc == "occasional":
        adpw = pres["active_days_per_week"] or 0.0
        freq_phrase = (f"~{max(1, round(adpw))} {_plural(round(adpw), 'day')}/week"
                       if adpw >= 1 else _fmt_every(pres["median_gap_days"]))
    else:
        freq_phrase = _fmt_every(pres["median_gap_days"])
    naive = (s["all_days"] or {}).get("mean")
    sentence = (f"Their lane is ~{_fmt_gal(typical)} on the days they lift ({freq_phrase}), "
                f"not a {_fmt_gal(naive)}/day average.") if typical is not None else None
    return {
        "active_day_size_typical": typical, "active_day_size_lo": lo, "active_day_size_hi": hi,
        "frequency_phrase": freq_phrase, "naive_daily_average": naive, "sentence": sentence,
    }


# ---- main entry: one customer's daily presence-aware profile ---------------------
def daily_profile(cl: pd.DataFrame, cfg: ScoringConfig, as_of: pd.Timestamp,
                  name: str | None = None) -> dict:
    """Daily presence-aware behavioral profile for ONE (master) customer's full lift history.

    Computed over rolling calendar windows (``cfg.behavior_windows`` + ``"all"``), each anchored to
    the last data date (``as_of``) and clipped at the customer's first active day so a brand-new
    account isn't charged for days before it existed. Returns the full per-window stats (presence +
    size-when-present + naive all-days + classification + daily bars), the headline label/read taken
    from a primary window, and the presence-aware lane restatement.
    """
    out_unavailable = {"available": False, "windows": {}, "primary_window": None, "label": None,
                       "frequency_class": None, "size_class": None, "intermittent": False,
                       "misleading_average": False, "headline": None, "presence_lane": None}
    if cl is None or not len(cl) or as_of is None:
        return out_unavailable

    dts = pd.to_datetime(cl["lift_datetime"], errors="coerce")
    net = pd.to_numeric(cl["net_gallons"], errors="coerce")
    keep = dts.notna()
    dts, net = dts[keep], net[keep].fillna(0.0)
    if not len(dts):
        return out_unavailable

    day = dts.dt.normalize()
    by_net = net.groupby(day).sum()
    by_cnt = net.groupby(day).size()
    first_active = pd.Timestamp(by_net.index.min())
    end = pd.Timestamp(as_of).normalize()

    def grid(window: str):
        if window == "all":
            start = first_active
        else:
            start = max(end - pd.Timedelta(days=int(window) - 1), first_active)
        if start > end:
            start = end
        idx = pd.date_range(start, end, freq="D")
        net_arr = by_net.reindex(idx, fill_value=0.0).to_numpy(dtype=float)
        cnt_arr = by_cnt.reindex(idx, fill_value=0).to_numpy(dtype=float)
        return net_arr, cnt_arr, idx

    window_keys = [str(w) for w in cfg.behavior_windows] + ["all"]
    windows: dict[str, dict] = {}
    for w in window_keys:
        net_arr, cnt_arr, idx = grid(w)
        stats = _window_stats(net_arr, cnt_arr, idx, w, cfg)
        # make each window self-describing so the detail's 7/30/90/all toggle stays consistent
        stats["headline"] = _headline(name, stats, cfg)
        stats["presence_lane"] = _presence_lane(stats)
        windows[w] = stats

    # primary window for the headline: the configured one, falling back to wider windows when the
    # configured window is too thin to read (so a sparse account is classified on what data exists).
    order, seen = [], set()
    for w in [cfg.behavior_primary_window, "90", "all", "30", "7"]:
        if w in windows and w not in seen:
            order.append(w)
            seen.add(w)
    primary = next((w for w in order if windows[w]["n_active_days"] >= cfg.behavior_min_active_days),
                   "all")
    head = windows[primary]

    return {
        "available": True,
        "primary_window": primary,
        "windows": windows,
        "label": head["label"], "label_blurb": LABEL_BLURB.get(head["label"], ""),
        "frequency_class": head["frequency_class"], "size_class": head["size_class"],
        "timing_class": head["timing_class"],
        "intermittent": head["intermittent"], "misleading_average": head["misleading_average"],
        "misleading_severity": head.get("misleading_severity"),
        "headline": head["headline"],
        "presence_lane": head["presence_lane"],
    }


def slim_behavior(b: dict | None) -> dict | None:
    """The small behavioral summary the ranked table + the 2×2 map need (drops the heavy per-window
    bars/stats, keeps just the headline axes from the primary window)."""
    if not b or not b.get("available"):
        return {"available": False} if b is not None else None
    primary = b.get("primary_window")
    head = (b.get("windows") or {}).get(primary, {})
    pres = head.get("presence") or {}
    size = head.get("size_when_present") or {}
    alld = head.get("all_days") or {}
    return {
        "available": True, "primary_window": primary, "label": b.get("label"),
        "label_blurb": b.get("label_blurb"),
        "frequency_class": b.get("frequency_class"), "size_class": b.get("size_class"),
        "intermittent": b.get("intermittent"), "misleading_average": b.get("misleading_average"),
        "misleading_severity": head.get("misleading_severity"),
        "headline": b.get("headline"),
        "active_day_rate": pres.get("active_day_rate"),
        "active_days_per_week": pres.get("active_days_per_week"),
        "median_gap_days": pres.get("median_gap_days"),
        "longest_silent_days": pres.get("longest_silent_days"),
        "size_median_active": size.get("median"), "size_cv_active": size.get("cv"),
        "all_days_mean": alld.get("mean"), "all_days_median": alld.get("median"),
    }
