"""Scoring configuration — every weight, threshold, and window is a parameter here.

The scoring engine (``scoring.py``) takes a :class:`ScoringConfig`; nothing is hard-coded in
the math. ``ScoringConfig.with_overrides({...})`` produces a tweaked copy (used by the
``/api/scores/recompute`` endpoint so an analyst can re-weight without a code change).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace


# Rolling windows (in days) plus "all" for all-time. Every score is computed per window.
WINDOWS = ["30", "90", "365", "all"]


@dataclass(frozen=True)
class ScoringConfig:
    # ---- VAR (variability) lane model -------------------------------------------
    # NOTE: this VAR is the *variability* score — distinct from the financial VaR in any
    # price-risk module. Higher = steadier.
    var_w_in_band: float = 0.45
    var_w_tightness: float = 0.35
    var_w_excursion: float = 0.20
    var_blend_volume: float = 0.70        # headline VAR = vol*0.70 + cadence*0.30
    var_blend_cadence: float = 0.30
    base_range_mode: str = "sigma"        # "sigma" (base ± k·σ) | "percent" (base ± p%)
    base_range_sigma_k: float = 1.0       # base range half-width in robust σ
    base_range_pct: float = 0.20          # base range half-width in percent mode
    variability_sigma_k: float = 2.0      # variability band half-width in robust σ
    # Sufficiency guard for a VAR score.
    var_min_lifts: int = 8
    var_min_weeks: int = 12

    # ---- VAR advanced statistics (diagnostics only — these NEVER change the score) ----
    # The headline VAR formula above is frozen; these parameters drive the transparency /
    # statistics layer (bootstrap CI on base volume, steadiness drift test, trend test).
    var_bootstrap_iters: int = 400        # residual-bootstrap resamples for the base-volume CI
    var_bootstrap_ci: float = 0.90        # central CI mass reported for the base volume
    var_steadiness_min_periods: int = 6   # need this many periods to call a steadiness trend
    var_steadiness_delta_band: float = 0.10   # |Δ in-band rate| below this ⇒ "steady"
    var_trend_sig_p: float = 0.10         # Mann-Kendall / drift p-value significance threshold

    # ---- Forward projection (VAR → forecast) -----------------------------------
    # The forecast is a real per-customer engine (``forecasting.py``): multiple candidate models
    # are backtested walk-forward and the lowest-error one is selected PER CUSTOMER, seasonality
    # is preserved on sparse history, the band comes from that customer's OWN backtest error, and
    # every horizon is anchored to TODAY (not the last data date). NOTHING here touches the VAR score.
    forecast_horizons: tuple = (7, 30, 90)    # days projected forward (per-customer + book)
    forecast_band_z: float = 1.0          # band half-width in σ (1.0 ≈ a 68% "likely" range)
    forecast_max_horizon_days: int = 90   # how far the forward lane continuation is drawn
    forecast_rough_rel: float = 0.45      # band half-width ÷ expected ≥ this ⇒ flag as a ROUGH
    #                                       forecast (honest "wide lane — treat as a range")

    # Model selection (walk-forward backtest; counted in *active*, non-zero periods).
    forecast_min_periods: int = 4             # below this active count → flat only
    forecast_min_seasonal_months: int = 8     # distinct months-of-year needed for the seasonal model
    forecast_min_holt_periods: int = 8        # at/above (+ active frac) → eligible for the trend model
    forecast_min_holt_active_frac: float = 0.30
    forecast_seasonal_period_weeks: int = 52  # weekly seasonal cycle for Holt-Winters
    forecast_min_seasonal_cycles: float = 2.0 # need ≥ this many full cycles for Holt-Winters seasonal
    forecast_recent_level_periods: int = 8    # periods averaged for the seasonal model's recent level
    forecast_shrink_model_max: float = 0.85   # reliability shrinkage: max weight on the model path
    forecast_shrink_model_min: float = 0.35   # min weight on the model (poor backtest → shrink to level)
    forecast_recency_window: int = 12         # periods in the recency-weighted average
    forecast_recency_halflife: float = 4.0    # exponential half-life (periods) for recency weighting
    forecast_select_tol_pct: float = 2.0      # MAPE-pt tolerance for the model-character tie-break
    forecast_clockwork_cv: float = 0.35       # cadence CV at/below this ⇒ prefer the cadence model
    forecast_seasonal_pref_strength: float = 0.45  # seasonal strength ≥ this ⇒ prefer the seasonal model
    forecast_low_pred_skill: float = 0.0      # selected must beat naive by > this skill, else low-pred

    # Backtest-driven uncertainty (the honest, per-customer band).
    forecast_backtest_steps: int = 10         # expanding one-step over the last N periods
    forecast_backtest_min_train: int = 6
    forecast_mape_floor_gallons: float = 500.0  # MAPE denominator floor (a near-zero period can't blow it up)
    forecast_rel_sigma_default: float = 0.40  # relative-error σ when a customer can't be backtested
    forecast_rel_sigma_floor: float = 0.08
    forecast_rel_sigma_band_cap: float = 1.25 # cap the band σ (a 3000%-MAPE marine account would
    #                                           otherwise show an absurd ±10MM band) — the honest MAPE
    #                                           still shows in the text; the band stays wide-but-sane
    forecast_sigma_floor_gallons: float = 200.0
    forecast_sigma_growth_cap: int = 8        # √h band growth plateaus after this many periods
    forecast_var_band_lambda: float = 0.5     # VAR weighting: σ ×= 1 + λ·(1 − VAR/100)
    forecast_var_default: float = 50.0        # VAR used when a customer is too thin to score

    # Today-anchoring + recency gap.
    forecast_gap_note_days: int = 10          # surface the data-recency note when ≥ this many days behind
    forecast_recency_overdue_mult: float = 1.5  # silent past this × cadence ⇒ damp + widen (slowdown/churn)
    forecast_recency_damp_min: float = 0.35   # floor on the recency damping factor
    forecast_recency_damp_k: float = 0.5      # damping slope per unit of overdue-ness

    # ---- Excursion (lane-break) weather pattern --------------------------------
    excursion_min_breaks: int = 3         # need this many lane breaks to call a weather pattern
    excursion_pattern_share: float = 0.6  # ≥ this share of breaks on snap weeks ⇒ a pattern
    weather_snap_quantile: float = 0.70   # a period is a cold-snap/hot-spell at/above this HDD/CDD quantile

    # ---- VAR trend over time (tightening / widening) ---------------------------
    # Re-fit the lane at an earlier as-of and compare the VAR score: is the lane tightening
    # (more reliable) or widening (becoming a problem)? Drives the home-page "movers" list.
    var_trend_lookback_days: int = 365    # trailing window each trend point is scored over
    var_trend_month_days: int = 30        # "this month vs prior" shift
    var_trend_quarter_days: int = 90      # "this quarter vs prior" shift
    var_trend_move_band: float = 3.0      # |ΔVAR| below this ⇒ "steady" (else tighten/widen)

    # ---- Daily presence-aware behavioral profile (ENRICHES VAR; never changes the score) ----
    # The core move: split PRESENCE (how often they buy, over ALL calendar days incl. zeros) from
    # SIZE-WHEN-PRESENT (only the days they actually lift). This fixes the "average hides the
    # pattern" problem — a steady daily buyer and a silent-then-spiky buyer can share a weekly total
    # but look obviously different here. Computed at DAILY resolution per master customer over rolling
    # calendar windows, anchored to the last data date. NOTHING here touches the VAR lane score.
    behavior_windows: tuple = (7, 30, 90)     # calendar-day windows the profile is computed over ("all" always added)
    behavior_primary_window: str = "30"       # window the headline label/read is taken from (falls back if too thin)
    behavior_min_active_days: int = 3         # need this many active days to read size-consistency / timing
    behavior_intermittent_min_days: int = 7   # window must span ≥ this many days for the intermittency flag to mean anything
    # Frequency bucket on the active-day rate (share of calendar days with ≥1 lift).
    behavior_freq_daily: float = 0.6          # ≥ ⇒ "daily" (lifts most days — baseload)
    behavior_freq_frequent: float = 0.30      # ≥ ⇒ "frequent" (several days a week, ~3+); below
    #                                           this a twice-a-week burst buyer reads "occasional"
    behavior_freq_occasional: float = 0.08    # ≥ ⇒ "occasional" (once/twice a week); below ⇒ "rare"
    # Size-consistency bucket on the coefficient of variation of ACTIVE-day sizes.
    behavior_size_tight_cv: float = 0.25      # ≤ ⇒ "tight" (very consistent load size)
    behavior_size_variable_cv: float = 0.60   # ≤ ⇒ "variable"; above ⇒ "erratic"
    # Timing regularity (robust CV of gaps between active days) — separates predictable bursts
    # ("Steady Intermittent") from genuinely sporadic ones ("Sporadic/Bursty").
    behavior_regular_gap_cv: float = 0.50     # ≤ ⇒ "regular" cadence; above ⇒ "irregular"
    behavior_size_bucket_gallons: float = 5000.0   # mode-bucket width floor (adaptive above this)
    behavior_max_bar_days: int = 120          # cap the daily-bar series length (the "all" window) for the chart

    # ---- Data sufficiency (is an account "established"?) ------------------------
    suff_min_lifts: int = 12
    suff_min_days: int = 90

    # ---- Period grain ----------------------------------------------------------
    # Accounts whose median inter-lift gap exceeds this go to monthly buckets.
    monthly_gap_threshold_days: float = 20.0

    # ---- Grades ----------------------------------------------------------------
    grade_a: float = 80.0
    grade_b: float = 60.0
    grade_c: float = 40.0

    # ---- Base Value (Layer 3) --------------------------------------------------
    bv_w_rfap: float = 0.50
    bv_w_profit_constraint: float = 0.30  # weight on profit-per-(default constraint)
    bv_w_strategic: float = 0.20
    default_constraint: str = "rackhour"  # which profit_per_* is the binding constraint
    strategic_uplift_min: float = 0.8
    strategic_uplift_max: float = 1.5

    # Friction cost model ($ per event, annualized via event rates).
    friction_cost_small_order: float = 120.0
    friction_cost_rush: float = 250.0
    friction_cost_split: float = 90.0
    friction_cost_special_handling: float = 150.0
    friction_cost_wait: float = 60.0
    friction_cost_paperwork: float = 40.0
    small_order_gallons: float = 1500.0   # below this an order is "small" (friction)
    rush_gap_days: float = 1.0            # a lift within this of the prior = "rush"
    hours_per_order: float = 1.5          # rack-hours consumed per order (for per-rackhour)

    # Credit cost model.
    cost_of_capital: float = 0.10         # annual
    pd_base: float = 0.01                 # baseline probability of default
    pd_late_multiplier: float = 0.15      # extra PD per unit late-rate

    # ---- Discount efficiency ---------------------------------------------------
    discount_delta: float = 0.02          # the δ¢/gal cut modeled ($/gal)

    # ---- Churn risk ------------------------------------------------------------
    churn_w_recency: float = 0.50
    churn_w_neg_trend: float = 0.30
    churn_w_accept_decline: float = 0.20
    churn_recency_cadence_mult: float = 3.0   # recency gap normalized by this × base cadence

    # ---- Quote score -----------------------------------------------------------
    quote_w_accept: float = 0.45
    quote_w_negotiate: float = 0.20
    quote_w_latency: float = 0.20
    quote_w_lowest_only: float = 0.15
    quote_latency_norm_min: float = 1440.0    # minutes that maps latency → 0 (a day)

    # ---- Account Value ---------------------------------------------------------
    # Account Value Score = normalize(volume × margin × VAR/100).

    # ---- Archetype classifier --------------------------------------------------
    archetype_ambiguous_gap: float = 0.03     # top1 - top2 below this ⇒ flag for review

    def to_dict(self) -> dict:
        return asdict(self)

    def with_overrides(self, overrides: dict | None) -> "ScoringConfig":
        if not overrides:
            return self
        known = set(self.__dataclass_fields__)  # type: ignore[attr-defined]
        clean = {k: v for k, v in overrides.items() if k in known}
        return replace(self, **clean)


DEFAULT_CONFIG = ScoringConfig()


def grade(score: float | None, cfg: ScoringConfig) -> str | None:
    if score is None:
        return None
    if score >= cfg.grade_a:
        return "A"
    if score >= cfg.grade_b:
        return "B"
    if score >= cfg.grade_c:
        return "C"
    return "D"


# Standing posture (pricing / terms / allocation) each archetype triggers.
ARCHETYPE_POSTURE: dict[str, dict[str, str]] = {
    "Anchor Base-Load": {
        "pricing": "Defend with contract pricing; small loyalty spread.",
        "terms": "Standard terms; reward reliability.",
        "allocation": "Protect supply first — this is your floor."},
    "Flex Buyer": {
        "pricing": "Dynamic to rack; capture upside when they lean in.",
        "terms": "Standard terms.",
        "allocation": "Serve after anchors; flexible."},
    "Premium Spot": {
        "pricing": "Hold premium — they pay for availability, not price.",
        "terms": "Prepay / tight terms acceptable.",
        "allocation": "Fill from surplus at a premium."},
    "Price Shopper": {
        "pricing": "Quote thin only to fill troughs; never chase.",
        "terms": "Prepay; minimize credit.",
        "allocation": "Lowest priority; surplus only."},
    "Surplus Absorber": {
        "pricing": "Clearing price for length; protects working capital.",
        "terms": "Prepay preferred.",
        "allocation": "Use to drain long inventory."},
    "Scarcity Buyer": {
        "pricing": "Premium in tight markets; they buy on availability.",
        "terms": "Tighter terms when short.",
        "allocation": "Ration in scarcity; monetize."},
    "Weather-Triggered": {
        "pricing": "Pre-season hedge offers; premium in cold snaps.",
        "terms": "Seasonal credit watch.",
        "allocation": "Pre-build for HDD spikes."},
    "Credit Drag": {
        "pricing": "Price in the carry; no discounts.",
        "terms": "Shorten terms / prepay; cap exposure.",
        "allocation": "Gate on credit, not volume."},
    "Operationally Expensive": {
        "pricing": "Add a small-order / handling surcharge.",
        "terms": "Standard; consolidate orders.",
        "allocation": "Encourage fewer, larger loads."},
    "Strategic Platform": {
        "pricing": "Invest spread for growth / cross-sell.",
        "terms": "Flexible to deepen the relationship.",
        "allocation": "Prioritize; expansion potential."},
    "Backup-Only": {
        "pricing": "Spot premium; you are their fallback.",
        "terms": "Prepay.",
        "allocation": "Surplus only; no commitment."},
    "Contract Candidate": {
        "pricing": "Offer a term deal to lock steady volume.",
        "terms": "Contract terms; volume commitment.",
        "allocation": "Reserve committed volume."},
}

ARCHETYPES = list(ARCHETYPE_POSTURE.keys())
