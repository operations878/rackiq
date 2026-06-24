"""Credit & Account-Risk configuration — every weight/threshold/norm is a parameter here.

The credit engine (``credit.py``) takes a :class:`CreditConfig`; nothing is hard-coded in the
math. ``CreditConfig.with_overrides({...})`` produces a tweaked copy (used by
``POST /api/credit/recompute`` so an analyst can re-tune the score weights / conversion
profile without a code change). Mirrors :mod:`scoring_config` and :mod:`reconciliation_config`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace


@dataclass(frozen=True)
class CreditConfig:
    # ---- Credit-risk score (0–100, higher = SAFER) ------------------------------
    # A raw "safety" value is built from five risk components (each normalized to 0–1),
    # then the headline credit score is the *percentile rank* of that safety across the
    # active book — exactly like the VAR sub-scores, for cross-module consistency.
    cr_w_pct_late: float = 0.25          # share of bills paid (or sitting) late
    cr_w_avg_days_late: float = 0.25     # how late, in days, on average
    cr_w_utilization: float = 0.20       # open exposure ÷ credit limit
    cr_w_dso_excess: float = 0.15        # collection period beyond terms
    cr_w_trend: float = 0.15             # worsening pay behavior over time

    # Normalizers turning a raw component into a 0–1 penalty (value that maps the
    # component to full penalty). Higher norm ⇒ more forgiving.
    days_late_norm: float = 30.0         # 30 days late ⇒ full late-days penalty
    utilization_norm: float = 1.0        # at/over the credit limit ⇒ full util penalty
    dso_excess_norm: float = 30.0        # paying 30d past terms ⇒ full DSO penalty
    trend_norm: float = 20.0             # +20d slower (recent vs early) ⇒ full trend penalty

    # ---- Grades (on the 0–100 credit score) -------------------------------------
    grade_a: float = 80.0
    grade_b: float = 60.0
    grade_c: float = 40.0

    # ---- Account-risk map (VAR × credit) quadrant split -------------------------
    # Cells are split at the book median of each axis (so all four populate), unless a
    # fixed cut is preferred.
    quadrant_split: str = "median"       # "median" | "fixed"
    var_fixed_cut: float = 60.0
    credit_fixed_cut: float = 60.0

    # ---- Conversion targeting (spot → ratable term) -----------------------------
    # Target profile = high volume + erratic (low VAR) + price-elastic + acceptable credit.
    conv_w_volume: float = 0.35          # weight on volume percentile
    conv_w_erratic: float = 0.35         # weight on (100 − VAR): the more erratic, the bigger the prize
    conv_w_elastic: float = 0.30         # weight on price-sensitivity percentile
    conv_credit_floor: float = 40.0      # min credit score to be a *conversion* target (gate)
    conv_var_ceiling: float = 75.0       # already-steady accounts (VAR ≥ this) are not "conversion" plays
    conv_min_volume_pct: float = 40.0    # ignore tiny accounts — not worth a term conversation

    # ---- Grow-me (steady, growing, good credit) ---------------------------------
    grow_w_var: float = 0.40
    grow_w_trend: float = 0.35
    grow_w_credit: float = 0.25
    grow_min_trend_pct: float = 5.0      # must be visibly growing
    grow_credit_floor: float = 55.0

    # ---- Revenue-at-risk (a good account fading) --------------------------------
    rar_min_fade_pct: float = 8.0        # volume trend ≤ −this ⇒ "fading"
    rar_min_base_value: float = 45.0     # only flag accounts that are actually worth keeping

    def to_dict(self) -> dict:
        return asdict(self)

    def with_overrides(self, overrides: dict | None) -> "CreditConfig":
        if not overrides:
            return self
        known = set(self.__dataclass_fields__)  # type: ignore[attr-defined]
        clean = {k: v for k, v in overrides.items() if k in known}
        return replace(self, **clean)


DEFAULT_CONFIG = CreditConfig()


def grade(score: float | None, cfg: CreditConfig) -> str | None:
    if score is None:
        return None
    if score >= cfg.grade_a:
        return "A"
    if score >= cfg.grade_b:
        return "B"
    if score >= cfg.grade_c:
        return "C"
    return "D"


# Account-risk quadrant labels. x = VAR (supply/variability risk), y = credit (financial risk).
# Higher VAR = steadier supply; higher credit = pays well. Four cells:
QUADRANTS = {
    ("hi", "hi"): "Anchor",         # steady + pays — protect
    ("hi", "lo"): "Watch – Credit",  # steady volume but slow-pay — tighten terms
    ("lo", "hi"): "Watch – Supply",  # pays well but erratic — convert / forecast-proof
    ("lo", "lo"): "Danger",          # erratic + slow-pay — gate exposure
}

QUADRANT_ORDER = ["Anchor", "Watch – Supply", "Watch – Credit", "Danger"]
