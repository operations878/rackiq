/**
 * Opportunity — the morning worklist. "Who do I call today?" Two ranked splits read off the existing
 * channel mismatch: WIN (steady accounts on spot — pull them onto rack/term, ranked by winnable $)
 * and RISK (erratic accounts on firm commitments — move them to spot). Each row carries the
 * prescriptive action, the confidence flag, and the dollar value; click → the customer dossier.
 * INTERIM source (channel-mismatch volume, not modeled demand) — labelled; Phase 6 swaps the data.
 */
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ProfileCustomerListRow, ProfileCustomersResponse } from "../api/types";
import { PageHeader, Card, QuadrantChip, ConfidencePill, ActionChip, gal, money } from "../lib/ui";

function Row({ c, navigate, side }: {
  c: ProfileCustomerListRow; navigate: (to: string) => void; side: "win" | "risk";
}) {
  const gallons = side === "win" ? c.winnable_gal_per_yr : c.total_net_gallons;
  const dollars = side === "win" ? c.winnable_dollars_per_yr : null;
  return (
    <tr onClick={() => navigate(`customer/${c.customer_id}`)}
      className="cursor-pointer border-b border-slate-100 last:border-0 hover:bg-indigo-50/40">
      <td className="px-3 py-2.5">
        <div className="flex items-center gap-2">
          <span className="font-medium text-slate-800">{c.name}</span>
          <ActionChip action={c.action} small />
        </div>
        <div className="text-[11px] text-slate-400">{c.primary_terminal ?? "—"} · {c.n_lifts.toLocaleString()} lifts</div>
      </td>
      <td className="px-3 py-2.5"><QuadrantChip quadrant={c.quadrant} label={c.quadrant_label} small /></td>
      <td className="px-3 py-2.5">
        <ConfidencePill tier={c.confidence_tier} small />
        {c.confidence_provisional && <div className="mt-0.5 text-[10px] text-rose-500">provisional</div>}
      </td>
      <td className="px-3 py-2.5 text-xs text-slate-500">
        {c.current_channel_label} → <span className={`font-semibold ${side === "win" ? "text-emerald-700" : "text-rose-700"}`}>{side === "win" ? "rack/term" : "spot"}</span>
      </td>
      <td className="px-3 py-2.5 text-right">
        <div className={`font-semibold ${side === "win" ? "text-emerald-700" : "text-rose-700"}`}>{gal(gallons)}<span className="text-[10px] text-slate-400">/yr</span></div>
        {dollars ? <div className="text-[11px] text-slate-400">≈ {money(dollars)}/yr</div> : null}
      </td>
    </tr>
  );
}

function Split({ title, blurb, rows, navigate, side }: {
  title: string; blurb: string; rows: ProfileCustomerListRow[];
  navigate: (to: string) => void; side: "win" | "risk";
}) {
  return (
    <Card className="overflow-hidden">
      <div className="border-b border-slate-100 px-4 py-3">
        <h2 className="text-sm font-semibold text-slate-700">{title}</h2>
        <p className="text-[11px] text-slate-400">{blurb}</p>
      </div>
      {rows.length ? (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 text-xs text-slate-400">
                <th className="px-3 py-2 text-left font-medium">Customer</th>
                <th className="px-3 py-2 text-left font-medium">Steadiness</th>
                <th className="px-3 py-2 text-left font-medium">Confidence</th>
                <th className="px-3 py-2 text-left font-medium">Move</th>
                <th className="px-3 py-2 text-right font-medium">{side === "win" ? "Winnable" : "At risk"}</th>
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

  const { win, risk, totalGal, totalDol } = useMemo(() => {
    const cs = data?.customers ?? [];
    const win = cs.filter((c) => c.opportunity_kind === "win")
      .sort((a, b) => (b.winnable_dollars_per_yr || b.winnable_gal_per_yr || 0) - (a.winnable_dollars_per_yr || a.winnable_gal_per_yr || 0));
    const risk = cs.filter((c) => c.opportunity_kind === "risk")
      .sort((a, b) => (b.total_net_gallons || 0) - (a.total_net_gallons || 0));
    const totalGal = win.reduce((s, c) => s + (c.winnable_gal_per_yr || 0), 0);
    const totalDol = win.reduce((s, c) => s + (c.winnable_dollars_per_yr || 0), 0);
    return { win, risk, totalGal, totalDol };
  }, [data]);

  if (error) return <div className="text-sm text-rose-600">Could not load: {error}</div>;
  if (!data) return <div className="text-sm text-slate-400">Loading…</div>;

  if (!data.deals_available) {
    return (
      <div className="mx-auto max-w-3xl">
        <PageHeader title="Opportunity" subtitle="Who should you sell more to?" />
        <Card className="p-8 text-center">
          <p className="text-sm text-slate-500">
            This worklist compares each customer's <b>recommended</b> channel to the one they're actually on —
            which needs the <b>deal book</b> loaded.
          </p>
          <button onClick={() => navigate("data")}
            className="mt-4 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
            Connect the deal book
          </button>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <PageHeader
        title="Opportunity — who to call today"
        subtitle="The channel-mismatch worklist. Win steady accounts off spot onto rack/term; de-risk erratic accounts off firm commitments. Margin is context only — it never sets the channel."
        right={totalGal > 0 ? (
          <div className="text-right">
            <div className="text-2xl font-semibold text-emerald-700">{gal(totalGal)}</div>
            <div className="text-[11px] text-slate-400">{totalDol ? `≈ ${money(totalDol)}/yr · ` : ""}winnable</div>
          </div>
        ) : undefined}
      />
      <Split side="win"
        title={`Win it back — steady accounts on spot (${win.length})`}
        blurb="Plannable buyers being priced opportunistically. Lock the volume on a rack/term deal."
        rows={win} navigate={navigate} />
      <Split side="risk"
        title={`De-risk — over-committed erratic accounts (${risk.length})`}
        blurb="Unpredictable buyers carrying firm commitments. Move them to spot to cut volume risk."
        rows={risk} navigate={navigate} />
      <p className="text-[11px] text-slate-400">
        Interim: ranked on channel-mismatch volume, not a demand model. Dollar figures are gallons × the
        existing margin ¢/gal (ranking-only). Phase 6 swaps in modeled missing-volume.
      </p>
    </div>
  );
}
