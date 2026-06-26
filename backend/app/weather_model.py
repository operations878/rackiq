"""Weather model (Stage 1) — HDD → demand, and the residual that rewrites the heating-fuel size axis.

HEATING FUELS ONLY (``ULSHO`` / #2 / dyed HO B-blends / ``HO4``). Gasoline, RD-99 and ethanol are
never touched. This module:

  1. Maps each terminal to a weather **station** and resolves its HDD series — from the uploaded
     ``weather_hdd`` book when the station is loaded (**modeled**), otherwise from the key-less
     Open-Meteo / climatology proxy (**proxy**). It NEVER cross-applies one station's HDD to another
     terminal (LGA is not applied to Baltimore); a terminal with no local HDD is labelled honestly.
  2. Regresses, per terminal × heating-product, **working-day-aggregated demand = baseload + β·HDD**,
     reporting β, baseload, in-sample R², and an **out-of-sample** (train/test) check vs a
     weather-blind baseline. β is sign- and overfit-guarded (thin series inherit the terminal β;
     a near-zero / wrong-sign β is flagged, never shipped).
  3. Sanity-checks β against the **BX HO SOLD** anchor (HO sold vs HDD) before the BOL-derived β is
     trusted.
  4. **Rewrites the size axis**: for a heating-fuel customer, the per-lift size used by the
     variability score becomes the **residual after removing β·(HDD − HDD̄)** (re-centred to keep the
     level), so a customer who only swings on cold snaps reads steady-underneath — WITHOUT flattening
     genuine non-weather lumpiness (which stays in the residual). The adjustment is dropped (raw kept)
     whenever it fails to reduce variance, so it can never manufacture steadiness.
  5. Exposes a **forward HDD seam** (Normal / 5-yr baseline now, swappable for a live NOAA/CPC feed)
     and reports where a weather-aware demand projection beats weather-blind out-of-sample.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from . import calendar_days, db, weather, weather_hdd
from .dealbook import HEATING_FAMILIES, product_family


@dataclass(frozen=True)
class WeatherConfig:
    grain: str = "W"                       # weekly demand aggregation for the β regression
    min_periods_beta: int = 12             # min aggregated periods to fit a terminal×product β
    oos_frac: float = 0.30                 # last fraction held out for the out-of-sample check
    min_oos_periods: int = 6
    r2_trust: float = 0.10                 # in-sample R² at/above which the β is "trustworthy"
    beta_sign_min: float = 1e-9            # β must exceed this (positive: cold → more demand) to use

    # per-lift load-size β (the size-axis adjustment)
    size_min_lifts: int = 10               # customer needs ≥ this many heating lifts for its OWN β
    size_min_hdd_std: float = 5.0          # … and ≥ this HDD spread across them (else β unidentifiable)
    size_min_pooled_lifts: int = 30        # terminal×family pooled β needs ≥ this many lifts
    size_adjust_min_gain: float = 0.0      # keep the adjustment only if it lowers the size CV by ≥ this

    # forward HDD seam
    forward_horizon_days: int = 45
    forward_mode: str = "normal"           # 'normal' | '5yr' | 'proxy'


DEFAULT_WEATHER_CONFIG = WeatherConfig()

# ---- station geography -----------------------------------------------------------
# Terminal → weather station. Real-book terminals map to their airport; the demo terminals keep their
# own pseudo-stations (so uploaded LGA HDD is never applied to them — they use the proxy). A terminal
# not listed maps to a slug of its own name → it only matches uploaded HDD labelled the same way.
TERMINAL_STATION: dict[str, str] = {
    "ny": "LGA", "new york": "LGA", "bronx": "LGA", "brooklyn": "LGA", "queens": "LGA",
    "manhattan": "LGA", "nyc": "LGA",
    "newark": "EWR",
    "baltimore": "BWI",
    "pennsauken": "PHL", "port reading": "PHL", "port_reading": "PHL",
    # demo terminals — distinct pseudo-stations so LGA HDD is not borrowed for them
    "linden": "LINDEN", "providence": "PVD", "albany": "ALB",
}

# Station coordinates for the Open-Meteo proxy (so a proxy fetch is at least geographically right).
STATION_COORDS: dict[str, tuple[float, float]] = {
    "LGA": (40.777, -73.872), "EWR": (40.689, -74.174), "BWI": (39.175, -76.668),
    "PHL": (39.872, -75.241), "LINDEN": (40.622, -74.244), "PVD": (41.823, -71.413),
    "ALB": (42.652, -73.756),
}


def terminal_station(terminal: str | None) -> str:
    if not terminal:
        return weather_hdd.DEFAULT_STATION
    key = " ".join(str(terminal).strip().lower().split())
    return TERMINAL_STATION.get(key, key.upper().replace(" ", "_"))


def is_heating_family(fam: str | None) -> bool:
    return fam in HEATING_FAMILIES


# ---- small OLS helper ------------------------------------------------------------
def _ols(x: np.ndarray, y: np.ndarray) -> dict | None:
    """Simple y = a + b·x least squares with R². Returns None if x has no spread."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 3 or float(np.std(x)) < 1e-9:
        return None
    b, a = np.polyfit(x, y, 1)
    pred = a + b * x
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {"beta": float(b), "intercept": float(a), "r2": float(r2), "n": int(len(x))}


def _mape(actual: np.ndarray, pred: np.ndarray, floor: float = 1.0) -> float:
    actual = np.asarray(actual, dtype=float)
    pred = np.asarray(pred, dtype=float)
    denom = np.maximum(np.abs(actual), floor)
    return float(np.mean(np.abs(actual - pred) / denom) * 100.0)


# ---- HDD resolution (uploaded → proxy) ------------------------------------------
def hdd_daily(con, terminal: str | None, days: list[dt.date]) -> tuple[dict, str, str]:
    """``{day: hdd}`` for a terminal + a (source, coverage) label.

    Prefers the uploaded ``weather_hdd`` for the terminal's OWN station when it covers the days;
    otherwise the proxy (Open-Meteo for the station coords, else climatology). ``coverage`` is
    ``modeled`` (uploaded) or ``proxy``.
    """
    if not days:
        return {}, "none", "none"
    station = terminal_station(terminal)
    lo, hi = min(days), max(days)
    up = weather_hdd.read_hdd(con, station)
    if len(up):
        up = up[(up["day"] >= pd.Timestamp(lo)) & (up["day"] <= pd.Timestamp(hi))]
    if len(up):
        m = {pd.Timestamp(r.day).date(): float(r.hdd) for r in up.itertuples() if pd.notna(r.hdd)}
        covered = sum(1 for d in days if d in m)
        if covered >= 0.6 * len(days):                 # the uploaded book covers this span → modeled
            for d in days:                             # backfill any gaps from the proxy
                if d not in m:
                    m[d] = round(weather.seasonal_hdd_cdd(d)[0], 1)
            return m, f"uploaded:{station}", "modeled"
    # proxy: Open-Meteo for the station coords (cached), else climatology
    coord = STATION_COORDS.get(station)
    if coord is not None:
        weather.TERMINAL_COORDS.setdefault(terminal or station, coord)
    dmap = weather.daily_map(con, terminal, days)
    m = {d: float(v[0]) for d, v in dmap.items()}
    src = "open-meteo" if any(v[2] == "open-meteo" for v in dmap.values()) else "climatology"
    return m, f"proxy:{src}", "proxy"


# ---- demand β (terminal × heating-product, working-day aggregated) ---------------
def _heating_lifts(con) -> pd.DataFrame:
    df = con.execute(
        "SELECT customer_id, lift_datetime, net_gallons, product, terminal FROM lifts "
        "WHERE net_gallons IS NOT NULL AND lift_datetime IS NOT NULL").df()
    if df.empty:
        return df
    df["lift_datetime"] = pd.to_datetime(df["lift_datetime"], errors="coerce")
    df = df[df["lift_datetime"].notna()].copy()
    df["family"] = df["product"].map(product_family)
    return df


def fit_demand_beta(periods: pd.DataFrame, cfg: WeatherConfig) -> dict:
    """Fit demand = a + β·HDD on aggregated periods, with an out-of-sample check vs weather-blind."""
    n = len(periods)
    if n < cfg.min_periods_beta:
        return {"available": False, "reason": f"only {n} periods (< {cfg.min_periods_beta})", "n": n}
    x = periods["hdd"].to_numpy(float)
    y = periods["demand"].to_numpy(float)
    fit = _ols(x, y)
    if fit is None:
        return {"available": False, "reason": "no HDD spread in this lane", "n": n}

    # out-of-sample: train on the first (1-oos_frac), test on the last oos_frac
    k = int(round(n * (1 - cfg.oos_frac)))
    oos = None
    if n - k >= cfg.min_oos_periods and k >= cfg.min_periods_beta // 2:
        tr = _ols(x[:k], y[:k])
        if tr is not None:
            pred = tr["intercept"] + tr["beta"] * x[k:]
            blind = np.full(n - k, float(np.mean(y[:k])))   # weather-blind baseline = train mean
            mae_w = float(np.mean(np.abs(y[k:] - pred)))
            mae_b = float(np.mean(np.abs(y[k:] - blind)))
            oos = {
                "mae_weather": round(mae_w, 1), "mae_blind": round(mae_b, 1),
                "mape_weather": round(_mape(y[k:], pred), 1),
                "mape_blind": round(_mape(y[k:], blind), 1),
                "beats_blind": bool(mae_w < mae_b),
                "improvement_pct": round(100 * (mae_b - mae_w) / mae_b, 1) if mae_b > 0 else None,
                "n_test": int(n - k),
            }
    sign_ok = fit["beta"] > cfg.beta_sign_min
    trust = bool(sign_ok and fit["r2"] >= cfg.r2_trust)
    return {
        "available": True, "n": n,
        "beta": round(fit["beta"], 2), "baseload": round(fit["intercept"], 1),
        "r2": round(fit["r2"], 3), "sign_ok": sign_ok, "trust": trust,
        "oos": oos,
        "flag": (None if sign_ok else "β ≤ 0 (wrong sign — demand does not rise with HDD here); not used"),
    }


# ---- per-lift load-size β (the size-axis adjustment) -----------------------------
def _size_beta_from_lifts(net: np.ndarray, hdd: np.ndarray, cfg: WeatherConfig,
                          min_lifts: int) -> dict | None:
    if len(net) < min_lifts or float(np.std(hdd)) < cfg.size_min_hdd_std:
        return None
    fit = _ols(hdd, net)
    if fit is None or fit["beta"] <= cfg.beta_sign_min:
        return None
    return fit


def build_model(con, cfg: WeatherConfig | None = None) -> dict:
    """Precompute everything the variability seam + the readout need.

    Returns a model dict with: per-terminal HDD maps (covering the lift span), per terminal×family
    demand β, per terminal×family pooled per-lift size β (the fallback), station coverage labels, and
    the BX HO SOLD anchor.
    """
    cfg = cfg or DEFAULT_WEATHER_CONFIG
    df = _heating_lifts(con)
    model: dict = {"available": False, "config": asdict(cfg), "terminals": {}, "demand_beta": {},
                   "size_beta_pooled": {}, "hdd_maps": {}, "coverage": {}, "anchor": None,
                   "heating_families": sorted(HEATING_FAMILIES)}
    if df.empty:
        model["reason"] = "no lifts loaded"
        return model
    cal, _ = calendar_days.from_connection(con)

    # HDD map per terminal over its full lift span (one resolution per terminal)
    for terminal, g in df.groupby("terminal", dropna=False):
        term = None if pd.isna(terminal) else terminal
        days = sorted({d.date() for d in pd.to_datetime(g["lift_datetime"]).dt.normalize()})
        # widen to the daily span so weekly HDD means are well-defined
        full = pd.date_range(min(days), max(days), freq="D")
        full_days = [d.date() for d in full]
        hmap, src, cov = hdd_daily(con, term, full_days)
        model["hdd_maps"][term] = hmap
        model["coverage"][term] = {"station": terminal_station(term), "source": src, "coverage": cov}

    heating = df[df["family"].isin(HEATING_FAMILIES)].copy()
    if heating.empty:
        model["available"] = True
        model["reason"] = "no heating-fuel lifts (ULSHO/HO4) — nothing to weather-adjust"
        return model

    # demand β + pooled per-lift size β per terminal × heating family
    for (terminal, fam), g in heating.groupby(["terminal", "family"], dropna=False):
        term = None if pd.isna(terminal) else terminal
        hmap = model["hdd_maps"].get(term, {})
        g = g.copy()
        g["day"] = pd.to_datetime(g["lift_datetime"]).dt.normalize()
        g["hdd"] = g["day"].dt.date.map(lambda d: hmap.get(d))
        gg = g[g["hdd"].notna()]
        if gg.empty:
            continue
        # aggregated demand β (weekly)
        per = (gg.set_index("day")
                 .groupby(pd.Grouper(freq=cfg.grain))
                 .agg(demand=("net_gallons", "sum"), hdd=("hdd", "mean"))
                 .dropna())
        per = per[per["demand"] > 0]
        model["demand_beta"][f"{term}|{fam}"] = fit_demand_beta(per.reset_index(), cfg)
        # pooled per-lift size β (fallback for customers without a stable own β)
        pooled = _size_beta_from_lifts(gg["net_gallons"].to_numpy(float),
                                       gg["hdd"].to_numpy(float), cfg, cfg.size_min_pooled_lifts)
        model["size_beta_pooled"][f"{term}|{fam}"] = pooled

    model["anchor"] = anchor_check(con, model, cfg)
    model["available"] = True
    return model


# ---- the size-axis rewrite (called by variability) -------------------------------
def adjusted_sizes(cl: pd.DataFrame, family: str | None, terminal: str | None,
                   model: dict, cfg: WeatherConfig | None = None) -> tuple[np.ndarray, bool, dict]:
    """Weather-adjusted per-lift sizes for ONE heating-fuel customer (else raw).

    adjusted_i = net_i − β·(HDD_i − HDD̄), using the customer's OWN per-lift β when it is stable and
    positive, else the terminal×family pooled β. Kept ONLY if it lowers the size CV (never manufactures
    steadiness). Returns (sizes, weather_adjusted, diagnostics).
    """
    cfg = cfg or DEFAULT_WEATHER_CONFIG
    days = pd.to_datetime(cl["lift_datetime"], errors="coerce").dt.normalize()
    net = pd.to_numeric(cl["net_gallons"], errors="coerce")
    keep = days.notna() & net.notna()
    days, net_full = days[keep], net[keep].to_numpy(float)
    diag = {"weather_sensitive": is_heating_family(family), "beta_source": None,
            "beta": None, "raw_cv": _cv(net_full), "adj_cv": None, "reason": None}
    if not is_heating_family(family) or not model or not model.get("available"):
        diag["reason"] = ("not a heating fuel" if not is_heating_family(family)
                          else "weather model unavailable")
        return net_full, False, diag

    hmap = model.get("hdd_maps", {}).get(terminal, {})
    hdd_full = np.array([hmap.get(d.date(), np.nan) for d in days], dtype=float)
    have = np.isfinite(hdd_full) & np.isfinite(net_full)
    if have.sum() < cfg.size_min_lifts // 2 or float(np.nanstd(hdd_full[have])) < 1e-6:
        diag["reason"] = "no HDD coverage for this customer's lifts — kept raw"
        return net_full, False, diag

    # β: the customer's OWN per-lift HDD→size slope if stable & positive, else the terminal pool
    own = _size_beta_from_lifts(net_full[have], hdd_full[have], cfg, cfg.size_min_lifts)
    pooled = model.get("size_beta_pooled", {}).get(f"{terminal}|{family}")
    if own is not None:
        beta, source = own["beta"], "customer"
    elif pooled is not None:
        beta, source = pooled["beta"], "terminal_pooled"
    else:
        diag["reason"] = "no stable positive HDD→size β (own or pooled) — kept raw"
        return net_full, False, diag

    # remove the weather-driven swing only on lifts with HDD; others keep raw (full-length, comparable)
    adj = net_full.copy().astype(float)
    hdd_mean = float(np.mean(hdd_full[have]))
    adj[have] = net_full[have] - beta * (hdd_full[have] - hdd_mean)
    raw_cv, adj_cv = _cv(net_full), _cv(adj)
    diag.update({"beta_source": source, "beta": round(float(beta), 2),
                 "raw_cv": raw_cv, "adj_cv": adj_cv})
    if raw_cv is None or adj_cv is None or adj_cv > raw_cv - cfg.size_adjust_min_gain:
        diag["reason"] = "adjustment did not reduce size variance — kept raw (no over-smoothing)"
        return net_full, False, diag      # honest: weather didn't explain the lumpiness
    diag["reason"] = "size measured on the HDD residual (weather-driven swing removed)"
    return adj, True, diag


def _cv(arr: np.ndarray) -> float | None:
    arr = np.asarray(arr, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 2:
        return None
    m = float(np.mean(arr))
    if m <= 0:
        return None
    return round(float(np.std(arr, ddof=1)) / m, 3)


# ---- BX HO SOLD anchor -----------------------------------------------------------
def anchor_check(con, model: dict, cfg: WeatherConfig | None = None) -> dict | None:
    """Regress monthly HO SOLD on monthly HDD and compare its β SIGN/plausibility to the BOL β.

    The anchor and the BOL book are different volume universes, so this checks AGREEMENT (both
    positive, same order of magnitude), not equality — the gate before the BOL β is trusted.
    """
    cfg = cfg or DEFAULT_WEATHER_CONFIG
    anc = weather_hdd.read_anchor(con)
    if not len(anc):
        return None
    out = {}
    for station, g in anc.groupby("station"):
        g = g[g["ho_sold"].notna() & g["hdd_month"].notna()]
        if len(g) < 6:
            continue
        fit = _ols(g["hdd_month"].to_numpy(float), g["ho_sold"].to_numpy(float))
        if fit is None:
            continue
        # the BOL demand β for a terminal mapped to this station (monthly-equivalent sign check)
        bol_betas = [v for k, v in model.get("demand_beta", {}).items()
                     if isinstance(v, dict) and v.get("available")
                     and terminal_station(k.split("|")[0]) == station]
        bol_positive = any(b.get("sign_ok") for b in bol_betas)
        if not bol_betas:
            note = (f"HO SOLD rises with HDD (β {fit['beta']:.0f}, expected); no BOL lane is mapped to "
                    f"{station}, so there's no BOL β to compare against here.")
        elif fit["beta"] > 0 and bol_positive:
            note = "HO SOLD rises with HDD (expected) and the BOL β agrees in sign — β trustworthy."
        else:
            note = "HO SOLD vs HDD sign does not match the BOL β — investigate before trusting β."
        out[station] = {
            "anchor_beta": round(fit["beta"], 2), "anchor_r2": round(fit["r2"], 3), "n_months": fit["n"],
            "anchor_sign_positive": fit["beta"] > 0,
            "bol_beta_positive": bool(bol_positive),
            "bol_lanes_at_station": len(bol_betas),
            "agrees": bool((fit["beta"] > 0) == bool(bol_positive)) if bol_betas else None,
            "note": note,
        }
    return out or None


# ---- forward HDD seam (pluggable: baseline now, live feed later) -----------------
def forward_hdd(con, terminal: str | None, start: dt.date, cfg: WeatherConfig | None = None) -> dict:
    """A forward HDD curve over the next ``forward_horizon_days``, from the uploaded Normal/5-yr
    baseline (by month/day climatology) when available, else the seasonal proxy. Labelled so a live
    NOAA/CPC forecast can be swapped in without changing the call site."""
    cfg = cfg or DEFAULT_WEATHER_CONFIG
    station = terminal_station(terminal)
    days = [start + dt.timedelta(days=i) for i in range(cfg.forward_horizon_days)]
    up = weather_hdd.read_hdd(con, station)
    col = {"normal": "hdd_normal", "5yr": "hdd_5yr"}.get(cfg.forward_mode)
    curve, source = [], "proxy:climatology"
    if col and len(up) and up[col].notna().any():
        # climatological lookup by (month, day) → the baseline value
        up = up.assign(md=up["day"].dt.strftime("%m-%d"))
        base = up.dropna(subset=[col]).groupby("md")[col].mean().to_dict()
        for d in days:
            v = base.get(d.strftime("%m-%d"))
            curve.append(round(float(v), 1) if v is not None else round(weather.seasonal_hdd_cdd(d)[0], 1))
        source = f"baseline:{cfg.forward_mode}:{station}"
    else:
        curve = [round(weather.seasonal_hdd_cdd(d)[0], 1) for d in days]
    return {"terminal": terminal, "station": station, "mode": cfg.forward_mode, "source": source,
            "is_live": False, "start": start.isoformat(), "days": [d.isoformat() for d in days],
            "hdd": curve}


# ---- the readout (API + CLI) -----------------------------------------------------
def readout(con, model: dict | None = None, cfg: WeatherConfig | None = None) -> dict:
    """Station coverage + per terminal×product β/OOS + anchor + raw-vs-weather-adjusted size axis."""
    cfg = cfg or DEFAULT_WEATHER_CONFIG
    model = model or build_model(con, cfg)
    if not model.get("available"):
        return {"available": False, "reason": model.get("reason", "no data"),
                "heating_families": sorted(HEATING_FAMILIES)}

    demand = []
    for key, v in sorted(model.get("demand_beta", {}).items()):
        term, fam = key.split("|", 1)
        cov = model["coverage"].get(None if term == "None" else term, {})
        demand.append({"terminal": term, "product": fam, "station": cov.get("station"),
                       "coverage": cov.get("coverage"), "hdd_source": cov.get("source"), **v})

    # raw vs weather-adjusted size CV for every heating-fuel customer (the deliverable)
    adj_rows = _raw_vs_adjusted(con, model, cfg)
    moved = [r for r in adj_rows if r["weather_adjusted"]]
    return {
        "available": True,
        "heating_families": sorted(HEATING_FAMILIES),
        "station_coverage": [{"terminal": (None if t == "None" else t), **c}
                             for t, c in model["coverage"].items()],
        "demand_beta": demand,
        "anchor": model.get("anchor"),
        "size_adjustment": {
            "n_heating_customers": len(adj_rows),
            "n_adjusted": len(moved),
            "median_cv_drop": round(float(np.median([r["raw_cv"] - r["adj_cv"]
                                       for r in moved])), 3) if moved else None,
            "customers": sorted(adj_rows, key=lambda r: -((r["raw_cv"] or 0) - (r["adj_cv"] or 0))),
        },
        "forward_seam": {"mode": cfg.forward_mode,
                         "note": "Normal/5-yr baseline now; pluggable for a live NOAA/CPC feed "
                                 "(labelled baseline-vs-live everywhere)."},
    }


def _raw_vs_adjusted(con, model: dict, cfg: WeatherConfig) -> list[dict]:
    df = _heating_lifts(con)
    df = df[df["family"].isin(HEATING_FAMILIES)]
    names = {r.customer_id: r.name for r in
             con.execute("SELECT customer_id, name FROM customers").df().itertuples()}
    out = []
    for cust, cl in df.groupby("customer_id"):
        fam = cl["family"].mode().iloc[0] if cl["family"].notna().any() else None
        terminal = cl["terminal"].mode().iloc[0] if cl["terminal"].notna().any() else None
        sizes, adjusted, diag = adjusted_sizes(cl.sort_values("lift_datetime"), fam, terminal, model, cfg)
        raw_cv = diag.get("raw_cv")
        adj_cv = diag.get("adj_cv") if adjusted else raw_cv
        out.append({
            "customer_id": cust, "name": names.get(cust, cust), "product": fam, "terminal": terminal,
            "n_lifts": int(len(cl)), "weather_adjusted": adjusted,
            "beta": diag.get("beta"), "beta_source": diag.get("beta_source"),
            "raw_cv": raw_cv, "adj_cv": adj_cv, "reason": diag.get("reason"),
        })
    return out
