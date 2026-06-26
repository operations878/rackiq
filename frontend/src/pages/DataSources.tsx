/**
 * Data — the single front door for getting everything in and SEEING that it worked. The full
 * source-connectivity list (with per-row upload / re-upload), plus links to the deep Data Studio
 * mapping wizard (for BOLs) and the standing Data Health page.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { ProfileHome } from "../api/types";
import { PageHeader, Card } from "../lib/ui";
import SourceList from "../components/converge/SourceList";

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
      <SourceList
        sources={home.sources}
        nConnected={home.n_connected}
        nTotal={home.n_total}
        freshness={home.freshness}
        onChange={load}
        navigate={navigate}
      />
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
