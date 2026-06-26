"""Margin & pricing configuration — every threshold/window/assumption is a parameter here.

The margin engine (``margin.py``) and the price/cost ingestion (``pricegrid.py``) take a
:class:`MarginConfig`; nothing is hard-coded in the math. ``MarginConfig.with_overrides({...})``
produces a tweaked copy (used by ``POST /api/margin/recompute`` so the desk can re-tune the
cost-basis window, the units heuristics, the plausibility gate, or the term basis assumption without
a code change).

Two design rules baked in here (see docs/margin/MODELING_DECISION.md):
  • **Term margin** uses ``term_basis_assumption`` for the index-to-index spread (default 0 =
    same-index) and is reported with that assumption flagged — never silently absorbed.
  • The **plausibility gate** (``margin_warn_cents``) is what catches the "$1/gal margin" units/basis
    bug the brief warns about: a margin past this many ¢/gal raises a ``units_warning``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace


@dataclass(frozen=True)
class MarginConfig:
    # ---- Running landed-cost basis (WAC of recent barges into a terminal×product) -----
    cost_basis_window_days: int = 45       # trailing window for the volume-weighted landed cost
    cost_basis_min_barges: int = 3         # ...or at least the last N barges, whichever covers more
    replacement_lookback_days: int = 120   # how far back the "most-recent" replacement cost may reach

    # ---- Units / unit-recovery heuristics ---------------------------------------------
    gallons_per_barrel: float = 42.0
    mb_threshold_bbl: float = 1000.0       # Trips Product Vol < this ⇒ read as "mb" (thousand-bbl, ×1000)
    # Estimated Trip Value ÷ net gallons must land in this $/gal band to be trusted as an ALL-IN
    # cargo flat (i.e. it embeds the index); outside it ⇒ logistics-only cost + a flagged cargo gap.
    etv_flat_lo: float = 1.50
    etv_flat_hi: float = 4.50

    # ---- Margin plausibility gate (¢/gal) ---------------------------------------------
    # Rack diesel margins read single-digit to low-double-digit ¢/gal. A margin past the warn band
    # is almost certainly a units/basis error (e.g. ~$1/gal) ⇒ the payload carries a units_warning.
    margin_warn_cents: float = 35.0
    margin_plausible_lo_cents: float = -25.0
    margin_plausible_hi_cents: float = 35.0

    # ---- Forward-fixed mark-to-market -------------------------------------------------
    mtm_thin_cents: float = 3.0            # 0 ≤ mtm_per_gal < this (¢/gal) ⇒ "thin" (vs underwater <0)

    # ---- Term margin: index-to-index basis assumption ($/gal) -------------------------
    # I_sell − I_buy. Default 0 (sell and cargo reference the same barge index, so the flat cancels).
    # Surfaced on every term margin as basis_assumption so the desk sees what is baked in.
    term_basis_assumption: float = 0.0

    # ---- Roll-up ----------------------------------------------------------------------
    window_days: int | None = None         # None ⇒ use the full overlap of lifts × prices/costs
    sufficiency_min_gallons: float = 1.0    # a cell below this lifted volume is not ranked

    # ---- Matrix concat-key product prefixes (longest-first) ---------------------------
    # The Matrix sheet concatenates PRODUCT+CUSTOMER with no delimiter ("ULSHO4416 Oil Corp").
    # A key is split by matching the longest known product prefix; an unmatched key is FLAGGED
    # ambiguous, never split arbitrarily. Blend prefixes (B5/B10/B20/B99) may precede the product.
    product_prefixes: tuple[str, ...] = (
        "B99 ULSHO", "B20 ULSHO", "B10 ULSHO", "B5 ULSHO",
        "B20 ULSD", "B10 ULSD", "B5 ULSD",
        "ULSHO", "ULSD", "DYED", "RBOB", "HO4", "HO", "RD", "GEC", "DD",
    )

    def to_dict(self) -> dict:
        return asdict(self)

    def with_overrides(self, overrides: dict | None) -> "MarginConfig":
        if not overrides:
            return self
        known = set(self.__dataclass_fields__)  # type: ignore[attr-defined]
        clean = {k: v for k, v in overrides.items() if k in known}
        if "product_prefixes" in clean and clean["product_prefixes"] is not None:
            clean["product_prefixes"] = tuple(clean["product_prefixes"])
        return replace(self, **clean)


DEFAULT_CONFIG = MarginConfig()
