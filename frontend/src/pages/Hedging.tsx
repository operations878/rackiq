import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Summary, HedgingResponse, HedgingHorizon } from "../api/types";
import Panel from "../components/Panel";
import { fmtGal } from "../lib/scoreui";

const WINDOW_LABEL: Record<string, string> = { all: "All-time", "365": "365d", "90": "90d" };

function Seg({ options, value, onChange, labels }: {
  options: string[]; value: string; onChange: (v: string) => void; labels?: Record<string, string>;
}) {
  return (
    <div className="flex flex-wrap gap-1 rounded-lg bg-slate-100 p-0.5 text-xs">
      {options.map((o) => (
        <button
          key={o}
          onClick={() => onChange(o)}
          className={`rounded-md px-2.5 py-1 font-medium ${o === value ? "bg-white text-slate-900 shadow-sm" : "text-slate-500"}`}
        >
          {labels?.[o] ?? o}
        </button>
      ))}
    </div>
  );
}

/** Stacked composition bar: expected demand (P50) + statistical band buffer + behavior coil buffer. */
function StagingBar({ h }: { h: HedgingHorizon }) {
  const total = Math.max(h.recommended_staging, 1);
  const seg = (v: number) => `${(Math.max(0, v) / total) * 100}%`;
  return (
    <div>
      <div className="flex h-7 w-full overflow-hidden rounded-lg ring-1 ring-slate-200">
        <div className="flex items-center justify-center bg-slate-800" style={{ width: seg(h.p50) }} title={`Expected demand ${fmtGal(h.p50)}`} />
        <div className="flex items-center justify-center bg-cyan-500" style={{ width: seg(h.band_buffer) }} title={`Statistical buffer ${fmtGal(h.band_buffer)}`} />
        <div className="flex items-center justify-center bg-amber-500" style={{ width: seg(h.coil_buffer) }} title={`Overdue-burst buffer ${fmtGal(h.coil_buffer)}`} />
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-slate-500">
        <span className="flex items-center gap-1"><i className="inline-block h-2 w-2 rounded-sm bg-slate-800" /> Expected {fmtGal(h.p50)}</span>
        <span className="flex items-center gap-1"><i className="inline-block h-2 w-2 rounded-sm bg-cyan-500" /> Safety {fmtGal(h.band_buffer)}</span>
        <span className="flex items-center gap-1"><i className="inline-block h-2 w-2 rounded-sm bg-amber-500" /> Overdue coil {fmtGal(h.coil_buffer)}</span>
        <span className="ml-auto font-semibold text-slate-700">Stage ≈ {fmtGal(h.recommended_staging)}</span>
      </div>
    </div>
  );
}

function freqTone(label: string | null): string {
  if (!label) return "bg-slate-100 text-slate-500";
  if (label.startsWith("Steady")) return "bg-emerald-100 text-emerald-700";
  if (label.startsWith("Sporadic")) return "bg-rose-100 text-rose-700";
  if (label.includes("Intermittent") || label.startsWith("Rare")) return "bg-amber-100 text-amber-700";
  return "bg-slate-100 text-slate-600";
}

export default function Hedging({ summary, navigate }: { summary: Summary; navigate: (to: string) => void }) {
  const [terminal, setTerminal] = useState<string | null>(null);
  const [window, setWindow] = useState("all");
  const [serviceLevel, setServiceLevel] = useState(0.9);
  const [hIdx, setHIdx] = useState(0);

  const [data, setData] = useState<HedgingResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const firstLoad = useRef(true);

  useEffect(() => {
    if (!summary.connected) return;
    const handle = setTimeout(() => {
      setPending(true);
      setError(null);
      api.hedging
        .get({ terminal, window, serviceLevel })
        .then((d) => {
          setData(d);
          if (firstLoad.current) {
            if (!terminal && d.terminal) setTerminal(d.terminal);
            firstLoad.current = false;
          }
        })
        .catch((e) => setError(String(e)))
        .finally(() => setPending(false));
    }, 180);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summary.connected, terminal, window, serviceLevel]);

  if (!summary.connected) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-slate-500">
        Load a book in <button onClick={() => navigate("studio")} className="font-medium text-indigo-600 underline">Data Studio</button> to open Demand Hedging.
      </div>
    );
  }
  if (error) return <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700">{error}</div>;
  if (!data) return <div className="text-sm text-slate-500">Building the hedging readout…</div>;

  const h = data.horizons[hIdx] ?? data.horizons[0];

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold tracking-tight text-slate-900">Demand Hedging</h1>
          <p className="text-xs text-slate-500">
            Morning staging plan · {data.n_customers} accounts ·{" "}
            <span className="font-medium text-slate-700">{data.terminal ?? "all terminals"}</span>
            {" · "}as of {data.as_of} · Sat weight {data.saturday_weight}
            {pending && <span className="ml-2 text-slate-400">updating…</span>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {data.terminals.length > 0 && (
            <Seg options={data.terminals} value={data.terminal ?? ""} onChange={setTerminal} />
          )}
          <Seg options={Object.keys(WINDOW_LABEL)} value={window} onChange={setWindow} labels={WINDOW_LABEL} />
        </div>
      </div>

      {data.recency_note && (
        <div className="rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2 text-[11px] text-amber-800">
          {data.recency_note}
        </div>
      )}

      {/* The morning readout — the headline deliverable */}
      <div className="rounded-xl border border-slate-200 bg-gradient-to-br from-slate-900 to-slate-700 p-5 text-white shadow-sm">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-300">Morning readout · {data.terminal}</div>
        <p className="mt-1.5 text-[15px] leading-relaxed">{h.readout}</p>
        {!data.inventory_connected && data.inventory_note && (
          <p className="mt-2 text-[11px] text-slate-300">⚠ {data.inventory_note}</p>
        )}
      </div>

      {/* Service level + horizon controls */}
      <div className="flex flex-wrap items-center gap-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="min-w-[240px] flex-1">
          <div className="mb-1 flex items-baseline justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Service level</span>
            <span className="text-sm font-bold text-cyan-700">{Math.round(serviceLevel * 100)}%</span>
          </div>
          <input
            type="range" min={0.5} max={0.99} step={0.01} value={serviceLevel}
            onChange={(e) => setServiceLevel(Number(e.target.value))}
            className="w-full accent-cyan-600"
          />
          <div className="mt-0.5 text-[10px] text-slate-400">Cover this share of demand outcomes with staged product.</div>
        </div>
        <div>
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Horizon</div>
          <Seg
            options={data.horizons.map((_, i) => String(i))}
            value={String(hIdx)}
            onChange={(v) => setHIdx(Number(v))}
            labels={Object.fromEntries(data.horizons.map((hh, i) => [String(i), `${hh.horizon_working_days} working days`]))}
          />
        </div>
      </div>

      {/* Expected + staging cards per horizon */}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {data.horizons.map((hh, i) => (
          <Panel key={i} title={`Next ${hh.horizon_working_days} working days — by ${hh.by_date}`}>
            <div className="grid grid-cols-3 gap-2 text-center">
              <div>
                <div className="text-[10px] uppercase tracking-wide text-slate-400">Expected</div>
                <div className="text-xl font-bold text-slate-900">{fmtGal(hh.p50)}</div>
                <div className="text-[10px] text-slate-500">{fmtGal(hh.p10)}–{fmtGal(hh.p90)}</div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wide text-slate-400">Reliable floor</div>
                <div className="text-xl font-bold text-emerald-600">{fmtGal(hh.floor)}</div>
                <div className="text-[10px] text-slate-500">{hh.floor_share != null ? `${Math.round(hh.floor_share * 100)}% of demand` : "steady base"}</div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wide text-slate-400">Stage</div>
                <div className="text-xl font-bold text-cyan-700">{fmtGal(hh.recommended_staging)}</div>
                <div className="text-[10px] text-slate-500">{hh.buffer_elevated ? "buffer elevated" : "normal buffer"}</div>
              </div>
            </div>
            <div className="mt-3"><StagingBar h={hh} /></div>
          </Panel>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        {/* Dynamic buffer breakdown */}
        <Panel title={`Why the buffer — ${h.horizon_working_days} working days`}>
          <div className="space-y-2 text-xs">
            <Row label="Expected demand (P50)" value={fmtGal(h.p50)} />
            <Row label={`Statistical safety (${h.service_level}% @ z=${h.z})`} value={`+ ${fmtGal(h.band_buffer)}`} tone="text-cyan-700" />
            <Row label="Overdue-burst coil" value={`+ ${fmtGal(h.coil_buffer)}`} tone="text-amber-700" />
            <div className="my-1 border-t border-slate-100" />
            <Row label="Recommended staging" value={fmtGal(h.recommended_staging)} strong />
          </div>
          {h.overdue_drivers.length > 0 ? (
            <div className="mt-3 border-t border-slate-100 pt-2">
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">Overdue burst buyers driving the coil</div>
              <div className="space-y-1.5">
                {h.overdue_drivers.map((d) => (
                  <div key={d.customer_id} className="flex items-center justify-between gap-2 text-xs">
                    <span className="min-w-0 flex-1 truncate text-slate-700">{d.name}</span>
                    <span className="text-[10px] text-amber-600">{d.working_days_since_last}d silent · {d.overdue_ratio}× cadence</span>
                    <span className="w-14 text-right font-medium text-slate-800">+{fmtGal(d.coil_gallons)}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <p className="mt-3 text-[11px] text-slate-400">No overdue burst buyers right now — the buffer is purely statistical.</p>
          )}
        </Panel>

        {/* Risk watch-list */}
        <Panel title="Risk watch-list — who drives your uncertainty">
          <div className="space-y-1.5">
            {data.watch_list.map((w) => (
              <div key={w.customer_id} className="flex items-center gap-2 text-xs">
                <div className="h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-slate-100">
                  <div className="h-full bg-rose-400" style={{ width: `${Math.min(100, (w.variability_share ?? 0) * 100)}%` }} />
                </div>
                <span className="min-w-0 flex-1 truncate text-slate-700">{w.name}</span>
                {w.overdue && <span className="rounded bg-amber-100 px-1 text-[9px] font-semibold text-amber-700">overdue</span>}
                {w.single_lift_exceeds_buffer && <span className="rounded bg-rose-100 px-1 text-[9px] font-semibold text-rose-700" title="One load could exceed the buffer">⚠ 1-lift</span>}
                <span className="w-10 text-right font-medium text-slate-800">{Math.round((w.variability_share ?? 0) * 100)}%</span>
              </div>
            ))}
          </div>
          <p className="mt-2 text-[10px] text-slate-400">Ranked by contribution to demand variability, not volume — these make the buffer necessary.</p>
        </Panel>

        {/* Summary stats */}
        <Panel title="At a glance">
          <div className="space-y-2 text-xs">
            <Row label="Accounts at terminal" value={String(data.n_customers)} />
            <Row label="Expected (3 wd)" value={fmtGal(data.horizons[0]?.expected ?? 0)} />
            <Row label="Reliable floor (3 wd)" value={fmtGal(data.horizons[0]?.floor ?? 0)} tone="text-emerald-700" />
            <Row label="Volatile upside (3 wd)" value={fmtGal(data.horizons[0]?.upside ?? 0)} tone="text-amber-700" />
            <Row label="Saturday weight" value={String(data.saturday_weight)} />
            {data.inventory_connected && data.inventory && (
              <Row label="On-hand inventory" value={fmtGal(data.inventory.inventory)} />
            )}
          </div>
          <button
            onClick={() => navigate("calendar")}
            className="mt-3 w-full rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50"
          >
            View the working-day calendar →
          </button>
        </Panel>
      </div>

      {/* Operational customer view */}
      <Panel title="Operational customer view — what each account demands of your supply">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-200 text-[10px] uppercase tracking-wide text-slate-400">
                <th className="py-2 pr-3">Customer</th>
                <th className="px-2">Behavioral type</th>
                <th className="px-2 text-right">Cadence (wd)</th>
                <th className="px-2 text-right">Working days since</th>
                <th className="px-2 text-right">Typical load</th>
                <th className="px-2 text-right">Exp. {h.horizon_working_days}wd</th>
                <th className="px-2 text-right">Risk %</th>
                <th className="px-2 text-center">Flags</th>
              </tr>
            </thead>
            <tbody>
              {data.customers.map((c) => (
                <tr key={c.customer_id} className="border-b border-slate-50 hover:bg-slate-50/60">
                  <td className="py-1.5 pr-3 font-medium text-slate-800">{c.name}</td>
                  <td className="px-2">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${freqTone(c.behavior_label)}`}>{c.behavior_label ?? "—"}</span>
                  </td>
                  <td className="px-2 text-right text-slate-600">{c.cadence_working_days ?? "—"}</td>
                  <td className={`px-2 text-right ${c.overdue ? "font-semibold text-amber-700" : "text-slate-600"}`}>
                    {c.working_days_since_last ?? "—"}{c.overdue_ratio != null ? ` (${c.overdue_ratio}×)` : ""}
                  </td>
                  <td className="px-2 text-right text-slate-600">{fmtGal(c.typical_load)}</td>
                  <td className="px-2 text-right text-slate-700">{fmtGal(c.expected_primary_horizon)}</td>
                  <td className="px-2 text-right font-medium text-slate-800">{c.variability_share != null ? `${Math.round(c.variability_share * 100)}%` : "—"}</td>
                  <td className="px-2 text-center">
                    <span className="inline-flex gap-1">
                      {c.overdue && <span className="rounded bg-amber-100 px-1 text-[9px] font-semibold text-amber-700">overdue</span>}
                      {c.single_lift_exceeds_buffer && <span className="rounded bg-rose-100 px-1 text-[9px] font-semibold text-rose-700">⚠</span>}
                      {c.misleading_severity === "high" && <span className="rounded bg-indigo-100 px-1 text-[9px] font-semibold text-indigo-700" title="Daily average is misleading">avg⚠</span>}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function Row({ label, value, tone = "text-slate-800", strong = false }: {
  label: string; value: string; tone?: string; strong?: boolean;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-slate-500">{label}</span>
      <span className={`${strong ? "text-sm font-bold" : "font-medium"} ${tone}`}>{value}</span>
    </div>
  );
}
