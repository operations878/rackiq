"""Daily operating API — the regime selector, the nine ranked panels, scorecards, playbook.

Backs **Blueprint C** (Daily Operating Dashboard), **Blueprint E** (one-page scorecards), and
**Blueprint G** (the in-app Sales Playbook). All reads compute live over the shared connection
(reusing the scoring engine's cache); ``/api/daily/persist`` writes ``daily_recommendations``.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from .. import db, regime, scoring
from ..playbook import build_playbook
from ..regime_config import (DEFAULT_REGIME, REGIME_AXES, REGIME_MULTIPLIER, normalize_regime)
from ..scoring_config import ARCHETYPE_POSTURE, ARCHETYPES, DEFAULT_CONFIG

router = APIRouter(prefix="/api")


def _con():
    return db.get_shared_connection()


def _regime_params(inventory, market, capacity, credit) -> dict:
    return normalize_regime({"inventory": inventory, "market": market,
                             "capacity": capacity, "credit": credit})


@router.get("/regime/config")
def regime_config():
    """Axes + states + the full V1 multiplier matrix (the frontend mirrors this to re-rank)."""
    return {"axes": REGIME_AXES, "default": DEFAULT_REGIME,
            "multiplier": REGIME_MULTIPLIER, "archetypes": ARCHETYPES,
            "posture": ARCHETYPE_POSTURE}


@router.get("/daily")
def daily(terminal: str | None = Query(default=None),
          inventory: str | None = Query(default=None),
          market: str | None = Query(default=None),
          capacity: str | None = Query(default=None),
          credit: str | None = Query(default=None),
          window: str = Query(default="all"),
          limit: int = Query(default=12)):
    reg = _regime_params(inventory, market, capacity, credit)
    with db.lock():
        con = _con()
        scoring.ensure_tables(con)
        return regime.build_daily(con, reg, terminal=terminal, window=window, limit=limit)


class PersistRequest(BaseModel):
    regime: dict | None = Field(default=None)
    window: str = "all"


@router.post("/daily/persist")
def daily_persist(req: PersistRequest):
    with db.lock():
        con = _con()
        return regime.persist_daily(con, req.regime, window=req.window)


@router.get("/daily/recommendations")
def daily_recommendations(run_date: str | None = Query(default=None),
                          terminal: str | None = Query(default=None)):
    """Read back the persisted §14 worklist (most recent run by default)."""
    with db.lock():
        con = _con()
        regime.ensure_tables(con)
        if run_date is None:
            row = con.execute("SELECT max(run_date) FROM daily_recommendations").fetchone()
            run_date = row[0] if row else None
        if run_date is None:
            return {"run_date": None, "rows": []}
        sql = ("SELECT run_date, computed_at, terminal, regime_label, panel, rank, customer_id, "
               "customer_name, archetype, action, why_now, expected_impact, impact_value, "
               "base_value, regime_score FROM daily_recommendations WHERE run_date = ?")
        params = [run_date]
        if terminal:
            sql += " AND terminal = ?"
            params.append(terminal)
        sql += " ORDER BY panel, rank"
        rows = con.execute(sql, params).fetchall()
        cols = ["run_date", "computed_at", "terminal", "regime_label", "panel", "rank",
                "customer_id", "customer_name", "archetype", "action", "why_now",
                "expected_impact", "impact_value", "base_value", "regime_score"]
        return {"run_date": run_date, "rows": [dict(zip(cols, r)) for r in rows]}


@router.get("/scorecards")
def scorecards(terminal: str | None = Query(default=None),
               inventory: str | None = Query(default=None),
               market: str | None = Query(default=None),
               capacity: str | None = Query(default=None),
               credit: str | None = Query(default=None),
               window: str = Query(default="all")):
    reg = _regime_params(inventory, market, capacity, credit)
    with db.lock():
        con = _con()
        scoring.ensure_tables(con)
        return regime.scorecards(con, reg, terminal=terminal, window=window)


@router.get("/playbook")
def playbook(terminal: str | None = Query(default=None), window: str = Query(default="all")):
    """The Sales Playbook (Blueprint G): per-archetype plays + regime cheat-sheets + routine.

    Scoped to the archetypes actually present in the book when a window/terminal is given.
    """
    with db.lock():
        con = _con()
        scoring.ensure_tables(con)
        res = scoring.compute_scores(con, DEFAULT_CONFIG, window)
        present = sorted({c["archetype"]["primary"] for c in res["customers"]
                          if (terminal is None or c["home_terminal"] == terminal)})
    return build_playbook(present_archetypes=present or None)
