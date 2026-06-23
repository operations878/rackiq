import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type {
  ScoresResponse,
  ScoreCustomer,
  CustomerScoreResponse,
  QuadrantResponse,
  Summary,
} from "../api/types";
import Panel from "../components/Panel";
import BaseRangeChart from "../components/scores/BaseRangeChart";
import QuadrantScatter from "../components/scores/QuadrantScatter";
import { humanize } from "../lib/format";

const WINDOW_LABEL: Record<string, string> = { "30": "30d", "90": "90d", "365": "365d", all: "All-time" };

function gradeTone(g: string | null): string {
  return { A: "bg-emerald-100 text-emerald-700", B: "bg-emerald-50 text-emerald-700", C: "bg-amber-100 text-amber-700", D: "bg-red-100 text-red-700" }[g ?? ""] ?? "bg-slate-100 text-slate-500";
}

function ScorePill({ score, grade }: { score: number | null; grade: string | null }) {
  if (score == null) return <span className="text-xs text-slate-400">insufficient</span>;
  return (
    <span className="inline-flex items-center gap-1">
      <span className="font-semibold text-slate-800">{score}</span>
      {grade && <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${gradeTone(grade)}`}>{grade}</span>}
    </span>
  );
}

function Bar({ value, color = "bg-indigo-500" }: { value: number | null; color?: string }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded bg-slate-200">
      <div className={`h-1.5 rounded ${color}`} style={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%` }} />
    </div>
  );
}

function AvailabilityStrip({ availability }: { availability: ScoresResponse["availability"] }) {
  const items = Object.entries(availability);
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map(([k, v]) => (
        <span
          key={k}
          title={v.reason}
          className={`rounded px-2 py-0.5 text-[10px] font-medium ${
            v.available ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-400 line-through"
          }`}
        >
          {humanize(k)}
        </span>
      ))}
    </div>
  );
}

const SUBSCORE_ORDER = [
  "volume_steadiness", "timing_steadiness", "price_sensitivity", "evr", "discount_efficiency",
  "market_sensitivity", "weather_sensitivity", "quote_score", "churn_risk",
];

function SubScores({ c }: { c: ScoreCustomer }) {
  return (
    <div className="space-y-1.5">
      {SUBSCORE_ORDER.map((k) => {
        const s = c.subscores[k];
        if (!s) return null;
        const churn = k === "churn_risk";
        const collecting = s.collecting;
        return (
          <div key={k} className={s.available ? "" : "opacity-40"}>
            <div className="flex items-center justify-between text-[11px]">
              <span className="text-slate-600" title={s.reason}>{humanize(k)}</span>
              <span className="font-medium text-slate-700">
                {!s.available ? "—" : collecting ? "collecting" : s.value ?? "—"}
                {s.beta != null && s.available && <span className="ml-1 text-slate-400">β={s.beta}</span>}
                {s.ratio != null && s.available && <span className="ml-1 text-slate-400">×{s.ratio}</span>}
                {s.accept_rate != null && s.available && <span className="ml-1 text-slate-400">acc {Math.round(s.accept_rate * 100)}%</span>}
              </span>
            </div>
            <Bar value={s.available && !collecting ? s.value : 0} color={churn ? "bg-rose-500" : "bg-indigo-500"} />
          </div>
        );
      })}
    </div>
  );
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
  const bv = c.base_value;
  const a = c.archetype;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 className="text-base font-bold text-slate-900">{c.name}</h3>
          <p className="text-[11px] text-slate-500">
            {c.home_terminal} · {c.grain} buckets · {c.n_lifts} lifts · {(c.total_net_gallons / 1e6).toFixed(2)} MM gal
            {!c.data_sufficient && <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-amber-700">thin history</span>}
          </p>
        </div>
        <div className="flex items-center gap-4 text-sm">
          <div className="text-center">
            <div className="text-[10px] uppercase text-slate-400">VAR</div>
            <ScorePill score={c.var.score} grade={c.var.grade} />
          </div>
          <div className="text-center">
            <div className="text-[10px] uppercase text-slate-400">Base value</div>
            <ScorePill score={bv.score} grade={bv.grade} />
          </div>
          <div className="text-center">
            <div className="text-[10px] uppercase text-slate-400">Account value</div>
            <span className="font-semibold text-slate-800">{c.account_value ?? "—"}</span>
          </div>
        </div>
      </div>

      <BaseRangeChart series={c.lane_series ?? []} grain={c.grain} />

      <div className="rounded-lg border border-indigo-100 bg-indigo-50/50 p-3 text-[11px] text-slate-700">
        <span className="font-semibold text-indigo-700">Why this VAR: </span>{c.var.explanation}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Layer-2 sub-scores</h4>
          <SubScores c={c} />
        </div>
        <div className="space-y-3">
          <div>
            <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Layer-3 base value</h4>
            <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-slate-600">
              <div>EGP <b className="text-slate-800">${Math.round(bv.egp).toLocaleString()}</b></div>
              <div>RFAP <b className="text-slate-800">${Math.round(bv.rfap).toLocaleString()}</b></div>
              <div>Friction <b className="text-slate-800">${Math.round(bv.friction_cost).toLocaleString()}</b></div>
              <div>Credit <b className="text-slate-800">${Math.round(bv.credit_cost).toLocaleString()}</b></div>
              <div>$/gal <b className="text-slate-800">{bv.profit_per_gallon ?? "—"}</b></div>
              <div>$/rack-hr <b className="text-slate-800">{bv.profit_per_rackhour ?? "—"}</b></div>
              <div>Strategic uplift <b className="text-slate-800">×{bv.strategic_uplift}</b></div>
              <div>Recency gap <b className="text-slate-800">{c.recency_gap}×</b></div>
            </div>
          </div>
          <div>
            <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
              Archetype {a.ambiguous && <span className="ml-1 rounded bg-amber-100 px-1 text-amber-700">ambiguous</span>}
            </h4>
            <div className="text-sm">
              <span className="font-semibold text-slate-800">{a.primary}</span>
              <span className="text-slate-400"> + {a.secondary}</span>
              <span className="ml-2 text-[11px] text-slate-400">conf {a.confidence}</span>
            </div>
            <div className="mt-1 space-y-0.5 text-[11px] text-slate-600">
              {Object.entries(a.posture).map(([k, v]) => (
                <div key={k}><span className="capitalize text-slate-400">{k}:</span> {v}</div>
              ))}
            </div>
            <div className="mt-1 text-[11px] text-slate-400">
              Quadrant: <b className="text-slate-600">{c.quadrant.quadrant ?? "—"}</b>
              {c.quadrant.explainability != null && <> (EVR {c.quadrant.explainability} · profit {c.quadrant.profitability})</>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Scores({ summary }: { summary: Summary }) {
  const [window, setWindow] = useState("all");
  const [data, setData] = useState<ScoresResponse | null>(null);
  const [quad, setQuad] = useState<QuadrantResponse | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    setError(null);
    Promise.all([api.scores.list(window), api.scores.quadrant(window)])
      .then(([s, q]) => {
        setData(s);
        setQuad(q);
        setSelected((cur) => cur ?? s.customers[0]?.customer_id ?? null);
      })
      .catch((e) => setError(String(e)));
  }, [window]);

  useEffect(reload, [reload]);

  async function recompute() {
    setBusy(true);
    try {
      await api.scores.recompute();
      reload();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  if (!summary.connected) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-slate-500">
        Load a book in Data Studio to compute customer scores.
      </div>
    );
  }
  if (error) return <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700">{error}</div>;
  if (!data) return <div className="text-sm text-slate-500">Computing scores…</div>;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold tracking-tight text-slate-900">Customer Scores</h1>
          <p className="text-xs text-slate-500">
            VAR variability lane · base value · archetypes — {data.n_customers} customers, as of {data.as_of}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex gap-1 rounded-lg bg-slate-100 p-0.5 text-xs">
            {data.windows.map((w) => (
              <button
                key={w}
                onClick={() => setWindow(w)}
                className={`rounded-md px-2.5 py-1 font-medium ${w === window ? "bg-white text-slate-900 shadow-sm" : "text-slate-500"}`}
              >
                {WINDOW_LABEL[w] ?? w}
              </button>
            ))}
          </div>
          <button
            onClick={recompute}
            disabled={busy}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50"
          >
            {busy ? "Recomputing…" : "Recompute & persist"}
          </button>
        </div>
      </div>

      <Panel title="Metric availability (capability-gated)">
        <AvailabilityStrip availability={data.availability} />
      </Panel>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-5">
        <section className="xl:col-span-3">
          <Panel title="Ranked customers">
            <div className="max-h-[36rem] overflow-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-white text-left text-[10px] uppercase tracking-wide text-slate-400">
                  <tr>
                    <th className="pb-2">Customer</th>
                    <th className="pb-2">Archetype</th>
                    <th className="pb-2 text-right">VAR</th>
                    <th className="pb-2 text-right">Base value</th>
                    <th className="pb-2 text-right">MM gal</th>
                    <th className="pb-2 text-right">Trend</th>
                  </tr>
                </thead>
                <tbody>
                  {data.customers.map((c) => (
                    <tr
                      key={c.customer_id}
                      onClick={() => setSelected(c.customer_id)}
                      className={`cursor-pointer border-t border-slate-100 hover:bg-slate-50 ${selected === c.customer_id ? "bg-indigo-50" : ""}`}
                    >
                      <td className="py-1.5">
                        <div className="font-medium text-slate-700">{c.name}</div>
                        {!c.data_sufficient && <span className="text-[10px] text-amber-600">thin history</span>}
                      </td>
                      <td className="py-1.5 text-[11px] text-slate-500">
                        {c.archetype.primary}
                        {c.archetype.ambiguous && <span title="ambiguous" className="ml-1 text-amber-500">≈</span>}
                        <div className="text-slate-400">{c.archetype.secondary}</div>
                      </td>
                      <td className="py-1.5 text-right"><ScorePill score={c.var.score} grade={c.var.grade} /></td>
                      <td className="py-1.5 text-right"><ScorePill score={c.base_value.score} grade={c.base_value.grade} /></td>
                      <td className="py-1.5 text-right text-slate-600">{(c.total_net_gallons / 1e6).toFixed(2)}</td>
                      <td className={`py-1.5 text-right ${c.trend_pct >= 0 ? "text-emerald-600" : "text-rose-600"}`}>
                        {c.trend_pct >= 0 ? "+" : ""}{c.trend_pct}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </section>

        <section className="xl:col-span-2">
          <Panel title="Variability Quality Quadrant">
            {quad && <QuadrantScatter data={quad} onSelect={setSelected} />}
          </Panel>
        </section>
      </div>

      <Panel title="Customer drill-down — base-range chart">
        {selected ? <DrillDown id={selected} window={window} /> : <div className="text-sm text-slate-500">Select a customer.</div>}
      </Panel>
    </div>
  );
}
