"""Pricing Sandbox + Pricing Engine API (Blueprint I).

The *heavy* half — the per-customer base (P3 β + P5 forecast + realized price/cost) and the fitted
acceptance model — is cached per ``(data signature, window, terminal)`` so the sandbox spread
slider and the regime selector re-derive only the cheap parts (the margin curve and the
GP-maximizing recommendations) on every interaction.

Capability-gated on ``unit_price`` + ``rack_benchmark``: when either is absent the payload returns
``available: false`` with the missing feeds (the lock), plus the 'collecting' counters for the
rack-benchmark / quote feeds that mature the acceptance model.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db, pricing, schema
from ..pricing_config import DEFAULT_CONFIG, PricingConfig
from ..regime_config import normalize_regime
from ..scoring_config import WINDOWS

router = APIRouter(prefix="/api/pricing")


def _con():
    return db.get_shared_connection()


# ---- live-compute cache for the heavy (regime-independent) base ------------------
_CACHE: dict = {}


def _data_sig(con) -> tuple:
    return (db.row_count(con, schema.LIFTS), db.row_count(con, schema.MARKET),
            db.row_count(con, schema.QUOTES), str(db.get_meta(con, "last_import_at")),
            str(db.get_meta(con, "generated_at")), str(db.get_meta(con, "profile")),
            str(db.get_meta(con, "demand_computed_at")))


def _base(con, cfg: PricingConfig, window: str, terminal: str | None) -> dict:
    # cfg has dict-valued fields (the shadow-price schedules), so key on a stable JSON dump.
    sig = (_data_sig(con), json.dumps(cfg.to_dict(), sort_keys=True, default=str),
           window, terminal or "")
    if sig not in _CACHE:
        _CACHE.clear()
        _CACHE[sig] = pricing.build_base(con, cfg, None, window, terminal)
    return _CACHE[sig]


def _regime(inventory, market, capacity, credit) -> dict:
    return normalize_regime({"inventory": inventory, "market": market,
                             "capacity": capacity, "credit": credit})


def _check_window(window: str) -> str:
    if window not in WINDOWS:
        raise HTTPException(status_code=400, detail=f"window must be one of {WINDOWS}.")
    return window


def _scope(base: dict, cfg: PricingConfig) -> dict:
    return {"window": base["window"], "terminal": base["terminal"], "terminals": base["terminals"],
            "products": base["products"], "as_of": base["as_of"], "config": cfg.to_dict()}


@router.get("")
def pricing_view(terminal: str | None = Query(default=None),
                 window: str = Query(default="all"),
                 inventory: str | None = Query(default=None),
                 market: str | None = Query(default=None),
                 capacity: str | None = Query(default=None),
                 credit: str | None = Query(default=None)):
    """Combined payload: availability + acceptance model + sandbox + regime recommendations."""
    window = _check_window(window)
    reg = _regime(inventory, market, capacity, credit)
    with db.lock():
        con = _con()
        base = _base(con, DEFAULT_CONFIG, window, terminal)
        if not base["available"]:
            return {**_scope(base, DEFAULT_CONFIG), "available": False,
                    "availability": base["availability"], "acceptance": None,
                    "sandbox": None, "recommendations": None}
        return {
            **_scope(base, DEFAULT_CONFIG), "available": True,
            "availability": base["availability"],
            "acceptance": pricing._acceptance_summary(base["acceptance"]),
            "sandbox": pricing.sandbox(base, DEFAULT_CONFIG, regime=None),
            "recommendations": pricing.recommendations(base, DEFAULT_CONFIG, reg),
        }


@router.get("/recommendations")
def recommendations(terminal: str | None = Query(default=None),
                    window: str = Query(default="all"),
                    inventory: str | None = Query(default=None),
                    market: str | None = Query(default=None),
                    capacity: str | None = Query(default=None),
                    credit: str | None = Query(default=None)):
    """Per-customer GP-maximizing quote prices + today's ranked pricing opportunities (regime-aware).

    Used by the Pricing view's opportunity list and surfaced inline on each customer scorecard.
    """
    window = _check_window(window)
    reg = _regime(inventory, market, capacity, credit)
    with db.lock():
        con = _con()
        base = _base(con, DEFAULT_CONFIG, window, terminal)
        if not base["available"]:
            return {**_scope(base, DEFAULT_CONFIG), "available": False,
                    "availability": base["availability"], "recommendations": None}
        return {**_scope(base, DEFAULT_CONFIG), "available": True,
                "availability": base["availability"],
                "acceptance": pricing._acceptance_summary(base["acceptance"]),
                "recommendations": pricing.recommendations(base, DEFAULT_CONFIG, reg)}


@router.get("/config")
def config():
    return {"config": DEFAULT_CONFIG.to_dict(), "windows": WINDOWS}


class RecomputeRequest(BaseModel):
    overrides: dict | None = Field(default=None)
    window: str = "all"
    terminal: str | None = None
    regime: dict | None = Field(default=None)


@router.post("/recompute")
def recompute(req: RecomputeRequest):
    """Recompute the full payload with config overrides (busts the cache); for tuning the grids,
    the shadow-price schedule, or the acceptance-model priors without a code change."""
    cfg = DEFAULT_CONFIG.with_overrides(req.overrides)
    window = req.window if req.window in WINDOWS else "all"
    reg = normalize_regime(req.regime)
    with db.lock():
        con = _con()
        _CACHE.clear()
        return pricing.compute_pricing(con, cfg, None, window, req.terminal, reg)
