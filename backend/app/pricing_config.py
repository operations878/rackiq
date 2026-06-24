"""Pricing configuration — every weight, grid, and threshold for the Pricing Sandbox +
Pricing Engine (Blueprint I) is a parameter here.

The pricing engine (``pricing.py``) takes a :class:`PricingConfig`; nothing is hard-coded in
the math. ``PricingConfig.with_overrides({...})`` produces a tweaked copy (used by
``POST /api/pricing/recompute`` so an analyst can re-tune the spread grid, the shadow-price
schedule, or the acceptance-model priors without a code change). Mirrors ``scoring_config`` /
``reconciliation_config`` / ``demand.DemandConfig``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace


@dataclass(frozen=True)
class PricingConfig:
    # ---- Sandbox spread grid (our posted rack vs. the street/OPIS benchmark, $/gal) ----
    # The slider moves a single book-wide spread; the engine evaluates total margin across it.
    spread_min: float = -0.08
    spread_max: float = 0.08
    spread_step: float = 0.0025

    # ---- Engine price-search grid (candidate quote price, relative to the reference) -----
    price_search_min: float = -0.10       # how far below the rack reference we will search
    price_search_max: float = 0.18        # …and above
    price_search_step: float = 0.0025

    # ---- Acceptance model (logistic fit from the quote log) ----------------------------
    # P(accept) = logistic(a + b·price_spread + c·customer_features + d·regime), fit per
    # archetype SEGMENT where there is enough data, else pooled, else an elasticity proxy.
    min_quotes_segment: int = 60          # quotes needed to fit a per-archetype model
    min_quotes_global: int = 25           # quotes needed to fit the pooled fallback model
    logit_l2: float = 2.0                 # ridge penalty (stabilizes near-separable fits)
    logit_max_iter: int = 30
    default_accept_rate: float = 0.70     # baseline P(accept) at the reference (no quote data)
    accept_floor: float = 0.02
    accept_ceil: float = 0.985
    # Regime → which inventory/capacity states tighten the acceptance model's d·regime term.
    inv_tight_states: tuple[str, ...] = ("tight", "short", "tank_constrained")
    cap_tight_states: tuple[str, ...] = ("tight", "constrained")

    # ---- Elasticity proxy (no/thin quote log) → linear accept-prob in the spread --------
    # accept_prob(spread) = clamp(baseline + beta·(spread − s0)). beta is P3's accept-incidence
    # slope ($/gal); when a customer has none we use the book-median (or this default).
    proxy_beta_default: float = -3.0
    vol_ratio_floor: float = 0.10         # clamp counterfactual volume swing to a sane band…
    vol_ratio_ceil: float = 3.00          # …so a near-zero baseline accept can't explode it

    # ---- Shadow price of the binding constraint ($/gal) --------------------------------
    # The opportunity cost of one gallon under today's regime. POSITIVE when supply/capacity is
    # binding (each gallon is scarce — never discount); ~0 or negative when product is long.
    # rec_price floor = cost + shadow_price; and when shadow_price > 0 we never post a discount
    # below the street reference. Summed across the inventory + capacity axes, then clamped.
    shadow_price_by_inventory: dict = field(default_factory=lambda: {
        "long": -0.010, "balanced": 0.0, "tight": 0.030, "tank_constrained": -0.020})
    shadow_price_by_capacity: dict = field(default_factory=lambda: {
        "ample": -0.005, "normal": 0.0, "constrained": 0.015})
    shadow_price_min: float = -0.03
    shadow_price_max: float = 0.08

    # ---- Volume / GP -------------------------------------------------------------------
    annual_weeks: float = 52.0
    forecast_horizon_weeks: int = 13      # P5 horizon → annualize horizon_p50 × (52/13)
    min_annual_gallons: float = 1.0       # below this a customer is dropped from the sandbox

    # ---- Price-driven vs. captive classification ---------------------------------------
    # price-driven = strong negative β (high |β| percentile) AND thin margin; captive = β≈0.
    price_driven_beta_pctl: float = 60.0  # |β| percentile at/above ⇒ price-responsive
    captive_beta_pctl: float = 30.0       # |β| percentile at/below ⇒ captive
    thin_margin_pctl: float = 45.0        # margin/gal percentile below ⇒ "thin margin"

    # ---- Recommendation surfacing -------------------------------------------------------
    underpriced_min_gap: float = 0.003    # rec_price − current_price above this ⇒ "underpriced"
    recent_days_for_price: int = 120      # window for a customer's current realized price/cost

    def to_dict(self) -> dict:
        return asdict(self)

    def with_overrides(self, overrides: dict | None) -> "PricingConfig":
        if not overrides:
            return self
        known = set(self.__dataclass_fields__)  # type: ignore[attr-defined]
        clean = {k: v for k, v in overrides.items() if k in known}
        return replace(self, **clean)


DEFAULT_CONFIG = PricingConfig()


def shadow_price(regime: dict[str, str] | None, cfg: PricingConfig) -> float:
    """The $/gal shadow price of the binding constraint for a regime (inventory + capacity).

    Positive ⇒ a gallon is scarce today (don't discount). Clamped to the config band.
    """
    regime = regime or {}
    inv = cfg.shadow_price_by_inventory.get(regime.get("inventory", "balanced"), 0.0)
    cap = cfg.shadow_price_by_capacity.get(regime.get("capacity", "normal"), 0.0)
    return max(cfg.shadow_price_min, min(cfg.shadow_price_max, inv + cap))
