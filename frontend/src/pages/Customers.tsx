/**
 * Customers — "Who can I plan around?". ONE list that answers many questions: every customer with
 * every facet (steadiness, confidence, margin, channel, winnable volume) joined from the fan-out,
 * sortable and filterable by any of them. Click a row → the unified customer view.
 */
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ProfileCustomerListRow, ProfileCustomersResponse } from "../api/types";
import {
  PageHeader, Card, ConfidencePill, ChannelChip, MismatchFlag, QuadrantChip, ActionChip,
  cents, gal, money,
} from "../lib/ui";

type SortKey = "name" | "cadence" | "size" | "margin" | "winnable" | "volume" | "confidence" | "lifts";
type QuickFilter = "all" | "mismatch" | "metronome" | "lowconf" | "weather" | "winnable";

const CONF_ORDER: Record<string, number> = { High: 3, Medium: 2, Low: 1 };

export default function Customers({ navigate, initialFilter }: {
  navigate: (to: string) => void; initialFilter?: QuickFilter;
}) {
  const [data, setData] = useState<ProfileCustomersResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [terminal, setTerminal] = useState<string>("");
  const [quick, setQuick] = useState<QuickFilter>(initialFilter ?? "all");
  const [sort, setSort] = useState<SortKey>("volume");
  const [desc, setDesc] = useState(true);

  useEffect(() => {
    api.profile.customers().then(setData).catch((e) => setError(String(e)));
  }, []);

  const terminals = useMemo(
    () => Array.from(new Set((data?.customers ?? []).map((c) => c.primary_terminal).filter(Boolean))) as string[],
    [data],
  );

  const rows = useMemo(() => {
    let cs = (data?.customers ?? []).slice();
    if (q.trim()) {
      const needle = q.toLowerCase();
      cs = cs.filter((c) => c.name?.toLowerCase().includes(needle));
    }
    if (terminal) cs = cs.filter((c) => c.primary_terminal === terminal);
    if (quick === "mismatch") cs = cs.filter((c) => c.mismatch);
    else if (quick === "metronome") cs = cs.filter((c) => c.quadrant === "metronome");
    else if (quick === "lowconf") cs = cs.filter((c) => c.confidence_tier === "Low");
    else if (quick === "weather") cs = cs.filter((c) => c.weather_sensitive);
    else if (quick === "winnable") cs = cs.filter((c) => (c.winnable_gal_per_yr || 0) > 0);

    const val = (c: ProfileCustomerListRow): number | string => {
      switch (sort) {
        case "name": return c.name ?? "";
        case "cadence": return c.cadence_consistency ?? -1;
        case "size": return c.size_consistency ?? -1;
        case "margin": return c.margin_cents_gal ?? -1;
        case "winnable": return c.winnable_gal_per_yr ?? -1;
        case "confidence": return CONF_ORDER[c.confidence_tier ?? ""] ?? 0;
        case "lifts": return c.n_lifts ?? -1;
        default: return c.total_net_gallons ?? -1;
      }
    };
    cs.sort((a, b) => {
      const va = val(a), vb = val(b);
      const cmp = typeof va === "string" ? String(va).localeCompare(String(vb)) : (va as number) - (vb as number);
      return desc ? -cmp : cmp;
    });
    return cs;
  }, [data, q, terminal, quick, sort, desc]);

  if (error) return <div className="text-sm text-rose-600">Could not load: {error}</div>;
  if (!data) return <div className="text-sm text-slate-400">Loading customers…</div>;

  const marginOn = data.margin_available;
  const dealsOn = data.deals_available;

  function SortTh({ k, label, className = "" }: { k: SortKey; label: string; className?: string }) {
    const active = sort === k;
    return (
      <th className={`px-3 py-2 text-left font-medium ${className}`}>
        <button
          onClick={() => { if (active) setDesc((d) => !d); else { setSort(k); setDesc(true); } }}
          className={`inline-flex items-center gap-1 ${active ? "text-slate-800" : "text-slate-400 hover:text-slate-600"}`}>
          {label}{active && <span className="text-[9px]">{desc ? "▼" : "▲"}</span>}
        </button>
      </th>
    );
  }

  const QUICKS: { k: QuickFilter; label: string }[] = [
    { k: "all", label: "All" },
    { k: "metronome", label: "Plannable" },
    { k: "mismatch", label: "Wrong channel" },
    { k: "winnable", label: "Winnable" },
    { k: "lowconf", label: "Low confidence" },
    { k: "weather", label: "Weather-driven" },
  ];

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Customers"
        subtitle="Who can you plan around? One row per account, every facet joined. Sort or filter by any of them; click a customer to see everything in one place."
      />

      {/* filters */}
      <Card className="mb-4 flex flex-wrap items-center gap-2 p-3">
        <input
          value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search a customer…"
          className="w-48 rounded-lg border border-slate-300 px-3 py-1.5 text-sm outline-none focus:border-indigo-400"
        />
        <select value={terminal} onChange={(e) => setTerminal(e.target.value)}
          className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm text-slate-600 outline-none focus:border-indigo-400">
          <option value="">All terminals</option>
          {terminals.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <div className="ml-1 flex flex-wrap gap-1">
          {QUICKS.map(({ k, label }) => (
            <button key={k} onClick={() => setQuick(k)}
              className={`rounded-full px-2.5 py-1 text-xs font-medium ${
                quick === k ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>
              {label}
            </button>
          ))}
        </div>
        <span className="ml-auto text-xs text-slate-400">{rows.length} shown</span>
      </Card>

      {(!marginOn || !dealsOn) && (
        <div className="mb-3 text-xs text-slate-400">
          {!dealsOn && <>Channel & winnable columns need the <b>deal book</b>. </>}
          {!marginOn && <>Margin column needs the <b>price &amp; cost grid</b>.</>}
        </div>
      )}

      {/* table */}
      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 text-xs">
                <SortTh k="name" label="Customer" />
                <th className="px-3 py-2 text-left font-medium text-slate-400">Steadiness</th>
                <SortTh k="confidence" label="Confidence" />
                <SortTh k="margin" label="Margin" />
                <th className="px-3 py-2 text-left font-medium text-slate-400">Channel</th>
                <SortTh k="winnable" label="Winnable" />
                <SortTh k="volume" label="Volume" className="text-right" />
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.customer_id}
                  onClick={() => navigate(`customer/${c.customer_id}`)}
                  className="cursor-pointer border-b border-slate-100 last:border-0 hover:bg-indigo-50/40">
                  {/* customer */}
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-slate-800">{c.name}</span>
                      <ActionChip action={c.action} small />
                    </div>
                    <div className="text-[11px] text-slate-400">
                      {c.primary_terminal ?? "—"}{c.top_product ? ` · ${c.top_product}` : ""} · {c.n_lifts.toLocaleString()} lifts
                    </div>
                  </td>
                  {/* steadiness */}
                  <td className="px-3 py-2.5">
                    {c.quadrant === "insufficient" ? (
                      <span className="text-xs text-slate-400">Too new to read</span>
                    ) : (
                      <div className="flex flex-col items-start gap-1">
                        <QuadrantChip quadrant={c.quadrant} label={c.quadrant_label} small />
                        {c.behavior_label && <span className="text-[11px] text-slate-400">{c.behavior_label}</span>}
                      </div>
                    )}
                  </td>
                  {/* confidence */}
                  <td className="px-3 py-2.5"><ConfidencePill tier={c.confidence_tier} small /></td>
                  {/* margin */}
                  <td className="px-3 py-2.5">
                    {marginOn && c.margin_cents_gal != null ? (
                      <div>
                        <div className="font-medium text-slate-800">{cents(c.margin_cents_gal)}</div>
                        {c.margin_dollars != null && (
                          <div className="text-[11px] text-slate-400">
                            {money(c.margin_dollars)}{c.rank_by_margin ? ` · #${c.rank_by_margin}` : ""}
                          </div>
                        )}
                      </div>
                    ) : <span className="text-xs text-slate-300">—</span>}
                  </td>
                  {/* channel */}
                  <td className="px-3 py-2.5">
                    {dealsOn ? (
                      <div className="flex flex-col items-start gap-1">
                        <ChannelChip rec={c.recommended_channel} label={c.channel_label} small />
                        {c.mismatch && <MismatchFlag direction={c.mismatch_direction} strength={c.mismatch_strength} small />}
                      </div>
                    ) : (
                      <ChannelChip rec={c.recommended_channel} label={c.channel_label} small />
                    )}
                  </td>
                  {/* winnable */}
                  <td className="px-3 py-2.5">
                    {(c.winnable_gal_per_yr || 0) > 0 ? (
                      <div>
                        <span className="font-medium text-emerald-700">{gal(c.winnable_gal_per_yr)}<span className="text-[10px] text-slate-400">/yr</span></span>
                        {c.winnable_dollars_per_yr ? <div className="text-[11px] text-slate-400">≈ {money(c.winnable_dollars_per_yr)}/yr</div> : null}
                      </div>
                    ) : <span className="text-xs text-slate-300">—</span>}
                  </td>
                  {/* volume */}
                  <td className="px-3 py-2.5 text-right">
                    <span className="font-medium text-slate-700">{gal(c.total_net_gallons)}</span>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr><td colSpan={7} className="px-3 py-10 text-center text-sm text-slate-400">
                  No customers match these filters.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
