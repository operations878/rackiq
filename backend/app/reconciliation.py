"""Reconciliation & gain/loss engine — terminal loss control for wholesale fuel.

Gated on physical inventory + receipt detail (the P2.5 feed). Reads the canonical store and,
per tank · terminal · product · period, computes:

  1. BOOK vs PHYSICAL gain/loss in GROSS and NET:
        loss = opening_physical + receipts − BOL_disbursements − closing_physical
     BOL disbursements are grouped by ``bol_number`` and summed across compartments — a
     compartment row is never treated as a standalone lift (``bol_compartments``).
  2. NET-RECON cross-check: where a BOL carries a stated/billed net AND temp+gravity allow an
     independent ASTM D1250 recompute, the two are compared and SYSTEMATIC divergence is flagged
     by lane/meter/terminal (probe calibration / VCF-table mismatch). The billed net is never
     overwritten — the delta is reported.
  3. LOSS-MECHANISM split: each tank's loss is decomposed into
        (a) volumetric/temperature  = gross-loss − net-loss   (vanishes under VCF correction)
        (b) measurement             = recomputed-net − billed-net   (meter drift / gauging)
        (c) physical                = net-loss − measurement        (evaporation/line-fill/theft)
     The three sum to the GROSS book-to-physical gap.
  4. RECEIPT measurement basis: marine vessel B/L-vs-shore (VEF) and pipeline B/L-vs-received
     shrinkage surfaced as their own line items from the receipt detail.
  5. LOSS TRACKING: loss-% of throughput over time, with routine shrinkage vs anomalies.
  6. METER-DRIFT detection: control-chart logic on each tank's loss-% vs the network routine
     distribution; tanks running persistently outside the limits are flagged and ranked.
  7. DOLLARIZE: losses valued at unit cost, ranked, with a network recoverable total.

Everything is capability-gated and every threshold lives in :class:`ReconConfig`.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import db, schema
from .hygiene import vcf
from .reconciliation_config import DEFAULT_CONFIG, PERIOD_GRAINS, ReconConfig


# ---- Gating ---------------------------------------------------------------------
def _present(con, table: str, field: str) -> bool:
    try:
        return int(con.execute(f'SELECT count("{field}") FROM {table}').fetchone()[0]) > 0
    except Exception:  # noqa: BLE001 — table/column may not exist on a thin store
        return False


def availability(con) -> dict:
    """Hard gate: physical_inventory + receipt detail. Returns missing feeds for the lock."""
    missing = []
    if not _present(con, schema.INVENTORY, "physical_inventory"):
        missing.append("physical_inventory")
    if not _present(con, schema.RECEIPTS, "receipt_source"):
        missing.append("receipt_source")
    has_bol = _present(con, schema.BOL, "compartment_net_gallons")
    reason = ("Reconciliation runs on physical inventory + receipt detail."
              if not missing else
              "Feed me " + " and ".join(missing) + " to unlock reconciliation & loss control.")
    return {"available": not missing, "missing_fields": missing, "has_bol": has_bol,
            "reason": reason}


# ---- Small helpers --------------------------------------------------------------
def _robust_sigma(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 2:
        return 0.0
    med = float(np.median(x))
    s = 1.4826 * float(np.median(np.abs(x - med)))
    return s if s > 0 else float(np.std(x))


def _pstart(ts, grain: str) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.to_period("W").start_time if grain == "week" else ts.to_period("M").start_time


def _safe_pct(num: float, den: float) -> float:
    return 100.0 * num / den if den else 0.0


def _longest_run_above(vals: list[float], center: float) -> int:
    best = cur = 0
    for v in vals:
        cur = cur + 1 if v > center else 0
        best = max(best, cur)
    return best


# ---- Data loading ---------------------------------------------------------------
def _load(con, grain: str) -> dict:
    inv = con.execute(
        "SELECT snapshot_datetime, terminal, product, tank_id, tank_capacity, min_heel, "
        "inventory_snapshot, physical_inventory, receipts FROM inventory_snapshots "
        "WHERE physical_inventory IS NOT NULL AND snapshot_datetime IS NOT NULL").df()
    rec = con.execute(
        "SELECT receipt_datetime, terminal, product, receipt_source, receipt_gross_gallons, "
        "receipt_net_gallons, measurement_basis, bl_vs_received_variance FROM receipts").df()
    bol = (con.execute(
        "SELECT bol_number, bol_datetime, terminal, product, tank_id, meter_id, "
        "compartment_gross_gallons, compartment_net_gallons, compartment_temp, "
        "compartment_api, compartment_unit_cost FROM bol_compartments").df()
        if db.row_count(con, schema.BOL) else pd.DataFrame())
    lifts = con.execute(
        "SELECT terminal, product, lift_datetime, net_gallons, unit_cost FROM lifts "
        "WHERE terminal IS NOT NULL AND product IS NOT NULL AND net_gallons IS NOT NULL").df()

    for df, col in ((inv, "snapshot_datetime"), (rec, "receipt_datetime"),
                    (bol, "bol_datetime"), (lifts, "lift_datetime")):
        if len(df):
            df[col] = pd.to_datetime(df[col])
    if len(inv):
        inv["pkey"] = inv["snapshot_datetime"].map(lambda t: _pstart(t, grain))
        # tank_id may be null on a thin inventory feed — fall back to a terminal·product label.
        inv["tank_id"] = inv["tank_id"].where(inv["tank_id"].notna(),
                                               inv["terminal"].astype(str) + "·" + inv["product"].astype(str))
    return {"inv": inv, "rec": rec, "bol": bol, "lifts": lifts, "has_bol": bool(len(bol))}


def _per_bol(bol: pd.DataFrame, grain: str) -> pd.DataFrame:
    """Group raw compartment rows into one row per BOL (sum compartments).

    A compartment is never a standalone disbursement: ``billed`` is the BOL's metered net, and
    ``recomputed`` is the independent ASTM D1250 net summed over compartments that carry the
    temp + gravity to recompute. ``has_cross`` flags BOLs where every compartment supports it.
    """
    g = pd.to_numeric(bol["compartment_gross_gallons"], errors="coerce")
    t = pd.to_numeric(bol["compartment_temp"], errors="coerce")
    a = pd.to_numeric(bol["compartment_api"], errors="coerce")
    nb = pd.to_numeric(bol["compartment_net_gallons"], errors="coerce")
    cost = pd.to_numeric(bol["compartment_unit_cost"], errors="coerce")
    prod = bol["product"].astype(str)
    cross = g.notna() & t.notna() & a.notna()
    rec = nb.to_numpy(dtype=float).copy()
    gi, ti, ai = g.to_numpy(float), t.to_numpy(float), a.to_numpy(float)
    pv = prod.to_numpy()
    for i in np.where(cross.to_numpy())[0]:
        rec[i] = gi[i] * vcf(ai[i], ti[i], pv[i])
    work = pd.DataFrame({
        "bol_number": bol["bol_number"], "bol_datetime": bol["bol_datetime"],
        "terminal": bol["terminal"], "product": bol["product"], "tank_id": bol["tank_id"],
        "meter_id": bol["meter_id"], "billed": nb, "gross": g.fillna(0.0),
        "recomputed": rec, "cost": cost, "cross": cross,
    })
    per = work.groupby("bol_number", as_index=False).agg(
        bol_datetime=("bol_datetime", "first"), terminal=("terminal", "first"),
        product=("product", "first"), tank_id=("tank_id", "first"), meter_id=("meter_id", "first"),
        billed=("billed", "sum"), gross=("gross", "sum"), recomputed=("recomputed", "sum"),
        cost=("cost", "mean"), has_cross=("cross", "all"), n_comp=("billed", "size"))
    per["pkey"] = per["bol_datetime"].map(lambda x: _pstart(x, grain))
    return per


# ---- Per-tank · period reconciliation -------------------------------------------
def _period_records(data: dict, grain: str) -> tuple[list[dict], dict, bool]:
    """Build the (tank, period) reconciliation records + per-tank unit cost. Returns
    (records, unit_cost_by_tank, has_bol)."""
    inv, rec, has_bol = data["inv"], data["rec"], data["has_bol"]
    if not len(inv):
        return [], {}, has_bol

    gcols = ["terminal", "product", "tank_id"] if has_bol else ["terminal", "product"]

    # Inventory rolled to the reconciliation grain (sum tanks at each reading for the tp grain).
    inv2 = inv.groupby(gcols + ["snapshot_datetime", "pkey"], as_index=False).agg(
        physical=("physical_inventory", "sum"), book=("inventory_snapshot", "sum"),
        capacity=("tank_capacity", "sum"), min_heel=("min_heel", "sum"))

    # Disbursements per (grain, period): BOL-grouped when available, else lifts (net only).
    disb: dict = {}
    unit_cost: dict = {}
    if has_bol:
        per = _per_bol(data["bol"], grain)
        agg = per.groupby(gcols + ["pkey"], as_index=False).agg(
            billed=("billed", "sum"), gross=("gross", "sum"), recomputed=("recomputed", "sum"),
            n_bols=("bol_number", "size"), cross=("has_cross", "all"))
        for r in agg.to_dict("records"):
            disb[tuple(r[c] for c in gcols) + (r["pkey"],)] = r
        for r in per.groupby(gcols, as_index=False)["cost"].mean().to_dict("records"):
            unit_cost[tuple(r[c] for c in gcols)] = r["cost"]
    else:
        lifts = data["lifts"]
        if len(lifts):
            lifts = lifts.copy()
            lifts["pkey"] = lifts["lift_datetime"].map(lambda x: _pstart(x, grain))
            agg = lifts.groupby(gcols + ["pkey"], as_index=False).agg(
                billed=("net_gallons", "sum"), n_bols=("net_gallons", "size"))
            for r in agg.to_dict("records"):
                d = {**r, "gross": None, "recomputed": None, "cross": False}
                disb[tuple(r[c] for c in gcols) + (r["pkey"],)] = d
            for r in lifts.groupby(gcols, as_index=False)["unit_cost"].mean().to_dict("records"):
                unit_cost[tuple(r[c] for c in gcols)] = r["unit_cost"]

    # Receipts per (terminal, product, period) — allocated to tanks pro-rata by disbursement.
    rec_tp: dict = {}
    if len(rec):
        rr = rec.copy()
        rr["pkey"] = rr["receipt_datetime"].map(lambda x: _pstart(x, grain))
        ragg = rr.groupby(["terminal", "product", "pkey"], as_index=False).agg(
            net=("receipt_net_gallons", "sum"), gross=("receipt_gross_gallons", "sum"))
        for r in ragg.to_dict("records"):
            rec_tp[(r["terminal"], r["product"], r["pkey"])] = r

    # Disbursement share per (terminal, product, period) → receipt allocation weights.
    tp_disb: dict = {}
    for key, d in disb.items():
        tp = (key[0], key[1], key[-1])
        tp_disb[tp] = tp_disb.get(tp, 0.0) + float(d.get("billed") or 0.0)

    records: list[dict] = []
    for gkey, grp in inv2.groupby(gcols):
        gkey = gkey if isinstance(gkey, tuple) else (gkey,)
        grp = grp.sort_values("snapshot_datetime")
        prev_close = None
        for pkey, mrows in grp.groupby("pkey"):
            mrows = mrows.sort_values("snapshot_datetime")
            closing = float(mrows["physical"].iloc[-1])
            # The first period only seeds the opening gauge — reconcile from the 2nd on, so each
            # period's opening is a genuine prior-period close (no double-counted intra-period draw).
            if prev_close is None:
                prev_close = closing
                continue
            opening = prev_close
            prev_close = closing
            capacity = float(mrows["capacity"].iloc[-1])

            d = disb.get(gkey + (pkey,))
            billed = float(d["billed"]) if d else 0.0
            tp = (gkey[0], gkey[1], pkey)
            share = (billed / tp_disb[tp]) if tp_disb.get(tp) else (1.0 if d else 0.0)
            rinfo = rec_tp.get((gkey[0], gkey[1], pkey))
            receipts_net = float(rinfo["net"]) * share if rinfo else 0.0
            receipts_gross = float(rinfo["gross"]) * share if rinfo else 0.0

            net_loss = opening + receipts_net - billed - closing
            if d and d.get("gross") is not None and d.get("cross"):
                gross = float(d["gross"])
                recomputed = float(d["recomputed"])
                # NET loss splits cleanly into measurement (the cross-check delta) + physical
                # (the residual). TEMPERATURE/volumetric is the net thermal shrink embedded in
                # throughput (gross-vs-net at differing temps in vs out) — the apparent loss you'd
                # mis-read on a GROSS basis; it nets out of the net reconciliation. The three sum
                # to the gross book-to-physical gap (gross_loss = net_loss + temperature).
                measurement = recomputed - billed
                temperature = (gross - recomputed) - (receipts_gross - receipts_net)
                physical = net_loss - measurement
                gross_loss = net_loss + temperature
                mech_ok = True
            else:
                gross_loss = recomputed = measurement = temperature = None
                physical = net_loss
                mech_ok = False

            records.append({
                "terminal": gkey[0], "product": gkey[1],
                "tank_id": gkey[2] if has_bol else f"{gkey[0]}·{gkey[1]}",
                "pkey": pkey, "period": str(pd.Timestamp(pkey).date()),
                "opening": opening, "closing": closing, "capacity": capacity,
                "receipts_net": receipts_net, "throughput": billed, "n_bols": int(d["n_bols"]) if d else 0,
                "net_loss": net_loss, "gross_loss": gross_loss,
                "temperature": temperature, "measurement": measurement, "physical": physical,
                "loss_pct": _safe_pct(net_loss, billed), "mech_ok": mech_ok,
            })
    return records, unit_cost, has_bol


# ---- Orchestration --------------------------------------------------------------
def compute_reconciliation(con, cfg: ReconConfig | None = None, period: str | None = None) -> dict:
    cfg = cfg or DEFAULT_CONFIG
    grain = period if period in PERIOD_GRAINS else cfg.period_grain
    avail = availability(con)
    if not avail["available"]:
        return {"available": False, "reason": avail["reason"],
                "missing_fields": avail["missing_fields"], "period_grain": grain,
                "config": cfg.to_dict()}

    data = _load(con, grain)
    records, unit_cost, has_bol = _period_records(data, grain)
    if not records:
        return {"available": True, "period_grain": grain, "has_bol": has_bol,
                "as_of": None, "config": cfg.to_dict(), "network": None, "tanks": [],
                "net_recon": {"by_meter": [], "by_terminal": []},
                "receipts": {"by_source": []}, "loss_tracking": {"network_series": []},
                "meter_drift": {"ranked": [], "n_out_of_control": 0},
                "note": "No overlapping inventory/disbursement periods to reconcile yet."}

    rdf = pd.DataFrame(records)
    horizon_days = max(1, (rdf["pkey"].max() - rdf["pkey"].min()).days
                       + (30 if grain == "month" else 7))
    ann = (365.0 / horizon_days) if cfg.annualize else 1.0

    # ---- Control limits from the NETWORK routine-shrinkage distribution ----
    pool = rdf[rdf["throughput"] > 0]["loss_pct"].to_numpy(dtype=float)
    center = float(np.median(pool)) if cfg.baseline == "median" and len(pool) else (
        float(np.mean(pool)) if len(pool) else 0.0)
    sigma = _robust_sigma(pool) or (float(np.std(pool)) if len(pool) else 0.0) or 1e-6
    ucl = center + cfg.control_k * sigma
    lcl = center - cfg.control_k * sigma

    network_net = float(rdf["net_loss"].sum())
    network_thru = float(rdf["throughput"].sum())
    network_pct = _safe_pct(network_net, network_thru)

    # ---- Per-tank rollup (worst offenders, mechanism split, control chart, dollars) ----
    tanks = []
    for gkey, g in rdf.groupby(["terminal", "product", "tank_id"]):
        g = g.sort_values("pkey")
        terminal, product, tank_id = gkey
        thru = float(g["throughput"].sum())
        net_loss = float(g["net_loss"].sum())
        loss_pct = _safe_pct(net_loss, thru)
        mech_ok = bool(g["mech_ok"].any())
        cost = unit_cost.get((terminal, product, tank_id) if has_bol else (terminal, product))
        cost = float(cost) if cost is not None and not (isinstance(cost, float) and math.isnan(cost)) else cfg.default_unit_cost
        dollar_yr = net_loss * cost * ann
        recoverable_gal = max(0.0, net_loss - (center / 100.0) * thru)
        recoverable_yr = recoverable_gal * cost * ann

        period_pcts = g["loss_pct"].to_numpy(dtype=float)
        n_out = int(np.sum(period_pcts > ucl))
        run_above = _longest_run_above(list(period_pcts), center)
        mean_pct = float(np.mean(period_pcts)) if len(period_pcts) else 0.0
        last_pct = float(period_pcts[-1]) if len(period_pcts) else 0.0
        persistent = (n_out >= cfg.min_out_periods and last_pct > ucl) or (run_above >= cfg.run_rule_len)
        severity = round(max(0.0, (mean_pct - center) / sigma) + 0.25 * run_above, 2)

        mech = {"temperature": None, "measurement": None, "physical": None} if not mech_ok else {
            "temperature": round(float(g["temperature"].sum()), 1),
            "measurement": round(float(g["measurement"].sum()), 1),
            "physical": round(float(g["physical"].sum()), 1)}
        # The actionable driver of the NET loss is measurement vs physical (temperature is the
        # benign thermal bridge to the gross figure), so rank "what's moving" between those two.
        dominant = None
        if mech_ok:
            dominant = ("measurement" if abs(mech["measurement"] or 0.0) >= abs(mech["physical"] or 0.0)
                        else "physical")

        first_half = period_pcts[: len(period_pcts) // 2]
        second_half = period_pcts[len(period_pcts) // 2:]
        trend = "rising" if (len(second_half) and len(first_half)
                             and second_half.mean() - first_half.mean() > 0.5 * sigma) else (
            "falling" if (len(second_half) and len(first_half)
                          and first_half.mean() - second_half.mean() > 0.5 * sigma) else "flat")

        tanks.append({
            "tank_id": tank_id, "terminal": terminal, "product": product,
            "meter_id": f"MTR-{tank_id}" if not has_bol else None,
            "throughput_gal": round(thru, 1), "net_loss_gal": round(net_loss, 1),
            "gross_loss_gal": round(float(g["gross_loss"].sum()), 1) if mech_ok else None,
            "loss_pct": round(loss_pct, 4), "unit_cost": round(cost, 4),
            "dollar_loss_per_yr": round(dollar_yr, 0),
            "recoverable_dollar_per_yr": round(recoverable_yr, 0),
            "mechanism": mech, "dominant_mechanism": dominant,
            "control": {"mean_pct": round(mean_pct, 4), "last_pct": round(last_pct, 4),
                        "ucl_pct": round(ucl, 4), "lcl_pct": round(lcl, 4),
                        "n_out": n_out, "run_above": run_above,
                        "persistent_out": bool(persistent), "severity": severity, "trend": trend},
            "vs_network": f"{loss_pct:.2f}% vs {network_pct:.2f}% network avg ≈ ${dollar_yr:,.0f}/yr",
            "series": [{
                "period": r["period"], "throughput": round(r["throughput"], 1),
                "net_loss_gal": round(r["net_loss"], 1),
                "gross_loss_gal": round(r["gross_loss"], 1) if r["gross_loss"] is not None else None,
                "loss_pct": round(r["loss_pct"], 4),
                "temperature_gal": round(r["temperature"], 1) if r["temperature"] is not None else None,
                "measurement_gal": round(r["measurement"], 1) if r["measurement"] is not None else None,
                "physical_gal": round(r["physical"], 1) if r["physical"] is not None else None,
                "out_of_control": bool(r["loss_pct"] > ucl),
            } for r in g.to_dict("records")],
        })
    tanks.sort(key=lambda t: t["dollar_loss_per_yr"], reverse=True)

    # ---- Network mechanism split (sums to gross book-to-physical gap) ----
    mech_ok_any = bool(rdf["mech_ok"].any())
    network_mech = None
    network_gross = None
    if mech_ok_any:
        temp_sum = float(rdf["temperature"].dropna().sum())
        meas_sum = float(rdf["measurement"].dropna().sum())
        phys_sum = float(rdf["physical"].where(rdf["mech_ok"]).dropna().sum())
        network_gross = float(rdf["gross_loss"].dropna().sum())
        denom = abs(temp_sum) + abs(meas_sum) + abs(phys_sum) or 1.0
        network_mech = {
            "temperature_gal": round(temp_sum, 1), "measurement_gal": round(meas_sum, 1),
            "physical_gal": round(phys_sum, 1),
            "temperature_pct": round(100.0 * temp_sum / denom, 1),
            "measurement_pct": round(100.0 * meas_sum / denom, 1),
            "physical_pct": round(100.0 * phys_sum / denom, 1)}

    network_dollar = sum(t["dollar_loss_per_yr"] for t in tanks)
    network_recoverable = sum(t["recoverable_dollar_per_yr"] for t in tanks)

    network = {
        "throughput_gal": round(network_thru, 1), "net_loss_gal": round(network_net, 1),
        "gross_loss_gal": round(network_gross, 1) if network_gross is not None else None,
        "loss_pct": round(network_pct, 4),
        "dollar_loss_per_yr": round(network_dollar, 0),
        "recoverable_dollar_per_yr": round(network_recoverable, 0),
        "mechanism": network_mech, "n_tanks": len(tanks),
        "n_bols": int(rdf["n_bols"].sum()), "horizon_days": horizon_days,
        "control": {"center_pct": round(center, 4), "sigma_pct": round(sigma, 4),
                    "ucl_pct": round(ucl, 4), "lcl_pct": round(lcl, 4), "k": cfg.control_k},
    }

    # ---- Loss tracking: network loss-% per period (routine vs anomaly) ----
    net_series = []
    for pkey, g in rdf.groupby("pkey"):
        thru = float(g["throughput"].sum())
        nl = float(g["net_loss"].sum())
        lp = _safe_pct(nl, thru)
        net_series.append({"period": str(pd.Timestamp(pkey).date()), "throughput": round(thru, 1),
                           "net_loss_gal": round(nl, 1), "loss_pct": round(lp, 4),
                           "anomaly": bool(lp > ucl)})
    net_series.sort(key=lambda r: r["period"])

    # ---- Meter-drift ranking (control-chart offenders) ----
    drift_ranked = sorted(
        ({"tank_id": t["tank_id"], "meter_id": t["meter_id"] or f"MTR-{t['tank_id']}",
          "terminal": t["terminal"], "product": t["product"],
          "severity": t["control"]["severity"], "mean_pct": t["control"]["mean_pct"],
          "last_pct": t["control"]["last_pct"], "ucl_pct": t["control"]["ucl_pct"],
          "n_out": t["control"]["n_out"], "run_above": t["control"]["run_above"],
          "persistent_out": t["control"]["persistent_out"], "trend": t["control"]["trend"],
          "dominant_mechanism": t["dominant_mechanism"]}
         for t in tanks if t["control"]["severity"] > 0),
        key=lambda r: r["severity"], reverse=True)

    return {
        "available": True, "period_grain": grain, "has_bol": has_bol,
        "as_of": str(rdf["pkey"].max().date()), "config": cfg.to_dict(),
        "network": network, "tanks": tanks,
        "net_recon": _net_recon(data, cfg) if has_bol else {
            "available": False, "by_meter": [], "by_terminal": [],
            "reason": "BOL compartment detail (gross + temp + gravity) needed for the cross-check."},
        "receipts": _receipt_basis(data),
        "loss_tracking": {"network_series": net_series},
        "meter_drift": {"ranked": drift_ranked,
                        "n_out_of_control": sum(1 for r in drift_ranked if r["persistent_out"])},
    }


# ---- (2) Net-recon cross-check: billed net vs independent ASTM recompute ---------
def _net_recon(data: dict, cfg: ReconConfig) -> dict:
    per = _per_bol(data["bol"], data_grain := "month")  # grain irrelevant for the meter rollup
    per = per[per["has_cross"]]
    if not len(per):
        return {"available": False, "by_meter": [], "by_terminal": [],
                "reason": "No BOLs carry the temp + gravity needed to recompute net."}
    per = per.copy()
    per["delta"] = per["recomputed"] - per["billed"]
    per["sign"] = np.sign(per["delta"])

    def _rollup(by: list[str]) -> list[dict]:
        out = []
        for gkey, g in per.groupby(by):
            gkey = gkey if isinstance(gkey, tuple) else (gkey,)
            billed = float(g["billed"].sum())
            recomputed = float(g["recomputed"].sum())
            delta = recomputed - billed
            delta_pct = _safe_pct(delta, billed)
            n = int(len(g))
            dom_sign = 1.0 if delta >= 0 else -1.0
            consistency = float((g["sign"] == dom_sign).mean())
            systematic = (n >= cfg.min_bols_for_systematic
                          and abs(delta_pct) >= cfg.systematic_pct_threshold * 100.0
                          and consistency >= cfg.sign_consistency)
            g = g.sort_values("bol_datetime")
            half = len(g) // 2
            fp = _safe_pct(float(g["delta"].iloc[:half].sum()), float(g["billed"].iloc[:half].sum())) if half else 0.0
            sp = _safe_pct(float(g["delta"].iloc[half:].sum()), float(g["billed"].iloc[half:].sum())) if half else delta_pct
            trend = "rising" if sp - fp > 0.05 else ("falling" if fp - sp > 0.05 else "flat")
            row = {by[i]: gkey[i] for i in range(len(by))}
            label = None
            if systematic:
                direction = "under" if delta > 0 else "over"
                cause = ("totalizer drift" if trend == "rising" else "VCF / temperature-probe calibration")
                label = (f"Billed net runs {abs(delta_pct):.2f}% {direction} the ASTM recompute "
                         f"across {n} BOLs — likely {cause}.")
            row.update({"n_bols": n, "billed_net": round(billed, 1),
                        "recomputed_net": round(recomputed, 1), "delta_gal": round(delta, 1),
                        "delta_pct": round(delta_pct, 4), "consistency": round(consistency, 3),
                        "systematic": bool(systematic), "trend": trend, "flag_label": label})
            out.append(row)
        out.sort(key=lambda r: abs(r["delta_gal"]), reverse=True)
        return out

    return {"available": True, "by_meter": _rollup(["meter_id", "terminal", "product"]),
            "by_terminal": _rollup(["terminal"]),
            "checked_bols": int(len(per)), "checked_compartments": int(per["n_comp"].sum())}


# ---- (4) Receipt measurement basis: vessel VEF / pipeline shrinkage --------------
def _receipt_basis(data: dict) -> dict:
    rec = data["rec"]
    if not len(rec):
        return {"available": False, "by_source": []}
    rec = rec.copy()
    rec["net"] = pd.to_numeric(rec["receipt_net_gallons"], errors="coerce")
    rec["gross"] = pd.to_numeric(rec["receipt_gross_gallons"], errors="coerce")
    rec["bl"] = pd.to_numeric(rec["bl_vs_received_variance"], errors="coerce")
    _LABEL = {"marine": "Vessel B/L vs shore tank (VEF)",
              "pipeline": "Pipeline B/L vs received (line shrink)",
              "truck": "Truck B/L vs received"}
    by_source = []
    for src, g in rec.groupby(rec["receipt_source"].astype(str).str.lower()):
        net = float(g["net"].sum())
        gross = float(g["gross"].sum())
        bl = float(g["bl"].dropna().sum())
        basis = g["measurement_basis"].dropna()
        by_source.append({
            "source": src, "n": int(len(g)), "gross_gal": round(gross, 1), "net_gal": round(net, 1),
            "bl_variance_gal": round(bl, 1), "bl_variance_pct": round(_safe_pct(bl, net), 4),
            "thermal_gap_gal": round(gross - net, 1),
            "measurement_basis": basis.mode().iloc[0] if len(basis) else None,
            "label": _LABEL.get(src, f"{src} receipts")})
    by_source.sort(key=lambda r: r["net_gal"], reverse=True)
    vef = next((r["bl_variance_pct"] for r in by_source if r["source"] == "marine"), None)
    shrink = next((r["bl_variance_pct"] for r in by_source if r["source"] == "pipeline"), None)
    return {"available": True, "by_source": by_source,
            "vessel_vef_pct": vef, "pipeline_shrink_pct": shrink}
