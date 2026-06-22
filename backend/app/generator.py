"""Synthetic "Soundview" book generator — parameterized and regenerable.

Produces a realistic wholesale fuel terminal book: ~40 customers across 2-3 terminals and
a few products, 18-24 months, with five behavioral archetypes, plus matching AR/invoices,
physical-inventory snapshots (with small gain/loss), and daily market prices.

Data PROFILES let the generator omit optional field groups so the capability matrix
visibly flexes:
  - core : only the 3 required fields populated (no inventory / invoices / market) -> 4 features
  - lite : core + terminal + product on lifts                                       -> 6 features
  - full : every canonical field populated                                          -> 17 features
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from . import db, schema


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
    rows = []
    for p in cfg.products:
        for t in cfg.terminals:
            rack = market[p] + basis[p] + terminal_markup[t] + rng.normal(0, 0.004, n)
            street[(p, t)] = rack
            cb = _ou(rng, n, mean=500000, start=float(rng.uniform(300000, 700000)),
                     theta=0.04, vol=35000, floor=0)
            cs = _ou(rng, n, mean=500000, start=float(rng.uniform(300000, 700000)),
                     theta=0.04, vol=35000, floor=0)
            for i in range(n):
                rows.append((dates[i], p, t, round(float(market[p][i]), 4), round(float(basis[p][i]), 4),
                             round(float(rack[i]), 4), round(float(cb[i]), 0), round(float(cs[i]), 0)))
    df = pd.DataFrame(rows, columns=["price_date", "product", "terminal", "market_price",
                                     "nyh_basis", "street_rack", "committed_buys", "committed_sells"])
    df["price_date"] = pd.to_datetime(df["price_date"])
    px = {"market": market, "basis": basis, "street": street, "cost": cost}
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


def _build_inventory(cfg: GenConfig, lifts, dates, rng):
    cols = schema.column_names(schema.INVENTORY)
    if lifts is None or len(lifts) == 0:
        return pd.DataFrame(columns=cols)
    n = len(dates)
    date_index = {d: i for i, d in enumerate(dates)}
    tmp = lifts.copy()
    tmp["d"] = tmp["lift_datetime"].dt.date
    grp = tmp.groupby(["terminal", "product", "d"])["net_gallons"].sum().reset_index()

    draw = {}
    total_draw = defaultdict(float)
    max_day = defaultdict(float)
    for r in grp.itertuples(index=False):
        di = date_index.get(r.d)
        if di is None:
            continue
        v = float(r.net_gallons)
        draw[(r.terminal, r.product, di)] = v
        total_draw[(r.terminal, r.product)] += v
        max_day[(r.terminal, r.product)] = max(max_day[(r.terminal, r.product)], v)

    rows = []
    for (t, p) in sorted(total_draw.keys()):
        avg_daily = total_draw[(t, p)] / n
        capacity = round(max(150000.0, avg_daily * 28, max_day[(t, p)] * 1.8), -3)
        min_heel = round(0.05 * capacity, -2)
        tank_id = f"{t[:3].upper()}-{p}-1"
        book = 0.70 * capacity
        for di in range(n):
            d = dates[di]
            book -= draw.get((t, p, di), 0.0)
            receipt = 0.0
            if book < 1.5 * min_heel:
                receipt = max(0.0, 0.85 * capacity - book)
                book += receipt
            if book < 0:  # emergency top-up
                receipt += 0.85 * capacity - book
                book = 0.85 * capacity
            book = min(book, capacity)
            physical = book + float(rng.normal(0, 0.0015 * capacity))
            rows.append((datetime(d.year, d.month, d.day, 23, 59), t, p, tank_id, capacity,
                         min_heel, round(book, 1), round(physical, 1), round(receipt, 1)))

    df = pd.DataFrame(rows, columns=cols)
    df["snapshot_datetime"] = pd.to_datetime(df["snapshot_datetime"])
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
    inventory_df = _build_inventory(cfg, lifts_df, dates, rng)

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

    db.set_meta(con, "profile", cfg.profile)
    db.set_meta(con, "seed", cfg.seed)
    db.set_meta(con, "n_customers", cfg.n_customers)
    db.set_meta(con, "months", cfg.months)
    db.set_meta(con, "terminals", ",".join(cfg.terminals))
    db.set_meta(con, "products", ",".join(cfg.products))
    db.set_meta(con, "generated_at", datetime.utcnow().isoformat(timespec="seconds"))
    return db.table_counts(con)
