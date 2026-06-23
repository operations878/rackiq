"""Reconciliation & loss-control configuration — every threshold/window is a parameter here.

The reconciliation engine (``reconciliation.py``) takes a :class:`ReconConfig`; nothing is
hard-coded in the math. ``ReconConfig.with_overrides({...})`` produces a tweaked copy (used by
``POST /api/reconciliation/recompute`` so ops can re-tune control limits without a code change).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

# Period grains the engine can bucket on (loss is computed opening→closing within each).
PERIOD_GRAINS = ["month", "week"]


@dataclass(frozen=True)
class ReconConfig:
    # ---- Period bucketing -------------------------------------------------------
    period_grain: str = "month"          # "month" | "week"

    # ---- Control-chart limits (meter-drift detection) ---------------------------
    # Limits are built from the NETWORK routine-shrinkage distribution (robust center ± k·σ),
    # so a single drifting tank does not inflate its own limits and hide itself.
    control_k: float = 3.0               # UCL/LCL = center ± k·σ (σ = robust MAD-based)
    run_rule_len: int = 5                # ≥ this many consecutive periods above center ⇒ out of control
    min_out_periods: int = 2             # periods beyond the UCL needed to call it "persistent"
    baseline: str = "median"             # network routine center: "median" | "mean"

    # ---- Net-recon cross-check (billed vs ASTM D1250 recompute) -----------------
    systematic_pct_threshold: float = 0.0015   # |billed−recomputed|/billed to flag a meter/lane
    min_bols_for_systematic: int = 8            # need this many BOLs on a lane before flagging
    sign_consistency: float = 0.70              # share of BOLs sharing the delta sign ⇒ systematic

    # ---- Dollarize --------------------------------------------------------------
    default_unit_cost: float = 2.50      # $/gal fallback when no compartment/lift cost is present
    annualize: bool = True               # scale loss $ to a yearly run-rate over the horizon

    def to_dict(self) -> dict:
        return asdict(self)

    def with_overrides(self, overrides: dict | None) -> "ReconConfig":
        if not overrides:
            return self
        known = set(self.__dataclass_fields__)  # type: ignore[attr-defined]
        clean = {k: v for k, v in overrides.items() if k in known}
        if "period_grain" in clean and clean["period_grain"] not in PERIOD_GRAINS:
            clean.pop("period_grain")
        return replace(self, **clean)


DEFAULT_CONFIG = ReconConfig()
