/**
 * The unified terminal view (same convergence pattern, lighter touch). Opens with a prescriptive
 * demand-vs-supply verdict, then tiles assembled from EXISTING endpoints: expected demand band
 * (hedging), committed vs spot (deal book), position / days-of-cover (demand cockpit — behind the
 * INTERIM position adapter, Phase 7 swaps in a gauge), and the margin at stake + barge-nomination
 * cure (margin gap helper). Drill-down: the inventory burn-down trend and the who-drives-risk list.
 */
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { HedgingResponse, DemandCockpit, MarginGap, ProfileTerminalRow } from "../api/types";
import {
  PageHeader, Card, FacetTile, FacetValue, ActionChip, Because, gal, money, cents, type Tone,
} from "../lib/ui";
import { positionSignal } from "../lib/adapters";
import BurnDownChart from "../components/demand/BurnDownChart";

export default function TerminalProfile({ name, navigate }: { name: string; navigate: (to: string) => void }) {
  const [row, setRow] = useState<ProfileTerminalRow | null>(null);
  const [hedge, setHedge] = useState<HedgingResponse | null>(null);
  const [cockpit, setCockpit] = useState<DemandCockpit | null>(null);
  const [gap, setGap] = useState<MarginGap | null>(null);
  const [invConnected, setInvConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setRow(null); setHedge(null); setCockpit(null); setGap(null); setError(null);
    // fetch the independent sources in PARALLEL (faster than chaining); margin.gap follows hedging
    api.profile.terminals()
      .then((terms) => { if (!alive) return; setInvConnected(terms.inventory_connected); setRow(terms.terminals.find((t) => t.terminal === name) ?? null); })
      .catch((e) => alive && setError(String(e)));
    api.hedging.get({ terminal: name, serviceLevel: 0.9 })
      .then((h) => {
        if (!alive) return;
        setHedge(h);
        const p50 = primaryHorizon(h)?.p50;
        if (p50 && p50 > 0) api.margin.gap({ terminal: name, quantity: p50 }).then((g) => alive && setGap(g)).catch(() => {});
      }).catch(() => {});
    api.demand.cockpit({ terminal: name }).then((ck) => alive && setCockpit(ck)).catch(() => {});
    return () => { alive = false; };
  }, [name]);

  if (error) return <div className="text-sm text-rose-600">Could not load terminal: {error}</div>;

  const hz = hedge ? primaryHorizon(hedge) : null;
  const pos = positionSignal(cockpit, invConnected);
  const committedShare = row && (row.committed_gallons + row.spot_gallons) > 0
    ? row.committed_gallons / (row.committed_gallons + row.spot_gallons) : null;

  // prescriptive terminal verdict
  const action = pos.tight ? "WATCH" : "LEAVE";
  const spineTone: Tone = pos.tight ? "rose" : "indigo";
  const verdict = buildVerdict(name, hz, committedShare, pos, gap);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeader
        back={{ label: "All terminals", onClick: () => navigate("terminals") }}
        title={name}
        subtitle={row ? <span className="flex flex-wrap gap-x-4">
          <span>{row.customers} customers</span><span>·</span>
          <span>{row.lifts.toLocaleString()} lifts</span><span>·</span>
          <span>{gal(row.total_net_gallons)} all-time</span>
        </span> : "Loading…"}
        right={pos.tight ? <span className="rounded-full bg-rose-100 px-3 py-1 text-xs font-semibold text-rose-700">Tight — {Math.round(pos.daysOfCover!)} days cover</span> : undefined}
      />

      {/* THE SPINE */}
      {hz && (
        <div className={`rounded-2xl border-l-4 px-6 py-5 ${spineBar(spineTone)}`}>
          <div className="mb-2 flex items-center gap-2">
            <ActionChip action={action} />
            <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">the verdict</span>
          </div>
          <p className="text-[18px] font-medium leading-snug text-slate-800">{verdict}</p>
        </div>
      )}

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

        {/* POSITION (INTERIM — Phase 7) */}
        <FacetTile title="Position" defKey="days_of_cover" accent={pos.tight ? "rose" : "emerald"}
          available={pos.available} unavailableNote={<span>{pos.caveat}</span>}>
          <div>
            <FacetValue value={<>{pos.daysOfCover != null ? Math.round(pos.daysOfCover) : "—"}<span className="text-sm font-normal text-slate-400"> days</span></>}
              tone={pos.tight ? "rose" : "emerald"} caption={pos.headline ?? "Days of cover at the expected burn."} />
            <p className="mt-1.5 rounded bg-slate-50 px-2 py-1 text-[10px] text-slate-400">{pos.caveat}</p>
          </div>
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

      {/* DRILL-DOWN: position trend (burn-down) */}
      {invConnected && cockpit?.burndown && cockpit.burndown.series?.length > 0 && (
        <Card className="p-5">
          <h3 className="mb-2 text-sm font-medium text-slate-700">Position trend — inventory burn-down</h3>
          <BurnDownChart burndown={cockpit.burndown} />
          <p className="mt-2 text-[10px] text-slate-400">Interim: projected at the forecast P50/P10/P90 burn — not a live gauge (Phase 7).</p>
        </Card>
      )}

      {/* who drives the surprise */}
      {hedge && hedge.watch_list?.length > 0 && (
        <Card className="overflow-hidden">
          <div className="border-b border-slate-100 px-4 py-3">
            <h2 className="text-sm font-semibold text-slate-700">Who drives the risk here</h2>
            <p className="text-[11px] text-slate-400">Ranked by share of demand variability — who makes the buffer necessary.</p>
          </div>
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
                    <td className="px-3 py-2.5 text-right text-slate-700">{gal(w.typical_load)}</td>
                    <td className="px-3 py-2.5 text-right font-medium text-slate-700">{Math.round((w.variability_share ?? 0) * 100)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

function spineBar(tone: Tone): string {
  return ({
    indigo: "border-indigo-400 bg-indigo-50/50", rose: "border-rose-400 bg-rose-50/50",
    emerald: "border-emerald-400 bg-emerald-50/50", amber: "border-amber-400 bg-amber-50/50",
    slate: "border-slate-300 bg-slate-50", neutral: "border-slate-300 bg-slate-50",
  } as Record<string, string>)[tone];
}

function buildVerdict(name: string, hz: ReturnType<typeof primaryHorizon>, committedShare: number | null,
                      pos: ReturnType<typeof positionSignal>, gap: MarginGap | null): string {
  const parts: string[] = [];
  if (hz) parts.push(`expects ~${gal(hz.p50)} over the next ${hz.horizon_working_days} working days (range ${gal(hz.p10)}–${gal(hz.p90)})`);
  if (committedShare != null) parts.push(`~${Math.round(committedShare * 100)}% committed must-serve`);
  if (pos.available && pos.daysOfCover != null) {
    parts.push(pos.tight ? `only ${Math.round(pos.daysOfCover)} days of cover — bring in a barge soon` : `${Math.round(pos.daysOfCover)} days of cover, comfortable`);
  } else {
    parts.push("position needs inventory loaded (target staging only)");
  }
  let s = `${name} — ` + parts.join(", ") + ".";
  if (gap?.available && gap.committed_margin_dollars != null && pos.tight) {
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
