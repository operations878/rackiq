import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ScoresResponse, ScoreCustomer, CustomerScoreResponse, Summary } from "../api/types";
import Panel from "../components/Panel";
import BaseRangeChart from "../components/scores/BaseRangeChart";
import VarBreakdown from "../components/scores/VarBreakdown";
import NameMapPanel from "../components/studio/NameMapPanel";
import { ScorePill, gradeTone, TrendArrow, fmtGal } from "../lib/scoreui";

const WINDOW_LABEL: Record<string, string> = { "30": "30d", "90": "90d", "365": "365d", all: "All-time" };

/** Bottom-up book forecast from each customer's base-volume lane, with error propagation. */
function bookForecast(custs: ScoreCustomer[]) {
  let annual = 0, varSq = 0, predictable = 0, wsum = 0, wvar = 0;
  const grades: Record<string, number> = { A: 0, B: 0, C: 0, D: 0 };
  for (const c of custs) {
    const v = c.var;
    const ppy = c.grain === "monthly" ? 12 : 52;
    const a = (v.base_level || 0) * ppy;
    annual += a;
    const sig = (v.sigma || 0) * Math.sqrt(ppy);
    varSq += sig * sig;
    if (v.grade) grades[v.grade] = (grades[v.grade] || 0) + 1;
    if (v.grade === "A" || v.grade === "B") predictable += a;
    if (v.score != null) { wsum += a; wvar += v.score * a; }
  }
  return {
    annual, band: Math.sqrt(varSq), predictable,
    predShare: annual ? predictable / annual : 0,
    avgVar: wsum ? wvar / wsum : null, grades,
  };
}

function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">{label}</div>
      <div className="mt-0.5 text-xl font-bold tracking-tight text-slate-900">{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

function LegendKey({ color, shape, label }: { color: string; shape: "line" | "band" | "dot"; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[10px] text-slate-500">
      {shape === "line" && <span className="inline-block h-0.5 w-4 rounded" style={{ background: color }} />}
      {shape === "band" && <span className="inline-block h-2.5 w-4 rounded-sm" style={{ background: color, opacity: 0.55 }} />}
      {shape === "dot" && <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: color }} />}
      {label}
    </span>
  );
}

function Detail({ id, window }: { id: string; window: string }) {
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
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 className="text-lg font-bold text-slate-900">{c.name}</h3>
          <p className="text-[11px] text-slate-500">
            {c.home_terminal ? `${c.home_terminal} · ` : ""}{c.grain} buckets · {c.n_lifts} lifts
            {!c.data_sufficient && <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-amber-700">thin history</span>}
          </p>
        </div>
        <div className="text-center">
          <div className="text-[10px] uppercase tracking-wide text-slate-400">VAR steadiness</div>
          <div className="flex items-center gap-2">
            <ScorePill score={v.score} grade={v.grade} />
            {v.descriptor && <span className="text-[11px] text-slate-500">{v.descriptor}</span>}
          </div>
        </div>
      </div>

      {/* Plain-English read */}
      {v.plain && (
        <div className="rounded-lg border border-indigo-100 bg-indigo-50/60 p-3 text-sm leading-snug text-slate-700">
          {v.plain}
        </div>
      )}

      {/* Hero: the base-range chart */}
      <BaseRangeChart series={c.lane_series ?? []} grain={c.grain} />
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg bg-slate-50 px-3 py-2">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">How to read it</span>
        <LegendKey shape="line" color="#4338ca" label="Base volume (their normal)" />
        <LegendKey shape="band" color="#818cf8" label="Base range ±1σ (normal orders)" />
        <LegendKey shape="band" color="#c7d2fe" label="Variability ±2σ (a surprise outside)" />
        <LegendKey shape="dot" color="#0f172a" label="Actual lifts" />
      </div>

      <VarBreakdown v={v} grain={c.grain} />
    </div>
  );
}

export default function VarHome({ summary, navigate }: { summary: Summary; navigate: (to: string) => void }) {
  const [window, setWindow] = useState("all");
  const [data, setData] = useState<ScoresResponse | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [showMore, setShowMore] = useState(false);
  const [showUnmapped, setShowUnmapped] = useState(false);
  const [nUnmapped, setNUnmapped] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    setError(null);
    api.scores
      .list(window)
      .then((s) => {
        setData(s);
        setSelected((cur) => (cur && s.customers.some((c) => c.customer_id === cur) ? cur : s.customers[0]?.customer_id ?? null));
      })
      .catch((e) => setError(String(e)));
  }, [window]);
  useEffect(reload, [reload]);

  useEffect(() => {
    api.studio.unmappedCustomers()
      .then((u) => { setNUnmapped(u.n_unmapped); setShowUnmapped(u.n_unmapped > 0); })
      .catch(() => {});
  }, [summary]);

  const fc = useMemo(() => (data ? bookForecast(data.customers) : null), [data]);

  if (!summary.connected) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-slate-500">
        No book loaded yet. Open <button onClick={() => navigate("studio")} className="font-medium text-indigo-600 underline">Data Studio</button> to upload your lift book (or load demo data), then come back here.
      </div>
    );
  }
  if (error) return <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700">{error}</div>;
  if (!data || !fc) return <div className="text-sm text-slate-500">Reading demand patterns…</div>;

  return (
    <div className="space-y-5">
      {/* Title + window */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-slate-900">Demand Predictability</h1>
          <p className="text-xs text-slate-500">
            {data.n_customers} customers ranked by how steadily they buy — as of {data.as_of}
          </p>
        </div>
        <div className="flex gap-1 rounded-lg bg-slate-100 p-0.5 text-xs">
          {data.windows?.map((w) => (
            <button key={w} onClick={() => setWindow(w)}
              className={`rounded-md px-2.5 py-1 font-medium ${w === window ? "bg-white text-slate-900 shadow-sm" : "text-slate-500"}`}>
              {WINDOW_LABEL[w] ?? w}
            </button>
          ))}
        </div>
      </div>

      {/* Plain-language explainer */}
      <div className="rounded-xl border border-indigo-100 bg-gradient-to-br from-indigo-50 to-white p-4">
        <div className="flex items-start gap-3">
          <div className="text-2xl">🎯</div>
          <div className="text-sm leading-snug text-slate-700">
            <span className="font-semibold text-slate-900">The VAR score measures how predictable each customer's buying is.</span>{" "}
            A high score (grade A/B) means they buy steady, repeatable volumes you can plan and forecast around.
            A low score (C/D) means they're erratic and hard to plan for. We learn each customer's normal
            <span className="font-medium"> "lane"</span> — their seasonally-adjusted base volume and how tightly they hug it —
            then forecast the whole book from the bottom up.
            <span className="ml-2 inline-flex gap-1 align-middle">
              {["A", "B", "C", "D"].map((g) => (
                <span key={g} className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${gradeTone(g)}`}>{g}</span>
              ))}
            </span>
          </div>
        </div>
      </div>

      {/* Total-book forecast summary */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Forecast run-rate (bottom-up)"
              value={`${(fc.annual / 1e6).toFixed(2)} MM gal/yr`}
              sub={`±${fc.annual ? Math.round((fc.band / fc.annual) * 100) : 0}% from per-customer lanes`} />
        <Stat label="Predictable volume (A/B)"
              value={`${Math.round(fc.predShare * 100)}%`}
              sub={`${fmtGal(fc.predictable)} gal/yr you can plan around`} />
        <Stat label="Avg VAR (volume-weighted)"
              value={fc.avgVar != null ? Math.round(fc.avgVar) : "—"}
              sub="higher = steadier book" />
        <Stat label="Steadiness grades"
              value={<span className="flex gap-1.5 text-sm">
                {(["A", "B", "C", "D"] as const).map((g) => (
                  <span key={g} className={`rounded px-1.5 py-0.5 text-xs font-semibold ${gradeTone(g)}`}>{fc.grades[g] || 0}</span>
                ))}
              </span>}
              sub={`${data.n_customers} customers`} />
      </div>

      {/* Ranked list + detail */}
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-5">
        <section className="xl:col-span-2">
          <Panel title="Ranked by VAR (steadiest first)"
                 right={<button onClick={() => setShowMore((s) => !s)} className="text-[11px] font-medium text-indigo-600 hover:underline">
                   {showMore ? "Fewer columns" : "Show more columns"}
                 </button>}>
            <div className="max-h-[40rem] overflow-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-white text-left text-[10px] uppercase tracking-wide text-slate-400">
                  <tr>
                    <th className="pb-2 pr-2">Customer</th>
                    <th className="pb-2 text-right">VAR</th>
                    <th className="pb-2 text-right">Base vol</th>
                    <th className="pb-2 text-right">Cadence</th>
                    <th className="pb-2 text-right">Trend</th>
                    {showMore && <th className="pb-2 text-right">Monthly</th>}
                    {showMore && <th className="pb-2 pl-2">Archetype</th>}
                  </tr>
                </thead>
                <tbody>
                  {data.customers.map((c) => {
                    const v = c.var;
                    const unit = c.grain === "monthly" ? "mo" : "wk";
                    return (
                      <tr key={c.customer_id} onClick={() => setSelected(c.customer_id)}
                        className={`cursor-pointer border-t border-slate-100 hover:bg-slate-50 ${selected === c.customer_id ? "bg-indigo-50" : ""}`}>
                        <td className="py-1.5 pr-2">
                          <div className="font-medium text-slate-800">{c.name}</div>
                          <div className="text-[10px] text-slate-400">{v.descriptor ?? (c.data_sufficient ? "" : "thin history")}</div>
                        </td>
                        <td className="py-1.5 text-right"><ScorePill score={v.score} grade={v.grade} /></td>
                        <td className="py-1.5 text-right text-slate-600">{fmtGal(v.base_level)}<span className="text-[9px] text-slate-400">/{unit}</span></td>
                        <td className="py-1.5 text-right text-slate-600">{v.base_cadence_days != null ? `${Math.round(v.base_cadence_days)}d` : "—"}</td>
                        <td className="py-1.5 text-right"><TrendArrow pct={c.trend_pct} /></td>
                        {showMore && <td className="py-1.5 text-right text-slate-600">{fmtGal(c.monthly_volume)}</td>}
                        {showMore && <td className="py-1.5 pl-2 text-[11px] text-slate-500">{c.archetype?.primary}</td>}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Panel>
        </section>

        <section className="xl:col-span-3">
          <Panel title="Customer demand pattern">
            {selected ? <Detail id={selected} window={window} /> : <div className="text-sm text-slate-500">Select a customer to see their base-range lane.</div>}
          </Panel>
        </section>
      </div>

      {/* Unmapped names */}
      <Panel
        title={<span>Customer name map {nUnmapped > 0 && <span className="ml-1 rounded-full bg-amber-500 px-1.5 py-0.5 text-[10px] font-semibold text-white">{nUnmapped} unmapped</span>}</span>}
        right={<button onClick={() => setShowUnmapped((s) => !s)} className="text-[11px] font-medium text-indigo-600 hover:underline">{showUnmapped ? "Hide" : "Show"}</button>}
      >
        {showUnmapped ? <NameMapPanel onState={() => reload()} compact /> : (
          <p className="text-[11px] text-slate-500">
            {nUnmapped > 0
              ? `${nUnmapped} raw account name(s) aren't mapped to a clean name yet.`
              : "Every customer name is mapped to a clean coded name."}
          </p>
        )}
      </Panel>
    </div>
  );
}
