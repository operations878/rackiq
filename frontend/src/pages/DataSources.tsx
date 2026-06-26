/**
 * Data — the single front door for getting everything in and SEEING that it worked. A one-click
 * "Load the sample book" fast path (demo insurance, clearly distinct from real uploads), the full
 * source-connectivity list (per-row upload / re-upload with honest feedback + what-it-unlocks /
 * what-it-costs-dark), and links to the deep Data Studio mapping wizard (BOLs) and Data Health.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { ProfileHome } from "../api/types";
import { PageHeader, Card, gal } from "../lib/ui";
import SourceList from "../components/converge/SourceList";

function SampleBook({ onLoaded }: { onLoaded: () => void }) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [extras, setExtras] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    setBusy(true); setErr(null); setDone(null); setExtras([]);
    try {
      const st = await api.studio.loadDemo("full");
      // best-effort: also light the deal / price / weather facets if bundled files exist locally
      // (a no-op on the cloud demo — those sources then degrade honestly).
      const got: string[] = [];
      await Promise.allSettled([
        api.deals.loadSamples().then(() => got.push("deal book")).catch(() => {}),
        api.margin.loadSamples().then(() => got.push("price & cost grid")).catch(() => {}),
        api.weather.hddLoadSamples().then(() => got.push("weather")).catch(() => {}),
      ]);
      const s = st.summary;
      setExtras(got);
      setDone(`Synthetic Soundview book — ${s.customers} customers · ${s.lifts.toLocaleString()} lifts · ${gal(s.total_net_gallons ?? 0)} across ${(s.terminals ?? []).join(", ")}.`);
      onLoaded();
    } catch (e) { setErr(String(e)); }
    finally { setBusy(false); }
  }

  return (
    <Card className="border-indigo-200 bg-gradient-to-br from-indigo-50/70 to-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-xl">
          <div className="flex items-center gap-2">
            <span className="rounded bg-indigo-600 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-white">Demo</span>
            <h2 className="text-sm font-semibold text-slate-800">Load the sample book</h2>
          </div>
          <p className="mt-1 text-xs leading-relaxed text-slate-500">
            One click loads a full synthetic Soundview book — enough to light steadiness, margin (estimated),
            modeled opportunity and days-of-cover end-to-end. Separate from your real uploads below; safe to
            run anytime to get a fully-populated screen.
          </p>
        </div>
        <button disabled={busy} onClick={load}
          className="shrink-0 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-indigo-700 disabled:opacity-50">
          {busy ? "Loading…" : "Load sample book"}
        </button>
      </div>
      {done && (
        <div className="riq-rise mt-3 rounded-lg border border-emerald-200 bg-emerald-50/70 px-3 py-2 text-xs text-emerald-800">
          <div className="font-medium">✓ {done}</div>
          <div className="mt-0.5 text-[11px] text-emerald-700/80">
            {extras.length
              ? `Also lit the ${extras.join(", ")} from bundled files.`
              : "Deal book / price grid / weather aren't bundled here, so channel-vs-contract and weather-adjusted reads stay dark (honestly) — upload them below to light those too."}
          </div>
        </div>
      )}
      {err && <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-600">{err}</div>}
    </Card>
  );
}

export default function DataSources({ navigate }: { navigate: (to: string) => void }) {
  const [home, setHome] = useState<ProfileHome | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => { api.profile.home().then(setHome).catch((e) => setError(String(e))); }, []);
  useEffect(load, [load]);

  if (error) return <div className="text-sm text-rose-600">Could not load: {error}</div>;
  if (!home) return <div className="text-sm text-slate-400">Loading…</div>;

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <PageHeader
        title="Data"
        subtitle="Connect every source here and confirm it landed. Each connection lights up the views that depend on it — nothing else to configure."
      />

      <SampleBook onLoaded={load} />

      <div>
        <h2 className="mb-2 text-sm font-semibold text-slate-700">
          Your real sources — <span className="text-slate-500">{home.n_connected} of {home.n_total} connected</span>
        </h2>
        <SourceList
          sources={home.sources}
          nConnected={home.n_connected}
          nTotal={home.n_total}
          freshness={home.freshness}
          onChange={load}
          navigate={navigate}
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Card hover onClick={() => navigate("studio")} className="flex items-center justify-between p-4">
          <div>
            <div className="text-sm font-semibold text-slate-800">Data Studio</div>
            <div className="mt-0.5 text-xs text-slate-500">Map columns, clean and commit a new file (BOLs, invoices, more).</div>
          </div>
          <span className="text-slate-300">→</span>
        </Card>
        <Card hover onClick={() => navigate("health")} className="flex items-center justify-between p-4">
          <div>
            <div className="text-sm font-semibold text-slate-800">Data Health</div>
            <div className="mt-0.5 text-xs text-slate-500">Quality score, drift alerts, quarantine and the customer name map.</div>
          </div>
          <span className="text-slate-300">→</span>
        </Card>
      </div>
    </div>
  );
}
