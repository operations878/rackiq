/**
 * Home — the front door. One calm, sparse screen that orients in seconds: book status, a few
 * headline numbers, the data-connectivity panel (the one place to feed RackIQ and see it worked),
 * and four plain-English doorways. Orientation, not analysis.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { ProfileHome } from "../api/types";
import { fmtDate } from "../lib/format";
import { Card, StatTile, gal, num } from "../lib/ui";
import SourceList from "../components/converge/SourceList";

function Doorway({ question, answer, onClick }: { question: string; answer: string; onClick: () => void }) {
  return (
    <Card hover onClick={onClick} className="group flex items-center justify-between gap-3 p-4">
      <div>
        <div className="text-sm font-semibold text-slate-800 group-hover:text-indigo-700">{question}</div>
        <div className="mt-0.5 text-xs text-slate-500">{answer}</div>
      </div>
      <span className="text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-indigo-500">→</span>
    </Card>
  );
}

export default function Home({ navigate }: { navigate: (to: string) => void }) {
  const [home, setHome] = useState<ProfileHome | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.profile.home().then(setHome).catch((e) => setError(String(e)));
  }, []);
  useEffect(load, [load]);

  if (error) return <div className="text-sm text-rose-600">Could not load: {error}</div>;
  if (!home) return <div className="text-sm text-slate-400">Loading…</div>;

  const tiles = home.tiles;

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      {/* header */}
      <div>
        <div className="text-[11px] font-semibold uppercase tracking-widest text-indigo-500">RackIQ</div>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight text-slate-900">{home.company}</h1>
        <p className="mt-1 text-sm text-slate-500">
          {home.data_through
            ? <>Book current through <span className="font-medium text-slate-700">{fmtDate(home.data_through)}</span>.</>
            : "No book loaded yet — connect your data below to begin."}
        </p>
      </div>

      {/* headline numbers */}
      {home.available && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {tiles.map((t) => {
            const muted = t.available === false;
            const value = t.format === "gal" ? gal(t.value) : num(t.value);
            return (
              <StatTile
                key={t.key}
                label={t.label}
                value={t.format === "gal" ? value.replace(" gal", "") : value}
                unit={t.format === "gal" ? "gal/yr" : t.unit}
                sub={muted ? "Connect the deal book to see this" : t.sub}
                tone={t.tone}
                muted={muted}
                onClick={() => navigate(t.route)}
              />
            );
          })}
        </div>
      )}

      {/* doorways */}
      <div>
        <h2 className="mb-3 text-sm font-semibold text-slate-700">What do you want to know?</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          {home.doorways.map((d) => (
            <Doorway key={d.key} question={d.question} answer={d.answer} onClick={() => navigate(d.route)} />
          ))}
        </div>
      </div>

      {/* data */}
      <div>
        <h2 className="mb-3 text-sm font-semibold text-slate-700">
          Your data — <span className="text-slate-500">{home.n_connected} of {home.n_total} connected</span>
        </h2>
        <SourceList
          sources={home.sources}
          nConnected={home.n_connected}
          nTotal={home.n_total}
          freshness={home.freshness}
          onChange={load}
          navigate={navigate}
        />
        <p className="mt-2 text-xs text-slate-400">
          This is the one place to get every source in and confirm it landed. Each connection lights up
          the views that depend on it.
        </p>
      </div>
    </div>
  );
}
