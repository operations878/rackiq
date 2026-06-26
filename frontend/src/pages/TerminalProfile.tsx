/**
 * The unified terminal view (same convergence pattern, lighter touch). Opens with a prescriptive
 * demand-vs-supply verdict, then tiles assembled from EXISTING endpoints: expected demand band
 * (hedging), committed vs spot (deal book), position / days-of-cover (the REAL Phase-7 engine —
 * gauge-anchored vs net-flow proxy, working-day cover, nominate-a-barge cure), and the margin at
 * stake (margin gap helper). Drill-down: cover by product, the burn-down trend, who-drives-risk.
 */
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type {
  HedgingResponse, DemandCockpit, MarginGap, ProfileTerminalRow, PositionResponse,
} from "../api/types";
import {
  PageHeader, Card, FacetTile, FacetValue, ActionChip, Because, Verdict, ProvenanceTag, Caveat,
  SectionHeading, SoWhat, gal, money, cents, num, type Tone,
} from "../lib/ui";
import { fmtDate } from "../lib/format";
import { positionSignal } from "../lib/adapters";
import BurnDownChart from "../components/demand/BurnDownChart";

export default function TerminalProfile({ name, navigate }: { name: string; navigate: (to: string) => void }) {
  const [row, setRow] = useState<ProfileTerminalRow | null>(null);
  const [hedge, setHedge] = useState<HedgingResponse | null>(null);
  const [cockpit, setCockpit] = useState<DemandCockpit | null>(null);
  const [gap, setGap] = useState<MarginGap | null>(null);
  const [position, setPosition] = useState<PositionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setRow(null); setHedge(null); setCockpit(null); setGap(null); setPosition(null); setError(null);
    api.profile.terminals()
      .then((terms) => { if (alive) setRow(terms.terminals.find((t) => t.terminal === name) ?? null); })
      .catch((e) => alive && setError(String(e)));
    api.hedging.get({ terminal: name, serviceLevel: 0.9 })
      .then((h) => {
        if (!alive) return;
        setHedge(h);
        const p50 = primaryHorizon(h)?.p50;
        if (p50 && p50 > 0) api.margin.gap({ terminal: name, quantity: p50 }).then((g) => alive && setGap(g)).catch(() => {});
      }).catch(() => {});
    api.demand.cockpit({ terminal: name }).then((ck) => alive && setCockpit(ck)).catch(() => {});
    api.position.get({ terminal: name }).then((p) => alive && setPosition(p)).catch(() => {});
    return () => { alive = false; };
  }, [name]);

  if (error) return <div className="text-sm text-rose-600">Could not load terminal: {error}</div>;

  const hz = hedge ? primaryHorizon(hedge) : null;
  const pos = positionSignal(position);
  const committedShare = row && (row.committed_gallons + row.spot_gallons) > 0
    ? row.committed_gallons / (row.committed_gallons + row.spot_gallons) : null;

  const action = pos.status === "short" ? "WATCH" : pos.status === "watch" ? "WATCH" : "LEAVE";
  const spineTone: Tone = pos.status === "short" ? "rose" : pos.status === "watch" ? "amber" : "indigo";
  const verdict = buildVerdict(name, hz, committedShare, pos, gap);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeader
        back={{ label: "All terminals", onClick: () => navigate("terminals") }}
        title={name}
        subtitle={row ? <span className="flex flex-wrap gap-x-4">
          <span>{row.customers} customers</span><span className="text-slate-300">·</span>
          <span>{row.lifts.toLocaleString()} lifts</span><span className="text-slate-300">·</span>
          <span>{gal(row.total_net_gallons)} all-time</span>
        </span> : "Loading…"}
        right={pos.available && pos.daysOfCover != null ? (
          <span className={`tnum rounded-full px-3 py-1 text-xs font-semibold ${
            pos.status === "short" ? "bg-rose-100 text-rose-700"
              : pos.status === "watch" ? "bg-amber-100 text-amber-800" : "bg-emerald-100 text-emerald-800"}`}>
            {fmtCover(pos.daysOfCover)} working days cover{pos.cell ? ` · ${pos.cell.product}` : ""}
          </span>
        ) : undefined}
      />

      {/* THE SPINE */}
      <Verdict action={<ActionChip action={action} />} tone={spineTone}>{verdict}</Verdict>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {/* EXPECTED DEMAND */}
        <FacetTile title="Expected demand" defKey="demand_band" accent="indigo" available={!!hz}
          unavailableNote="No forecast yet for this terminal.">
          {hz && (
            <div>
              <FacetValue value={gal(hz.p50)} caption={<>next {hz.horizon_working_days} working days · range {gal(hz.p10)}–{gal(hz.p90)}.</>} />
              <Because>Floor {gal(hz.floor)} steady · {gal(hz.upside)} upside. Summed from each customer's forecast.</Because>
            </div>
          )}
        </FacetTile>

        {/* COMMITTED VS SPOT */}
        <FacetTile title="Committed vs spot" defKey="committed_vs_spot" accent="indigo"
          available={!!row?.has_deals} unavailableNote={<span>Connect the <b>deal book</b>.</span>}>
          {row && (
            <div>
              <FacetValue value={<>{committedShare != null ? `${Math.round(committedShare * 100)}%` : "—"}<span className="text-sm font-normal text-slate-400"> committed</span></>}
                caption={<>{gal(row.committed_gallons)} term/forward · {gal(row.spot_gallons)} spot.</>} />
              <Because>Committed volume is must-serve first — it's the demand you can't walk away from if you're tight.</Because>
            </div>
          )}
        </FacetTile>

        {/* POSITION (REAL — Phase 7) */}
        <FacetTile title="Days of cover" defKey="days_of_cover"
          accent={pos.status === "short" ? "rose" : pos.status === "watch" ? "amber" : "emerald"}
          available={pos.available} unavailableNote={<span>{pos.caveat}</span>}>
          {pos.available && (
            <div>
              <div className="mb-1 flex justify-end"><ProvenanceTag kind={pos.isProxy ? "proxy" : "verified"} small /></div>
              <FacetValue value={<>{fmtCover(pos.daysOfCover)}<span className="text-sm font-normal text-slate-400"> wkg days</span></>}
                tone={pos.status === "short" ? "rose" : pos.status === "watch" ? "amber" : "emerald"}
                caption={pos.cell ? <>tightest: {pos.cell.product}, {pos.modeLabel}.</> : null} />
              {pos.cell?.cure?.short && pos.cell.cure.implied_barge_bbl ? (
                <SoWhat tone="rose">
                  Cure: nominate <b className="tnum">~{num(pos.cell.cure.implied_barge_bbl)} bbl</b>
                  {pos.cell.cure.nominate_by ? <> by {fmtDate(pos.cell.cure.nominate_by)}</> : ""} to hold {pos.cell.cure.to_hold_working_days ?? 10} working days.
                </SoWhat>
              ) : (
                <Because>Counted in working days at the recent burn. {pos.isProxy ? "Net-flow proxy — load a gauge for a true level." : "Anchored to a verified gauge."}</Because>
              )}
              {pos.caveat && <Caveat tone="amber">{pos.caveat}</Caveat>}
            </div>
          )}
        </FacetTile>

        {/* MARGIN AT STAKE + CURE */}
        <FacetTile title="Margin at stake" defKey="barge_cure" accent="indigo"
          available={!!gap?.available} unavailableNote={<span>Needs the <b>price grid + barge trips</b>.</span>}>
          {gap && (
            <div>
              <FacetValue value={money(gap.committed_margin_dollars)}
                caption={<>must-serve margin on committed volume.{gap.spot_margin_dollars != null && <span className="mt-1 block text-[11px] text-slate-400">+{money(gap.spot_margin_dollars)} spot upside · blended {cents(gap.blended_margin_cents_gal)}</span>}</>} />
              <Because>If you're tight, this committed margin is what's at risk first — weigh it against the barge-nomination cost.</Because>
            </div>
          )}
        </FacetTile>
      </div>

      {/* DRILL-DOWN: cover by product (the real position cells) */}
      {pos.available && position && position.positions.length > 0 && (
        <>
          <SectionHeading note={position.working_day_note}>Cover by product</SectionHeading>
          <Card className="overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50 text-xs text-slate-400">
                    <th className="px-3 py-2 text-left font-medium">Product</th>
                    <th className="px-3 py-2 text-left font-medium">Read</th>
                    <th className="px-3 py-2 text-right font-medium">Position</th>
                    <th className="px-3 py-2 text-right font-medium">Cover (wkg days)</th>
                    <th className="px-3 py-2 text-left font-medium">Cure</th>
                  </tr>
                </thead>
                <tbody>
                  {position.positions.map((p) => (
                    <tr key={p.product} className="border-b border-slate-100 last:border-0">
                      <td className="px-3 py-2.5 font-medium text-slate-800">{p.product}</td>
                      <td className="px-3 py-2.5"><ProvenanceTag kind={p.mode === "gauge" ? "verified" : "proxy"} small /></td>
                      <td className="tnum px-3 py-2.5 text-right text-slate-700">{gal(p.position_gallons)}</td>
                      <td className="tnum px-3 py-2.5 text-right">
                        <span className={`font-semibold ${p.status === "short" ? "text-rose-700" : p.status === "watch" ? "text-amber-700" : "text-emerald-700"}`}>
                          {fmtCover(p.days_of_cover)}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-xs text-slate-500">
                        {p.cure?.short && p.cure.implied_barge_bbl
                          ? <>nominate ~<span className="tnum">{num(p.cure.implied_barge_bbl)}</span> bbl{p.cure.nominate_by ? ` by ${fmtDate(p.cure.nominate_by)}` : ""}</>
                          : <span className="text-slate-300">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {position.recency_note && <div className="border-t border-slate-100 px-3 py-2 text-[11px] text-slate-400">{position.recency_note}</div>}
          </Card>
        </>
      )}

      {/* DRILL-DOWN: inventory burn-down trend */}
      {cockpit?.burndown && cockpit.burndown.series?.length > 0 && (
        <Card className="p-5">
          <h3 className="mb-2 text-sm font-medium text-slate-700">Position trend — inventory burn-down</h3>
          <BurnDownChart burndown={cockpit.burndown} />
          <p className="mt-2 text-[10px] text-slate-400">Projected at the forecast P50/P10/P90 burn vs. the heel &amp; capacity lines.</p>
        </Card>
      )}

      {/* who drives the surprise */}
      {hedge && hedge.watch_list?.length > 0 && (
        <>
          <SectionHeading note="Ranked by share of demand variability — who makes the buffer necessary.">Who drives the risk here</SectionHeading>
          <Card className="overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50 text-xs text-slate-400">
                    <th className="px-3 py-2 text-left font-medium">Customer</th>
                    <th className="px-3 py-2 text-left font-medium">Pattern</th>
                    <th className="px-3 py-2 text-right font-medium">Typical load</th>
                    <th className="px-3 py-2 text-right font-medium">Variability share</th>
                  </tr>
                </thead>
                <tbody>
                  {hedge.watch_list.slice(0, 8).map((w) => (
                    <tr key={w.customer_id} onClick={() => navigate(`customer/${w.customer_id}`)}
                      className="cursor-pointer border-b border-slate-100 last:border-0 hover:bg-indigo-50/40">
                      <td className="px-3 py-2.5 font-medium text-slate-800">
                        {w.name}{w.overdue && <span className="ml-1.5 rounded bg-amber-100 px-1 py-0.5 text-[9px] font-semibold text-amber-700">overdue</span>}
                      </td>
                      <td className="px-3 py-2.5 text-xs text-slate-500">{w.behavior_label ?? "—"}</td>
                      <td className="tnum px-3 py-2.5 text-right text-slate-700">{gal(w.typical_load)}</td>
                      <td className="tnum px-3 py-2.5 text-right font-medium text-slate-700">{Math.round((w.variability_share ?? 0) * 100)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

function fmtCover(d: number | null | undefined): string {
  if (d == null) return "—";
  return d >= 10 ? String(Math.round(d)) : d.toFixed(1);
}

function buildVerdict(name: string, hz: ReturnType<typeof primaryHorizon>, committedShare: number | null,
                      pos: ReturnType<typeof positionSignal>, gap: MarginGap | null): string {
  const parts: string[] = [];
  if (hz) parts.push(`expects ~${gal(hz.p50)} over the next ${hz.horizon_working_days} working days (range ${gal(hz.p10)}–${gal(hz.p90)})`);
  if (committedShare != null) parts.push(`~${Math.round(committedShare * 100)}% committed must-serve`);
  if (pos.available && pos.daysOfCover != null) {
    const prod = pos.cell ? `${pos.cell.product} ` : "";
    if (pos.status === "short") parts.push(`only ${fmtCover(pos.daysOfCover)} working days of ${prod}cover — bring in a barge soon`);
    else if (pos.status === "watch") parts.push(`${fmtCover(pos.daysOfCover)} working days of ${prod}cover, getting tight`);
    else parts.push(`${fmtCover(pos.daysOfCover)} working days of ${prod}cover, comfortable`);
  } else {
    parts.push("position needs supply + lifts loaded (target staging only)");
  }
  let s = `${name} — ` + parts.join(", ") + ".";
  if (pos.cell?.cure?.short && pos.cell.cure.implied_barge_bbl) {
    s += ` Nominate ~${num(pos.cell.cure.implied_barge_bbl)} bbl${pos.cell.cure.nominate_by ? ` by ${fmtDate(pos.cell.cure.nominate_by)}` : ""}.`;
  } else if (gap?.available && gap.committed_margin_dollars != null && pos.tight) {
    s += ` ${money(gap.committed_margin_dollars)} of committed margin is at stake.`;
  }
  return s;
}

function primaryHorizon(h: HedgingResponse) {
  if (!h.horizons?.length) return null;
  if (h.primary_horizon != null) {
    const m = h.horizons.find((x) => x.horizon_working_days === h.primary_horizon);
    if (m) return m;
  }
  return h.horizons[h.horizons.length - 1];
}
