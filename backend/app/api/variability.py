"""/api/variability/* — the two-axis variability score + the real-book validation readout.

Live-computed over the shared connection with a small data-signature cache (busted by a BOL load or a
deal upload). The two axes (cadence consistency, size consistency) are always returned separately;
the deal book annotates but never grades.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi import APIRouter, HTTPException

from .. import behavioral, calendar_days, db, schema, variability
from ..scoring_config import ScoringConfig

router = APIRouter(prefix="/api/variability")

_CACHE: dict = {}


def _con():
    return db.get_shared_connection()


def _sig(con) -> tuple:
    return (db.row_count(con, schema.LIFTS), str(db.get_meta(con, "last_import_at")),
            str(db.get_meta(con, "last_deal_import_at")), str(date.today()))


def _result(con) -> dict:
    sig = _sig(con)
    if _CACHE.get("sig") != sig:
        _CACHE.clear()
        _CACHE["sig"] = sig
        _CACHE["full"] = variability.compute_variability(con)
    return _CACHE["full"]


@router.get("")
def scores():
    with db.lock():
        return _result(_con())


@router.get("/validation")
def validation():
    with db.lock():
        return variability.validation_readout(_con())


@router.get("/config")
def config():
    from dataclasses import asdict
    return asdict(variability.DEFAULT_VAR_CONFIG)


@router.get("/customer/{customer_id}")
def customer(customer_id: str):
    with db.lock():
        con = _con()
        res = _result(con)
        row = next((c for c in res["customers"] if c["customer_id"] == customer_id), None)
        if row is None:
            raise HTTPException(status_code=404, detail=f"customer '{customer_id}' not found")
        # full behavioral drill-down (all windows + daily bars) for this one customer
        lifts = con.execute(
            "SELECT customer_id, lift_datetime, net_gallons, product, terminal FROM lifts "
            "WHERE customer_id = ? AND lift_datetime IS NOT NULL AND net_gallons IS NOT NULL",
            [customer_id]).df()
        cal, _ = calendar_days.from_connection(con)
        as_of = pd.to_datetime(lifts["lift_datetime"]).max() if len(lifts) else None
        terminal = (lifts["terminal"].mode().iloc[0] if len(lifts) and lifts["terminal"].notna().any()
                    else None)
        profile = behavioral.daily_profile(lifts, ScoringConfig(), as_of, name=row["name"],
                                            cal=cal, terminal=terminal) if as_of is not None else None
    return {**row, "behavioral_profile": profile}
