"""Regime configuration — the V1 regime-multiplier matrix.

A *regime* is the standing operating context of a terminal on a given day, captured on four
axes: **inventory**, **market**, **capacity**, and **credit**. Each axis has a small set of
named states. The regime-multiplier matrix says, per archetype, how much each state should
bump (or cut) that customer's standing **Base Value** when building the day's worklist.

The day's **Regime-Adjusted Score** for a customer is::

    regime_score = clamp(base_value * Π_axis multiplier[archetype][axis][state], 0, 100)

i.e. the product of the four per-axis multipliers applied to Base Value, clamped to 0–100.
Anything unspecified defaults to ``1.0`` (neutral). The same matrix is mirrored on the
frontend (``lib/regime.ts``) so the regime selector re-ranks instantly client-side; the
backend uses it to build the nine ranked panels and persist ``daily_recommendations``.

This is intentionally a *config* file — every multiplier is a tunable number, nothing is
hard-coded in the engine (mirrors ``scoring_config.py``).
"""

from __future__ import annotations

# ---- Regime axes + their states (the regime selector reads this) ----------------
# Each axis: ordered states with a short label and a one-line meaning for the UI.
REGIME_AXES: dict[str, dict] = {
    "inventory": {
        "label": "Inventory",
        "states": {
            "long": {"label": "Long", "hint": "Too much product — move volume."},
            "balanced": {"label": "Balanced", "hint": "Healthy book — steady state."},
            "tight": {"label": "Tight", "hint": "Short supply — ration & monetize."},
            "tank_constrained": {"label": "Tank-constrained", "hint": "No room — drain length fast."},
        },
        "default": "balanced",
    },
    "market": {
        "label": "Market",
        "states": {
            "rising": {"label": "Rising", "hint": "Prices climbing — sell forward, hold premium."},
            "falling": {"label": "Falling", "hint": "Prices dropping — move volume now."},
            "flat": {"label": "Flat", "hint": "Quiet tape — business as usual."},
            "volatile": {"label": "Volatile", "hint": "Whippy tape — reward stability, lock terms."},
        },
        "default": "flat",
    },
    "capacity": {
        "label": "Rack capacity",
        "states": {
            "ample": {"label": "Ample", "hint": "Plenty of rack/truck slots."},
            "normal": {"label": "Normal", "hint": "Typical throughput."},
            "constrained": {"label": "Constrained", "hint": "Slots scarce — favor big, clean loads."},
        },
        "default": "normal",
    },
    "credit": {
        "label": "Credit",
        "states": {
            "easy": {"label": "Easy", "hint": "Credit is cheap & available."},
            "normal": {"label": "Normal", "hint": "Standard credit posture."},
            "tight": {"label": "Tight", "hint": "Protect capital — gate risky accounts."},
        },
        "default": "normal",
    },
}

DEFAULT_REGIME: dict[str, str] = {axis: cfg["default"] for axis, cfg in REGIME_AXES.items()}

# Opposite-state map per axis, used to render the scorecard "flip side" line (how the score
# and action change under the *opposite* inventory/market regime).
REGIME_OPPOSITE: dict[str, dict[str, str]] = {
    "inventory": {"long": "tight", "tight": "long",
                  "tank_constrained": "tight", "balanced": "tight"},
    "market": {"rising": "falling", "falling": "rising",
               "flat": "volatile", "volatile": "flat"},
    "capacity": {"ample": "constrained", "constrained": "ample", "normal": "constrained"},
    "credit": {"easy": "tight", "tight": "easy", "normal": "tight"},
}

# ---- The V1 regime-multiplier matrix --------------------------------------------
# REGIME_MULTIPLIER[archetype][axis][state] -> float. Missing entries are neutral (1.0).
# Read it as: "in this regime state, how much more (or less) does this archetype matter today?"
_N = 1.0
REGIME_MULTIPLIER: dict[str, dict[str, dict[str, float]]] = {
    "Anchor Base-Load": {
        "inventory": {"long": _N, "tight": 1.15, "tank_constrained": 1.05},
        "market": {"volatile": 1.10, "rising": 1.05},
        "capacity": {"constrained": 1.15, "ample": _N},
        "credit": {"tight": 1.10},
    },
    "Flex Buyer": {
        "inventory": {"long": 1.20, "tight": 0.85, "tank_constrained": 1.15},
        "market": {"falling": 1.15, "rising": 0.90, "volatile": 1.10},
        "capacity": {"constrained": 0.95},
        "credit": {"tight": 0.95},
    },
    "Premium Spot": {
        "inventory": {"long": 0.80, "tight": 1.35, "tank_constrained": 0.85},
        "market": {"rising": 1.25, "volatile": 1.20, "falling": 0.85},
        "capacity": {"constrained": 1.15},
        "credit": {"tight": 1.15},
    },
    "Price Shopper": {
        "inventory": {"long": 1.25, "tight": 0.55, "tank_constrained": 1.20},
        "market": {"falling": 1.20, "rising": 0.70, "volatile": 0.85},
        "capacity": {"constrained": 0.70},
        "credit": {"tight": 0.80},
    },
    "Surplus Absorber": {
        "inventory": {"long": 1.50, "tank_constrained": 1.45, "tight": 0.50, "balanced": _N},
        "market": {"falling": 1.20, "rising": 0.80},
        "capacity": {"constrained": 0.90},
        "credit": {"tight": 0.85},
    },
    "Scarcity Buyer": {
        "inventory": {"tight": 1.45, "long": 0.75, "tank_constrained": 0.85},
        "market": {"rising": 1.30, "volatile": 1.15, "falling": 0.85},
        "capacity": {"constrained": 1.10},
        "credit": {"tight": 1.05},
    },
    "Weather-Triggered": {
        "inventory": {"tight": 1.10, "long": 1.05},
        "market": {"rising": 1.10, "volatile": 1.05},
        "capacity": {"constrained": _N},
        "credit": {"tight": _N},
    },
    "Credit Drag": {
        "inventory": {"tight": 0.80, "long": 1.05},
        "market": {"volatile": 0.95},
        "capacity": {"constrained": 0.85},
        "credit": {"tight": 0.45, "easy": 1.10},
    },
    "Operationally Expensive": {
        "inventory": {"tight": 0.85},
        "market": {},
        "capacity": {"constrained": 0.55, "ample": 1.10},
        "credit": {"tight": 0.95},
    },
    "Strategic Platform": {
        "inventory": {"long": 1.10, "tight": 1.05},
        "market": {"volatile": 1.10},
        "capacity": {"constrained": 1.05},
        "credit": {"tight": _N},
    },
    "Backup-Only": {
        "inventory": {"long": 1.15, "tight": 0.60, "tank_constrained": 1.10},
        "market": {"falling": 1.10, "rising": 0.80},
        "capacity": {"constrained": 0.70},
        "credit": {"tight": 0.85},
    },
    "Contract Candidate": {
        "inventory": {"tight": 1.20, "balanced": 1.10, "long": 1.05},
        "market": {"rising": 1.15, "volatile": 1.20},
        "capacity": {"constrained": 1.05},
        "credit": {"tight": _N},
    },
}


def axis_multiplier(archetype: str, axis: str, state: str) -> float:
    """One axis's multiplier for an archetype/state (neutral 1.0 if unspecified)."""
    return float(REGIME_MULTIPLIER.get(archetype, {}).get(axis, {}).get(state, 1.0))


def regime_multiplier(archetype: str, regime: dict[str, str]) -> float:
    """Total multiplier = product of the per-axis multipliers for the chosen regime."""
    m = 1.0
    for axis in REGIME_AXES:
        state = regime.get(axis, REGIME_AXES[axis]["default"])
        m *= axis_multiplier(archetype, axis, state)
    return m


def regime_breakdown(archetype: str, regime: dict[str, str]) -> dict[str, float]:
    """Per-axis multipliers (for the scorecard 'why' line)."""
    return {axis: axis_multiplier(archetype, axis, regime.get(axis, REGIME_AXES[axis]["default"]))
            for axis in REGIME_AXES}


def regime_score(base_value: float | None, archetype: str, regime: dict[str, str]) -> float | None:
    """Base Value re-ranked by the regime multiplier, clamped to 0–100."""
    if base_value is None:
        return None
    return round(max(0.0, min(100.0, base_value * regime_multiplier(archetype, regime))), 1)


def normalize_regime(raw: dict[str, str] | None) -> dict[str, str]:
    """Coerce an arbitrary regime dict into valid states, defaulting unknown axes/values."""
    raw = raw or {}
    out = {}
    for axis, cfg in REGIME_AXES.items():
        state = raw.get(axis)
        out[axis] = state if state in cfg["states"] else cfg["default"]
    return out


def opposite_regime(regime: dict[str, str]) -> dict[str, str]:
    """The 'flip side' regime — invert the two leadership axes (inventory & market)."""
    flip = dict(regime)
    for axis in ("inventory", "market"):
        cur = regime.get(axis, REGIME_AXES[axis]["default"])
        flip[axis] = REGIME_OPPOSITE[axis].get(cur, cur)
    return flip


def regime_label(regime: dict[str, str]) -> str:
    """Human one-liner for a regime, e.g. 'Long inventory · Falling market'."""
    parts = []
    for axis in ("inventory", "market", "capacity", "credit"):
        st = regime.get(axis, REGIME_AXES[axis]["default"])
        parts.append(REGIME_AXES[axis]["states"][st]["label"])
    return " · ".join(parts)
