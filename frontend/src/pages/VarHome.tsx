import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ScoresResponse, ScoreCustomer, CustomerScoreResponse, Summary, BookForecast, VarTrendComparison } from "../api/types";
import Panel from "../components/Panel";
import BaseRangeChart from "../components/scores/BaseRangeChart";
import VarBreakdown from "../components/scores/VarBreakdown";
import ForwardProjection from "../components/scores/ForwardProjection";
import LaneBreaks from "../components/scores/LaneBreaks";
import VarTrendBadge from "../components/scores/VarTrendBadge";
import NameMapPanel from "../components/studio/NameMapPanel";
import { ScorePill, gradeTone, TrendArrow, fmtGal } from "../lib/scoreui";

const WINDOW_LABEL: Record<string, string> = { "30": "30d", "90": "90d", "365": "365d", all: "All-time" };

/** Volume-weighted average VAR across the scored book (a one-number "how steady is my book"). */
function avgVar(custs: ScoreCustomer[]) {
  let wsum = 0, wvar = 0;
  for (const c of custs) {
    const v = c.var;
    const a = (v.base_level || 0) * (c.grain === "monthly" ? 12 : 52);
    if (v.score != null) { wsum += a; wvar += v.score * a; }
  }
  return wsum ? wvar / wsum : null;
}

/** Biggest VAR movers (quarter-over-quarter) for the home-page worklist. */
function movers(custs: ScoreCustomer[]) {
  const scored = custs
    .map((c) => ({ c, q: c.var_trend?.comparisons?.quarter }))
    .filter((s): s is { c: ScoreCustomer; q: VarTrendComparison } => !!s.q && s.q.direction !== "insufficient" && s.q.delta != null);
  const tightening = scored.filter((s) => s.q.direction === "tightening").sort((a, b) => (b.q.delta ?? 0) - (a.q.delta ?? 0)).slice(0, 5);
  const widening = scored.filter((s) => s.q.direction === "widening").sort((a, b) => (a.q.delta ?? 0) - (b.q.delta ?? 0)).slice(0, 5);
  return { tightening, widening };
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

function LegendKey({ color, shape, label }: { color: string; shape: "line" | "band" | "dot" | "dash"; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[10px] text-slate-500">
      {shape === "line" && <span className="inline-block h-0.5 w-4 rounded" style={{ background: color }} />}
      {shape === "dash" && <span className="inline-block h-0 w-4 border-t-2 border-dashed" style={{ borderColor: color }} />}
      {shape === "band" && <span className="inline-block h-2.5 w-4 rounded-sm" style={{ background: color, opacity: 0.55 }} />}
      {shape === "dot" && <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: color }} />}
      {label}
    </span>
  );
}

/** The bottom-up book demand forecast + forecastability headline (filterable by terminal/product). */
function BookForecastPanel({ window, summary, avgv }: { window: string; summary: Summary; avgv: number | null }) {
  const [bf, setBf] = useState<BookForecast | null>(null);
  const [term, setTerm] = useState<string>("");
  const [prod, setProd] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    api.scores.bookForecast({ window, terminal: term || null, product: prod || null }).then(setBf).catch((e) => setError(String(e)));
  }, [window, term, prod]);

  const h = (d: number) => bf?.horizons.find((x) => x.days === d);
  const h30 = h(30);
  const predShare = bf?.predictable_share ?? null;
  const delta = bf?.predictable_share_delta ?? null;
  const scope = [term || "all terminals", prod || "all products"].join(" · ");

  return (
    <Panel
      title="Book demand forecast — bottom-up from every customer's lane"
      right={
        <div className="flex gap-1.5 text-xs">
          <select value={term} onChange={(e) => setTerm(e.target.value)} className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-700">
            <option value="">All terminals</option>
            {(summary.terminals ?? []).map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <select value={prod} onChange={(e) => setProd(e.target.value)} className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-700">
            <option value="">All products</option>
            {(summary.products ?? []).map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
      }
    >
      {error && <div className="rounded bg-red-50 p-2 text-xs text-red-700">{error}</div>}
      {!bf ? (
        <div className="text-sm text-slate-500">Summing customer lanes…</div>
      ) : (
        <div className="space-y-3">
          {/* the headline number */}
          <div className="rounded-xl border border-indigo-100 bg-gradient-to-br from-indigo-50 to-white p-4">
            <div className="text-sm text-slate-600">
              Across <span className="font-semibold text-slate-900">{bf.n_customers}</span> customers ({scope}), expect about
            </div>
            <div className="mt-0.5 text-3xl font-bold tracking-tight text-indigo-700">
              {h30 ? fmtGal(h30.expected) : "—"} <span className="text-lg font-semibold text-slate-500">gal in the next 30 days</span>
            </div>
            {h30 && <div className="text-xs text-slate-500">likely range {fmtGal(h30.lo)}–{fmtGal(h30.hi)} gal · ±{h30.expected ? Math.round(((h30.hi - h30.lo) / 2 / h30.expected) * 100) : 0}%</div>}
          </div>

          {/* 7 / 30 / 90 horizons */}
          <div className="grid grid-cols-3 gap-2">
            {[7, 30, 90].map((d) => {
              const x = h(d);
              return (
                <div key={d} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-center">
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">Next {d} days</div>
                  <div className="mt-0.5 text-lg font-bold tracking-tight text-slate-900">{x ? fmtGal(x.expected) : "—"}</div>
                  <div className="text-[10px] text-slate-500">{x ? `${fmtGal(x.lo)}–${fmtGal(x.hi)}` : ""}</div>
                </div>
              );
            })}
          </div>

          {/* forecastability summary: A/B vs C/D share of volume + trend */}
          <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-3">
            <div className="flex items-baseline justify-between">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">How predictable is this book?</span>
              {avgv != null && <span className="text-[11px] text-slate-400">avg VAR {Math.round(avgv)}</span>}
            </div>
            <div className="mt-1 flex items-baseline gap-2">
              <span className="text-2xl font-bold tracking-tight text-emerald-700">{predShare != null ? `${Math.round(predShare * 100)}%` : "—"}</span>
              <span className="text-xs text-slate-600">of next-30-day volume comes from steady, forecastable A/B customers.</span>
              {delta != null && Math.abs(delta) >= 0.005 && (
                <span className={`text-[11px] font-medium ${delta > 0 ? "text-emerald-600" : "text-rose-600"}`}>
                  {delta > 0 ? "▲" : "▼"} {Math.abs(Math.round(delta * 100))}pt vs last qtr
                </span>
              )}
            </div>
            {/* two-segment bar */}
            <div className="mt-2 flex h-2.5 w-full overflow-hidden rounded-full bg-slate-200">
              <div className="h-2.5 bg-emerald-500" style={{ width: `${(predShare ?? 0) * 100}%` }} title="A/B steady" />
              <div className="h-2.5 bg-amber-400" style={{ width: `${(1 - (predShare ?? 0)) * 100}%` }} title="C/D erratic" />
            </div>
            <div className="mt-1 flex justify-between text-[10px] text-slate-400">
              <span>Steady A/B · {fmtGal(bf.predictable_volume)} gal/30d</span>
              <span>Erratic C/D · {fmtGal(bf.erratic_volume)} gal/30d</span>
            </div>
          </div>
        </div>
      )}
    </Panel>
  );
}

/** The home-page "movers" worklist — whose lane tightened or widened most this quarter. */
function MoversPanel({ custs, onPick }: { custs: ScoreCustomer[]; onPick: (id: string) => void }) {
  const { tightening, widening } = useMemo(() => movers(custs), [custs]);
  const Row = ({ c, q }: { c: ScoreCustomer; q: VarTrendComparison }) => (
    <button onClick={() => onPick(c.customer_id)} className="flex w-full items-center justify-between gap-2 border-t border-slate-100 py-1.5 text-left hover:bg-slate-50">
      <span className="min-w-0">
        <span className="block truncate text-[12px] font-medium text-slate-700">{c.name}</span>
        <span className="block truncate text-[10px] text-slate-400">{q.note}</span>
      </span>
      <span className="shrink-0"><VarTrendBadge trend={q} /></span>
    </button>
  );
  if (!tightening.length && !widening.length) {
    return <p className="text-[11px] text-slate-400">Not enough history yet to spot VAR movers (need a few months per account).</p>;
  }
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      <div>
        <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-emerald-600">▲ Getting more reliable</h4>
        {tightening.length ? tightening.map((s) => <Row key={s.c.customer_id} c={s.c} q={s.q} />) : <p className="text-[11px] text-slate-400">None this quarter.</p>}
      </div>
      <div>
        <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-rose-600">▼ Becoming a problem</h4>
        {widening.length ? widening.map((s) => <Row key={s.c.customer_id} c={s.c} q={s.q} />) : <p className="text-[11px] text-slate-400">None this quarter.</p>}
      </div>
    </div>
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
  const qtrend = c.var_trend?.comparisons?.quarter;

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
            {qtrend && <VarTrendBadge trend={qtrend} />}
          </div>
        </div>
      </div>

      {/* Plain-English read */}
      {v.plain && (
        <div className="rounded-lg border border-indigo-100 bg-indigo-50/60 p-3 text-sm leading-snug text-slate-700">
          {v.plain}
          {qtrend && qtrend.direction !== "steady" && <span className="ml-1 text-slate-500">{qtrend.note}</span>}
        </div>
      )}

      {/* Forward projection from the lane */}
      <ForwardProjection forecast={c.forecast} />

      {/* Hero: the base-range chart, now continued forward as a dotted projection */}
      <BaseRangeChart series={c.lane_series ?? []} grain={c.grain} forecast={c.forecast_series} />
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg bg-slate-50 px-3 py-2">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">How to read it</span>
        <LegendKey shape="line" color="#4338ca" label="Base volume (their normal)" />
        <LegendKey shape="band" color="#818cf8" label="Base range ±1σ" />
        <LegendKey shape="band" color="#c7d2fe" label="Variability ±2σ" />
        <LegendKey shape="dot" color="#0f172a" label="Actual lifts" />
        <LegendKey shape="dash" color="#4338ca" label="Forward projection" />
      </div>

      {/* Lane breaks + weather pattern */}
      <div>
        <h4 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          Lane breaks — lifts outside their range, and the weather behind them
        </h4>
        <LaneBreaks excursions={c.excursions} />
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

  const avgv = useMemo(() => (data ? avgVar(data.customers) : null), [data]);

  const pick = useCallback((id: string) => {
    setSelected(id);
    if (typeof globalThis !== "undefined") globalThis.scrollTo?.({ top: 0, behavior: "smooth" });
  }, []);

  if (!summary.connected) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-slate-500">
        No book loaded yet. Open <button onClick={() => navigate("studio")} className="font-medium text-indigo-600 underline">Data Studio</button> to upload your lift book (or load demo data), then come back here.
      </div>
    );
  }
  if (error) return <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700">{error}</div>;
  if (!data) return <div className="text-sm text-slate-500">Reading demand patterns…</div>;

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
            <span className="font-semibold text-slate-900">The VAR score measures how predictable each customer's buying is</span> — and turns it into a forecast.{" "}
            A high score (grade A/B) means they buy steady, repeatable volumes you can plan around; a low score (C/D) means they're erratic.
            We learn each customer's normal <span className="font-medium">"lane"</span> — their base volume and how tightly they hug it —
            then <span className="font-medium">project it forward</span> and sum every customer into the bottom-up book demand below.
            <span className="ml-2 inline-flex gap-1 align-middle">
              {["A", "B", "C", "D"].map((g) => (
                <span key={g} className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${gradeTone(g)}`}>{g}</span>
              ))}
            </span>
          </div>
        </div>
      </div>

      {/* Bottom-up book forecast + forecastability summary */}
      <BookForecastPanel window={window} summary={summary} avgv={avgv} />

      {/* VAR movers */}
      <Panel title="VAR movers — whose lane changed most this quarter">
        <MoversPanel custs={data.customers} onPick={pick} />
      </Panel>

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
                    <th className="pb-2 text-right">Next 30d</th>
                    <th className="pb-2 text-right">Trend</th>
                    {showMore && <th className="pb-2 text-right">Cadence</th>}
                    {showMore && <th className="pb-2 pl-2">Archetype</th>}
                  </tr>
                </thead>
                <tbody>
                  {data.customers.map((c) => {
                    const v = c.var;
                    const next30 = c.forecast?.horizons?.find((h) => h.days === 30);
                    return (
                      <tr key={c.customer_id} onClick={() => setSelected(c.customer_id)}
                        className={`cursor-pointer border-t border-slate-100 hover:bg-slate-50 ${selected === c.customer_id ? "bg-indigo-50" : ""}`}>
                        <td className="py-1.5 pr-2">
                          <div className="font-medium text-slate-800">{c.name}</div>
                          <div className="text-[10px] text-slate-400">{v.descriptor ?? (c.data_sufficient ? "" : "thin history")}</div>
                        </td>
                        <td className="py-1.5 text-right"><ScorePill score={v.score} grade={v.grade} /></td>
                        <td className="py-1.5 text-right text-slate-600">{next30 ? fmtGal(next30.expected) : "—"}</td>
                        <td className="py-1.5 text-right"><VarTrendBadge trend={c.var_trend?.comparisons?.quarter} /></td>
                        {showMore && <td className="py-1.5 text-right text-slate-600">{v.base_cadence_days != null ? `${Math.round(v.base_cadence_days)}d` : "—"}</td>}
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
          <Panel title="Customer demand pattern & forecast">
            {selected ? <Detail id={selected} window={window} /> : <div className="text-sm text-slate-500">Select a customer to see their base-range lane and forward projection.</div>}
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
