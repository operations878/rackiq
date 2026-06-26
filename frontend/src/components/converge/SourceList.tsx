/**
 * "Your data — N of M connected" — the single place to feed RackIQ and SEE that it worked.
 * Every ingestible source is a row: connected (count + through-date + match rate) or not-uploaded
 * (highlighted, with what it unlocks) and an upload / re-upload control on every row. Reuses the
 * existing upload endpoints; the BOL wizard hands off to Data Studio.
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

async function runUpload(action: string, file: File): Promise<void> {
  if (action === "deals") await api.deals.upload(file);
  else if (action === "prices") await api.margin.upload(file, "prices");
  else if (action === "trips") await api.margin.upload(file, "trips");
  else if (action === "weather") await api.weather.hddUpload(file);
}
async function runLoadSamples(action: string): Promise<void> {
  if (action === "deals") await api.deals.loadSamples();
  else if (action === "prices" || action === "trips") await api.margin.loadSamples();
  else if (action === "weather") await api.weather.hddLoadSamples();
}

function SourceRow({ s, onChange, navigate }: {
  s: ProfileSource; onChange: () => void; navigate?: (to: string) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function handleFile(f: File | undefined) {
    if (!f || !s.upload_action) return;
    setBusy(true); setErr(null); setMsg(null);
    try { await runUpload(s.upload_action, f); setMsg("Uploaded ✓"); onChange(); }
    catch (e) { setErr(String(e instanceof Error ? e.message : e)); }
    finally { setBusy(false); }
  }
  async function handleSamples() {
    if (!s.upload_action) return;
    setBusy(true); setErr(null); setMsg(null);
    try { await runLoadSamples(s.upload_action); setMsg("Loaded ✓"); onChange(); }
    catch (e) { setErr("No bundled file in this environment — upload your own."); void e; }
    finally { setBusy(false); }
  }

  return (
    <div className={`flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3.5 ${
      s.connected ? "" : "bg-amber-50/40"}`}>
      {/* identity + state */}
      <div className="min-w-[15rem] flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-slate-800">{s.label}</span>
          <StateBadge connected={s.connected} />
        </div>
        {s.connected ? (
          <div className="mt-0.5 text-xs text-slate-500">
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
        {msg && <span className="text-xs font-medium text-emerald-600">{msg}</span>}
        {err && <span className="max-w-[14rem] text-xs text-rose-500">{err}</span>}
        {s.upload_route === "studio" ? (
          <button
            onClick={() => navigate?.(s.upload_route!)}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:border-indigo-400 hover:text-indigo-600">
            {s.connected ? "Add more in Data Studio" : "Open Data Studio"}
          </button>
        ) : (
          <>
            <input ref={fileRef} type="file" className="hidden"
              accept=".csv,.tsv,.xlsx,.xls"
              onChange={(e) => handleFile(e.target.files?.[0])} />
            <button disabled={busy} onClick={() => fileRef.current?.click()}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium ${
                s.connected
                  ? "border border-slate-300 text-slate-700 hover:border-indigo-400 hover:text-indigo-600"
                  : "bg-indigo-600 text-white hover:bg-indigo-700"} disabled:opacity-50`}>
              {busy ? "Working…" : s.connected ? "Re-upload" : "Upload"}
            </button>
            <button disabled={busy} onClick={handleSamples} title="Load the bundled sample/real book if present"
              className="rounded-lg px-2 py-1.5 text-xs font-medium text-slate-400 hover:text-indigo-600 disabled:opacity-50">
              samples
            </button>
          </>
        )}
      </div>
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
        <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-600">
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
