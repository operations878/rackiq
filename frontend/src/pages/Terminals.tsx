/**
 * Terminals — "Where am I tight?". A WATCH list ranked by days-of-cover risk (tightest first), each
 * row assembling the orientation numbers (demand exposure, committed vs spot, position) from the
 * existing endpoints. Position rides the INTERIM adapter (forecast burn, not a gauge — Phase 7).
 * Click a terminal → the unified terminal dossier.
 */
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ProfileTerminalsResponse, DemandCockpit } from "../api/types";
import { PageHeader, Card, gal } from "../lib/ui";
import { positionSignal } from "../lib/adapters";

interface TerminalView {
  terminal: string; total: number; lifts: number; customers: number;
  committed: number; spot: number; winnable: number; atRisk: number; hasDeals: boolean;
  days: number | null; tight: boolean; headline: string | null;
}

export default function Terminals({ navigate }: { navigate: (to: string) => void }) {
  const [data, setData] = useState<ProfileTerminalsResponse | null>(null);
  const [views, setViews] = useState<TerminalView[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api.profile.terminals().then(async (d) => {
      if (!alive) return;
      setData(d);
      // pull per-terminal days-of-cover (only a handful of terminals) for the watch ranking
      const cockpits = await Promise.all(d.terminals.map((t) =>
        api.demand.cockpit({ terminal: t.terminal }).catch(() => null as DemandCockpit | null)));
      if (!alive) return;
      const vs: TerminalView[] = d.terminals.map((t, i) => {
        const pos = positionSignal(cockpits[i], d.inventory_connected);
        return {
          terminal: t.terminal, total: t.total_net_gallons, lifts: t.lifts, customers: t.customers,
          committed: t.committed_gallons, spot: t.spot_gallons,
          winnable: t.winnable_gal_per_yr ?? 0, atRisk: t.at_risk_gal_per_yr ?? 0, hasDeals: t.has_deals,
          days: pos.daysOfCover, tight: pos.tight, headline: pos.headline,
        };
      });
      // rank: tightest cover first when known; else by committed must-serve exposure
      vs.sort((a, b) => {
        if (a.days != null && b.days != null) return a.days - b.days;
        if (a.days != null) return -1;
        if (b.days != null) return 1;
        return b.committed - a.committed;
      });
      setViews(vs);
    }).catch((e) => alive && setError(String(e)));
    return () => { alive = false; };
  }, []);

  if (error) return <div className="text-sm text-rose-600">Could not load: {error}</div>;
  if (!data) return <div className="text-sm text-slate-400">Loading terminals…</div>;
  if (!data.available) return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="Terminals" subtitle="Where are you tight?" />
      <Card className="p-8 text-center text-sm text-slate-500">No lift book loaded yet — connect your BOLs to begin.</Card>
    </div>
  );

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <PageHeader
        title="Terminals — where am I tight?"
        subtitle="Ranked by days-of-cover risk. Each terminal's demand, committed-vs-spot and position — open one to see demand vs supply and the barge-nomination cure."
      />
      {!data.inventory_connected && (
        <div className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">
          Inventory isn't connected, so days-of-cover isn't available — terminals are ordered by committed
          must-serve exposure instead, and the detail shows target staging. A true gauge read lands in Phase 7.
        </div>
      )}
      <div className="space-y-3">
        {(views ?? []).map((t) => (
          <Card key={t.terminal} hover onClick={() => navigate(`terminal/${encodeURIComponent(t.terminal)}`)}
            className={`p-4 ${t.tight ? "border-rose-200" : ""}`}>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <div>
                  <div className="text-base font-semibold text-slate-800">{t.terminal}</div>
                  <div className="text-[11px] text-slate-400">{t.customers} customers · {t.lifts.toLocaleString()} lifts · {gal(t.total)}</div>
                </div>
                {t.days != null && (
                  <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${t.tight ? "bg-rose-100 text-rose-700" : "bg-emerald-100 text-emerald-800"}`}>
                    {Math.round(t.days)} days cover
                  </span>
                )}
              </div>
              <span className="text-slate-300">→</span>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
              <Mini label="Committed / spot" value={t.hasDeals ? <>{gal(t.committed)} <span className="text-slate-400">/ {gal(t.spot)}</span></> : <span className="text-slate-300">needs deals</span>} />
              <Mini label="Winnable here" value={t.winnable > 0 ? <span className="text-emerald-700">{gal(t.winnable)}/yr</span> : "—"} />
              <Mini label="At risk here" value={t.atRisk > 0 ? <span className="text-rose-700">{gal(t.atRisk)}/yr</span> : "—"} />
              <Mini label="Position" value={t.days != null ? `${Math.round(t.days)} days` : <span className="text-slate-300">needs inventory</span>} />
            </div>
          </Card>
        ))}
        {!views && <div className="text-xs text-slate-400">Reading position…</div>}
      </div>
    </div>
  );
}

function Mini({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-slate-400">{label}</div>
      <div className="font-medium text-slate-700">{value}</div>
    </div>
  );
}
