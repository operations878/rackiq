import { useEffect, useMemo, useState, type ChangeEvent } from "react";
import { api } from "../api/client";
import type {
  Summary,
  VariabilityResponse,
  VariabilityValidation,
  VarCustomer,
  MismatchEntry,
} from "../api/types";
import Panel from "../components/Panel";
import { DefTip } from "../lib/varGlossary";

const QUAD_TONE: Record<string, string> = {
  metronome: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  predictable_timing: "bg-sky-50 text-sky-700 ring-sky-200",
  predictable_size: "bg-violet-50 text-violet-700 ring-violet-200",
  unpredictable: "bg-rose-50 text-rose-700 ring-rose-200",
  insufficient: "bg-slate-50 text-slate-400 ring-slate-200",
};
const CONF_TONE: Record<string, string> = {
  High: "bg-emerald-100 text-emerald-700",
  Medium: "bg-amber-100 text-amber-700",
  Low: "bg-rose-100 text-rose-700",
};
const QUAD_KEYS = ["all", "metronome", "predictable_timing", "predictable_size", "unpredictable"];
const fmtGal = (n: number | null | undefined) =>
  n == null ? "—" : `${Math.round(n).toLocaleString()} gal`;

function ChannelBadge({ ch }: { ch: VarCustomer["channel"] }) {
  if (!ch || !ch.recommended_channel)
    return <span className="text-xs text-slate-400">—</span>;
  const rack = ch.recommended_channel === "RACK";
  return (
    <DefTip k="channel">
      <span
        className={`rounded px-1.5 py-0.5 text-xs font-semibold ${
          rack ? "bg-emerald-100 text-emerald-700" : "bg-rose-100 text-rose-700"
        }`}
      >
        {ch.channel_label}
      </span>
    </DefTip>
  );
}

function ConfBadge({ tier, flag }: { tier?: string; flag?: string | null }) {
  if (!tier) return null;
  return (
    <DefTip k="confidence">
      <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${CONF_TONE[tier] ?? ""}`}>
        {tier}
        {flag ? " ⚠" : ""}
      </span>
    </DefTip>
  );
}

function Bar({ value, tone }: { value: number | null; tone: string }) {
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-16 rounded bg-slate-100">
        <div className={`h-2 rounded ${tone}`} style={{ width: `${value ?? 0}%` }} />
      </div>
      <span className="w-7 text-right text-xs tabular-nums text-slate-600">{value == null ? "—" : value}</span>
    </div>
  );
}

function Histogram({ title, hist, sub }: { title: string; hist: Record<string, number>; sub?: string }) {
  const max = Math.max(...Object.values(hist), 1);
  return (
    <div>
      <div className="text-sm font-medium text-slate-700">{title}</div>
      {sub && <div className="mb-1 text-xs text-slate-400">{sub}</div>}
      <div className="flex items-end gap-2" style={{ height: 80 }}>
        {Object.entries(hist).map(([bin, n]) => (
          <div key={bin} className="flex flex-1 flex-col items-center justify-end">
            <span className="mb-1 text-[10px] text-slate-500">{n}</span>
            <div className="w-full rounded-t bg-indigo-400" style={{ height: `${(n / max) * 60}%` }} />
            <span className="mt-1 text-[9px] text-slate-400">{bin}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function MismatchList({ title, tone, rows, dir }: {
  title: string; tone: string; rows: MismatchEntry[]; dir: string;
}) {
  return (
    <div>
      <div className={`text-sm font-semibold ${tone}`}>{title} ({rows.length})</div>
      {rows.length === 0 ? (
        <div className="mt-1 text-xs text-slate-400">None found.</div>
      ) : (
        <ul className="mt-1.5 space-y-1.5">
          {rows.slice(0, 8).map((m) => (
            <li key={m.customer_id} className="rounded border border-slate-100 px-2 py-1.5 text-xs">
              <div className="flex items-center justify-between">
                <span className="font-medium text-slate-700">{m.name}</span>
                <span className="flex items-center gap-1">
                  <span className="text-slate-400">{m.current} →</span>
                  <span className={dir === "up" ? "font-semibold text-emerald-700" : "font-semibold text-rose-700"}>
                    {m.channel_label}
                  </span>
                  <ConfBadge tier={m.confidence} flag={m.provisional ? "p" : null} />
                </span>
              </div>
              <div className="mt-0.5 text-slate-500">{m.reason}</div>
              <div className="mt-0.5 text-[10px] text-slate-400">
                {m.quadrant} · {m.n_lifts.toLocaleString()} lifts · {fmtGal(m.volume)}
                {m.strength === "soft" ? " · borderline" : ""}
              </div>
              {m.margin_note && <div className="mt-0.5 text-[10px] italic text-amber-600">{m.margin_note}</div>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function Variability({ summary, navigate }: { summary: Summary; navigate?: (k: string) => void }) {
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
    return <Panel title="Spot vs Rack — Customer Variability">No data — open Data Studio, or load the real book below.
      <div className="mt-3"><button onClick={loadSamples} className="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white">Load real book</button></div>
    </Panel>;

  const cs = data?.channel_summary;
  const mism = data?.mismatches;

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-800">Spot vs Rack — channel by variability</h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-500">
            Two independent things decide a customer's channel:{" "}
            <DefTip k="cadence"><b className="cursor-help text-slate-700 underline decoration-dotted">cadence consistency</b></DefTip>{" "}
            (how regularly they show up — a steady weekly lifter counts, daily not required) and{" "}
            <DefTip k="size"><b className="cursor-help text-slate-700 underline decoration-dotted">size consistency</b></DefTip>{" "}
            (how alike each load is). The 2×2 sets the{" "}
            <DefTip k="channel"><b className="cursor-help text-slate-700 underline decoration-dotted">channel</b></DefTip>{" "}
            (rack/term vs spot); <DefTip k="confidence"><b className="cursor-help text-slate-700 underline decoration-dotted">confidence</b></DefTip>{" "}
            flags how much history backs it; <DefTip k="margin_note"><b className="cursor-help text-slate-700 underline decoration-dotted">margin</b></DefTip>{" "}
            ranks the book but never moves a channel.
          </p>
        </div>
        {navigate && (
          <button onClick={() => navigate("glossary")} className="shrink-0 rounded border border-slate-300 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50">
            Definitions →
          </button>
        )}
      </div>

      {err && <div className="rounded bg-rose-50 px-3 py-2 text-sm text-rose-700">{err}</div>}
      {busy && <div className="rounded bg-indigo-50 px-3 py-2 text-sm text-indigo-700">{busy}</div>}

      {/* HEADLINE: current vs recommended channel mismatches */}
      {mism && (
        <Panel title={`Channel mismatches — current vs recommended (${mism.n_mismatches})`}>
          <p className="mb-3 max-w-3xl text-xs text-slate-500">
            <DefTip k="mismatch"><span className="cursor-help underline decoration-dotted">Where today's channel disagrees with the rec</span></DefTip>{" "}
            — steady accounts stuck on spot (upside) and erratic accounts over-committed (risk). Low-confidence recs are flagged ⚠.
          </p>
          <div className="grid gap-5 md:grid-cols-2">
            <MismatchList title="Stuck on spot — should be rack/term" tone="text-emerald-700"
              rows={mism.stuck_on_spot_should_be_rack} dir="up" />
            <MismatchList title="Term-committed — should be spot" tone="text-rose-700"
              rows={mism.committed_should_be_spot} dir="down" />
          </div>
          {mism.n_mismatches === 0 && (
            <div className="text-xs text-slate-400">
              No mismatches — either current channels align with the recs, or no deal-book (current-channel)
              data is loaded. Load the deal book to compare.
            </div>
          )}
        </Panel>
      )}

      {/* Channel + confidence summary */}
      {cs && (
        <div className="grid gap-3 sm:grid-cols-4">
          <Stat label="Rack / Term" value={cs.recommended.RACK ?? 0} tone="text-emerald-700" />
          <Stat label="Spot" value={cs.recommended.SPOT ?? 0} tone="text-rose-700" />
          <Stat label="High confidence" value={cs.by_confidence.High ?? 0} tone="text-slate-700" />
          <Stat label="Provisional (Low conf.)" value={cs.n_provisional} tone="text-amber-700" />
        </div>
      )}

      {/* the all-spot fix proof + axis spread */}
      {val?.available && (
        <Panel title="Validation — do the quadrants spread (no longer all-spot)?">
          {val.quadrant_spread && (
            <div className="mb-3 rounded bg-slate-50 px-3 py-2 text-xs text-slate-600">
              <b>{val.quadrant_spread.verdict}.</b>{" "}
              {val.quadrant_spread.n_quadrants_populated}/4 quadrants populated · spot share{" "}
              {Math.round((val.quadrant_spread.spot_share ?? 0) * 100)}%.
            </div>
          )}
          <div className="grid gap-6 md:grid-cols-2">
            <Histogram title="Cadence consistency (timing regularity)" hist={val.axis1_hist}
              sub={`std ${val.axis1_cadence.std} · ${val.axis1_cadence.spreads ? "spreads ✓" : "bunched ✗"}`} />
            <Histogram title="Size consistency (active-day loads)" hist={val.axis2_hist}
              sub={`std ${val.axis2_size.std} · ${val.axis2_size.spreads ? "spreads ✓" : "bunched ✗"}`} />
          </div>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            {Object.entries(val.quadrants).map(([q, n]) => (
              <DefTip key={q} k={q}>
                <span className={`cursor-help rounded px-2 py-1 ring-1 ${QUAD_TONE[q] ?? "bg-slate-50 ring-slate-200"}`}>{q}: <b>{n}</b></span>
              </DefTip>
            ))}
          </div>
          {val.margin_audit && (
            <div className="mt-3 text-xs text-slate-500">
              <b>Audit:</b> {val.margin_audit.verdict} · channel set by {val.margin_audit.channel_set_by} ·
              margin role: {val.margin_audit.margin_role}.
            </div>
          )}
          {val.weather && (
            <div className="mt-1 text-xs text-slate-500">
              <b>Weather:</b> {val.weather.available ? `${val.weather.n_adjusted} heating customer(s) size-adjusted on the HDD residual` : "model not built (no heating lifts)"}.
            </div>
          )}
        </Panel>
      )}

      {/* deal-book bridge (current-channel source) */}
      {val?.bridge && (
        <Panel title={`Deal-book → master bridge — ${val.bridge.match_rate_by_committed_volume}% of committed volume bridged (the current-channel source)`}>
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
              <div className="mb-1 text-xs text-slate-500">Candidate matches — confirm to attach current-channel context (never auto-merged):</div>
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
          {QUAD_KEYS.map((q) => (
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
                <th className="px-2"><DefTip k="cadence"><span className="cursor-help underline decoration-dotted">Cadence</span></DefTip></th>
                <th className="px-2"><DefTip k="size"><span className="cursor-help underline decoration-dotted">Size</span></DefTip></th>
                <th className="px-2"><DefTip k="quadrant"><span className="cursor-help underline decoration-dotted">Quadrant</span></DefTip></th>
                <th className="px-2"><DefTip k="channel"><span className="cursor-help underline decoration-dotted">Channel</span></DefTip></th>
                <th className="px-2"><DefTip k="confidence"><span className="cursor-help underline decoration-dotted">Conf.</span></DefTip></th>
                <th className="px-2"><DefTip k="current_channel"><span className="cursor-help underline decoration-dotted">Current</span></DefTip></th>
                <th className="px-2">Volume</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c: VarCustomer) => (
                <tr key={c.customer_id} className="border-b border-slate-50 align-top hover:bg-slate-50">
                  <td className="py-1.5 pr-3">
                    <div className="font-medium text-slate-700">{c.name}</div>
                    <div className="text-[11px] text-slate-400">
                      {c.n_lifts.toLocaleString()} lifts · {c.dominant_product ?? "—"}
                      {c.size_weather_adjusted ? (
                        <DefTip k="weather_adjust"><span className="cursor-help text-sky-500"> · weather-adj ❄</span></DefTip>
                      ) : c.weather_sensitive ? " · heating" : ""}
                    </div>
                  </td>
                  <td className="px-2"><Bar value={c.cadence_consistency} tone="bg-emerald-400" /></td>
                  <td className="px-2">
                    <Bar value={c.size_consistency} tone="bg-sky-400" />
                    {c.size_weather_adjusted && c.size_consistency_raw != null && (
                      <div className="text-[9px] text-slate-400">raw {c.size_consistency_raw} → {c.size_consistency}</div>
                    )}
                  </td>
                  <td className="px-2"><span className={`rounded px-1.5 py-0.5 text-xs ring-1 ${QUAD_TONE[c.quadrant] ?? ""}`}>{c.quadrant_label}</span></td>
                  <td className="px-2"><ChannelBadge ch={c.channel} /></td>
                  <td className="px-2"><ConfBadge tier={c.confidence?.tier} flag={c.confidence?.flag} /></td>
                  <td className="px-2 text-xs text-slate-500">
                    {c.channel?.current_channel_known ? c.channel.current_channel_label : <span className="text-slate-300">—</span>}
                    {c.channel?.mismatch && <span className="ml-1 text-amber-600" title={c.channel.mismatch_reason ?? ""}>⇄</span>}
                  </td>
                  <td className="px-2 tabular-nums text-slate-600">{fmtGal(c.total_net_gallons)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data && <div className="mt-2 text-xs text-slate-400">
          {data.coverage.pct_volume_scored}% of volume scored · as of {data.as_of} · margin is a ranking note only — it never moves a channel.
        </div>}
      </Panel>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className="rounded-lg border border-slate-200 px-3 py-2">
      <div className={`text-2xl font-semibold tabular-nums ${tone}`}>{value}</div>
      <div className="text-xs text-slate-500">{label}</div>
    </div>
  );
}
