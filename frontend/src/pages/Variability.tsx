import { useEffect, useMemo, useState, type ChangeEvent } from "react";
import { api } from "../api/client";
import type { Summary, VariabilityResponse, VariabilityValidation, VarCustomer } from "../api/types";
import Panel from "../components/Panel";

const GRADE_TONE: Record<string, string> = {
  A: "bg-emerald-100 text-emerald-700",
  B: "bg-lime-100 text-lime-700",
  C: "bg-amber-100 text-amber-700",
  D: "bg-rose-100 text-rose-700",
};
const QUAD_TONE: Record<string, string> = {
  metronome: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  daily_variable_size: "bg-sky-50 text-sky-700 ring-sky-200",
  infrequent_identical: "bg-violet-50 text-violet-700 ring-violet-200",
  sporadic_bursty: "bg-rose-50 text-rose-700 ring-rose-200",
  insufficient: "bg-slate-50 text-slate-400 ring-slate-200",
};
const fmtGal = (n: number | null | undefined) =>
  n == null ? "—" : `${Math.round(n).toLocaleString()} gal`;

function Grade({ g }: { g: string | null }) {
  if (!g) return <span className="text-slate-300">—</span>;
  return <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${GRADE_TONE[g] ?? ""}`}>{g}</span>;
}

function Bar({ value, tone }: { value: number | null; tone: string }) {
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-20 rounded bg-slate-100">
        <div className={`h-2 rounded ${tone}`} style={{ width: `${value ?? 0}%` }} />
      </div>
      <span className="w-8 text-right text-xs tabular-nums text-slate-600">{value == null ? "—" : value}</span>
    </div>
  );
}

function Histogram({ title, hist, sub }: { title: string; hist: Record<string, number>; sub?: string }) {
  const max = Math.max(...Object.values(hist), 1);
  return (
    <div>
      <div className="text-sm font-medium text-slate-700">{title}</div>
      {sub && <div className="mb-1 text-xs text-slate-400">{sub}</div>}
      <div className="flex items-end gap-2" style={{ height: 90 }}>
        {Object.entries(hist).map(([bin, n]) => (
          <div key={bin} className="flex flex-1 flex-col items-center justify-end">
            <span className="mb-1 text-[10px] text-slate-500">{n}</span>
            <div className="w-full rounded-t bg-indigo-400" style={{ height: `${(n / max) * 70}%` }} />
            <span className="mt-1 text-[9px] text-slate-400">{bin}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Variability({ summary }: { summary: Summary; navigate?: (k: string) => void }) {
  const [data, setData] = useState<VariabilityResponse | null>(null);
  const [val, setVal] = useState<VariabilityValidation | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [quad, setQuad] = useState<string>("all");
  const [picks, setPicks] = useState<Record<string, boolean>>({});

  async function load() {
    setLoading(true);
    setErr(null);
    try {
      const [v, va] = await Promise.all([api.variability.get(), api.variability.validation()]);
      setData(v);
      setVal(va);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function loadSamples() {
    setBusy("Loading the real book (chart → BOLs → deals)…");
    try {
      await api.deals.loadSamples();
      await load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function confirmPicks() {
    const pairs: [string, string][] = (val?.bridge.candidates ?? [])
      .filter((c) => picks[c.customer_raw] && c.candidate_master)
      .map((c) => [c.customer_raw, c.candidate_master as string]);
    if (!pairs.length) return;
    setBusy(`Confirming ${pairs.length} bridge(s)…`);
    try {
      await api.deals.confirmBridge(pairs);
      setPicks({});
      await load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function onUpload(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(`Ingesting ${file.name}…`);
    try {
      await api.deals.upload(file);
      await load();
    } catch (e2) {
      setErr(String(e2));
    } finally {
      setBusy(null);
      e.target.value = "";
    }
  }

  const rows = useMemo(() => {
    const cs = data?.customers ?? [];
    return quad === "all" ? cs : cs.filter((c) => c.quadrant === quad);
  }, [data, quad]);

  if (!summary.connected)
    return <Panel title="Customer Variability">No data — open Data Studio, or load the real book below.
      <div className="mt-3"><button onClick={loadSamples} className="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white">Load real book</button></div>
    </Panel>;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-slate-800">Customer Variability — two independent axes</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-500">
          How predictably a customer lifts is <b>two</b> things, scored separately:{" "}
          <b className="text-slate-700">cadence consistency</b> (how regularly they show up, over working days) and{" "}
          <b className="text-slate-700">size consistency</b> (how alike each lift is). A daily-but-lumpy account and a
          weekly-but-identical account are opposite cases — and get opposite, useful labels. The deal book only
          <i> annotates</i> (commitment context); it never changes the scores.
        </p>
      </div>

      {err && <div className="rounded bg-rose-50 px-3 py-2 text-sm text-rose-700">{err}</div>}
      {busy && <div className="rounded bg-indigo-50 px-3 py-2 text-sm text-indigo-700">{busy}</div>}

      {val?.available && (
        <Panel title="Validation — do both axes spread?">
          <div className="grid gap-6 md:grid-cols-2">
            <Histogram title="AXIS 1 · Cadence consistency" hist={val.axis1_hist}
              sub={`std ${val.axis1_cadence.std} · ${val.axis1_cadence.spreads ? "spreads ✓" : "bunched ✗"} · grades ${JSON.stringify(val.axis1_cadence.grades)}`} />
            <Histogram title="AXIS 2 · Size consistency" hist={val.axis2_hist}
              sub={`std ${val.axis2_size.std} · ${val.axis2_size.spreads ? "spreads ✓" : "bunched ✗"} · grades ${JSON.stringify(val.axis2_size.grades)}`} />
          </div>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            {Object.entries(val.quadrants).map(([q, n]) => (
              <span key={q} className={`rounded px-2 py-1 ring-1 ${QUAD_TONE[q] ?? "bg-slate-50 ring-slate-200"}`}>{q}: <b>{n}</b></span>
            ))}
          </div>
        </Panel>
      )}

      {val?.bridge && (
        <Panel title={`Deal-book → master bridge — ${val.bridge.match_rate_by_committed_volume}% of committed volume bridged`}>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="text-emerald-700">{val.bridge.n_mapped} mapped</span>
            <span className="text-amber-700">{val.bridge.n_candidates} candidates</span>
            <span className="text-rose-700">{val.bridge.n_unmapped} unmapped</span>
            <label className="ml-auto cursor-pointer rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50">
              Upload a Deals file
              <input type="file" accept=".xlsx,.csv" className="hidden" onChange={onUpload} />
            </label>
            <button onClick={loadSamples} className="rounded bg-slate-100 px-2 py-1 text-xs hover:bg-slate-200">Reload real book</button>
          </div>
          {val.bridge.candidates.length > 0 && (
            <div className="mt-3">
              <div className="mb-1 text-xs text-slate-500">Candidate matches — confirm to attach commitment context (never auto-merged):</div>
              <div className="max-h-48 space-y-1 overflow-y-auto">
                {val.bridge.candidates.map((c) => (
                  <label key={c.customer_raw} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={!!picks[c.customer_raw]}
                      onChange={(e) => setPicks((p) => ({ ...p, [c.customer_raw]: e.target.checked }))} />
                    <span className="font-medium text-slate-700">{c.customer_raw}</span>
                    <span className="text-slate-400">→</span>
                    <span className="text-slate-600">{c.candidate_master}</span>
                    <span className="text-xs text-slate-400">({c.similarity}) · {fmtGal(c.committed_gallons)} committed</span>
                  </label>
                ))}
              </div>
              <button onClick={confirmPicks} className="mt-2 rounded bg-indigo-600 px-3 py-1.5 text-sm text-white disabled:opacity-40"
                disabled={!Object.values(picks).some(Boolean)}>Confirm selected bridges</button>
            </div>
          )}
        </Panel>
      )}

      <Panel title={`Customers (${rows.length})`}>
        <div className="mb-2 flex flex-wrap gap-1 text-xs">
          {["all", "metronome", "daily_variable_size", "infrequent_identical", "sporadic_bursty"].map((q) => (
            <button key={q} onClick={() => setQuad(q)}
              className={`rounded px-2 py-1 ${quad === q ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600"}`}>{q}</button>
          ))}
        </div>
        {loading && <div className="text-sm text-slate-400">Loading…</div>}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs uppercase tracking-wide text-slate-400">
                <th className="py-1.5 pr-3">Customer</th>
                <th className="px-2">Cadence</th>
                <th className="px-2">Size</th>
                <th className="px-2">Quadrant</th>
                <th className="px-2">Volume / yr</th>
                <th className="px-2">Commitment context</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c: VarCustomer) => (
                <tr key={c.customer_id} className="border-b border-slate-50 hover:bg-slate-50">
                  <td className="py-1.5 pr-3">
                    <div className="font-medium text-slate-700">{c.name}</div>
                    <div className="text-[11px] text-slate-400">{c.n_lifts.toLocaleString()} lifts · {c.dominant_product ?? "—"}{c.weather_sensitive ? " · weather-sensitive" : ""}</div>
                  </td>
                  <td className="px-2"><div className="flex items-center gap-1"><Bar value={c.cadence_consistency} tone="bg-emerald-400" /><Grade g={c.cadence_grade} /></div></td>
                  <td className="px-2"><div className="flex items-center gap-1"><Bar value={c.size_consistency} tone="bg-sky-400" /><Grade g={c.size_grade} /></div></td>
                  <td className="px-2"><span className={`rounded px-1.5 py-0.5 text-xs ring-1 ${QUAD_TONE[c.quadrant] ?? ""}`}>{c.quadrant_label}</span></td>
                  <td className="px-2 tabular-nums text-slate-600">{fmtGal(c.total_net_gallons)}</td>
                  <td className="px-2 text-xs text-slate-500">{c.commitment?.label}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data && <div className="mt-2 text-xs text-slate-400">
          {data.coverage.pct_volume_scored}% of volume scored · {data.coverage.pct_volume_annotated}% annotated with commitment context · as of {data.as_of}
        </div>}
      </Panel>
    </div>
  );
}
