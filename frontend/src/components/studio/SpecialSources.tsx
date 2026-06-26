import { useState, type ChangeEvent } from "react";
import { api } from "../../api/client";

/**
 * Re-uploadable, format-aware sources that don't go through the generic column-mapper: the Deal book,
 * the wholesale Price/Cost grid, and the HDD / weather book. Same UX as the BOL/Deals uploads —
 * drop in an updated file monthly, idempotent (re-upload never double-counts).
 */
type Kind = "deals" | "prices" | "hdd";

const SOURCES: Array<{ kind: Kind; title: string; blurb: string; accept: string }> = [
  { kind: "deals", title: "Deal book", blurb: "term · forward-fixed · spot (auto-detected)", accept: ".xlsx,.xlsm,.csv" },
  { kind: "prices", title: "Prices & Costs", blurb: "wholesale sell grid · barge Trips landed cost", accept: ".xlsx,.xlsm,.xls,.csv" },
  { kind: "hdd", title: "HDD / Weather", blurb: "Heating Degree Days + Normal/5-yr/10-yr + BX HO SOLD", accept: ".xlsx,.xlsm" },
];

export default function SpecialSources() {
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);

  async function run(kind: Kind, fn: () => Promise<Record<string, unknown>>, label: string) {
    setBusy(`${label}…`);
    setErr(null);
    try {
      const res = await fn();
      setMsg((m) => ({ ...m, [kind]: summarize(kind, res) }));
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
    }
  }

  function onUpload(kind: Kind) {
    return (e: ChangeEvent<HTMLInputElement>) => {
      const f = e.target.files?.[0];
      if (!f) return;
      const fn =
        kind === "deals" ? () => api.deals.upload(f)
        : kind === "prices" ? () => api.margin.upload(f)
        : () => api.weather.hddUpload(f);
      run(kind, fn, `Ingesting ${f.name}`);
      e.target.value = "";
    };
  }

  function loadSamples(kind: Kind) {
    const fn =
      kind === "deals" ? () => api.deals.loadSamples()
      : kind === "prices" ? () => api.margin.loadSamples()
      : () => api.weather.hddLoadSamples();
    run(kind, fn, "Loading sample(s)");
  }

  return (
    <div>
      <div className="mb-1 text-sm font-semibold text-slate-700">Re-uploadable sources</div>
      <p className="mb-3 text-xs text-slate-500">
        Format-aware, idempotent (re-upload never double-counts). Drop in an updated file monthly — no code.
      </p>
      {err && <div className="mb-2 rounded bg-rose-50 px-2 py-1 text-xs text-rose-700">{err}</div>}
      {busy && <div className="mb-2 rounded bg-indigo-50 px-2 py-1 text-xs text-indigo-700">{busy}</div>}
      <div className="space-y-2">
        {SOURCES.map((s) => (
          <div key={s.kind} className="rounded-lg border border-slate-200 p-2">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-medium text-slate-700">{s.title}</div>
                <div className="text-[11px] text-slate-400">{s.blurb}</div>
              </div>
              <div className="flex items-center gap-1">
                <label className="cursor-pointer rounded border border-slate-300 px-2 py-1 text-[11px] hover:bg-slate-50">
                  Upload
                  <input type="file" accept={s.accept} className="hidden" onChange={onUpload(s.kind)} />
                </label>
                <button onClick={() => loadSamples(s.kind)} className="rounded bg-slate-100 px-2 py-1 text-[11px] hover:bg-slate-200">
                  Sample
                </button>
              </div>
            </div>
            {msg[s.kind] && <div className="mt-1 text-[11px] text-emerald-700">{msg[s.kind]}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}

function summarize(kind: Kind, res: Record<string, unknown>): string {
  if (kind === "hdd") {
    const stores = (res.stores ?? res) as Record<string, unknown>;
    const obs = stores?.hdd_observations ?? (res.observations_written as number);
    const st = (stores?.stations as string[]) ?? [];
    return `✓ ${obs ?? 0} HDD days loaded${st.length ? ` · stations ${st.join(", ")}` : ""}`;
  }
  if (kind === "prices") {
    const s = (res.stores ?? {}) as Record<string, number>;
    return `✓ ${s.price_grid_rows ?? res.prices_written ?? 0} price rows · ${s.landed_cost_trips ?? 0} trips`;
  }
  const b = res.bridge as { match_rate_by_committed_volume?: number } | undefined;
  return `✓ deals loaded${b ? ` · ${b.match_rate_by_committed_volume}% committed-vol bridged` : ""}`;
}
