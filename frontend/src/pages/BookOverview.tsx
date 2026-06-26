import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ScoresResponse, ScoreCustomer, CustomerScoreResponse, Summary } from "../api/types";
import Panel from "../components/Panel";
import BaseRangeChart from "../components/scores/BaseRangeChart";
import { ScorePill, ArchetypeTag, TrendArrow } from "../lib/scoreui";

const WINDOW_LABEL: Record<string, string> = { "30": "30d", "90": "90d", "365": "365d", all: "All-time" };

type SortKey =
  | "name" | "var" | "base_value" | "total_net_gallons" | "trend_pct"
  | "margin" | "account_value" | "recency_gap" | "churn";

function factNum(c: ScoreCustomer, key: string): number | null {
  const v = (c.facts as Record<string, unknown> | undefined)?.[key];
  return typeof v === "number" ? v : null;
}

function dominantProduct(c: ScoreCustomer): string | null {
  const mix = (c.facts as Record<string, unknown> | undefined)?.product_mix as Record<string, number> | undefined;
  if (!mix) return null;
  const entries = Object.entries(mix);
  if (!entries.length) return null;
  return entries.sort((a, b) => b[1] - a[1])[0][0];
}

function sortValue(c: ScoreCustomer, key: SortKey): number | string {
  switch (key) {
    case "name": return c.name.toLowerCase();
    case "var": return c.var.score ?? -1;
    case "base_value": return c.base_value.score ?? -1;
    case "total_net_gallons": return c.total_net_gallons;
    case "trend_pct": return c.trend_pct;
    case "margin": return factNum(c, "gross_margin_per_gal_mean") ?? -1;
    case "account_value": return c.account_value ?? -1;
    case "recency_gap": return c.recency_gap;
    case "churn": return c.subscores.churn_risk?.value ?? -1;
  }
}

function scoutingNote(c: ScoreCustomer): string {
  const a = c.archetype.primary;
  const varTxt = c.var.score != null ? `VAR ${c.var.score}/${c.var.grade}` : "thin history";
  const trend = c.trend_pct >= 5 ? `growing ${c.trend_pct >= 0 ? "+" : ""}${c.trend_pct}%`
    : c.trend_pct <= -5 ? `fading ${c.trend_pct}%` : "flat volume";
  const overdue = c.recency_gap > 1.5 ? `, ${c.recency_gap}× overdue` : c.recency_gap < 0.8 ? ", lifting early" : "";
  const churn = c.subscores.churn_risk?.value ?? 0;
  const risk = churn >= 50 ? " — churn risk elevated" : "";
  const posture = c.archetype.posture?.allocation ?? "";
  return `${a} (${varTxt}); ${trend}${overdue}${risk}. ${posture}`;
}

function DrillDown({ id, window }: { id: string; window: string }) {
  const [data, setData] = useState<CustomerScoreResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setData(null);
    setError(null);
    api.scores.customer(id, window).then(setData).catch((e) => setError(String(e)));
  }, [id, window]);

  if (error) return <div className="rounded bg-red-50 p-3 text-xs text-red-700">{error}</div>;
  if (!data) return <div className="text-sm text-slate-500">Loading customer…</div>;
  const c = data.customer;
  const v = c.var;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 className="text-base font-bold text-slate-900">{c.name}</h3>
          <p className="text-[11px] text-slate-500">
            {c.home_terminal} · {c.grain} buckets · {c.n_lifts} lifts
            {!c.data_sufficient && <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-amber-700">thin history</span>}
          </p>
        </div>
        <div className="flex items-center gap-5 text-sm">
          <div className="text-center"><div className="text-[10px] uppercase text-slate-400">VAR</div><ScorePill score={v.score} grade={v.grade} /></div>
          <div className="text-center"><div className="text-[10px] uppercase text-slate-400">Base value</div><ScorePill score={c.base_value.score} grade={c.base_value.grade} /></div>
          <div className="text-center"><div className="text-[10px] uppercase text-slate-400">Account value</div><span className="font-semibold text-slate-800">{c.account_value ?? "—"}</span></div>
        </div>
      </div>

      <BaseRangeChart series={c.lane_series ?? []} grain={c.grain} />

      <div className="grid grid-cols-2 gap-3 text-[11px] text-slate-600 sm:grid-cols-4">
        <div className="rounded-lg bg-slate-50 p-2"><div className="text-slate-400">In-band rate</div><b className="text-slate-800">{v.in_band_rate != null ? `${Math.round(v.in_band_rate * 100)}%` : "—"}</b></div>
        <div className="rounded-lg bg-slate-50 p-2"><div className="text-slate-400">Base volume / {c.grain.slice(0, -2)}</div><b className="text-slate-800">{Math.round(v.base_level).toLocaleString()} gal</b></div>
        <div className="rounded-lg bg-slate-50 p-2"><div className="text-slate-400">Base cadence</div><b className="text-slate-800">{v.base_cadence_days != null ? `${v.base_cadence_days}d` : "—"}</b></div>
        <div className="rounded-lg bg-slate-50 p-2"><div className="text-slate-400">Recency gap</div><b className={c.recency_gap > 1.5 ? "text-rose-600" : "text-slate-800"}>{c.recency_gap}×</b></div>
      </div>

      <div className="rounded-lg border border-indigo-100 bg-indigo-50/60 p-3 text-[12px] text-slate-700">
        <span className="font-semibold text-indigo-700">Scouting note: </span>{scoutingNote(c)}
      </div>
    </div>
  );
}

function Th({ label, sortKey, sort, dir, onSort, right }: {
  label: string; sortKey: SortKey; sort: SortKey; dir: 1 | -1; onSort: (k: SortKey) => void; right?: boolean;
}) {
  const active = sort === sortKey;
  return (
    <th className={`cursor-pointer pb-2 ${right ? "text-right" : ""}`} onClick={() => onSort(sortKey)}>
      <span className={active ? "text-slate-700" : ""}>{label}{active ? (dir === 1 ? " ▲" : " ▼") : ""}</span>
    </th>
  );
}

export default function BookOverview({ summary, navigate }: { summary: Summary; navigate: (to: string) => void }) {
  const [window, setWindow] = useState("all");
  const [data, setData] = useState<ScoresResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>("base_value");
  const [dir, setDir] = useState<1 | -1>(-1);
  const [fTerminal, setFTerminal] = useState("");
  const [fProduct, setFProduct] = useState("");
  const [fGrade, setFGrade] = useState("");
  const [fArchetype, setFArchetype] = useState("");

  const reload = useCallback(() => {
    setError(null);
    api.scores.list(window).then((d) => {
      setData(d);
      setSelected((cur) => cur ?? d.customers[0]?.customer_id ?? null);
    }).catch((e) => setError(String(e)));
  }, [window]);
  useEffect(reload, [reload]);

  const onSort = (k: SortKey) => {
    if (k === sort) setDir((d) => (d === 1 ? -1 : 1));
    else { setSort(k); setDir(k === "name" ? 1 : -1); }
  };

  const { terminals, products, archetypes } = useMemo(() => {
    const t = new Set<string>(), p = new Set<string>(), a = new Set<string>();
    for (const c of data?.customers ?? []) {
      if (c.home_terminal) t.add(c.home_terminal);
      const mix = (c.facts as Record<string, unknown> | undefined)?.product_mix as Record<string, number> | undefined;
      if (mix) Object.keys(mix).forEach((k) => p.add(k));
      a.add(c.archetype.primary);
    }
    return { terminals: [...t].sort(), products: [...p].sort(), archetypes: [...a].sort() };
  }, [data]);

  const rows = useMemo(() => {
    let rs = (data?.customers ?? []).filter((c) => {
      if (fTerminal && c.home_terminal !== fTerminal) return false;
      if (fProduct) {
        const mix = (c.facts as Record<string, unknown> | undefined)?.product_mix as Record<string, number> | undefined;
        if (!mix || !(fProduct in mix)) return false;
      }
      if (fGrade && c.var.grade !== fGrade) return false;
      if (fArchetype && c.archetype.primary !== fArchetype) return false;
      return true;
    });
    rs = [...rs].sort((x, y) => {
      const a = sortValue(x, sort), b = sortValue(y, sort);
      if (typeof a === "string" || typeof b === "string") return String(a).localeCompare(String(b)) * dir;
      return (a - b) * dir;
    });
    return rs;
  }, [data, fTerminal, fProduct, fGrade, fArchetype, sort, dir]);

  if (!summary.connected) {
    return <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-slate-500">Load a book in Data Studio to see the customer book.</div>;
  }
  if (error) return <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700">{error}</div>;
  if (!data) return <div className="text-sm text-slate-500">Loading book…</div>;

  const marginOn = data.availability.margin?.available;
  const creditOn = data.availability.credit?.available;
  const Select = ({ value, onChange, options, placeholder }: { value: string; onChange: (v: string) => void; options: string[]; placeholder: string }) => (
    <select value={value} onChange={(e) => onChange(e.target.value)} className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs text-slate-600">
      <option value="">{placeholder}</option>
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold tracking-tight text-slate-900">Book Overview</h1>
          <p className="text-xs text-slate-500">{rows.length} of {data.n_customers} customers · as of {data.as_of}</p>
        </div>
        <div className="flex gap-1 rounded-lg bg-slate-100 p-0.5 text-xs">
          {Object.keys(WINDOW_LABEL).map((w) => (
            <button key={w} onClick={() => setWindow(w)} className={`rounded-md px-2.5 py-1 font-medium ${w === window ? "bg-white text-slate-900 shadow-sm" : "text-slate-500"}`}>{WINDOW_LABEL[w]}</button>
          ))}
        </div>
      </div>

      <Panel title="Customer book" right={
        <div className="flex flex-wrap gap-1.5">
          <Select value={fTerminal} onChange={setFTerminal} options={terminals} placeholder="All terminals" />
          <Select value={fProduct} onChange={setFProduct} options={products} placeholder="All products" />
          <Select value={fGrade} onChange={setFGrade} options={["A", "B", "C", "D"]} placeholder="All grades" />
          <Select value={fArchetype} onChange={setFArchetype} options={archetypes} placeholder="All archetypes" />
        </div>
      }>
        <div className="max-h-[34rem] overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10 bg-white text-left text-[10px] uppercase tracking-wide text-slate-400">
              <tr>
                <Th label="Customer" sortKey="name" sort={sort} dir={dir} onSort={onSort} />
                <th className="pb-2">Archetype</th>
                <Th label="VAR" sortKey="var" sort={sort} dir={dir} onSort={onSort} right />
                <Th label="Base val" sortKey="base_value" sort={sort} dir={dir} onSort={onSort} right />
                <Th label="MM gal" sortKey="total_net_gallons" sort={sort} dir={dir} onSort={onSort} right />
                <Th label="Trend" sortKey="trend_pct" sort={sort} dir={dir} onSort={onSort} right />
                <Th label="Margin¢" sortKey="margin" sort={sort} dir={dir} onSort={onSort} right />
                <Th label="Acct val" sortKey="account_value" sort={sort} dir={dir} onSort={onSort} right />
                <Th label="Recency" sortKey="recency_gap" sort={sort} dir={dir} onSort={onSort} right />
                <Th label="Churn" sortKey="churn" sort={sort} dir={dir} onSort={onSort} right />
                <th className="pb-2 text-right" title="Credit & quadrant — full credit risk lands in P9">Credit / Quadrant</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => {
                const margin = factNum(c, "gross_margin_per_gal_mean");
                const util = factNum(c, "credit_utilization");
                const churn = c.subscores.churn_risk?.value ?? null;
                return (
                  <tr key={c.customer_id} onClick={() => setSelected(c.customer_id)}
                    className={`cursor-pointer border-t border-slate-100 hover:bg-slate-50 ${selected === c.customer_id ? "bg-indigo-50" : ""}`}>
                    <td className="py-1.5">
                      <div className="font-medium text-slate-700">{c.name}</div>
                      <div className="text-[10px] text-slate-400">{c.home_terminal}{dominantProduct(c) ? ` · ${dominantProduct(c)}` : ""}</div>
                    </td>
                    <td className="py-1.5"><ArchetypeTag name={c.archetype.primary} secondary={c.archetype.secondary} /></td>
                    <td className="py-1.5 text-right"><ScorePill score={c.var.score} grade={c.var.grade} /></td>
                    <td className="py-1.5 text-right"><ScorePill score={c.base_value.score} grade={c.base_value.grade} /></td>
                    <td className="py-1.5 text-right text-slate-600">{(c.total_net_gallons / 1e6).toFixed(2)}</td>
                    <td className="py-1.5 text-right text-[12px]"><TrendArrow pct={c.trend_pct} /></td>
                    <td className={`py-1.5 text-right ${marginOn ? "text-slate-600" : "text-slate-300"}`}>{marginOn && margin != null ? (margin * 100).toFixed(2) : "—"}</td>
                    <td className="py-1.5 text-right text-slate-600">{c.account_value ?? "—"}</td>
                    <td className={`py-1.5 text-right ${c.recency_gap > 1.5 ? "text-rose-600" : "text-slate-600"}`}>{c.recency_gap}×</td>
                    <td className="py-1.5 text-right">{churn != null && churn >= 50 ? <span className="rounded bg-rose-100 px-1.5 py-0.5 text-[10px] font-semibold text-rose-700">{Math.round(churn)}</span> : <span className="text-slate-400">{churn != null ? Math.round(churn) : "—"}</span>}</td>
                    <td className="py-1.5 text-right text-[10px]">
                      <span className={creditOn ? "text-slate-600" : "text-slate-300"} title={creditOn ? "credit utilization" : "credit risk — P9"}>{creditOn && util != null ? `${Math.round(util * 100)}%` : "P9"}</span>
                      <span className="ml-1 text-slate-400">{c.quadrant.quadrant ? `· ${c.quadrant.quadrant.split(" ")[0]}` : ""}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Customer drill-down — base-range chart & scouting note">
        {selected ? (
          <>
            <DrillDown id={selected} window={window} />
            <div className="mt-3 text-right">
              <button onClick={() => navigate(`scorecard/${encodeURIComponent(selected)}`)} className="text-xs font-medium text-indigo-600 hover:underline">
                Open full scorecard →
              </button>
            </div>
          </>
        ) : <div className="text-sm text-slate-500">Select a customer.</div>}
      </Panel>
    </div>
  );
}
