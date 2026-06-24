"""Pricing Sandbox + Pricing Engine (Blueprint I) — interactive what-if and the recommendation.

Gated on ``unit_price`` + ``rack_benchmark`` (the street/OPIS reference). Reads **P3's price
elasticity β** (``scoring`` — the accept-incidence slope per customer) and **P5's per-customer
forecast** (``demand`` — the persisted forecast distribution, falling back to the scoring lane's
annualized base volume) and produces two things:

  THE SANDBOX (interactive what-if)
    1. A single book-wide "our rack vs. street" SPREAD lever (a slider on the frontend).
    2. Per customer, project expected VOLUME and MARGIN at a chosen spread using that customer's
       elasticity β (acceptance shifts with the spread), aggregated to the book.
    3. The total-margin-vs-spread curve, with the MARGIN-MAXIMIZING post marked.
    4. Each customer flagged price-driven (big |β|, thin margin) vs. captive (β ≈ 0).
    5. Per-customer volume/margin *curves* over the spread grid so the frontend can toggle
       accounts in/out and re-find the optimum client-side (book-level sensitivity).

  THE ENGINE (Blueprint I — the recommendation, not just the what-if)
    6. A per-segment ACCEPTANCE model fit from the quote log:
         P(accept) = logistic(a + b·price_spread + c·customer_features + d·regime)
       (per-archetype where there is enough data, else a pooled model, else an elasticity proxy).
    7. For each active account, the QUOTE PRICE maximizing expected gross profit
         (price − cost) · expected_gallons · P(accept | price, regime)
       with the SHADOW PRICE of the binding constraint as a floor — never a discount below the
       street reference when the shadow price is positive (supply/capacity binding).
    8. Per-customer recommendation (price, accept-probability, expected GP) + a ranked list of
       today's pricing opportunities (underpriced vs. demonstrated willingness).

Every weight / grid / threshold lives in :class:`pricing_config.PricingConfig`.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import db, demand, schema, scoring
from .pricing_config import DEFAULT_CONFIG, PricingConfig, shadow_price
from .regime_config import normalize_regime, regime_label
from .scoring_config import DEFAULT_CONFIG as SCORING_DEFAULT
from .scoring_config import WINDOWS


# ---- Gating ---------------------------------------------------------------------
def _present(con, table: str, field: str) -> int:
    try:
        return int(con.execute(f'SELECT count("{field}") FROM {table}').fetchone()[0])
    except Exception:  # noqa: BLE001 — table/column may not exist on a thin store
        return 0


def availability(con, cfg: PricingConfig | None = None) -> dict:
    """Hard gate (unit_price + rack_benchmark) + the maturing 'collecting' feed state.

    The module is *locked* without unit_price or rack_benchmark. With both, it is available — but
    the acceptance model improves as the quote log + rack-benchmark history accumulate, so we also
    surface the same 'collecting' counters the Pricing feeds report (the lock/'collecting' state).
    """
    cfg = cfg or DEFAULT_CONFIG
    has_price = _present(con, schema.LIFTS, "unit_price") > 0
    has_rack = _present(con, schema.MARKET, "rack_benchmark") > 0
    has_cost = _present(con, schema.LIFTS, "unit_cost") > 0
    n_quotes = _present(con, schema.QUOTES, "quoted_price")
    try:
        rack_days = int(con.execute(
            "SELECT count(DISTINCT price_date) FROM market_prices WHERE rack_benchmark IS NOT NULL"
        ).fetchone()[0] or 0)
    except Exception:  # noqa: BLE001
        rack_days = 0

    missing = []
    if not has_price:
        missing.append("unit_price")
    if not has_rack:
        missing.append("rack_benchmark")
    reason = ("Pricing runs on realized price (unit_price) vs. the street/OPIS rack benchmark."
              if not missing else
              "Feed me " + " and ".join(missing) + " to unlock the Pricing Sandbox & Engine.")

    # Where the acceptance model comes from given what's logged.
    if n_quotes >= cfg.min_quotes_global:
        accept_source = "quote_model"
    elif rack_days > 0 and has_price and has_cost:
        accept_source = "elasticity_proxy"
    else:
        accept_source = "elasticity_proxy"
    return {
        "available": not missing, "missing_fields": missing, "reason": reason,
        "has_cost": has_cost,
        "acceptance_source": accept_source,
        "collecting": {
            "rack_benchmark": {"count": rack_days, "target": 30, "unit": "days",
                               "matured": rack_days >= 30},
            "quotes": {"count": n_quotes, "target": 50, "unit": "quotes",
                       "matured": n_quotes >= 50},
        },
    }


# ---- Tiny stats helpers ---------------------------------------------------------
def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0)))


def _pct_rank(values: dict) -> dict:
    """Percentile rank (0–100) of each non-null value across the book; None stays None."""
    items = [(k, float(v)) for k, v in values.items()
             if v is not None and not (isinstance(v, float) and math.isnan(v))]
    out = {k: None for k in values}
    if not items:
        return out
    ks = [k for k, _ in items]
    vs = np.array([v for _, v in items])
    if len(vs) == 1:
        out[ks[0]] = 50.0
        return out
    order = vs.argsort()
    ranks = np.empty(len(vs))
    ranks[order] = np.arange(1, len(vs) + 1)
    pct = (ranks - 0.5) / len(vs) * 100.0
    for k, p in zip(ks, pct):
        out[k] = round(float(p), 1)
    return out


# ---- Ridge logistic regression (self-contained IRLS) ----------------------------
def _fit_logistic(X: np.ndarray, y: np.ndarray, cfg: PricingConfig) -> dict | None:
    """Newton-IRLS logistic fit with an L2 ridge (no intercept penalty); standardizes inputs.

    Returns a model dict consumable by :func:`_logistic_predict`, or None if it can't fit.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    if X.ndim == 1:
        X = X[:, None]
    n, k = X.shape
    if n < k + 2 or len(np.unique(y)) < 2:
        return None
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd < 1e-9] = 1.0
    Xs = (X - mu) / sd
    A = np.column_stack([np.ones(n), Xs])
    w = np.zeros(k + 1)
    ridge = np.eye(k + 1) * cfg.logit_l2
    ridge[0, 0] = 0.0
    for _ in range(cfg.logit_max_iter):
        p = _sigmoid(A @ w)
        Wd = np.clip(p * (1.0 - p), 1e-6, None)
        grad = A.T @ (p - y) + ridge @ w
        H = A.T @ (A * Wd[:, None]) + ridge
        try:
            step = np.linalg.solve(H, grad)
        except np.linalg.LinAlgError:
            return None
        w = w - step
        if np.max(np.abs(step)) < 1e-7:
            break
    if not np.all(np.isfinite(w)):
        return None
    return {"w": w, "mu": mu, "sd": sd, "k": k}


def _logistic_predict(model: dict, X) -> np.ndarray:
    X = np.atleast_2d(np.asarray(X, dtype=float))
    Xs = (X - model["mu"]) / model["sd"]
    A = np.column_stack([np.ones(len(Xs)), Xs])
    return _sigmoid(A @ model["w"])


def _b_spread(model: dict) -> float:
    """The de-standardized coefficient on price_spread (feature 0) — should be negative."""
    return float(model["w"][1] / model["sd"][0])


# ---- Data loading ---------------------------------------------------------------
# Everything is measured in CONTEMPORANEOUS SPREAD SPACE — relative to the rack_benchmark on
# each lift's own date. This cancels the absolute street level and its seasonal trend (distillate
# rack drifts up in winter), and it is robust to multi-product customers, so a customer's margin
# at a posted spread ``s`` is simply ``s − cost_rel`` where ``cost_rel`` = vol-weighted
# (unit_cost − rack_at_lift). ``ref_today`` (the latest rack) is kept only to restate spreads as
# absolute quote prices for display.
def _ref_today_table(market: pd.DataFrame) -> dict:
    """Latest rack_benchmark keyed by (product, terminal), with (product, ·) and global fallbacks."""
    refs: dict = {}
    if not len(market):
        return refs
    mk = market.dropna(subset=["rack_benchmark"]).copy()
    if not len(mk):
        return refs
    mk["price_date"] = pd.to_datetime(mk["price_date"])
    mk = mk.sort_values("price_date")
    for (prod, term), g in mk.groupby([mk["product"].astype(str), mk["terminal"].astype(str)]):
        refs[(prod, term)] = float(g["rack_benchmark"].iloc[-1])
    for prod, g in mk.groupby(mk["product"].astype(str)):
        refs[(prod, None)] = float(g["rack_benchmark"].iloc[-1])
    refs[(None, None)] = float(mk["rack_benchmark"].iloc[-1])
    return refs


def _resolve_ref(refs: dict, product, terminal) -> float | None:
    for key in ((product, terminal), (product, None), (None, None)):
        if key in refs:
            return refs[key]
    return None


def _operating_points(lift_ref: pd.DataFrame, ref_today: dict, cfg: PricingConfig) -> dict:
    """Per-customer operating point in spread space (recent, volume-weighted, contemporaneous).

    ``lift_ref`` is lifts joined to that day's (product, terminal) rack benchmark. Returns, per
    customer: ``current_spread`` (price − rack), ``cost_rel`` (cost − rack), ``base_margin``
    (price − cost), display price/cost, dominant product/terminal, and ``ref_today``.
    """
    out: dict = {}
    if not len(lift_ref):
        return out
    lr = lift_ref.copy()
    lr["lift_datetime"] = pd.to_datetime(lr["lift_datetime"])
    miss = lr["rb"].isna()
    if miss.any():  # a lift on a date the rack feed missed → fall back to today's reference
        lr.loc[miss, "rb"] = lr.loc[miss].apply(
            lambda r: _resolve_ref(ref_today, str(r["product"]) if pd.notna(r["product"]) else None,
                                   r["terminal"]), axis=1)
    lr = lr.dropna(subset=["rb"])
    if not len(lr):
        return out
    cutoff = lr["lift_datetime"].max() - pd.Timedelta(days=cfg.recent_days_for_price)
    for cid, g0 in lr.groupby("customer_id"):
        g = g0[g0["lift_datetime"] >= cutoff]
        g = g if len(g) else g0
        w = pd.to_numeric(g["net_gallons"], errors="coerce").fillna(0.0).to_numpy()
        price = pd.to_numeric(g["unit_price"], errors="coerce").to_numpy()
        cost = pd.to_numeric(g["unit_cost"], errors="coerce").to_numpy()
        rb = pd.to_numeric(g["rb"], errors="coerce").to_numpy()

        def _wmean(vals):
            m = np.isfinite(vals) & (w > 0)
            if m.sum() and w[m].sum() > 0:
                return float(np.average(vals[m], weights=w[m]))
            vv = vals[np.isfinite(vals)]
            return float(vv.mean()) if len(vv) else None

        has_cost = bool(np.isfinite(cost).any())
        prod = g["product"].mode().iloc[0] if g["product"].notna().any() else None
        term = g["terminal"].mode().iloc[0] if g["terminal"].notna().any() else None
        out[cid] = {
            "current_price": _wmean(price), "cost": _wmean(cost) if has_cost else None,
            "current_spread": _wmean(price - rb),
            "cost_rel": _wmean(cost - rb) if has_cost else None,
            "base_margin": _wmean(price - cost) if has_cost else None,
            "product": prod, "terminal": term,
            "ref_today": _resolve_ref(ref_today, str(prod) if prod is not None else None, term),
        }
    return out


def _customer_quote_baseline(quotes: pd.DataFrame) -> dict:
    """Per-customer baseline accept rate (for the elasticity-proxy acceptance fallback)."""
    out: dict = {}
    if not len(quotes):
        return out
    for cid, g in quotes.groupby("customer_id"):
        acc = (g["outcome"].astype(str).str.lower() == "accept").mean()
        out[cid] = float(acc)
    return out


def _p5_annual_gallons(con, window: str, cfg: PricingConfig) -> dict:
    """P5's per-customer forecast → annualized gallons, read from the persisted distributions.

    Uses the per-terminal ALL-PRODUCTS rollup rows (so products aren't double-counted), summed
    over the 13-week horizon and annualized. Empty until ``/api/demand/persist`` has run — the
    caller then falls back to the scoring lane's annualized base volume.
    """
    try:
        rows = con.execute(
            "SELECT customer_id, sum(p50) AS q FROM demand_forecast_customer "
            "WHERE product = ? GROUP BY customer_id", [demand.ALL_PRODUCTS]).df()
    except Exception:  # noqa: BLE001 — table may not exist yet
        return {}
    if not len(rows):
        return {}
    factor = cfg.annual_weeks / max(1, cfg.forecast_horizon_weeks)
    return {r.customer_id: float(r.q) * factor for r in rows.itertuples(index=False)
            if r.q is not None and float(r.q) > 0}


# ---- Acceptance model -----------------------------------------------------------
ACCEPT_FEATURES = ["price_spread", "inv_tight", "cap_tight", "size_z"]


def fit_acceptance(quotes: pd.DataFrame, arche_by_id: dict, size_z_by_id: dict,
                   cfg: PricingConfig) -> dict:
    """Fit P(accept) = logistic(a + b·spread + c·size + d·regime), per-archetype with fallbacks.

    Segment by the (behavioral) archetype; a segment with ≥ ``min_quotes_segment`` quotes gets its
    own model, otherwise it borrows the pooled model. If the whole book has < ``min_quotes_global``
    quotes (or no usable spread), there is no fitted model and the engine uses the elasticity proxy.
    """
    base = {"source": "elasticity_proxy", "features": ACCEPT_FEATURES,
            "segments": {}, "pooled": None, "n_quotes": int(len(quotes)),
            "n_accept": 0, "b_spread": None}
    if not len(quotes):
        return base
    q = quotes.dropna(subset=["quoted_price", "market_price_at_quote"]).copy()
    if not len(q):
        return base
    q["spread"] = (pd.to_numeric(q["quoted_price"], errors="coerce")
                   - pd.to_numeric(q["market_price_at_quote"], errors="coerce"))
    q = q.dropna(subset=["spread"])
    q["y"] = (q["outcome"].astype(str).str.lower() == "accept").astype(float)
    q["inv_tight"] = q.get("inventory_state").astype(str).str.lower().isin(
        cfg.inv_tight_states).astype(float) if "inventory_state" in q else 0.0
    q["cap_tight"] = q.get("capacity_state").astype(str).str.lower().isin(
        cfg.cap_tight_states).astype(float) if "capacity_state" in q else 0.0
    q["arche"] = q["customer_id"].map(arche_by_id)
    q["size_z"] = q["customer_id"].map(size_z_by_id).fillna(0.0)
    base["n_accept"] = int(q["y"].sum())

    if len(q) < cfg.min_quotes_global or q["spread"].std() < 1e-6 or q["y"].nunique() < 2:
        return base

    def _design(df):
        return df[["spread", "inv_tight", "cap_tight", "size_z"]].to_numpy(float), df["y"].to_numpy(float)

    pooled = _fit_logistic(*_design(q), cfg)
    if pooled is None:
        return base
    base["source"] = "quote_model"
    base["pooled"] = {"model": pooled, "n": int(len(q)), "b_spread": _b_spread(pooled),
                      "intercept": float(pooled["w"][0])}
    base["b_spread"] = _b_spread(pooled)
    for arche, g in q.groupby("arche"):
        if len(g) >= cfg.min_quotes_segment and g["spread"].std() > 1e-6 and g["y"].nunique() == 2:
            m = _fit_logistic(*_design(g), cfg)
            if m is not None:
                base["segments"][str(arche)] = {"model": m, "n": int(len(g)),
                                                 "b_spread": _b_spread(m),
                                                 "intercept": float(m["w"][0])}
    return base


def _regime_flags(regime: dict | None, cfg: PricingConfig) -> tuple[float, float]:
    regime = regime or {}
    inv = 1.0 if regime.get("inventory") in cfg.inv_tight_states else 0.0
    cap = 1.0 if regime.get("capacity") in cfg.cap_tight_states else 0.0
    return inv, cap


def accept_prob(acc: dict, cust: dict, spread: float, regime: dict | None,
                cfg: PricingConfig) -> float:
    """P(accept) for one customer at a posted spread under a regime (model or proxy)."""
    inv_t, cap_t = _regime_flags(regime, cfg)
    if acc["source"] == "quote_model":
        seg = acc["segments"].get(cust["archetype"]) or acc["pooled"]
        x = [[spread, inv_t, cap_t, cust.get("size_z", 0.0)]]
        p = float(_logistic_predict(seg["model"], x)[0])
    else:
        beta = cust.get("beta")
        beta = beta if beta is not None else cfg.proxy_beta_default
        base = cust.get("baseline_accept")
        base = base if base is not None else cfg.default_accept_rate
        s0 = cust.get("current_spread") or 0.0
        # tight supply/capacity makes buyers a touch less price-sensitive (accept a bit more).
        regime_bump = 0.05 * inv_t + 0.03 * cap_t
        p = base + regime_bump + beta * (spread - s0)
    return float(min(cfg.accept_ceil, max(cfg.accept_floor, p)))


# ---- Base build (shared by sandbox + recommendations) ---------------------------
def build_base(con, cfg: PricingConfig | None = None, scfg=None, window: str = "all",
               terminal: str | None = None) -> dict:
    """Assemble the per-customer pricing base + the fitted acceptance model (the heavy half).

    Reads **P3 β** (scoring) and **P5 forecast** (demand) once. Returns everything the cheap,
    regime-dependent sandbox/recommendation derivations need.
    """
    cfg = cfg or DEFAULT_CONFIG
    scfg = scfg or SCORING_DEFAULT
    if window not in WINDOWS:
        window = "all"
    avail = availability(con, cfg)

    terminals = [r[0] for r in con.execute(
        "SELECT DISTINCT terminal FROM lifts WHERE terminal IS NOT NULL ORDER BY 1").fetchall()]
    products = [r[0] for r in con.execute(
        "SELECT DISTINCT product FROM lifts WHERE product IS NOT NULL ORDER BY 1").fetchall()]
    if terminal is not None and terminals and terminal not in terminals:
        terminal = None

    as_of = None
    if not avail["available"]:
        return {"available": False, "availability": avail, "window": window,
                "terminal": terminal, "terminals": terminals, "products": products,
                "as_of": None, "customers": [], "acceptance": None, "config": cfg.to_dict()}

    # Lifts joined to that day's (product, terminal) rack benchmark → contemporaneous spreads.
    lift_ref = con.execute(
        "SELECT l.customer_id, l.lift_datetime, l.net_gallons, l.product, l.terminal, "
        "       l.unit_price, l.unit_cost, m.rb "
        "FROM lifts l "
        "LEFT JOIN (SELECT price_date, product, terminal, avg(rack_benchmark) AS rb "
        "           FROM market_prices WHERE rack_benchmark IS NOT NULL GROUP BY 1, 2, 3) m "
        "  ON CAST(l.lift_datetime AS DATE) = m.price_date "
        " AND l.product = m.product AND l.terminal = m.terminal "
        "WHERE l.customer_id IS NOT NULL AND l.lift_datetime IS NOT NULL "
        "  AND l.unit_price IS NOT NULL").df()
    market = con.execute(
        "SELECT price_date, product, terminal, rack_benchmark FROM market_prices "
        "WHERE rack_benchmark IS NOT NULL").df() if db.row_count(con, schema.MARKET) else pd.DataFrame()
    quotes = con.execute(
        "SELECT customer_id, quote_time, product, quoted_price, market_price_at_quote, outcome, "
        "inventory_state, capacity_state, final_gallons FROM quotes").df() \
        if db.row_count(con, schema.QUOTES) else pd.DataFrame()
    if len(lift_ref):
        as_of = str(pd.to_datetime(lift_ref["lift_datetime"]).max().date())

    ref_today = _ref_today_table(market)
    ops = _operating_points(lift_ref, ref_today, cfg)
    quote_baseline = _customer_quote_baseline(quotes)
    p5_annual = _p5_annual_gallons(con, window, cfg)

    # P3 β + archetype + lane-base forecast come from the scoring engine (one call).
    score_res = scoring.compute_scores(con, scfg, window)
    sc_by_id = {c["customer_id"]: c for c in score_res["customers"]}

    # book-median β (signed) to fill customers without a recoverable elasticity.
    betas = [c["subscores"]["price_sensitivity"].get("beta") for c in score_res["customers"]]
    betas = [b for b in betas if b is not None]
    median_beta = float(np.median(betas)) if betas else cfg.proxy_beta_default

    # standardized log size (the c·customer_features term), computed across the scored book.
    sizes = {cid: math.log1p(max(0.0, p5_annual.get(cid, sc["base_value"]["annual_gallons"] or 0.0)))
             for cid, sc in sc_by_id.items()}
    sv = np.array(list(sizes.values()), dtype=float)
    s_mu, s_sd = (float(sv.mean()), float(sv.std())) if len(sv) else (0.0, 1.0)
    s_sd = s_sd if s_sd > 1e-9 else 1.0
    size_z_by_id = {cid: (v - s_mu) / s_sd for cid, v in sizes.items()}
    arche_by_id = {cid: sc["archetype"]["primary"] for cid, sc in sc_by_id.items()}

    customers: list[dict] = []
    for cid, sc in sc_by_id.items():
        op = ops.get(cid)
        if op is None:
            continue
        product = op.get("product")
        cterm = op.get("terminal") or sc.get("home_terminal")
        if terminal is not None and cterm != terminal:
            continue
        reference = op.get("ref_today")
        if reference is None:
            continue
        annual_gallons = p5_annual.get(cid) or (sc["base_value"]["annual_gallons"] or 0.0)
        if annual_gallons < cfg.min_annual_gallons:
            continue
        beta = sc["subscores"]["price_sensitivity"].get("beta")
        beta = beta if beta is not None else median_beta
        current_spread = op.get("current_spread") or 0.0
        cost_rel = op.get("cost_rel")
        current_price = op.get("current_price")
        if current_price is None:
            current_price = reference + current_spread
        customers.append({
            "customer_id": cid, "name": sc["name"],
            "archetype": sc["archetype"]["primary"],
            "secondary_archetype": sc["archetype"]["secondary"],
            "home_terminal": sc.get("home_terminal"), "product": product, "terminal": cterm,
            "reference": reference, "cost": op.get("cost"), "cost_rel": cost_rel,
            "current_price": current_price, "current_spread": current_spread,
            "margin_per_gal": op.get("base_margin"),
            "annual_gallons": float(annual_gallons),
            "forecast_source": "P5_persisted" if cid in p5_annual else "scoring_lane",
            "beta": float(beta),
            "beta_pctl": sc["subscores"]["price_sensitivity"].get("value"),
            "baseline_accept": quote_baseline.get(cid),
            "size_z": size_z_by_id.get(cid, 0.0),
            "base_value": sc["base_value"]["score"],
        })

    acceptance = fit_acceptance(quotes, arche_by_id, size_z_by_id, cfg)

    # |β| and margin percentiles drive the price-driven / captive classification.
    abs_beta_pctl = _pct_rank({c["customer_id"]: abs(c["beta"]) for c in customers})
    margin_pctl = _pct_rank({c["customer_id"]: c["margin_per_gal"] for c in customers})
    for c in customers:
        bp = abs_beta_pctl.get(c["customer_id"])
        mp = margin_pctl.get(c["customer_id"])
        c["abs_beta_pctl"] = bp
        c["margin_pctl"] = mp
        price_driven = (bp is not None and bp >= cfg.price_driven_beta_pctl
                        and (mp is None or mp <= cfg.thin_margin_pctl))
        captive = bp is not None and bp <= cfg.captive_beta_pctl
        c["elasticity_class"] = ("price_driven" if price_driven
                                 else "captive" if captive else "mixed")

    return {"available": True, "availability": avail, "window": window,
            "terminal": terminal, "terminals": terminals, "products": products,
            "as_of": as_of, "customers": customers, "acceptance": acceptance,
            "config": cfg.to_dict()}


def _acceptance_summary(acc: dict | None) -> dict | None:
    if not acc:
        return None
    return {"source": acc["source"], "features": acc["features"],
            "n_quotes": acc["n_quotes"], "n_accept": acc["n_accept"],
            "b_spread": round(acc["b_spread"], 4) if acc.get("b_spread") is not None else None,
            "segments": {a: {"n": s["n"], "b_spread": round(s["b_spread"], 4),
                             "intercept": round(s["intercept"], 4)}
                         for a, s in (acc.get("segments") or {}).items()},
            "pooled": ({"n": acc["pooled"]["n"], "b_spread": round(acc["pooled"]["b_spread"], 4),
                        "intercept": round(acc["pooled"]["intercept"], 4)}
                       if acc.get("pooled") else None)}


# ---- (1–5) The Sandbox ----------------------------------------------------------
def _spread_grid(cfg: PricingConfig) -> np.ndarray:
    n = int(round((cfg.spread_max - cfg.spread_min) / cfg.spread_step)) + 1
    return np.round(cfg.spread_min + cfg.spread_step * np.arange(n), 6)


def sandbox(base: dict, cfg: PricingConfig | None = None, regime: dict | None = None) -> dict:
    """Per-customer volume/margin curves over the spread grid + the margin-maximizing post.

    The sandbox is evaluated at a neutral (default) regime — it is about the posted *spread*, not
    today's regime. The frontend sums the per-customer ``margin_curve`` for the toggled-in accounts
    and re-finds the optimum client-side (book-level sensitivity, no refetch).
    """
    cfg = cfg or DEFAULT_CONFIG
    grid = _spread_grid(cfg)
    acc = base["acceptance"]
    has_cost = base["availability"].get("has_cost", False)
    cust_out: list[dict] = []
    agg_margin = np.zeros(len(grid))
    agg_volume = np.zeros(len(grid))
    realized_margin = 0.0   # Σ annual_gallons · realized margin/gal (each at its own spread)
    wsum = 0.0
    wspread = 0.0

    for c in base["customers"]:
        cost_rel = c.get("cost_rel")
        priced = has_cost and cost_rel is not None
        p_cur = accept_prob(acc, c, c["current_spread"], regime, cfg)
        probs = np.array([accept_prob(acc, c, float(s), regime, cfg) for s in grid])
        ratio = np.clip(probs / p_cur if p_cur > 1e-9 else np.ones_like(probs),
                        cfg.vol_ratio_floor, cfg.vol_ratio_ceil)
        volume = c["annual_gallons"] * ratio
        # Margin in spread space: at posted spread s, margin/gal = s − cost_rel (street cancels).
        margin = volume * (grid - cost_rel) if priced else np.full(len(grid), np.nan)
        agg_volume += volume
        if priced:
            agg_margin += margin
            realized_margin += c["annual_gallons"] * (c["current_spread"] - cost_rel)
        wsum += c["annual_gallons"]
        wspread += c["annual_gallons"] * c["current_spread"]

        cust_out.append({
            "customer_id": c["customer_id"], "name": c["name"], "archetype": c["archetype"],
            "product": c["product"], "terminal": c["terminal"],
            "beta": round(c["beta"], 5), "beta_pctl": c["abs_beta_pctl"],
            "margin_pctl": c["margin_pctl"], "elasticity_class": c["elasticity_class"],
            "base_annual_gallons": round(c["annual_gallons"], 0),
            "cost": round(c["cost"], 4) if c["cost"] is not None else None,
            "reference": round(c["reference"], 4),
            "current_price": round(c["current_price"], 4),
            "current_spread": round(c["current_spread"], 4),
            "margin_per_gal": round(c["margin_per_gal"], 4) if c["margin_per_gal"] is not None else None,
            "forecast_source": c["forecast_source"],
            "volume_curve": [round(float(v), 1) for v in volume],
            "margin_curve": [round(float(m), 1) if np.isfinite(m) else None for m in margin],
        })

    current_spread = round(wspread / wsum, 4) if wsum else 0.0
    opt_idx = int(np.argmax(agg_margin)) if has_cost and len(agg_margin) and np.isfinite(agg_margin).any() else None
    cur_idx = int(np.argmin(np.abs(grid - current_spread)))
    total_curve = [{"spread": round(float(grid[i]), 4),
                    "margin": round(float(agg_margin[i]), 1) if has_cost else None,
                    "volume": round(float(agg_volume[i]), 1)} for i in range(len(grid))]

    return {
        "grid": [round(float(s), 4) for s in grid],
        "has_cost": has_cost,
        "current_spread": current_spread,
        "current_margin": round(float(agg_margin[cur_idx]), 1) if has_cost else None,
        "current_volume": round(float(agg_volume[cur_idx]), 1),
        "realized_margin": round(realized_margin, 1) if has_cost else None,
        "optimal_spread": round(float(grid[opt_idx]), 4) if opt_idx is not None else None,
        "optimal_margin": round(float(agg_margin[opt_idx]), 1) if opt_idx is not None else None,
        "optimal_volume": round(float(agg_volume[opt_idx]), 1) if opt_idx is not None else None,
        "margin_uplift": (round(float(agg_margin[opt_idx]) - realized_margin, 1)
                          if opt_idx is not None else None),
        "total_margin_curve": total_curve,
        "n_customers": len(cust_out),
        "n_price_driven": sum(1 for c in cust_out if c["elasticity_class"] == "price_driven"),
        "n_captive": sum(1 for c in cust_out if c["elasticity_class"] == "captive"),
        "customers": cust_out,
    }


# ---- (6–8) The Engine: recommended quote price ----------------------------------
def _spread_search_grid(cfg: PricingConfig) -> np.ndarray:
    n = int(round((cfg.price_search_max - cfg.price_search_min) / cfg.price_search_step)) + 1
    return np.round(cfg.price_search_min + cfg.price_search_step * np.arange(n), 6)


def _recommend_one(c: dict, acc: dict, regime: dict, shadow: float, cfg: PricingConfig) -> dict | None:
    """GP-maximizing quote price for one customer, with the shadow price as a floor.

    Works in spread space (vs. the street reference): contribution(s) = vol(s)·(s − cost_rel −
    shadow). The floor is s ≥ cost_rel + shadow (breakeven incl. the opportunity cost), and when
    the shadow price is positive we never post a discount below the street (s ≥ 0).
    """
    cost_rel = c.get("cost_rel")
    if cost_rel is None:
        return None
    ref = c["reference"]
    grid = _spread_search_grid(cfg)
    p_cur = accept_prob(acc, c, c["current_spread"], regime, cfg)

    floor_spread = cost_rel + shadow
    if shadow > 0:
        floor_spread = max(floor_spread, 0.0)   # binding constraint → never discount below street
    feasible = grid[grid >= floor_spread - 1e-9]
    if not len(feasible):
        feasible = np.array([max(float(grid.min()), floor_spread)])

    probs = np.array([accept_prob(acc, c, float(s), regime, cfg) for s in feasible])
    ratio = np.clip(probs / p_cur if p_cur > 1e-9 else np.ones_like(probs),
                    cfg.vol_ratio_floor, cfg.vol_ratio_ceil)
    volume = c["annual_gallons"] * ratio
    gp = volume * (feasible - cost_rel)
    contribution = volume * (feasible - cost_rel - shadow)   # shadow-adjusted objective
    best = int(np.argmax(contribution))

    rec_spread = float(feasible[best])
    rec_price = ref + rec_spread
    rec_accept = float(probs[best])
    rec_vol = float(volume[best])
    expected_gp = float(gp[best])
    current_gp = c["annual_gallons"] * (c["current_spread"] - cost_rel)
    gp_uplift = expected_gp - current_gp
    price_gap = rec_spread - c["current_spread"]        # change vs. today's street-restated price
    underpriced = price_gap > cfg.underpriced_min_gap and gp_uplift > 0

    return {
        "customer_id": c["customer_id"], "name": c["name"], "archetype": c["archetype"],
        "secondary_archetype": c["secondary_archetype"],
        "home_terminal": c["home_terminal"], "product": c["product"], "terminal": c["terminal"],
        "reference": round(ref, 4), "cost": round(c["cost"], 4) if c["cost"] is not None else None,
        "current_price": round(ref + c["current_spread"], 4),   # restated at today's street
        "current_spread": round(c["current_spread"], 4),
        "recommended_price": round(rec_price, 4),
        "recommended_spread": round(rec_spread, 4),
        "price_gap": round(price_gap, 4),
        "accept_prob": round(rec_accept, 3),
        "current_accept_prob": round(p_cur, 3),
        "expected_gallons": round(rec_vol, 0),
        "expected_gp": round(expected_gp, 0),
        "current_gp": round(current_gp, 0),
        "gp_uplift": round(gp_uplift, 0),
        "margin_per_gal": round(c["current_spread"] - cost_rel, 4),
        "rec_margin_per_gal": round(rec_spread - cost_rel, 4),
        "shadow_price": round(shadow, 4),
        "floor_spread": round(floor_spread, 4),
        "beta": round(c["beta"], 5),
        "elasticity_class": c["elasticity_class"],
        "underpriced": bool(underpriced),
        "direction": "raise" if price_gap > cfg.underpriced_min_gap else (
            "cut" if price_gap < -cfg.underpriced_min_gap else "hold"),
        "base_value": c["base_value"],
        "forecast_source": c["forecast_source"],
    }


def recommendations(base: dict, cfg: PricingConfig | None = None, regime: dict | None = None) -> dict:
    """Per-customer GP-maximizing quote prices + today's ranked pricing opportunities."""
    cfg = cfg or DEFAULT_CONFIG
    regime = normalize_regime(regime)
    shadow = shadow_price(regime, cfg)
    acc = base["acceptance"]

    recs = []
    for c in base["customers"]:
        r = _recommend_one(c, acc, regime, shadow, cfg)
        if r is not None:
            recs.append(r)

    cur_gp = sum(r["current_gp"] for r in recs)
    opt_gp = sum(r["expected_gp"] for r in recs)
    recs_by_uplift = sorted(recs, key=lambda r: r["gp_uplift"], reverse=True)
    underpriced = [r for r in recs_by_uplift if r["underpriced"]]

    return {
        "regime": regime, "regime_label": regime_label(regime),
        "shadow_price": round(shadow, 4),
        "has_cost": base["availability"].get("has_cost", False),
        "acceptance_source": acc["source"] if acc else "elasticity_proxy",
        "n": len(recs),
        "current_gp_per_yr": round(cur_gp, 0),
        "optimized_gp_per_yr": round(opt_gp, 0),
        "gp_uplift_per_yr": round(opt_gp - cur_gp, 0),
        "n_underpriced": len(underpriced),
        "recommendations": recs_by_uplift,
        "top_underpriced": underpriced,
    }


# ---- Orchestration --------------------------------------------------------------
def compute_pricing(con, cfg: PricingConfig | None = None, scfg=None, window: str = "all",
                    terminal: str | None = None, regime: dict | None = None) -> dict:
    """Full pricing payload: availability + acceptance model + sandbox + recommendations."""
    cfg = cfg or DEFAULT_CONFIG
    base = build_base(con, cfg, scfg, window, terminal)
    scope = {"window": window, "terminal": base["terminal"], "terminals": base["terminals"],
             "products": base["products"], "as_of": base["as_of"], "config": cfg.to_dict()}
    if not base["available"]:
        return {**scope, "available": False, "availability": base["availability"],
                "acceptance": None, "sandbox": None, "recommendations": None}
    return {
        **scope, "available": True, "availability": base["availability"],
        "acceptance": _acceptance_summary(base["acceptance"]),
        "sandbox": sandbox(base, cfg, regime=None),
        "recommendations": recommendations(base, cfg, regime),
    }
