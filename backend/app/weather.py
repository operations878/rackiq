"""Weather / degree-day feed — HDD & CDD per terminal-day, used to *explain* lane breaks.

The VAR engine flags when a customer buys outside their variability range (an "excursion").
This module attaches the **weather that day** to each excursion so a predictable-looking-erratic
account (e.g. a heating-oil dealer who spikes on cold snaps) can be separated from a genuinely
random one.

Degree-days are base 65 °F: ``HDD = max(0, 65 − T̄)``, ``CDD = max(0, T̄ − 65)`` from the daily
mean temperature.

Source strategy (free, no API key):
  1. A small **DuckDB cache** (``weather_daily``) so a date is fetched at most once.
  2. A best-effort **historical fetch** from the open, key-less Open-Meteo *archive* API
     (ERA5 reanalysis — the same surface analysis NOAA's products are built on). NOAA's own
     historical service (NCEI CDO) requires a token, so this open archive is the no-key path;
     the provider is isolated here so a tokened NOAA source can be swapped in later.
  3. A deterministic **seasonal climatology proxy** for anything we can't fetch (offline, or
     dates past the archive horizon). The proxy matches the generator's ambient-temperature
     curve, so the demo book still shows weather patterns with no network at all.

Everything is wrapped so a network failure never breaks scoring — it silently degrades to the
proxy, and a process-wide circuit breaker stops retrying after the first failure.
"""

from __future__ import annotations

import json
import math
import os
import ssl
import urllib.request
from datetime import date, datetime, timedelta

import pandas as pd

# ---- Terminal geography ---------------------------------------------------------
# Approx lat/lon for the Soundview terminals; unknown terminals fall back to the NY Harbor
# default (Linden) so an uploaded book with other terminal labels still resolves to a climate.
TERMINAL_COORDS: dict[str, tuple[float, float]] = {
    "Linden": (40.622, -74.244),       # Linden, NJ (NY Harbor)
    "Providence": (41.823, -71.413),   # Providence, RI
    "Albany": (42.652, -73.756),       # Albany, NY
}
DEFAULT_COORD = TERMINAL_COORDS["Linden"]

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_FETCH_TIMEOUT = float(os.environ.get("RACKIQ_WEATHER_TIMEOUT", "4"))
_FETCH_ENABLED = os.environ.get("RACKIQ_WEATHER_FETCH", "1") not in ("0", "false", "no")
_CA_BUNDLE = "/root/.ccr/ca-bundle.crt"

# Process-wide circuit breaker: once a fetch fails we stop trying (avoids repeated timeouts).
_net_dead = False


WEATHER_DDL = """
CREATE TABLE IF NOT EXISTS weather_daily (
    location VARCHAR, day DATE, tmean DOUBLE, hdd DOUBLE, cdd DOUBLE, source VARCHAR,
    PRIMARY KEY (location, day)
)
"""


def ensure_tables(con) -> None:
    con.execute(WEATHER_DDL)


# ---- Seasonal climatology proxy (deterministic, always available) ---------------
def seasonal_hdd_cdd(dt) -> tuple[float, float]:
    """Climatological degree-days from a date — the no-network fallback.

    Matches the generator's ambient mean ``50 − 18·cos(2π(doy−15)/365)`` so the synthetic
    book's cold-season distillate spikes line up with HDD even with no live fetch.
    """
    ts = pd.Timestamp(dt)
    doy = ts.dayofyear
    t = 50.0 - 18.0 * math.cos(2 * math.pi * (doy - 15) / 365.0)
    return max(0.0, 65.0 - t), max(0.0, t - 65.0)


def _coord(terminal: str | None) -> tuple[float, float]:
    if terminal and terminal in TERMINAL_COORDS:
        return TERMINAL_COORDS[terminal]
    return DEFAULT_COORD


def _ssl_context() -> ssl.SSLContext | None:
    try:
        if os.path.exists(_CA_BUNDLE):
            return ssl.create_default_context(cafile=_CA_BUNDLE)
    except Exception:  # noqa: BLE001
        return None
    return None


def _fetch_archive(lat: float, lon: float, start: date, end: date) -> dict[date, float]:
    """One key-less archive request → {day: tmean_F}. Best-effort; errors propagate to caller."""
    qs = (f"?latitude={lat:.3f}&longitude={lon:.3f}"
          f"&start_date={start.isoformat()}&end_date={end.isoformat()}"
          f"&daily=temperature_2m_mean&temperature_unit=fahrenheit&timezone=auto")
    req = urllib.request.Request(ARCHIVE_URL + qs, headers={"User-Agent": "rackiq/1.0"})
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT, context=_ssl_context()) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    daily = payload.get("daily") or {}
    times = daily.get("time") or []
    temps = daily.get("temperature_2m_mean") or []
    out: dict[date, float] = {}
    for d, t in zip(times, temps):
        if t is None:
            continue
        out[date.fromisoformat(d)] = float(t)
    return out


def _cached(con, location: str, start: date, end: date) -> dict[date, tuple[float, float, str]]:
    rows = con.execute(
        "SELECT day, hdd, cdd, source FROM weather_daily WHERE location = ? AND day BETWEEN ? AND ?",
        [location, start, end]).fetchall()
    out: dict[date, tuple[float, float, str]] = {}
    for day, hdd, cdd, source in rows:
        d = day if isinstance(day, date) else pd.Timestamp(day).date()
        out[d] = (float(hdd), float(cdd), source)
    return out


def _store(con, location: str, fetched: dict[date, float]) -> None:
    if not fetched:
        return
    recs = []
    for d, tmean in fetched.items():
        hdd, cdd = max(0.0, 65.0 - tmean), max(0.0, tmean - 65.0)
        recs.append({"location": location, "day": d, "tmean": tmean,
                     "hdd": hdd, "cdd": cdd, "source": "open-meteo"})
    df = pd.DataFrame(recs)
    con.register("_wx_df", df)
    try:
        con.execute("DELETE FROM weather_daily WHERE location = ? AND day IN "
                    "(SELECT day FROM _wx_df)", [location])
        con.execute("INSERT INTO weather_daily SELECT location, day, tmean, hdd, cdd, source FROM _wx_df")
    finally:
        con.unregister("_wx_df")


def daily_map(con, terminal: str | None, days: list[date],
              allow_fetch: bool = True) -> dict[date, tuple[float, float, str]]:
    """Return ``{day: (hdd, cdd, source)}`` for the requested days.

    Reads the cache, fetches the missing span once (best-effort, no key) when ``allow_fetch``,
    and fills anything still missing from the seasonal proxy. ``source`` is ``open-meteo`` for
    fetched/cached days and ``climatology`` for proxied days.
    """
    global _net_dead
    if not days:
        return {}
    location = terminal or "_default"
    ensure_tables(con)
    lo, hi = min(days), max(days)
    cache = _cached(con, location, lo, hi)
    missing = [d for d in days if d not in cache]

    # The archive lags ~5 days, so the very recent edge is never fetchable — exclude it from the
    # fetch trigger, otherwise every call would re-fetch the whole span chasing days that can't
    # be filled (they fall through to the proxy). Under pytest, always use the proxy (deterministic).
    horizon = date.today() - timedelta(days=5)
    missing_fetchable = [d for d in missing if d <= horizon]
    if (allow_fetch and missing_fetchable and _FETCH_ENABLED and not _net_dead
            and "PYTEST_CURRENT_TEST" not in os.environ):
        lat, lon = _coord(terminal)
        try:
            fetched = _fetch_archive(lat, lon, min(missing_fetchable), max(missing_fetchable))
            _store(con, location, fetched)
            cache = _cached(con, location, lo, hi)
        except Exception:  # noqa: BLE001 — any failure → proxy, and stop retrying
            _net_dead = True

    out: dict[date, tuple[float, float, str]] = {}
    for d in days:
        if d in cache:
            out[d] = cache[d]
        else:
            hdd, cdd = seasonal_hdd_cdd(d)
            out[d] = (round(hdd, 1), round(cdd, 1), "climatology")
    return out


def period_series(con, terminal: str | None, period_starts, grain: str,
                  allow_fetch: bool = True) -> dict[str, dict]:
    """Mean daily HDD/CDD for each lane period (a 'cold-snap week' is a high-HDD period).

    ``period_starts`` are the lane's bucket starts (weekly Mon or monthly 1st). Each period's
    weather is the average daily HDD/CDD over its day span — comparable across weekly/monthly grains.
    Returns ``{period_start_iso: {"hdd", "cdd", "source"}}``.
    """
    starts = [pd.Timestamp(s).date() for s in period_starts]
    if not starts:
        return {}
    step = timedelta(days=7) if grain != "monthly" else None

    def span_days(s: date) -> list[date]:
        if grain == "monthly":
            nxt = (pd.Timestamp(s) + pd.offsets.MonthBegin(1)).date()
        else:
            nxt = s + step
        n = max(1, (nxt - s).days)
        return [s + timedelta(days=i) for i in range(n)]

    all_days = sorted({d for s in starts for d in span_days(s)})
    dmap = daily_map(con, terminal, all_days, allow_fetch=allow_fetch)
    out: dict[str, dict] = {}
    for s in starts:
        ds = span_days(s)
        vals = [dmap.get(d) for d in ds if dmap.get(d) is not None]
        if not vals:
            out[s.isoformat()] = {"hdd": None, "cdd": None, "source": "none"}
            continue
        hdd = round(sum(v[0] for v in vals) / len(vals), 1)
        cdd = round(sum(v[1] for v in vals) / len(vals), 1)
        # the period's source is "open-meteo" if any day was real, else "climatology"
        source = "open-meteo" if any(v[2] == "open-meteo" for v in vals) else "climatology"
        out[s.isoformat()] = {"hdd": hdd, "cdd": cdd, "source": source}
    return out
