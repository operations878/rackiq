"""Margin engine (Phase 2) — rank the book by VALUE, mark the forward book to market, price the gap.

A layer ON TOP of the Phase-1 book. It **reads** the ``deals`` spine (term/forward/spot as deal-TYPE
metadata for margin math — NOT a scoring split), the ``lifts`` volume spine, and the Phase-2
``price_grid`` (sell) / ``landed_costs`` (cost) stores. It **never** touches the VAR score, ingestion,
inventory/position (Phase 3), or ``hedging.py`` — and it never imports ``hedging`` (the dependency is
one-way: hedge → margin).

What it produces (see docs/margin/MODELING_DECISION.md for the math):
  • Per-lift realized margin in TWO views — **BOOK** (vs the running landed-cost basis) and
    **REPLACEMENT** (vs the most-recent landed cost) — rolled up to customer / product family /
    terminal, with the **margin ranking explicitly contrasted against the volume ranking**.
  • **Deal-type margins** respecting index-on-index physics: TERM = sell_diff − cargo_diff −
    logistics − basis (the flat cancels; recoverable with NO market data); FORWARD = locked − landed;
    SPOT = realized − landed.
  • **Forward-fixed mark-to-market** on the open committed book ($ exposure, underwater/thin flags).
  • A **margin-priced gap helper** (:func:`margin_for_gap`) Phase-3's hedge calls: committed/must-serve
    margin vs spot upside, in dollars.
  • **Coverage + a plausibility gate** (¢/gal sanity; the "$1/gal" units bug is flagged, never shipped).

Sell/cost are sourced by a priority chain with provenance recorded on every row, so the engine runs on
BOTH the real book (grid + Trips) and the synthetic/sample book (lift ``unit_price``/``unit_cost``).
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from . import dealbook, db, pricegrid, schema
from .margin_config import DEFAULT_CONFIG, MarginConfig

WINDOWS = ["all", "365d", "180d", "90d"]
_WINDOW_DAYS = {"365d": 365, "180d": 180, "90d": 90}


def _fam(x) -> str:
    return dealbook.product_family(x)


def _today(today: dt.date | None) -> dt.date:
    return today or dt.date.today()


# ================================================================================
# Cost basis index — running WAC (BOOK) and most-recent (REPLACEMENT) landed cost
# ================================================================================
class CostIndex:
    """Landed-cost lookups over ``landed_costs``, keyed by normalized (terminal, family).

    ``book_wac`` is the volume-weighted all-in landed cost of recent barges (the inventory cost basis
    at time *t*); ``replacement`` is the most-recent all-in landed cost (what it would cost today).
    Both fall back to a family-level (terminal-agnostic) pool, then report incompleteness honestly.
    """

    def __init__(self, costs: pd.DataFrame, cfg: MarginConfig):
        self.cfg = cfg
        self.empty = costs is None or len(costs) == 0
        self._by_key: dict[tuple, pd.DataFrame] = {}
        self._by_fam: dict[str, pd.DataFrame] = {}
        if self.empty:
            return
        c = costs.copy()
        c["discharge_date"] = pd.to_datetime(c["discharge_date"], errors="coerce")
        c = c[c["discharge_date"].notna()].sort_values("discharge_date")
        c["_tkey"] = c["terminal"].map(_n)
        for (tk, fam), g in c.groupby(["_tkey", "product_family"], dropna=False):
            self._by_key[(tk, fam)] = g.reset_index(drop=True)
        for fam, g in c.groupby("product_family", dropna=False):
            self._by_fam[fam] = g.sort_values("discharge_date").reset_index(drop=True)

    def _pool(self, terminal, family) -> pd.DataFrame | None:
        g = self._by_key.get((_n(terminal), family))
        if g is not None and len(g):
            return g
        return self._by_fam.get(family)

    def book_wac(self, terminal, family, when) -> tuple[float | None, str]:
        """Volume-weighted all-in landed $/gal of barges in the trailing window before ``when``."""
        g = self._pool(terminal, family)
        if g is None or not len(g):
            return None, "no_barge"
        when = pd.Timestamp(when)
        prior = g[g["discharge_date"] <= when]
        if not len(prior):
            prior = g.head(self.cfg.cost_basis_min_barges)   # before first barge → use earliest
        win_start = when - pd.Timedelta(days=self.cfg.cost_basis_window_days)
        win = prior[prior["discharge_date"] >= win_start]
        if len(win) < self.cfg.cost_basis_min_barges:
            win = prior.tail(self.cfg.cost_basis_min_barges)  # widen to the last N barges
        allin = win[win["all_in_landed"].notna()]
        if len(allin):
            w = allin["volume_gal"].fillna(1.0).clip(lower=1.0).to_numpy()
            v = allin["all_in_landed"].to_numpy(dtype=float)
            return float(np.average(v, weights=w)), "all_in"
        # only logistics known → the cargo flat is the un-loaded index gap
        log = win[win["logistics_cost"].notna()]
        if len(log):
            w = log["volume_gal"].fillna(1.0).clip(lower=1.0).to_numpy()
            return float(np.average(log["logistics_cost"].to_numpy(dtype=float), weights=w)), "logistics_only"
        return None, "no_barge"

    def replacement(self, terminal, family, when=None) -> tuple[float | None, str]:
        """Most-recent all-in landed $/gal (≤ ``when`` if given) for that family."""
        g = self._pool(terminal, family)
        if g is None or not len(g):
            return None, "no_barge"
        allin = g[g["all_in_landed"].notna()]
        if when is not None:
            cutoff = pd.Timestamp(when)
            sub = allin[allin["discharge_date"] <= cutoff]
            allin = sub if len(sub) else allin
        if len(allin):
            return float(allin.sort_values("discharge_date")["all_in_landed"].iloc[-1]), "all_in"
        log = g[g["logistics_cost"].notna()]
        if len(log):
            return float(log.sort_values("discharge_date")["logistics_cost"].iloc[-1]), "logistics_only"
        return None, "no_barge"

    def leg(self, terminal, family, column) -> float | None:
        """Volume-weighted value of a leg column (logistics_cost / fixed_differential) for a cell."""
        g = self._pool(terminal, family)
        if g is None or not len(g):
            return None
        sub = g[g[column].notna()]
        if not len(sub):
            return None
        w = sub["volume_gal"].fillna(1.0).clip(lower=1.0).to_numpy()
        return float(np.average(sub[column].to_numpy(dtype=float), weights=w))


def _n(x) -> str:
    from .ingest import _norm
    return _norm(x) if x is not None and not (isinstance(x, float) and pd.isna(x)) else ""


# ================================================================================
# Sell index — grid lookups (terminal sheet preferred; Matrix fills gaps)
# ================================================================================
class SellIndex:
    """Grid sell-price lookups over ``price_grid``, keyed by (master, family, terminal).

    Resolves to the nearest-dated grid price for that customer×product (terminal sheet preferred over
    Matrix). Falls back to a terminal-agnostic match, then reports a miss.
    """

    def __init__(self, grid: pd.DataFrame):
        self.empty = grid is None or len(grid) == 0
        self._idx: dict[tuple, tuple] = {}    # (master, family, tkey_or_None, source) -> (dates[], prices[])
        if self.empty:
            return
        g = grid.copy()
        g = g[g["customer_master"].notna() & g["sell_price"].notna()]
        g["price_date"] = pd.to_datetime(g["price_date"], errors="coerce")
        g = g[g["price_date"].notna()]
        g["_tkey"] = g["terminal"].map(lambda t: _n(t) or None)
        for (m, fam, tk, src), sub in g.groupby(
                ["customer_master", "product_family", "_tkey", "source"], dropna=False):
            sub = sub.sort_values("price_date")
            self._idx[(m, fam, tk, src)] = (
                sub["price_date"].to_numpy(), sub["sell_price"].to_numpy(dtype=float))

    def sell(self, master, family, terminal, when) -> tuple[float | None, str | None]:
        if self.empty or master is None:
            return None, None
        when = np.datetime64(pd.Timestamp(when))
        tk = _n(terminal) or None
        # preference order: terminal sheet @terminal, matrix @terminal, terminal sheet any, matrix any
        for key, src in ((( master, family, tk, "terminal_sheet"), "grid_terminal"),
                         ((master, family, tk, "matrix"), "grid_matrix"),
                         ((master, family, None, "terminal_sheet"), "grid_terminal"),
                         ((master, family, None, "matrix"), "grid_matrix")):
            hit = self._idx.get(key)
            if hit is None:
                continue
            dates, prices = hit
            pos = np.searchsorted(dates, when, side="right") - 1
            if pos < 0:
                pos = 0           # before first quote → use earliest
            return float(prices[pos]), src
        return None, None


# ================================================================================
# Base build — per-lift realized margin (BOOK + REPLACEMENT) with provenance
# ================================================================================
def availability(con, cfg: MarginConfig = DEFAULT_CONFIG) -> dict:
    pricegrid.ensure_tables(con)
    nlifts = db.row_count(con, schema.LIFTS)
    nn = db.nonnull_counts(con, schema.LIFTS)
    counts = pricegrid.store_counts(con)
    has_grid = counts["price_grid_rows"] > 0
    has_landed = counts["landed_cost_trips"] > 0
    has_unit_price = nn.get("unit_price", 0) > 0
    has_unit_cost = nn.get("unit_cost", 0) > 0
    has_sell = has_grid or has_unit_price
    has_cost = has_landed or has_unit_cost
    missing = []
    if not has_sell:
        missing.append("sell price (load the wholesale grid, or unit_price on lifts)")
    if not has_cost:
        missing.append("landed cost (load the Trips report, or unit_cost on lifts)")
    return {
        "available": bool(nlifts and has_sell and has_cost),
        "has_lifts": bool(nlifts), "has_grid": has_grid, "has_landed_costs": has_landed,
        "has_unit_price": has_unit_price, "has_unit_cost": has_unit_cost,
        "missing": missing, "stores": counts,
    }


def _load_lifts(con, window: str, terminal: str | None) -> pd.DataFrame:
    df = con.execute(
        "SELECT customer_id, lift_datetime, net_gallons, terminal, product, unit_price, unit_cost "
        "FROM lifts WHERE customer_id IS NOT NULL AND lift_datetime IS NOT NULL "
        "AND net_gallons IS NOT NULL").df()
    if not len(df):
        return df
    df["lift_datetime"] = pd.to_datetime(df["lift_datetime"], errors="coerce")
    df = df[df["lift_datetime"].notna() & (df["net_gallons"] != 0)]
    if terminal:
        df = df[df["terminal"].map(_n) == _n(terminal)]
    if window in _WINDOW_DAYS and len(df):
        as_of = df["lift_datetime"].max()
        df = df[df["lift_datetime"] >= as_of - pd.Timedelta(days=_WINDOW_DAYS[window])]
    df["family"] = df["product"].map(_fam)
    return df.reset_index(drop=True)


def _master_names(con) -> dict:
    rows = con.execute("SELECT customer_id, name FROM customers").fetchall()
    return {r[0]: (r[1] or r[0]) for r in rows}


def build_base(con, cfg: MarginConfig = DEFAULT_CONFIG, window: str = "all",
               terminal: str | None = None, today: dt.date | None = None) -> dict:
    """Compute per-lift BOOK & REPLACEMENT margin with sell/cost provenance. The shared base every
    roll-up / MTM / gap view reads."""
    pricegrid.ensure_tables(con)
    av = availability(con, cfg)
    scope = {"window": window, "terminal": terminal, "as_of": None,
             "today": _today(today).isoformat()}
    if not av["available"]:
        return {**scope, "available": False, "availability": av, "lifts": pd.DataFrame()}

    lifts = _load_lifts(con, window, terminal)
    if not len(lifts):
        return {**scope, "available": False,
                "availability": {**av, "available": False, "missing": ["no lifts in window"]},
                "lifts": pd.DataFrame()}

    grid = pricegrid.read_price_grid(con)
    costs = pricegrid.read_landed_costs(con)
    sell_ix = SellIndex(grid)
    cost_ix = CostIndex(costs, cfg)
    names = _master_names(con)

    n = len(lifts)
    sell = np.full(n, np.nan)
    sell_src = np.array([None] * n, dtype=object)
    book_cost = np.full(n, np.nan)
    book_basis = np.array([None] * n, dtype=object)
    repl_cost = np.full(n, np.nan)
    cost_src = np.array([None] * n, dtype=object)

    fast_sell = sell_ix.empty           # no grid → use lift unit_price
    fast_cost = cost_ix.empty           # no Trips → use lift unit_cost
    up = pd.to_numeric(lifts.get("unit_price"), errors="coerce").to_numpy()
    uc = pd.to_numeric(lifts.get("unit_cost"), errors="coerce").to_numpy()

    for i, r in enumerate(lifts.itertuples(index=False)):
        # ---- sell (priority: grid → lift unit_price) ----
        if not fast_sell:
            s, src = sell_ix.sell(r.customer_id, r.family, r.terminal, r.lift_datetime)
            if s is None and not np.isnan(up[i]):
                s, src = float(up[i]), "lift_unit_price"
        else:
            s, src = (float(up[i]), "lift_unit_price") if not np.isnan(up[i]) else (None, None)
        if s is not None:
            sell[i], sell_src[i] = s, src
        # ---- cost (priority: Trips running WAC → lift unit_cost) ----
        if not fast_cost:
            bc, basis = cost_ix.book_wac(r.terminal, r.family, r.lift_datetime)
            rc, _ = cost_ix.replacement(r.terminal, r.family, r.lift_datetime)
            csrc = "trips_wac"
            if bc is None and not np.isnan(uc[i]):
                bc, basis, csrc = float(uc[i]), "lift_unit_cost", "lift_unit_cost"
            if rc is None:
                rc = bc
        else:
            bc = float(uc[i]) if not np.isnan(uc[i]) else None
            basis = "lift_unit_cost" if bc is not None else None
            rc, csrc = bc, ("lift_unit_cost" if bc is not None else None)
        if bc is not None:
            book_cost[i], book_basis[i], cost_src[i] = bc, basis, csrc
        if rc is not None:
            repl_cost[i] = rc

    out = lifts.copy()
    out["master_name"] = out["customer_id"].map(lambda c: names.get(c, c))
    out["sell"] = sell
    out["sell_source"] = sell_src
    out["book_cost"] = book_cost
    out["book_basis"] = book_basis
    out["repl_cost"] = repl_cost
    out["cost_source"] = cost_src
    out["book_margin"] = out["sell"] - out["book_cost"]
    out["repl_margin"] = out["sell"] - out["repl_cost"]
    # a lift is "defensible" iff it got both a sell and a (cargo-complete) book cost
    out["complete"] = out["sell"].notna() & out["book_cost"].notna() & \
        (out["book_basis"] != "logistics_only")
    out["book_margin_gal"] = out["book_margin"] * out["net_gallons"]
    out["repl_margin_gal"] = out["repl_margin"] * out["net_gallons"]

    return {**scope, "available": True, "availability": av, "lifts": out,
            "as_of": str(out["lift_datetime"].max().date()),
            "cost_index": cost_ix, "sell_index": sell_ix, "names": names}


# ================================================================================
# STEP 3 — roll-ups + the value-vs-volume contrast
# ================================================================================
def _cents(x) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x) * 100.0, 3)


def customer_rollup(base: dict, cfg: MarginConfig = DEFAULT_CONFIG) -> list[dict]:
    """Per-master margin ($ + ¢/gal, BOOK & REPLACEMENT) with the margin rank vs the volume rank."""
    df = base["lifts"]
    cm = df[df["complete"]]
    if not len(cm):
        return []
    g = cm.groupby("customer_id").agg(
        name=("master_name", "first"),
        gallons=("net_gallons", "sum"),
        book_margin_dollars=("book_margin_gal", "sum"),
        repl_margin_dollars=("repl_margin_gal", "sum"),
        lifts=("net_gallons", "size"),
    ).reset_index()
    g["book_cents_gal"] = (g["book_margin_dollars"] / g["gallons"]).map(_cents)
    g["repl_cents_gal"] = (g["repl_margin_dollars"] / g["gallons"]).map(_cents)
    g = g[g["gallons"] >= cfg.sufficiency_min_gallons]
    g["rank_by_volume"] = g["gallons"].rank(ascending=False, method="min").astype(int)
    g["rank_by_margin"] = g["book_margin_dollars"].rank(ascending=False, method="min").astype(int)
    # positive ⇒ ranks higher on margin than on volume (the fat-margin tell); negative ⇒ thinner
    g["rank_delta"] = g["rank_by_volume"] - g["rank_by_margin"]
    g = g.sort_values("book_margin_dollars", ascending=False)
    return [{
        "customer_id": r.customer_id, "name": r.name,
        "gallons": round(float(r.gallons), 0), "lifts": int(r.lifts),
        "book_margin_dollars": round(float(r.book_margin_dollars), 0),
        "repl_margin_dollars": round(float(r.repl_margin_dollars), 0),
        "book_cents_gal": r.book_cents_gal, "repl_cents_gal": r.repl_cents_gal,
        "rank_by_volume": int(r.rank_by_volume), "rank_by_margin": int(r.rank_by_margin),
        "rank_delta": int(r.rank_delta),
    } for r in g.itertuples(index=False)]


def _dim_rollup(base: dict, by: str) -> list[dict]:
    df = base["lifts"]
    cm = df[df["complete"]]
    if not len(cm):
        return []
    g = cm.groupby(by).agg(
        gallons=("net_gallons", "sum"),
        book_margin_dollars=("book_margin_gal", "sum"),
        repl_margin_dollars=("repl_margin_gal", "sum"),
    ).reset_index()
    g["book_cents_gal"] = (g["book_margin_dollars"] / g["gallons"]).map(_cents)
    g["repl_cents_gal"] = (g["repl_margin_dollars"] / g["gallons"]).map(_cents)
    g = g.sort_values("book_margin_dollars", ascending=False)
    return [{by: (None if pd.isna(getattr(r, by)) else getattr(r, by)),
             "gallons": round(float(r.gallons), 0),
             "book_margin_dollars": round(float(r.book_margin_dollars), 0),
             "repl_margin_dollars": round(float(r.repl_margin_dollars), 0),
             "book_cents_gal": r.book_cents_gal, "repl_cents_gal": r.repl_cents_gal}
            for r in g.itertuples(index=False)]


def product_rollup(base: dict) -> list[dict]:
    return _dim_rollup(base, "family")


def terminal_rollup(base: dict) -> list[dict]:
    return _dim_rollup(base, "terminal")


def coverage(base: dict) -> dict:
    """% of lifted volume with a defensible margin vs flagged incomplete (the honesty report)."""
    df = base["lifts"]
    total_gal = float(df["net_gallons"].sum()) or 1.0
    complete = df[df["complete"]]
    comp_gal = float(complete["net_gallons"].sum())
    no_sell = float(df[df["sell"].isna()]["net_gallons"].sum())
    no_cost = float(df[df["book_cost"].isna()]["net_gallons"].sum())
    logistics_only = float(df[df["book_basis"] == "logistics_only"]["net_gallons"].sum())
    src = (df[df["complete"]]["cost_source"].value_counts(normalize=True) * 100).round(1).to_dict()
    sell_src = (df[df["complete"]]["sell_source"].value_counts(normalize=True) * 100).round(1).to_dict()
    return {
        "total_gallons": round(total_gal, 0),
        "covered_gallons": round(comp_gal, 0),
        "coverage_pct": round(100 * comp_gal / total_gal, 1),
        "incomplete_gallons": round(total_gal - comp_gal, 0),
        "incomplete_pct": round(100 * (total_gal - comp_gal) / total_gal, 1),
        "missing_sell_gallons": round(no_sell, 0),
        "missing_cost_gallons": round(no_cost, 0),
        "cargo_flat_gap_gallons": round(logistics_only, 0),
        "cost_source_mix_pct": src, "sell_source_mix_pct": sell_src,
    }


def plausibility(base: dict, cfg: MarginConfig = DEFAULT_CONFIG) -> dict:
    """Sanity gate: book ¢/gal should be single-to-low-double-digit. Flag the '$1/gal' units bug."""
    df = base["lifts"]
    cm = df[df["complete"]]
    if not len(cm):
        return {"ok": True, "units_warning": False, "n": 0}
    w = cm["net_gallons"].clip(lower=1.0).to_numpy()
    book = cm["book_margin"].to_numpy(dtype=float)
    wmean_cents = float(np.average(book, weights=w)) * 100
    p50 = float(np.median(book)) * 100
    share_implausible = float(((np.abs(book) * 100) > cfg.margin_warn_cents).mean())
    units_warning = abs(wmean_cents) > cfg.margin_warn_cents or share_implausible > 0.5
    return {
        "ok": not units_warning, "units_warning": bool(units_warning),
        "vol_weighted_cents_gal": round(wmean_cents, 2),
        "median_cents_gal": round(p50, 2),
        "share_outside_band": round(share_implausible, 3),
        "warn_threshold_cents": cfg.margin_warn_cents,
        "note": ("margins near $1/gal — likely a units/basis error; do NOT trust these numbers"
                 if units_warning else "margins read in a plausible ¢/gal band"),
    }


def deal_grid_crosscheck(con, base: dict) -> dict:
    """Sanity cross-check: realized SPOT prices (deals) vs the GRID sell on the same cell/date should
    land in the same neighborhood. A large gap means the grid is mis-scaled or mis-mapped."""
    sell_ix: SellIndex | None = base.get("sell_index")
    if sell_ix is None or sell_ix.empty:
        return {"available": False, "reason": "no grid sell prices loaded"}
    deals = _read_deals(con)
    spot = deals[(deals["source"] == dealbook.SOURCE_SPOT) & deals["price"].notna()]
    diffs = []
    for r in spot.itertuples(index=False):
        when = r.deal_date if pd.notna(r.deal_date) else r.month
        if pd.isna(when) or r.customer_master is None:
            continue
        g, _ = sell_ix.sell(r.customer_master, r.product_family, r.terminal, when)
        if g is not None:
            diffs.append(abs(float(r.price) - g))
    if not diffs:
        return {"available": False, "reason": "no spot deals overlap the grid"}
    arr = np.array(diffs)
    return {"available": True, "n_compared": len(diffs),
            "median_abs_diff_cents_gal": round(float(np.median(arr)) * 100, 2),
            "share_within_10cents": round(float((arr <= 0.10).mean()), 3),
            "note": ("spot realized prices and the grid agree (same neighborhood)"
                     if float(np.median(arr)) <= 0.15 else
                     "spot vs grid disagree — check grid scaling / customer mapping")}


def worked_example(base: dict) -> dict | None:
    """One customer end-to-end (sell, cost, margin) with the arithmetic shown."""
    rows = customer_rollup(base)
    if not rows:
        return None
    top = rows[len(rows) // 2]            # a mid-book account (representative, not the outlier)
    df = base["lifts"]
    one = df[(df["customer_id"] == top["customer_id"]) & df["complete"]]
    if not len(one):
        return None
    last = one.sort_values("lift_datetime").iloc[-1]
    return {
        "customer": top["name"], "product": last["family"], "terminal": last["terminal"],
        "lift_date": str(pd.Timestamp(last["lift_datetime"]).date()),
        "sell_per_gal": round(float(last["sell"]), 4), "sell_source": last["sell_source"],
        "book_cost_per_gal": round(float(last["book_cost"]), 4), "cost_source": last["cost_source"],
        "book_margin_per_gal": round(float(last["book_margin"]), 4),
        "book_margin_cents_gal": _cents(last["book_margin"]),
        "arithmetic": (f"{round(float(last['sell']),4)} sell − {round(float(last['book_cost']),4)} "
                       f"cost = {round(float(last['book_margin']),4)} $/gal "
                       f"({_cents(last['book_margin'])} ¢/gal)"),
        "account_book_margin_dollars": top["book_margin_dollars"],
        "account_book_cents_gal": top["book_cents_gal"],
    }


# ================================================================================
# STEP 2 — deal-type margin decomposition (term / forward / spot)
# ================================================================================
def _read_deals(con) -> pd.DataFrame:
    df = db.read_deals_df(con)
    if len(df):
        df["month"] = pd.to_datetime(df["month"], errors="coerce")
        df["deal_date"] = pd.to_datetime(df["deal_date"], errors="coerce")
    return df


def deal_type_margins(con, base: dict, cfg: MarginConfig = DEFAULT_CONFIG) -> dict:
    """Per-deal margins by TYPE, respecting index-on-index physics. Returns rows + a per-source roll-up.

    TERM  : sell_diff − cargo_diff − logistics − basis  (the flat cancels; no market level needed)
    FORWARD: locked_sell − landed_cost (book WAC around the month)
    SPOT  : realized_sell − landed_cost (around the deal date)
    """
    deals = _read_deals(con)
    cost_ix: CostIndex = base.get("cost_index") or CostIndex(pricegrid.read_landed_costs(con), cfg)
    rows: list[dict] = []
    for r in deals.itertuples(index=False):
        terminal, fam = r.terminal, r.product_family
        when = r.month if pd.notna(r.month) else r.deal_date
        gal = None
        per_gal = None
        basis_flag = None
        cost_complete = True
        if r.source == dealbook.SOURCE_TERM:
            gal = r.committed_gallons
            sell_diff = r.price
            cargo_diff = cost_ix.leg(terminal, fam, "fixed_differential") or 0.0
            logistics = cost_ix.leg(terminal, fam, "logistics_cost")
            if sell_diff is None or logistics is None:
                cost_complete = False
            per_gal = (None if sell_diff is None or logistics is None
                       else sell_diff - cargo_diff - logistics - cfg.term_basis_assumption)
            basis_flag = "same_index_zero"
        elif r.source == dealbook.SOURCE_FORWARD:
            gal = r.committed_gallons
            landed, lb = cost_ix.book_wac(terminal, fam, when) if pd.notna(when) else (None, "no_barge")
            cost_complete = lb == "all_in"
            per_gal = None if (r.price is None or landed is None or not cost_complete) else r.price - landed
        elif r.source == dealbook.SOURCE_SPOT:
            gal = r.realized_gallons
            landed, lb = cost_ix.replacement(terminal, fam, when) if pd.notna(when) else (None, "no_barge")
            cost_complete = lb == "all_in"
            per_gal = None if (r.price is None or landed is None or not cost_complete) else r.price - landed
        rows.append({
            "source": r.source, "customer_master": r.customer_master, "customer_raw": r.customer_raw,
            "product_family": fam, "terminal": terminal,
            "month": str(r.month.date()) if pd.notna(r.month) else None,
            "gallons": None if gal is None else round(float(gal), 0),
            "margin_per_gal": None if per_gal is None else round(float(per_gal), 5),
            "margin_cents_gal": _cents(per_gal),
            "margin_dollars": None if (per_gal is None or gal is None) else round(per_gal * float(gal), 0),
            "basis_assumption": basis_flag, "cost_complete": bool(cost_complete),
        })
    by_source = _summarize_deal_margins(rows)
    return {"by_source": by_source, "rows": rows,
            "basis_note": ("TERM margins assume sell and cargo reference the same index "
                           f"(I_sell − I_buy = {cfg.term_basis_assumption}); the flat price cancels.")}


def _summarize_deal_margins(rows: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for r in rows:
        s = out.setdefault(r["source"], {"source": r["source"], "deals": 0, "priced": 0,
                                         "gallons": 0.0, "margin_dollars": 0.0, "wsum": 0.0, "wgal": 0.0,
                                         "incomplete": 0})
        s["deals"] += 1
        if r["margin_per_gal"] is None:
            s["incomplete"] += 1
            continue
        s["priced"] += 1
        if r["gallons"]:
            s["gallons"] += r["gallons"]
            s["margin_dollars"] += r["margin_dollars"] or 0.0
            s["wsum"] += r["margin_per_gal"] * r["gallons"]
            s["wgal"] += r["gallons"]
    res = []
    for s in out.values():
        cents = _cents(s["wsum"] / s["wgal"]) if s["wgal"] else None
        res.append({"source": s["source"], "deals": s["deals"], "priced": s["priced"],
                    "incomplete": s["incomplete"], "gallons": round(s["gallons"], 0),
                    "margin_dollars": round(s["margin_dollars"], 0), "avg_cents_gal": cents})
    res.sort(key=lambda x: x["source"])
    return res


# ================================================================================
# STEP 4 — forward-fixed mark-to-market on the OPEN committed book
# ================================================================================
def forward_mtm(con, base: dict | None = None, cfg: MarginConfig = DEFAULT_CONFIG,
                today: dt.date | None = None) -> dict:
    """Mark every OPEN forward-fixed deal (future committed volume) to current replacement cost."""
    pricegrid.ensure_tables(con)
    cost_ix: CostIndex = (base or {}).get("cost_index") or CostIndex(pricegrid.read_landed_costs(con), cfg)
    deals = _read_deals(con)
    asof = _today(today)
    month0 = dt.date(asof.year, asof.month, 1)
    ff = deals[(deals["source"] == dealbook.SOURCE_FORWARD) & deals["committed_gallons"].notna()]
    rows: list[dict] = []
    for r in ff.itertuples(index=False):
        if pd.isna(r.month) or r.month.date() < month0:    # only OPEN (future) commitments
            continue
        if r.price is None or (r.committed_gallons or 0) <= 0:
            continue
        repl, lb = cost_ix.replacement(r.terminal, r.product_family, None)
        complete = lb == "all_in"
        mtm = None if repl is None else r.price - repl
        gal = float(r.committed_gallons)
        status = "cost_incomplete" if not complete or mtm is None else (
            "underwater" if mtm < 0 else
            "thin" if mtm * 100 < cfg.mtm_thin_cents else "ok")
        rows.append({
            "customer_master": r.customer_master, "customer_raw": r.customer_raw,
            "product_family": r.product_family, "terminal": r.terminal,
            "month": str(r.month.date()), "locked_sell": round(float(r.price), 4),
            "replacement_cost": None if repl is None else round(float(repl), 4),
            "mtm_per_gal": None if mtm is None else round(float(mtm), 5),
            "mtm_cents_gal": _cents(mtm), "committed_gallons": round(gal, 0),
            "mtm_dollars": None if mtm is None else round(mtm * gal, 0),
            "status": status, "cost_complete": bool(complete),
        })
    priced = [x for x in rows if x["mtm_dollars"] is not None]
    total_gal = sum(x["committed_gallons"] for x in rows)
    total_exposure = sum(x["mtm_dollars"] for x in priced)
    underwater = [x for x in priced if x["status"] == "underwater"]
    thin = [x for x in priced if x["status"] == "thin"]
    rows.sort(key=lambda x: (x["mtm_dollars"] if x["mtm_dollars"] is not None else 1e18))
    return {
        "as_of": asof.isoformat(),
        "open_deals": len(rows), "priced_deals": len(priced),
        "cost_incomplete_deals": len(rows) - len(priced),
        "committed_gallons": round(total_gal, 0),
        "mtm_total_dollars": round(total_exposure, 0),
        "underwater_deals": len(underwater), "thin_deals": len(thin),
        "underwater_exposure_dollars": round(sum(x["mtm_dollars"] for x in underwater), 0),
        "worst": rows[:15],
        "note": ("Mark-to-market on price-locked forward commitments. Deals flagged "
                 "'cost_incomplete' lack a recoverable cargo flat (index not loaded) and are "
                 "excluded from the MTM total."),
    }


# ================================================================================
# STEP 5 — margin-priced gap helper (Phase-3's hedge calls this)
# ================================================================================
def margin_for_gap(con, terminal: str | None, product: str | None, quantity_gallons: float,
                   cfg: MarginConfig = DEFAULT_CONFIG, month: dt.date | None = None,
                   today: dt.date | None = None) -> dict:
    """Price a demand quantity at a terminal×product: the $ margin at stake, split into
    committed/must-serve margin vs spot upside. The contract Phase-3's hedge layer reads.

    Volume up to the committed (term+forward) book is valued at its committed margin; the remainder is
    spot upside valued at the spot/replacement margin. Never imports hedging (one-way dependency).
    """
    pricegrid.ensure_tables(con)
    fam = _fam(product) if product else None
    qty = max(0.0, float(quantity_gallons or 0))
    base = build_base(con, cfg, "all", None, today=today)
    cost_ix: CostIndex = base.get("cost_index") or CostIndex(pricegrid.read_landed_costs(con), cfg)
    deals = _read_deals(con)
    asof = _today(today)
    month0 = month or dt.date(asof.year, asof.month, 1)

    def _cell(d):
        ok = (d["product_family"] == fam) if fam else d["product_family"].notna()
        if terminal:
            ok = ok & (d["terminal"].map(_n) == _n(terminal))
        return d[ok]

    # ---- committed / must-serve: term + forward for the reference month (or nearest future) ----
    committed = _cell(deals[deals["source"].isin([dealbook.SOURCE_TERM, dealbook.SOURCE_FORWARD])])
    committed = committed[committed["committed_gallons"].notna()]
    fut = committed[committed["month"].notna() & (committed["month"].dt.date >= month0)]
    pick = fut if len(fut) else committed
    # value committed volume at its blended deal-type margin
    dt_rows = deal_type_margins(con, base, cfg)["rows"]
    dt_by_key = {}
    for r in dt_rows:
        dt_by_key.setdefault((r["source"], r["customer_master"], r["product_family"], r["terminal"],
                              r["month"]), r)
    committed_gal = float(pick["committed_gallons"].sum())
    wsum = 0.0
    wgal = 0.0
    incomplete = 0
    for r in pick.itertuples(index=False):
        key = (r.source, r.customer_master, r.product_family, r.terminal,
               str(r.month.date()) if pd.notna(r.month) else None)
        dm = dt_by_key.get(key)
        g = float(r.committed_gallons or 0)
        if dm and dm["margin_per_gal"] is not None and g:
            wsum += dm["margin_per_gal"] * g
            wgal += g
        elif g:
            incomplete += 1
    committed_margin_gal = (wsum / wgal) if wgal else None

    # ---- spot upside: realized spot margin for the cell, else grid − replacement ----
    spot = _cell(deals[deals["source"] == dealbook.SOURCE_SPOT])
    spot_margin_gal = None
    if len(spot):
        sm_rows = [dt_by_key.get((dealbook.SOURCE_SPOT, r.customer_master, r.product_family, r.terminal,
                                  str(r.month.date()) if pd.notna(r.month) else None))
                   for r in spot.itertuples(index=False)]
        vals = [(x["margin_per_gal"], x["gallons"]) for x in sm_rows
                if x and x["margin_per_gal"] is not None and x["gallons"]]
        if vals:
            tot = sum(g for _, g in vals)
            spot_margin_gal = sum(m * g for m, g in vals) / tot if tot else None
    if spot_margin_gal is None:
        # fall back to the realized rack margin for that cell from the per-lift base
        lf = base["lifts"]
        if len(lf):
            cell = lf[lf["complete"] & (lf["family"] == fam if fam else lf["family"].notna())]
            if terminal:
                cell = cell[cell["terminal"].map(_n) == _n(terminal)]
            if len(cell):
                w = cell["net_gallons"].clip(lower=1.0)
                spot_margin_gal = float(np.average(cell["repl_margin"], weights=w))

    served_committed = min(qty, committed_gal)
    spot_gal = max(0.0, qty - committed_gal)
    committed_dollars = (served_committed * committed_margin_gal) if committed_margin_gal is not None else None
    spot_dollars = (spot_gal * spot_margin_gal) if spot_margin_gal is not None else None
    parts = [x for x in (committed_dollars, spot_dollars) if x is not None]
    total = sum(parts) if parts else None
    return {
        "terminal": terminal, "product": product, "product_family": fam,
        "quantity_gallons": round(qty, 0), "reference_month": month0.isoformat(),
        "committed_gallons": round(min(served_committed, qty), 0),
        "committed_margin_cents_gal": _cents(committed_margin_gal),
        "committed_margin_dollars": None if committed_dollars is None else round(committed_dollars, 0),
        "spot_gallons": round(spot_gal, 0),
        "spot_margin_cents_gal": _cents(spot_margin_gal),
        "spot_margin_dollars": None if spot_dollars is None else round(spot_dollars, 0),
        "total_margin_dollars": None if total is None else round(total, 0),
        "blended_margin_cents_gal": _cents(total / qty) if (total is not None and qty) else None,
        "available": bool(parts),
        "flags": {"committed_incomplete_cells": incomplete,
                  "spot_basis": "deal_realized" if len(spot) else "replacement_proxy",
                  "basis_assumption": cfg.term_basis_assumption},
    }


# ================================================================================
# Full payload (the API reads this)
# ================================================================================
def compute_margin(con, cfg: MarginConfig = DEFAULT_CONFIG, window: str = "all",
                   terminal: str | None = None, today: dt.date | None = None) -> dict:
    base = build_base(con, cfg, window, terminal, today=today)
    if not base["available"]:
        return {"window": window, "terminal": terminal, "available": False,
                "availability": base["availability"], "config": cfg.to_dict()}
    cust = customer_rollup(base, cfg)
    return {
        "window": window, "terminal": terminal, "as_of": base["as_of"],
        "today": base["today"], "available": True, "availability": base["availability"],
        "config": cfg.to_dict(),
        "coverage": coverage(base),
        "plausibility": plausibility(base, cfg),
        "deal_grid_crosscheck": deal_grid_crosscheck(con, base),
        "worked_example": worked_example(base),
        "customers": cust,
        "by_product": product_rollup(base),
        "by_terminal": terminal_rollup(base),
        "deal_type": deal_type_margins(con, base, cfg),
        "forward_mtm": forward_mtm(con, base, cfg, today=today),
        "value_vs_volume": _value_vs_volume(cust),
    }


def _value_vs_volume(cust: list[dict]) -> dict:
    """Surface the headline contrast: who ranks far higher on margin than volume, and vice versa."""
    if not cust:
        return {"fat_margin_movers": [], "thin_margin_movers": []}
    fat = sorted([c for c in cust if c["rank_delta"] > 0], key=lambda c: -c["rank_delta"])[:5]
    thin = sorted([c for c in cust if c["rank_delta"] < 0], key=lambda c: c["rank_delta"])[:5]
    pick = lambda c: {"name": c["name"], "rank_by_volume": c["rank_by_volume"],
                      "rank_by_margin": c["rank_by_margin"], "rank_delta": c["rank_delta"],
                      "book_cents_gal": c["book_cents_gal"], "gallons": c["gallons"]}
    return {"fat_margin_movers": [pick(c) for c in fat],
            "thin_margin_movers": [pick(c) for c in thin]}
