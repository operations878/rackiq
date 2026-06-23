"""Synthetic "Soundview" book generator — parameterized and regenerable.

Produces a realistic wholesale fuel terminal book: ~40 customers across 2-3 terminals and
a few products, 18-24 months, with five behavioral archetypes, plus matching AR/invoices,
physical-inventory snapshots (with small gain/loss), and daily market prices.

Data PROFILES let the generator omit optional field groups so the capability matrix
visibly flexes:
  - core : only the 3 required fields populated (no inventory / invoices / market) -> 4 features
  - lite : core + terminal + product on lifts                                       -> 6 features
  - full : every canonical field populated                                          -> 21 features
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from . import db, schema
from .hygiene import vcf as _astm_vcf


# ---- Configuration --------------------------------------------------------------
@dataclass
class GenConfig:
    seed: int = 42
    n_customers: int = 40
    months: int = 21
    terminals: tuple = ("Linden", "Providence", "Albany")
    products: tuple = ("RBOB", "ULSD", "ULSHO")
    profile: str = "full"
    end_date: object = None          # date | "YYYY-MM-DD" | None (=today)
    archetype_mix: dict | None = None


DEFAULT_MIX = {
    "ratable": 12,
    "weather_distillate": 9,
    "price_chaser": 8,
    "marine": 4,
    "cstore_chain": 7,
}

API_GRAVITY = {"RBOB": 60.0, "ULSD": 36.0, "ULSHO": 35.0}
VCF_ALPHA = {"RBOB": 0.00095, "ULSD": 0.00070, "ULSHO": 0.00070}  # per deg F
PRICE_ANCHOR = {"RBOB": 2.30, "ULSD": 2.65, "ULSHO": 2.60}        # $/gal
PRICE_SIGMA = {"RBOB": 0.018, "ULSD": 0.015, "ULSHO": 0.015}
SEAS_AMP = {"RBOB": 0.05, "ULSD": 0.12, "ULSHO": 0.12}
BASIS_MEAN = {"RBOB": 0.010, "ULSD": 0.020, "ULSHO": 0.015}

NAME_PREFIX = {
    "ratable": ["Hudson", "Empire", "Keystone", "Liberty", "Colonial", "Atlantic",
                "Patriot", "Sterling", "Beacon", "Granite", "Harbor", "Summit", "Capital"],
    "weather_distillate": ["Northeast", "Valley", "Cardinal", "Snowbelt", "Hearth",
                           "Maple", "Frontier", "Evergreen", "Birchwood", "Yankee"],
    "price_chaser": ["Apex", "Vector", "Momentum", "Arbor", "Quicksilver", "Tactical",
                     "Spot", "Nimbus", "Catalyst"],
    "marine": ["Tidewater", "Mariner", "Seaboard", "Anchor", "Bluewater", "Narragansett"],
    "cstore_chain": ["QuickStop", "GoMart", "Cornerstone", "FuelExpress", "PitStop",
                     "DayBreak", "RoadKing", "ValuFill"],
}
NAME_SUFFIX = {
    "ratable": ["Petroleum", "Energy", "Fuels", "Oil Co", "Distributors"],
    "weather_distillate": ["Heating Oil", "Fuel Oil", "Home Heat", "Energy", "Oil & Propane"],
    "price_chaser": ["Trading", "Supply", "Commodities", "Fuel Group", "Partners"],
    "marine": ["Marine Fuels", "Bunkering", "Marine Services", "Shipping"],
    "cstore_chain": ["Stores", "Markets", "Mart", "Retail", "Stations"],
}


# ---- Small helpers --------------------------------------------------------------
def _ou(rng, n, mean, start, theta, vol, floor=None):
    """Ornstein-Uhlenbeck (mean-reverting) path."""
    x = np.empty(n)
    x[0] = start
    for t in range(1, n):
        x[t] = x[t - 1] + theta * (mean - x[t - 1]) + rng.normal(0, vol)
        if floor is not None and x[t] < floor:
            x[t] = floor
    return x


def _date_index(cfg: GenConfig) -> list[date]:
    end = cfg.end_date
    if end is None:
        end = date.today()
    elif isinstance(end, str):
        end = date.fromisoformat(end)
    start = end - timedelta(days=int(round(cfg.months * 30.44)))
    span = (end - start).days
    return [start + timedelta(days=i) for i in range(span + 1)]


def _resolve_mix(n: int, mix: dict | None) -> dict:
    base = mix or DEFAULT_MIX
    total = sum(base.values())
    counts = {k: int(round(v * n / total)) for k, v in base.items()}
    diff = n - sum(counts.values())
    keys = list(counts.keys())
    i = 0
    while diff != 0:
        k = keys[i % len(keys)]
        if diff > 0:
            counts[k] += 1
            diff -= 1
        elif counts[k] > 0:
            counts[k] -= 1
            diff += 1
        i += 1
    return counts


def _pick_products(products_all, preferred, rng, kmin, kmax):
    avail = [p for p in preferred if p in products_all] or list(products_all)
    k = min(int(rng.integers(kmin, kmax + 1)), len(avail))
    idx = sorted(rng.choice(len(avail), size=max(1, k), replace=False).tolist())
    return [avail[i] for i in idx]


def _make_name(archetype, rng, used: set) -> str:
    prefixes = NAME_PREFIX[archetype]
    suffixes = NAME_SUFFIX[archetype]
    for _ in range(40):
        name = f"{prefixes[int(rng.integers(0, len(prefixes)))]} {suffixes[int(rng.integers(0, len(suffixes)))]}"
        if name not in used:
            used.add(name)
            return name
    name = f"{name} {len(used)}"
    used.add(name)
    return name


def _archetype_params(archetype, products_all, rng) -> dict:
    if archetype == "ratable":
        return dict(products=_pick_products(products_all, ["ULSD", "RBOB"], rng, 1, 2),
                    base_volume=rng.uniform(5000, 9000), cadence=rng.uniform(2.5, 4.5),
                    cv=0.12, hdd_k=0.0, min_gap=1, terms_days=int(rng.choice([10, 15, 15, 30])),
                    late=False, discount=rng.uniform(0.008, 0.018))
    if archetype == "weather_distillate":
        return dict(products=_pick_products(products_all, ["ULSHO", "ULSD"], rng, 1, 2),
                    base_volume=rng.uniform(4000, 8000), cadence=rng.uniform(4.0, 7.0),
                    cv=0.20, hdd_k=rng.uniform(1.4, 2.4), min_gap=1,
                    terms_days=int(rng.choice([15, 30, 30])),
                    late=bool(rng.random() < 0.25), discount=rng.uniform(0.006, 0.014))
    if archetype == "price_chaser":
        return dict(products=_pick_products(products_all, ["RBOB", "ULSD"], rng, 1, 2),
                    base_volume=rng.uniform(8000, 16000), cadence=None, cv=0.30,
                    hdd_k=0.0, min_gap=int(rng.integers(5, 12)),
                    terms_days=int(rng.choice([10, 15])),
                    late=bool(rng.random() < 0.35), discount=rng.uniform(0.002, 0.008),
                    chase_pct=float(rng.uniform(0.25, 0.45)))
    if archetype == "marine":
        return dict(products=_pick_products(products_all, ["ULSD"], rng, 1, 1),
                    base_volume=rng.uniform(60000, 140000), cadence=rng.uniform(18, 34),
                    cv=0.35, hdd_k=0.0, min_gap=5, terms_days=30,
                    late=bool(rng.random() < 0.30), discount=rng.uniform(0.004, 0.012))
    if archetype == "cstore_chain":
        return dict(products=_pick_products(products_all, ["RBOB", "ULSD"], rng, 1, 2),
                    base_volume=rng.uniform(3000, 7000), cadence=rng.uniform(2.0, 3.5),
                    cv=0.16, hdd_k=0.0, min_gap=1, terms_days=int(rng.choice([10, 15])),
                    late=bool(rng.random() < 0.10), discount=rng.uniform(0.010, 0.020))
    raise ValueError(archetype)


# ---- Builders -------------------------------------------------------------------
def _build_customers(cfg: GenConfig, rng):
    counts = _resolve_mix(cfg.n_customers, cfg.archetype_mix)
    profiles = []
    used_names: set = set()
    cid_n = 1
    for archetype, k in counts.items():
        for _ in range(k):
            home = cfg.terminals[int(rng.integers(0, len(cfg.terminals)))]
            params = _archetype_params(archetype, cfg.products, rng)
            profiles.append(dict(customer_id=f"C{cid_n:03d}", name=_make_name(archetype, rng, used_names),
                                 archetype=archetype, home_terminal=home, **params))
            cid_n += 1
    customers_df = pd.DataFrame([{k: p[k] for k in ("customer_id", "name", "archetype", "home_terminal")}
                                 for p in profiles])
    return customers_df, profiles


def _build_market_prices(cfg: GenConfig, dates, rng):
    n = len(dates)
    doy = np.array([d.timetuple().tm_yday for d in dates])
    winter = np.cos(2 * np.pi * (doy - 15) / 365.0)  # +1 ~ mid-Jan, -1 ~ mid-Jul
    market, basis, cost = {}, {}, {}
    for p in cfg.products:
        shocks = rng.normal(0, PRICE_SIGMA.get(p, 0.015), n)
        logp = np.cumsum(shocks)
        logp -= logp.mean()
        series = PRICE_ANCHOR.get(p, 2.4) * np.exp(logp) * (1 + SEAS_AMP.get(p, 0.06) * winter)
        market[p] = np.clip(series, 0.8, None)
        basis[p] = _ou(rng, n, mean=BASIS_MEAN.get(p, 0.01), start=BASIS_MEAN.get(p, 0.01),
                       theta=0.12, vol=0.006)
        cost[p] = market[p] + basis[p] + 0.005  # terminal acquisition cost ($/gal)

    terminal_markup = {t: 0.020 + 0.012 * i for i, t in enumerate(cfg.terminals)}
    street = {}
    bench = {}
    rows = []
    for p in cfg.products:
        for t in cfg.terminals:
            rack = market[p] + basis[p] + terminal_markup[t] + rng.normal(0, 0.004, n)
            street[(p, t)] = rack
            # External street/OPIS rack benchmark: tracks our posting with its own noise.
            benchmark = market[p] + basis[p] + terminal_markup[t] + rng.normal(0, 0.005, n)
            bench[(p, t)] = benchmark
            cb = _ou(rng, n, mean=500000, start=float(rng.uniform(300000, 700000)),
                     theta=0.04, vol=35000, floor=0)
            cs = _ou(rng, n, mean=500000, start=float(rng.uniform(300000, 700000)),
                     theta=0.04, vol=35000, floor=0)
            for i in range(n):
                rows.append((dates[i], p, t, round(float(market[p][i]), 4), round(float(basis[p][i]), 4),
                             round(float(rack[i]), 4), round(float(cb[i]), 0), round(float(cs[i]), 0),
                             round(float(benchmark[i]), 4)))
    df = pd.DataFrame(rows, columns=["price_date", "product", "terminal", "market_price",
                                     "nyh_basis", "street_rack", "committed_buys", "committed_sells",
                                     "rack_benchmark"])
    df["price_date"] = pd.to_datetime(df["price_date"])
    px = {"market": market, "basis": basis, "street": street, "cost": cost, "bench": bench}
    return df, px


def _schedule_events(prof, dates, hdd_norm, px, rng):
    n = len(dates)
    arche = prof["archetype"]
    products = prof["products"]

    def pick_product():
        return products[int(rng.integers(0, len(products)))]

    events = []
    if arche in ("ratable", "cstore_chain"):
        day = int(rng.integers(0, int(prof["cadence"]) + 1))
        while day < n:
            if arche == "cstore_chain" and dates[day].weekday() >= 5 and rng.random() < 0.6:
                day += 1
                continue
            vol = prof["base_volume"] * (1 + rng.normal(0, prof["cv"]))
            events.append((day, pick_product(), max(300.0, vol)))
            day += max(1, int(round(rng.normal(prof["cadence"], prof["cadence"] * 0.18))))
    elif arche == "weather_distillate":
        day = int(rng.integers(0, int(prof["cadence"]) + 1))
        while day < n:
            sf = 1.0 + prof["hdd_k"] * hdd_norm[day]
            vol = prof["base_volume"] * sf * (1 + rng.normal(0, prof["cv"]))
            events.append((day, pick_product(), max(300.0, vol)))
            day += max(1, int(round(rng.normal(prof["cadence"], prof["cadence"] * 0.25) / sf)))
    elif arche == "marine":
        day = int(rng.integers(0, int(prof["cadence"]) + 1))
        while day < n:
            vol = prof["base_volume"] * (1 + rng.normal(0, prof["cv"]))
            events.append((day, pick_product(), max(20000.0, vol)))
            day += max(5, int(round(rng.normal(prof["cadence"], prof["cadence"] * 0.4))))
    elif arche == "price_chaser":
        home = prof["home_terminal"]
        thresh = {}
        for p in products:
            arr = px["street"].get((p, home))
            if arr is None:
                arr = next(iter(px["street"].values()))
            thresh[p] = float(np.quantile(arr, prof["chase_pct"]))
        last = -10**9
        for day in range(n):
            if day - last < prof["min_gap"]:
                continue
            p = pick_product()
            price = float(px["street"][(p, home)][day])
            if price <= thresh[p] and rng.random() < 0.55:
                events.append((day, p, max(1500.0, prof["base_volume"] * (1 + rng.normal(0, prof["cv"])))))
                last = day
            elif rng.random() < 0.015:  # occasional must-buy regardless of price
                events.append((day, p, max(1000.0, 0.5 * prof["base_volume"] * (1 + abs(rng.normal(0, prof["cv"]))))))
                last = day
    return events


def _build_lifts(cfg: GenConfig, profiles, px, dates, ambient, hdd_norm, rng):
    records = []
    for prof in profiles:
        home = prof["home_terminal"]
        for (di, product, vol) in _schedule_events(prof, dates, hdd_norm, px, rng):
            d = dates[di]
            ldt = datetime(d.year, d.month, d.day, int(rng.integers(6, 18)), int(rng.integers(0, 60)))
            temp = float(ambient[di] + rng.normal(0, 4.0))
            api = API_GRAVITY.get(product, 40.0) + float(rng.normal(0, 0.5))
            vcf = 1.0 - VCF_ALPHA.get(product, 0.0008) * (temp - 60.0)
            gross = max(200.0, float(vol))
            net = gross * vcf
            cost = float(px["cost"][product][di])
            price = max(float(px["street"][(product, home)][di]) - prof["discount"], cost + 0.003)
            records.append((prof["customer_id"], ldt, round(net, 1), home, product,
                            round(gross, 1), round(temp, 1), round(api, 2),
                            round(price, 4), round(cost, 4)))
    df = pd.DataFrame(records, columns=["customer_id", "lift_datetime", "net_gallons", "terminal",
                                        "product", "gross_gallons", "observed_temp", "api_gravity",
                                        "unit_price", "unit_cost"])
    df["lift_datetime"] = pd.to_datetime(df["lift_datetime"])
    return df.sort_values("lift_datetime").reset_index(drop=True)


def _build_invoices(cfg: GenConfig, lifts, profiles, rng):
    cols = ["customer_id", "invoice_date", "due_date", "paid_date", "invoice_amount", "credit_limit"]
    if lifts is None or len(lifts) == 0:
        return pd.DataFrame(columns=cols)
    prof_by_id = {p["customer_id"]: p for p in profiles}
    tmp = lifts.copy()
    tmp["amount"] = tmp["net_gallons"] * tmp["unit_price"]
    monthly = tmp.groupby("customer_id")["amount"].sum() / max(1, cfg.months)
    credit = {cid: round(float(m) * float(rng.uniform(2.0, 3.5)), -2) for cid, m in monthly.items()}
    end = tmp["lift_datetime"].max().date()

    rows = []
    for r in tmp.itertuples(index=False):
        prof = prof_by_id.get(r.customer_id, {})
        terms = int(prof.get("terms_days", 15))
        late = bool(prof.get("late", False))
        inv_date = r.lift_datetime.date() + timedelta(days=int(rng.integers(0, 2)))
        due = inv_date + timedelta(days=terms)
        days_to_end = (end - inv_date).days
        paid = None
        open_prob = (0.85 if days_to_end < terms
                     else 0.50 if days_to_end < terms + 20
                     else 0.25 if late else 0.06)
        if days_to_end >= 0 and not (rng.random() < open_prob and days_to_end < terms + 45):
            delay = int(rng.integers(5, 36)) if late else int(round(rng.normal(-1, 2)))
            pay_date = max(inv_date, due + timedelta(days=delay))
            paid = pay_date if pay_date <= end else None
        rows.append((r.customer_id, inv_date, due, paid, round(float(r.amount), 2), credit.get(r.customer_id)))

    df = pd.DataFrame(rows, columns=cols)
    for c in ("invoice_date", "due_date", "paid_date"):
        df[c] = pd.to_datetime(df[c])  # None -> NaT -> NULL on insert
    return df


def _tank_id_for(t: str, p: str) -> str:
    return f"{t[:3].upper()}-{p}-1"


def _meter_id_for(t: str, p: str) -> str:
    return f"MTR-{t[:3].upper()}-{p}"


# Per-tank loss scenarios deliberately seeded into the synthetic book so the reconciliation
# module has something real to find. Assigned deterministically over the sorted (terminal,
# product) tanks so the same offenders appear for a given book:
#   bad_vcf   : the loading meter's temperature probe reads hot → it over-corrects → the
#               billed net is systematically below an independent ASTM D1250 recompute
#               (a VCF/probe calibration problem, temperature-correlated). [measurement]
#   drift     : the meter totalizer drifts low over time → billed net runs progressively
#               under the true draw → tank loss-% trends up out of control. [measurement]
#   high_evap : elevated physical shrink (evaporation / line-fill / theft). [physical]
#   routine   : ordinary small shrink + gauge noise.
_DRIFT_MAX = 0.006          # meter under-reads up to 0.6% by end of horizon
_BAD_VCF_PROBE_ERR = 5.0    # deg F the bad lane's temperature probe reads high
_EVAP_RATE = 0.0005         # routine physical shrink as a fraction of true throughput
_HIGH_EVAP_MULT = 4.0       # the seeded physical-loss tank
_GAUGE_SD_FRAC = 0.0003     # gauge-reading noise as a fraction of capacity


def _tank_scenarios(lifts) -> tuple[dict, list]:
    keys = sorted({(t, p) for t, p in zip(lifts["terminal"], lifts["product"])
                   if t is not None and p is not None})
    scen = {k: "routine" for k in keys}
    if len(keys) >= 1:
        scen[keys[0]] = "bad_vcf"
    if len(keys) >= 2:
        scen[keys[1]] = "drift"
    if len(keys) >= 3:
        scen[keys[2]] = "high_evap"
    if len(keys) >= 4:
        scen[keys[3]] = "drift"
    return scen, keys


def _build_bol_compartments(cfg: GenConfig, lifts, dates, ambient, rng):
    """Explode each lift into a bill-of-lading with 1-N metered compartments.

    The compartments' BILLED net (the meter ticket) sums to the lift's net, but the TRUE
    physical net diverges per the tank's seeded meter scenario; gross is derived from the
    true net via ASTM D1250 so an independent recompute (engine side) recovers the truth and
    the net-recon cross-check can flag the billed-vs-recomputed gap. Also returns the per
    (terminal, product, day) BILLED and TRUE draws used to roll book vs physical inventory.
    """
    cols = schema.column_names(schema.BOL)
    draw_billed: dict = defaultdict(float)
    draw_true: dict = defaultdict(float)
    if lifts is None or len(lifts) == 0:
        return pd.DataFrame(columns=cols), draw_billed, draw_true

    n = len(dates)
    date_index = {d: i for i, d in enumerate(dates)}
    scen, _keys = _tank_scenarios(lifts)
    rows = []
    seq = 0
    for r in lifts.itertuples(index=False):
        t, p = r.terminal, r.product
        if t is None or p is None:
            continue
        bdt = pd.Timestamp(r.lift_datetime)
        di = date_index.get(bdt.date())
        if di is None:
            continue
        frac = di / max(1, n - 1)
        sc = scen.get((t, p), "routine")
        tank_id, meter_id = _tank_id_for(t, p), _meter_id_for(t, p)
        net = float(r.net_gallons)
        ncomp = int(min(8, max(1, round(net / 4500.0))))
        parts = np.clip(rng.normal(1.0, 0.12, ncomp), 0.4, None)
        parts = parts / parts.sum() * net          # billed compartment nets sum to the lift
        seq += 1
        bol_number = f"BOL-{t[:3].upper()}-{seq:06d}"
        cost = None if pd.isna(r.unit_cost) else float(r.unit_cost)
        for j in range(ncomp):
            billed = float(parts[j])
            temp = float(ambient[di] + rng.normal(0, 4.0))
            api = API_GRAVITY.get(p, 40.0) + float(rng.normal(0, 0.5))
            vcf_c = _astm_vcf(api, temp, p)
            if sc == "drift":
                true_net = billed / max(1e-6, 1.0 - _DRIFT_MAX * frac)
            elif sc == "bad_vcf":
                vcf_probe = _astm_vcf(api, temp + _BAD_VCF_PROBE_ERR, p)
                true_net = billed * (vcf_c / max(1e-6, vcf_probe))
            else:
                true_net = billed * (1.0 + float(rng.normal(0, 0.0003)))
            gross = true_net / max(1e-6, vcf_c)     # recompute(gross, temp, api) == true_net
            rows.append((bol_number, bdt, t, p, tank_id, meter_id, r.customer_id,
                         f"{bol_number}-C{j + 1}", round(gross, 1), round(billed, 1),
                         round(temp, 1), round(api, 2),
                         round(cost, 4) if cost is not None else None))
            draw_billed[(t, p, di)] += billed
            draw_true[(t, p, di)] += true_net

    df = pd.DataFrame(rows, columns=cols)
    df["bol_datetime"] = pd.to_datetime(df["bol_datetime"])
    return df, draw_billed, draw_true


def _build_inventory(cfg: GenConfig, lifts, draw_billed, draw_true, dates, rng):
    """Daily per-tank snapshots: book rolls on BILLED disbursements (what the office sees);
    physical rolls on TRUE disbursements minus seeded evaporation (what the gauge would read).
    Their difference is the gain/loss the reconciliation module recovers."""
    cols = schema.column_names(schema.INVENTORY)
    if lifts is None or len(lifts) == 0 or not draw_billed:
        return pd.DataFrame(columns=cols)
    n = len(dates)
    scen, _keys = _tank_scenarios(lifts)

    total_true = defaultdict(float)
    max_day = defaultdict(float)
    for (t, p, di), v in draw_true.items():
        total_true[(t, p)] += v
        max_day[(t, p)] = max(max_day[(t, p)], v)

    rows = []
    for (t, p) in sorted(total_true.keys()):
        avg_daily = total_true[(t, p)] / n
        capacity = round(max(150000.0, avg_daily * 28, max_day[(t, p)] * 1.8), -3)
        min_heel = round(0.05 * capacity, -2)
        tank_id = _tank_id_for(t, p)
        evap_mult = _HIGH_EVAP_MULT if scen.get((t, p)) == "high_evap" else 1.0
        gauge_sd = _GAUGE_SD_FRAC * capacity
        book = phys = 0.70 * capacity
        for di in range(n):
            d = dates[di]
            book -= draw_billed.get((t, p, di), 0.0)
            phys -= draw_true.get((t, p, di), 0.0)
            phys -= evap_mult * _EVAP_RATE * draw_true.get((t, p, di), 0.0) + 1.5e-6 * capacity
            receipt = 0.0
            if book < 1.5 * min_heel:                 # refill to 0.85·cap (covers negative book)
                receipt = 0.85 * capacity - book
                book += receipt
                phys += receipt                       # the receipt lands in both books equally
            book = min(book, capacity)
            phys = min(phys, capacity)
            reading = phys + float(rng.normal(0, gauge_sd))
            rows.append((datetime(d.year, d.month, d.day, 23, 59), t, p, tank_id, capacity,
                         min_heel, round(book, 1), round(reading, 1), round(receipt, 1)))

    df = pd.DataFrame(rows, columns=cols)
    df["snapshot_datetime"] = pd.to_datetime(df["snapshot_datetime"])
    return df


# ---- Early feeds: quote log + receipt detail ------------------------------------
# Per-archetype price elasticity (slope of accept-incidence vs price-over-reference).
# Higher = more price-sensitive (rejects more as the quote climbs above the rack benchmark).
_ELASTICITY = {"ratable": 1.6, "weather_distillate": 2.2, "price_chaser": 6.0,
               "marine": 1.4, "cstore_chain": 3.2}


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + np.exp(-x))


def _build_quotes(cfg: GenConfig, profiles, lifts, px, dates, rng):
    """Synthesize a quote log (accepts AND rejections) — the elasticity training set.

    Each quote prices ``quoted_price`` against the street/rack ``reference``; the accept
    probability falls as the quote climbs above the reference, with a per-archetype
    elasticity slope, so a downstream model can recover a (negative) price-elasticity β.
    """
    cols = schema.column_names(schema.QUOTES)
    if lifts is None or len(lifts) == 0:
        return pd.DataFrame(columns=cols)
    n = len(dates)
    date_index = {d: i for i, d in enumerate(dates)}
    lifts2 = lifts.copy()
    lifts2["d"] = lifts2["lift_datetime"].dt.date
    span = lifts2.groupby("customer_id")["d"].agg(["min", "max", "count"])
    prof_by_id = {p["customer_id"]: p for p in profiles}

    inv_states = ["long", "balanced", "balanced", "short"]
    cap_states = ["open", "balanced", "tight"]
    comp_ctx = ["none", "competitor_undercut", "competitor_matched", "sole_supplier"]

    rows = []
    for cid, r in span.iterrows():
        prof = prof_by_id.get(cid)
        if prof is None:
            continue
        el = _ELASTICITY.get(prof["archetype"], 3.0)
        home = prof["home_terminal"]
        products = prof["products"]
        i0 = date_index.get(r["min"], 0)
        i1 = date_index.get(r["max"], n - 1)
        if i1 <= i0:
            i1 = min(n - 1, i0 + 1)
        nq = int(max(8, min(140, round(r["count"] * 1.4))))
        center = -float(prof["discount"])  # we typically quote a touch under the rack
        for _ in range(nq):
            di = int(rng.integers(i0, i1 + 1))
            d = dates[di]
            p = products[int(rng.integers(0, len(products)))]
            ref_arr = px["street"].get((p, home))
            if ref_arr is None:
                ref_arr = next(iter(px["street"].values()))
            reference = float(ref_arr[di])
            cost = float(px["cost"][p][di])
            offset = float(rng.uniform(-0.06, 0.11))
            quoted = max(reference + offset, cost + 0.002)
            spread = quoted - reference
            paccept = min(0.97, max(0.02, _sigmoid(1.1 - el * ((spread - center) / 0.05))))
            u = float(rng.random())
            if u < paccept:
                outcome, final, ttd = "accept", max(300.0, prof["base_volume"] * (1 + rng.normal(0, prof["cv"]))), max(2.0, float(rng.normal(120, 50)))
            elif u < paccept + 0.12:
                outcome, final, ttd = "no_response", None, max(60.0, float(rng.normal(1440, 600)))
            else:
                outcome, final, ttd = "reject", None, max(3.0, float(rng.normal(90, 60)))
            qt = datetime(d.year, d.month, d.day, int(rng.integers(7, 17)), int(rng.integers(0, 60)))
            rows.append((cid, qt, p, round(quoted, 4), round(reference, 4),
                         inv_states[int(rng.integers(0, len(inv_states)))],
                         cap_states[int(rng.integers(0, len(cap_states)))],
                         comp_ctx[int(rng.integers(0, len(comp_ctx)))],
                         outcome, round(ttd, 1),
                         round(float(final), 1) if final is not None else None))
    df = pd.DataFrame(rows, columns=cols)
    df["quote_time"] = pd.to_datetime(df["quote_time"])
    df["final_gallons"] = pd.to_numeric(df["final_gallons"], errors="coerce")
    return df


def _build_receipts(cfg: GenConfig, inventory_df, dates, ambient, rng):
    """Synthesize receipt detail from inventory replenishment events.

    Gross is derived from net via an ASTM VCF at the (cooler, bulk) receipt temperature so
    there is a real gross-vs-net thermal gap to attribute. The B/L-vs-received variance is
    biased by source so marine carries a vessel-experience-factor loss and pipeline a transit
    shrink — the line items the reconciliation module surfaces (the VEF / shrinkage argument).
    """
    cols = schema.column_names(schema.RECEIPTS)
    if inventory_df is None or len(inventory_df) == 0:
        return pd.DataFrame(columns=cols)
    date_index = {d: i for i, d in enumerate(dates)}
    n = len(dates)
    ev = inventory_df[inventory_df["receipts"] > 0.0]
    basis_for = {"marine": "ship_meter", "pipeline": "pipeline_meter", "truck": "truck_meter"}
    rows = []
    for r in ev.itertuples(index=False):
        net = float(r.receipts)
        if net > 300000:
            src = "marine" if rng.random() < 0.7 else "pipeline"
        elif net > 80000:
            src = "pipeline" if rng.random() < 0.6 else "marine"
        else:
            src = "truck" if rng.random() < 0.6 else "pipeline"
        di = date_index.get(pd.Timestamp(r.snapshot_datetime).date(), 0)
        api = API_GRAVITY.get(r.product, 40.0)
        temp = float(ambient[min(di, n - 1)] - 4.0 + rng.normal(0, 3.0))  # bulk supply a touch cool
        gross = net / max(1e-6, _astm_vcf(api, temp, r.product))
        if src == "marine":        # vessel experience factor: ship delivers a hair under the B/L
            variance = -abs(float(rng.normal(0, 0.0015))) * net
        elif src == "pipeline":    # pipeline transit / line shrink
            variance = -abs(float(rng.normal(0, 0.0009))) * net
        else:
            variance = float(rng.normal(0, 0.0004)) * net
        mb = basis_for[src]
        if src == "marine" and rng.random() < 0.4:
            mb = "shore_tank"
        rows.append((r.snapshot_datetime, r.terminal, r.product, src,
                     round(gross, 1), round(net, 1), mb, round(variance, 1)))
    df = pd.DataFrame(rows, columns=cols)
    df["receipt_datetime"] = pd.to_datetime(df["receipt_datetime"])
    return df


# ---- Profile handling -----------------------------------------------------------
def _profile_optional_fields(profile: str) -> set:
    if profile == "full":
        return set(schema.optional_field_names())
    if profile == "lite":
        return {"terminal", "product"}
    if profile == "core":
        return set()
    raise ValueError(f"unknown profile: {profile!r} (expected core|lite|full)")


def _filter_optional_columns(df, table, allowed):
    keep = []
    for c in df.columns:
        f = schema.FIELDS_BY_NAME.get(c)
        if f is None or f.required or c in allowed:
            keep.append(c)
    return df[keep]


def _table_enabled(table, allowed) -> bool:
    return any(o in allowed for o in schema.optional_fields_for_table(table))


# ---- Orchestration --------------------------------------------------------------
def generate(cfg: GenConfig, con) -> dict:
    rng = np.random.default_rng(cfg.seed)
    dates = _date_index(cfg)
    n = len(dates)
    doy = np.array([d.timetuple().tm_yday for d in dates])
    ambient = 50 - 18 * np.cos(2 * np.pi * (doy - 15) / 365.0) + rng.normal(0, 3, n)
    hdd = np.maximum(0.0, 65 - ambient)
    hdd_norm = (hdd - hdd.min()) / ((hdd.max() - hdd.min()) or 1.0)

    customers_df, profiles = _build_customers(cfg, rng)
    market_df, px = _build_market_prices(cfg, dates, rng)
    lifts_df = _build_lifts(cfg, profiles, px, dates, ambient, hdd_norm, rng)
    invoices_df = _build_invoices(cfg, lifts_df, profiles, rng)
    bol_df, draw_billed, draw_true = _build_bol_compartments(cfg, lifts_df, dates, ambient, rng)
    inventory_df = _build_inventory(cfg, lifts_df, draw_billed, draw_true, dates, rng)
    quotes_df = _build_quotes(cfg, profiles, lifts_df, px, dates, rng)
    receipts_df = _build_receipts(cfg, inventory_df, dates, ambient, rng)

    db.drop_all(con)
    db.init_db(con)
    allowed = _profile_optional_fields(cfg.profile)

    db.insert_df(con, schema.CUSTOMERS, customers_df)
    db.insert_df(con, schema.LIFTS, _filter_optional_columns(lifts_df, schema.LIFTS, allowed))
    if _table_enabled(schema.INVENTORY, allowed):
        db.insert_df(con, schema.INVENTORY, inventory_df)
    if _table_enabled(schema.INVOICES, allowed):
        db.insert_df(con, schema.INVOICES, invoices_df)
    if _table_enabled(schema.MARKET, allowed):
        db.insert_df(con, schema.MARKET, market_df)
    if _table_enabled(schema.QUOTES, allowed):
        db.insert_df(con, schema.QUOTES, quotes_df)
    if _table_enabled(schema.RECEIPTS, allowed):
        db.insert_df(con, schema.RECEIPTS, receipts_df)
    if _table_enabled(schema.BOL, allowed):
        db.insert_df(con, schema.BOL, bol_df)

    db.set_meta(con, "profile", cfg.profile)
    db.set_meta(con, "seed", cfg.seed)
    db.set_meta(con, "n_customers", cfg.n_customers)
    db.set_meta(con, "months", cfg.months)
    db.set_meta(con, "terminals", ",".join(cfg.terminals))
    db.set_meta(con, "products", ",".join(cfg.products))
    db.set_meta(con, "generated_at", datetime.utcnow().isoformat(timespec="seconds"))
    return db.table_counts(con)
