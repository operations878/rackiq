/**
 * Opportunity — the morning worklist. "Who do I call today?" Built on the REAL Phase-6 modeled
 * missing-volume engine (peak ≈ wallet, MODELED):
 *   • WIN — under-served accounts with modeled winnable upside, ranked by winnable $ then gallons.
 *   • DE-RISK — over-committed erratic accounts on a firm commitment (the channel signal; needs deals).
 *   • LOOKS SHRUNK — a gap on paper but buying less year-over-year; shown honestly, NOT as a tempting
 *     number to chase.
 * Each row carries the prescriptive action, confidence (flagged, never suppressed), the spot/rack tag,
 * and the dollar value; click → the customer dossier.
 */
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ProfileCustomerListRow, ProfileCustomersResponse } from "../api/types";
import {
  PageHeader, Card, QuadrantChip, ConfidencePill, ActionChip, ProvenanceTag, gal, money, type Tone,
} from "../lib/ui";

type Side = "win" | "risk" | "shrunk";

function WinnabilityBar({ v }: { v: number | null | undefined }) {
  if (v == null) return <span className="text-slate-300">—</span>;
  const tone = v >= 70 ? "bg-emerald-500" : v >= 45 ? "bg-amber-500" : "bg-rose-400";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-12 overflow-hidden rounded-full bg-slate-200">
        <div className={`h-1.5 rounded-full ${tone}`} style={{ width: `${Math.max(4, Math.min(100, v))}%` }} />
      </div>
      <span className="tnum text-[11px] text-slate-500">{Math.round(v)}</span>
    </div>
  );
}

function Row({ c, navigate, side }: { c: ProfileCustomerListRow; navigate: (to: string) => void; side: Side }) {
  return (
    <tr onClick={() => navigate(`customer/${c.customer_id}`)}
      className="cursor-pointer border-b border-slate-100 last:border-0 hover:bg-indigo-50/40">
      <td className="px-3 py-2.5">
        <div className="flex items-center gap-2">
          <span className="font-medium text-slate-800">{c.name}</span>
          <ActionChip action={c.action} small />
        </div>
        <div className="tnum text-[11px] text-slate-400">{c.primary_terminal ?? "—"} · {c.n_lifts.toLocaleString()} lifts</div>
      </td>
      <td className="px-3 py-2.5"><QuadrantChip quadrant={c.quadrant} label={c.quadrant_label} small /></td>
      <td className="px-3 py-2.5">
        <ConfidencePill tier={c.confidence_tier} small />
        {c.confidence_provisional && <div className="mt-0.5 text-[10px] text-rose-500">provisional</div>}
      </td>
      {side === "win" ? (
        <>
          <td className="px-3 py-2.5"><WinnabilityBar v={c.winnability} /></td>
          <td className="px-3 py-2.5 text-xs text-slate-500">
            chase via <span className="font-semibold text-emerald-700">{c.chase_channel ?? "rack/term"}</span>
          </td>
          <td className="px-3 py-2.5 text-right">
            <div className="tnum font-semibold text-emerald-700">{gal(c.winnable_gal_per_yr)}<span className="text-[10px] text-slate-400">/yr</span></div>
            {c.winnable_dollars_per_yr ? <div className="tnum text-[11px] text-slate-400">≈ {money(c.winnable_dollars_per_yr)}/yr</div> : null}
          </td>
        </>
      ) : side === "risk" ? (
        <>
          <td className="px-3 py-2.5 text-xs text-slate-500">{c.behavior_label ?? "—"}</td>
          <td className="px-3 py-2.5 text-xs text-slate-500">
            {c.current_channel_label} → <span className="font-semibold text-rose-700">spot</span>
          </td>
          <td className="px-3 py-2.5 text-right">
            <div className="tnum font-semibold text-rose-700">{gal(c.total_net_gallons)}</div>
            <div className="text-[11px] text-slate-400">on a firm commitment</div>
          </td>
        </>
      ) : (
        <>
          <td className="px-3 py-2.5"><WinnabilityBar v={c.winnability} /></td>
          <td className="px-3 py-2.5 text-xs text-slate-400">buying less YoY · stale peak</td>
          <td className="px-3 py-2.5 text-right">
            <div className="tnum font-medium text-slate-500">{gal(c.gap_gal_per_yr)}<span className="text-[10px] text-slate-400">/yr gap</span></div>
            <div className="text-[11px] text-slate-400">on paper, not winnable</div>
          </td>
        </>
      )}
    </tr>
  );
}

function Split({ title, blurb, rows, navigate, side, tone, headers }: {
  title: string; blurb: string; rows: ProfileCustomerListRow[]; navigate: (to: string) => void;
  side: Side; tone: Tone; headers: [string, string, string];
}) {
  const barTone: Record<string, string> = {
    emerald: "bg-emerald-400", rose: "bg-rose-400", amber: "bg-amber-400",
    slate: "bg-slate-300", indigo: "bg-indigo-400", neutral: "bg-slate-300",
  };
  return (
    <Card className="overflow-hidden">
      <div className="flex items-center gap-3 border-b border-slate-100 px-4 py-3">
        <span className={`h-8 w-1 rounded-full ${barTone[tone]}`} />
        <div>
          <h2 className="text-sm font-semibold text-slate-700">{title}</h2>
          <p className="text-[11px] text-slate-400">{blurb}</p>
        </div>
      </div>
      {rows.length ? (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 text-xs text-slate-400">
                <th className="px-3 py-2 text-left font-medium">Customer</th>
                <th className="px-3 py-2 text-left font-medium">Steadiness</th>
                <th className="px-3 py-2 text-left font-medium">Confidence</th>
                <th className="px-3 py-2 text-left font-medium">{headers[0]}</th>
                <th className="px-3 py-2 text-left font-medium">{headers[1]}</th>
                <th className="px-3 py-2 text-right font-medium">{headers[2]}</th>
              </tr>
            </thead>
            <tbody>{rows.map((c) => <Row key={c.customer_id} c={c} navigate={navigate} side={side} />)}</tbody>
          </table>
        </div>
      ) : (
        <div className="px-4 py-8 text-center text-sm text-slate-400">Nothing here right now.</div>
      )}
    </Card>
  );
}

export default function Opportunity({ navigate }: { navigate: (to: string) => void }) {
  const [data, setData] = useState<ProfileCustomersResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.profile.customers().then(setData).catch((e) => setError(String(e))); }, []);

  const { win, risk, shrunk, totalGal, totalDol } = useMemo(() => {
    const cs = data?.customers ?? [];
    const win = cs.filter((c) => c.opportunity_kind === "win" && (c.winnable_gal_per_yr || 0) > 0)
      .sort((a, b) => (b.winnable_dollars_per_yr || b.winnable_gal_per_yr || 0) - (a.winnable_dollars_per_yr || a.winnable_gal_per_yr || 0));
    const risk = cs.filter((c) => c.mismatch && c.mismatch_direction === "downgrade_to_spot")
      .sort((a, b) => (b.total_net_gallons || 0) - (a.total_net_gallons || 0));
    const shrunk = cs.filter((c) => c.opportunity_kind === "shrunk")
      .sort((a, b) => (b.gap_gal_per_yr || 0) - (a.gap_gal_per_yr || 0));
    const totalGal = win.reduce((s, c) => s + (c.winnable_gal_per_yr || 0), 0);
    const totalDol = win.reduce((s, c) => s + (c.winnable_dollars_per_yr || 0), 0);
    return { win, risk, shrunk, totalGal, totalDol };
  }, [data]);

  if (error) return <div className="text-sm text-rose-600">Could not load: {error}</div>;
  if (!data) return <LoadingWorklist />;

  const oppOn = data.opportunity_available ?? win.length > 0;
  const dealsOn = data.deals_available;

  if (!oppOn && !dealsOn) {
    return (
      <div className="mx-auto max-w-3xl">
        <PageHeader title="Opportunity" subtitle="Who should you sell more to?" />
        <Card className="p-8 text-center">
          <p className="text-sm text-slate-500">
            This worklist models each customer's <b>missing volume</b> (peak ≈ wallet) from their lift
            history — it needs more book loaded before it can read a demand pattern.
          </p>
          <button onClick={() => navigate("data")}
            className="mt-4 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
            Connect more data
          </button>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <PageHeader
        title="Opportunity — who to call today"
        subtitle="Modeled missing volume (peak ≈ wallet): win under-served accounts, de-risk over-committed ones. Margin is context only — it never sets the channel."
        right={totalGal > 0 ? (
          <div className="text-right">
            <div className="tnum text-2xl font-semibold text-emerald-700">{gal(totalGal)}</div>
            <div className="text-[11px] text-slate-400">{totalDol ? `≈ ${money(totalDol)}/yr · ` : ""}winnable</div>
            <div className="mt-1 flex justify-end"><ProvenanceTag kind="modeled" small /></div>
          </div>
        ) : undefined}
      />
      <Split side="win" tone="emerald"
        title={`Win it back — under-served accounts (${win.length})`}
        blurb="Steady buyers consistently below their own weather-adjusted peak. Real room to win more volume."
        headers={["Winnability", "Chase via", "Winnable / yr"]}
        rows={win} navigate={navigate} />
      {dealsOn && (
        <Split side="risk" tone="rose"
          title={`De-risk — over-committed erratic accounts (${risk.length})`}
          blurb="Unpredictable buyers carrying firm commitments. Move them to spot to cut volume risk."
          headers={["Pattern", "Move", "Volume"]}
          rows={risk} navigate={navigate} />
      )}
      {shrunk.length > 0 && (
        <Split side="shrunk" tone="slate"
          title={`Looks shrunk — gap on paper, not winnable (${shrunk.length})`}
          blurb="A gap exists on paper, but they're buying less year-over-year and their big days are old. Shown for honesty — don't chase the number."
          headers={["Winnability", "Why", "Gap / yr"]}
          rows={shrunk} navigate={navigate} />
      )}
      <p className="text-[11px] leading-snug text-slate-400">
        MODELED (peak ≈ wallet): true demand is estimated from each account's weather-normalized peak active
        days — an estimate of opportunity, not measured demand. Dollar figures are winnable gallons × the
        existing margin ¢/gal (ranking-only). Winnability splits under-served (winnable) from shrunk.
      </p>
    </div>
  );
}

function LoadingWorklist() {
  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div className="h-8 w-80 animate-pulse rounded bg-slate-200" />
      {[0, 1].map((i) => <div key={i} className="h-48 animate-pulse rounded-xl bg-slate-100" />)}
    </div>
  );
}
