/**
 * "Your data — N of M connected" — the single place to feed RackIQ and SEE that it worked.
 * Every ingestible source is a row: connected (count + through-date + match rate) or not-uploaded
 * (highlighted, with what it unlocks AND what it costs while dark). Upload / re-upload on every row,
 * with HONEST post-upload feedback (rows landed, idempotency, what just unlocked) read straight off
 * the format-aware parser's response. Reuses the existing upload endpoints; BOLs hand off to Studio.
 */
import { useRef, useState } from "react";
import { api } from "../../api/client";
import type { ProfileSource, ProfileFreshness } from "../../api/types";
import { fmtDate } from "../../lib/format";

function StateBadge({ connected }: { connected: boolean }) {
  return connected ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-semibold text-emerald-800">
      <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> Connected
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-800">
      <span className="h-1.5 w-1.5 rounded-full bg-amber-500" /> Not uploaded
    </span>
  );
}

type Res = Record<string, unknown>;
async function runUpload(action: string, file: File): Promise<Res> {
  if (action === "deals") return api.deals.upload(file);
  if (action === "prices") return api.margin.upload(file, "prices");
  if (action === "trips") return api.margin.upload(file, "trips");
  if (action === "weather") return api.weather.hddUpload(file);
  return {};
}
async function runLoadSamples(action: string): Promise<Res> {
  if (action === "deals") return api.deals.loadSamples();
  if (action === "prices" || action === "trips") return api.margin.loadSamples();
  if (action === "weather") return api.weather.hddLoadSamples();
  return {};
}

/** Honest one-line summary of what landed — read off the parser's own response. */
function uploadSummary(action: string, res: Res): string {
  const n = (v: unknown) => (typeof v === "number" ? v.toLocaleString() : "0");
  const stores = (res.stores ?? {}) as Record<string, unknown>;
  if (action === "deals") {
    const br = res.bridge as { match_rate_by_committed_volume?: number } | undefined;
    return `${n(res.written)} deal rows (${res.source ?? "auto"})` +
      (br?.match_rate_by_committed_volume != null ? ` · ${Math.round(br.match_rate_by_committed_volume)}% of committed volume bridged to a customer` : "");
  }
  if (action === "prices") {
    return `${n(stores.price_grid_rows)} sell-grid rows · ${n(stores.landed_cost_trips)} barge trip legs`;
  }
  if (action === "trips") {
    return `${n(stores.landed_cost_trips)} trip legs (barrels → gallons ×42 applied once)`;
  }
  if (action === "weather") {
    const st = (stores.stations as string[]) ?? [];
    return `${n(stores.hdd_observations ?? res.observations_written)} HDD days` + (st.length ? ` · ${st.join(", ")}` : "");
  }
  return "Loaded.";
}

function SourceRow({ s, onChange, navigate }: {
  s: ProfileSource; onChange: () => void; navigate?: (to: string) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function handleFile(f: File | undefined) {
    if (!f || !s.upload_action) return;
    setBusy(true); setErr(null); setFeedback(null);
    try { const res = await runUpload(s.upload_action, f); setFeedback(uploadSummary(s.upload_action, res)); onChange(); }
    catch (e) { setErr(String(e instanceof Error ? e.message : e)); }
    finally { setBusy(false); }
  }
  async function handleSamples() {
    if (!s.upload_action) return;
    setBusy(true); setErr(null); setFeedback(null);
    try { const res = await runLoadSamples(s.upload_action); setFeedback(uploadSummary(s.upload_action, res)); onChange(); }
    catch { setErr("No bundled file in this environment — upload your own."); }
    finally { setBusy(false); }
  }

  return (
    <div className={`px-4 py-3.5 transition ${s.connected ? "" : "bg-amber-50/40"}`}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {/* identity + state */}
        <div className="min-w-[15rem] flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-slate-800">{s.label}</span>
            <StateBadge connected={s.connected} />
            {s.primary && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-slate-500">spine</span>}
          </div>
          {s.connected ? (
            <div className="tnum mt-0.5 text-xs text-slate-500">
              {s.count.toLocaleString()} {s.unit}
              {s.through ? ` · through ${fmtDate(s.through)}` : ""}
              {s.match_rate != null ? ` · ${Math.round(s.match_rate)}% ${s.match_label ?? ""}` : ""}
            </div>
          ) : (
            <div className="mt-0.5 space-y-0.5 text-xs">
              <div className="text-slate-500"><span className="font-medium text-slate-600">Unlocks:</span> {s.unlocks}</div>
              {s.cost_when_dark && (
                <div className="text-amber-700"><span className="font-semibold">While it's dark:</span> {s.cost_when_dark}</div>
              )}
            </div>
          )}
        </div>

        {/* action */}
        <div className="flex items-center gap-2">
          {s.upload_route === "studio" ? (
            <button
              onClick={() => navigate?.(s.upload_route!)}
              className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:border-indigo-400 hover:text-indigo-600">
              {s.connected ? "Add more in Data Studio" : "Open Data Studio"}
            </button>
          ) : (
            <>
              <input ref={fileRef} type="file" className="hidden"
                accept=".csv,.tsv,.xlsx,.xls,.xlsm"
                onChange={(e) => handleFile(e.target.files?.[0])} />
              <button disabled={busy} onClick={() => fileRef.current?.click()}
                className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                  s.connected
                    ? "border border-slate-300 text-slate-700 hover:border-indigo-400 hover:text-indigo-600"
                    : "bg-indigo-600 text-white hover:bg-indigo-700"} disabled:opacity-50`}>
                {busy ? "Working…" : s.connected ? "Re-upload" : "Upload"}
              </button>
              <button disabled={busy} onClick={handleSamples} title="Load the bundled sample/real book if present"
                className="rounded-lg px-2 py-1.5 text-xs font-medium text-slate-400 transition hover:text-indigo-600 disabled:opacity-50">
                samples
              </button>
            </>
          )}
        </div>
      </div>

      {/* honest post-upload feedback */}
      {feedback && (
        <div className="riq-rise mt-2 rounded-lg border border-emerald-200 bg-emerald-50/70 px-3 py-2 text-xs text-emerald-800">
          <div className="font-medium">✓ Landed — {feedback}</div>
          <div className="mt-0.5 text-[11px] text-emerald-700/80">
            Re-uploading updates rows in place (idempotent — never duplicates). Now powers: {s.unlocks}.
          </div>
        </div>
      )}
      {err && <div className="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-600">{err}</div>}
    </div>
  );
}

export default function SourceList({ sources, nConnected, nTotal, onChange, navigate, compact, freshness }: {
  sources: ProfileSource[]; nConnected: number; nTotal: number;
  onChange: () => void; navigate?: (to: string) => void; compact?: boolean; freshness?: ProfileFreshness;
}) {
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-700">Your data</h2>
          {!compact && (
            <p className="text-[11px] text-slate-400">
              Everything RackIQ can read. Connect a source and the views that need it light up; leave one
              dark and the views that lean on it say so.
            </p>
          )}
        </div>
        <span className="tnum rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-600">
          {nConnected} of {nTotal} connected
        </span>
      </div>
      {freshness && (
        <div className="border-b border-slate-100 bg-slate-50/60 px-4 py-2 text-[11px] text-slate-500">
          <span className="font-medium text-slate-600">Freshness:</span>{" "}
          {freshness.last_upload_at ? <>last upload {fmtDate(freshness.last_upload_at)}. </> : "no uploads yet. "}
          {freshness.note}
        </div>
      )}
      <div className="divide-y divide-slate-100">
        {sources.map((s) => (
          <SourceRow key={s.key} s={s} onChange={onChange} navigate={navigate} />
        ))}
      </div>
    </div>
  );
}
