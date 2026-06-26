/**
 * Terminals — "Where am I tight?". A WATCH list ranked by days-of-cover risk (tightest first), each
 * row assembling the orientation numbers (demand exposure, committed vs spot, position) from the
 * existing endpoints. Position reads the REAL Phase-7 engine (gauge-anchored vs net-flow proxy,
 * working-day cover, barge cure). Click a terminal → the unified terminal dossier.
 */
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ProfileTerminalsResponse, PositionResponse, PositionCell } from "../api/types";
import { PageHeader, Card, ProvenanceTag, gal, num } from "../lib/ui";
import { fmtDate } from "../lib/format";

interface TerminalView {
  terminal: string; total: number; lifts: number; customers: number;
  committed: number; spot: number; winnable: number; atRisk: number; hasDeals: boolean;
  cover: number | null; status: PositionCell["status"] | null; mode: "gauge" | "proxy" | null;
  tightProduct: string | null; cell: PositionCell | null;
}

const STATUS_RANK: Record<string, number> = { short: 0, watch: 1, ok: 2, unknown: 3 };

export default function Terminals({ navigate }: { navigate: (to: string) => void }) {
  const [data, setData] = useState<ProfileTerminalsResponse | null>(null);
  const [position, setPosition] = useState<PositionResponse | null>(null);
  const [views, setViews] = useState<TerminalView[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([
      api.profile.terminals(),
      api.position.get().catch(() => null as PositionResponse | null),
    ]).then(([d, pos]) => {
      if (!alive) return;
      setData(d); setPosition(pos);
      // group the real position cells by terminal → the tightest cover drives the ranking
      const byTerm = new Map<string, PositionCell[]>();
      for (const c of pos?.positions ?? []) {
        if (!byTerm.has(c.terminal)) byTerm.set(c.terminal, []);
        byTerm.get(c.terminal)!.push(c);
      }
      const vs: TerminalView[] = d.terminals.map((t) => {
        const cells = byTerm.get(t.terminal) ?? [];
        const withCover = cells.filter((c) => c.days_of_cover != null)
          .sort((a, b) => (a.days_of_cover ?? Infinity) - (b.days_of_cover ?? Infinity));
        const tight = withCover[0] ?? null;
        return {
          terminal: t.terminal, total: t.total_net_gallons, lifts: t.lifts, customers: t.customers,
          committed: t.committed_gallons, spot: t.spot_gallons,
          winnable: t.winnable_gal_per_yr ?? 0, atRisk: t.at_risk_gal_per_yr ?? 0, hasDeals: t.has_deals,
          cover: tight?.days_of_cover ?? null, status: tight?.status ?? null,
          mode: tight?.mode ?? null, tightProduct: tight?.product ?? null, cell: tight,
        };
      });
      // rank: tightest cover first when known; else by committed must-serve exposure
      vs.sort((a, b) => {
        const sa = a.status ? STATUS_RANK[a.status] : 9, sb = b.status ? STATUS_RANK[b.status] : 9;
        if (a.cover != null && b.cover != null) return a.cover - b.cover;
        if (a.cover != null) return -1;
        if (b.cover != null) return 1;
        if (sa !== sb) return sa - sb;
        return b.committed - a.committed;
      });
      setViews(vs);
    }).catch((e) => alive && setError(String(e)));
    return () => { alive = false; };
  }, []);

  if (error) return <div className="text-sm text-rose-600">Could not load: {error}</div>;
  if (!data) return <LoadingList />;
  if (!data.available) return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="Terminals" subtitle="Where are you tight?" />
      <Card className="p-8 text-center text-sm text-slate-500">No lift book loaded yet — connect your BOLs to begin.</Card>
    </div>
  );

  const posConnected = !!position?.availability?.available;
  const anyShort = (views ?? []).filter((v) => v.status === "short").length;

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <PageHeader
        title="Terminals — where am I tight?"
        subtitle="Ranked by days-of-cover risk. Each terminal's demand, committed-vs-spot and position — open one to see demand vs supply and the barge-nomination cure."
        right={posConnected && anyShort > 0 ? (
          <span className="rounded-full bg-rose-100 px-3 py-1 text-xs font-semibold text-rose-700">{anyShort} short</span>
        ) : undefined}
      />
      {!posConnected && (
        <div className="rounded-lg border-l-2 border-amber-300 bg-amber-50/60 px-3 py-2 text-xs text-amber-700">
          No inbound supply connected, so days-of-cover isn't available — terminals are ordered by committed
          must-serve exposure instead. Load the <b>Trips report</b> (or receipts) for a true cover read.
        </div>
      )}
      <div className="space-y-3">
        {(views ?? []).map((t) => (
          <Card key={t.terminal} hover onClick={() => navigate(`terminal/${encodeURIComponent(t.terminal)}`)}
            className={`p-4 ${t.status === "short" ? "border-rose-200" : t.status === "watch" ? "border-amber-200" : ""}`}>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <div>
                  <div className="text-base font-semibold text-slate-800">{t.terminal}</div>
                  <div className="tnum text-[11px] text-slate-400">{t.customers} customers · {t.lifts.toLocaleString()} lifts · {gal(t.total)}</div>
                </div>
                {t.cover != null && (
                  <span className={`tnum rounded-full px-2.5 py-1 text-xs font-semibold ${
                    t.status === "short" ? "bg-rose-100 text-rose-700"
                      : t.status === "watch" ? "bg-amber-100 text-amber-800" : "bg-emerald-100 text-emerald-800"}`}>
                    {fmtCover(t.cover)} wkg days{t.tightProduct ? ` · ${t.tightProduct}` : ""}
                  </span>
                )}
                {t.mode && <ProvenanceTag kind={t.mode === "gauge" ? "verified" : "proxy"} small />}
              </div>
              <span className="text-slate-300">→</span>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
              <Mini label="Committed / spot" value={t.hasDeals ? <>{gal(t.committed)} <span className="text-slate-400">/ {gal(t.spot)}</span></> : <span className="text-slate-300">needs deals</span>} />
              <Mini label="Winnable (modeled)" value={t.winnable > 0 ? <span className="text-emerald-700">{gal(t.winnable)}/yr</span> : "—"} />
              <Mini label="At risk here" value={t.atRisk > 0 ? <span className="text-rose-700">{gal(t.atRisk)}/yr</span> : "—"} />
              <Mini label="Cover" value={t.cover != null
                ? <span className={t.status === "short" ? "text-rose-700" : t.status === "watch" ? "text-amber-700" : "text-slate-700"}>{fmtCover(t.cover)} wkg days</span>
                : <span className="text-slate-300">needs supply</span>} />
            </div>
            {t.cell?.cure?.short && t.cell.cure.implied_barge_bbl ? (
              <div className="mt-2 rounded-lg bg-rose-50 px-2.5 py-1.5 text-[11px] text-rose-700">
                Cure: nominate <b className="tnum">~{num(t.cell.cure.implied_barge_bbl)} bbl</b>
                {t.cell.cure.nominate_by ? <> by {fmtDate(t.cell.cure.nominate_by)}</> : ""} to restore cover.
              </div>
            ) : null}
          </Card>
        ))}
        {!views && <div className="text-xs text-slate-400">Reading position…</div>}
      </div>
    </div>
  );
}

function fmtCover(d: number | null | undefined): string {
  if (d == null) return "—";
  return d >= 10 ? String(Math.round(d)) : d.toFixed(1);
}
function LoadingList() {
  return (
    <div className="mx-auto max-w-4xl space-y-3">
      <div className="h-8 w-72 animate-pulse rounded bg-slate-200" />
      {[0, 1, 2].map((i) => <div key={i} className="h-28 animate-pulse rounded-xl bg-slate-100" />)}
    </div>
  );
}
function Mini({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-slate-400">{label}</div>
      <div className="tnum font-medium text-slate-700">{value}</div>
    </div>
  );
}
