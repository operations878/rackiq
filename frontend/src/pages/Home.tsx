/**
 * Home — the front door. One calm screen that orients in seconds: book status, a few headline
 * numbers (with the modeled-upside labelled), a client-side channel-mix read, four plain-English
 * doorways, and the data-connectivity panel (the one place to feed RackIQ and see it worked).
 * Orientation, not analysis.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { ProfileHome, ProfileCustomersResponse } from "../api/types";
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

/** Recommended channel mix by gallons, computed CLIENT-SIDE from the loaded customer facets. */
function ChannelMix({ data, navigate }: { data: ProfileCustomersResponse; navigate: (to: string) => void }) {
  let rack = 0, spot = 0, unrated = 0, mismatched = 0;
  for (const c of data.customers) {
    const g = c.total_net_gallons || 0;
    if (c.recommended_channel === "RACK") rack += g;
    else if (c.recommended_channel === "SPOT") spot += g;
    else unrated += g;
    if (c.mismatch) mismatched += 1;
  }
  const known = rack + spot;
  if (known <= 0) return null;
  const rackPct = Math.round((rack / known) * 100);
  const spotPct = 100 - rackPct;
  return (
    <Card className="p-5">
      <div className="flex items-baseline justify-between">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Channel mix — by gallons</div>
        {mismatched > 0 && (
          <button onClick={() => navigate("opportunity")}
            className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-800 hover:bg-amber-200">
            {mismatched} mismatched →
          </button>
        )}
      </div>
      <div className="mt-3 flex h-3 overflow-hidden rounded-full bg-slate-100">
        <div className="bg-indigo-500" style={{ width: `${rackPct}%` }} title={`Rack/term ${rackPct}%`} />
        <div className="bg-amber-400" style={{ width: `${spotPct}%` }} title={`Spot ${spotPct}%`} />
      </div>
      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-500">
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-indigo-500" /> Rack / term <b className="tnum text-slate-700">{rackPct}%</b> · {gal(rack)}</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-amber-400" /> Spot <b className="tnum text-slate-700">{spotPct}%</b> · {gal(spot)}</span>
        {unrated > 0 && <span className="flex items-center gap-1.5 text-slate-400"><span className="h-2 w-2 rounded-full bg-slate-300" /> Unrated · {gal(unrated)}</span>}
      </div>
      <p className="mt-2 text-[11px] text-slate-400">
        The channel each account <i>should</i> be on (from how steadily it buys).
        {data.deals_available ? " Mismatches compare it to the deal book." : " Load the deal book to compare against contract terms."}
      </p>
    </Card>
  );
}

export default function Home({ navigate }: { navigate: (to: string) => void }) {
  const [home, setHome] = useState<ProfileHome | null>(null);
  const [custs, setCusts] = useState<ProfileCustomersResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.profile.home().then(setHome).catch((e) => setError(String(e)));
    api.profile.customers().then(setCusts).catch(() => setCusts(null));
  }, []);
  useEffect(load, [load]);

  if (error) return <div className="text-sm text-rose-600">Could not load: {error}</div>;
  if (!home) return <LoadingHome />;

  const tiles = home.tiles;

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      {/* header */}
      <div className="riq-rise">
        <div className="text-[11px] font-semibold uppercase tracking-widest text-indigo-500">RackIQ · demand &amp; margin intelligence</div>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight text-slate-900">{home.company}</h1>
        <p className="mt-1 text-sm text-slate-500">
          {home.data_through
            ? <>Book current through <span className="font-medium text-slate-700">{fmtDate(home.data_through)}</span>. {home.freshness?.note}</>
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
                sub={muted ? (t.unavailable_note ?? "Connect more data to see this") : t.sub}
                tone={t.tone}
                muted={muted}
                modeled={t.modeled}
                onClick={() => navigate(t.route)}
              />
            );
          })}
        </div>
      )}

      {/* channel mix (client-side) */}
      {home.available && custs && custs.customers.length > 0 && <ChannelMix data={custs} navigate={navigate} />}

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

function LoadingHome() {
  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div className="space-y-2"><div className="h-8 w-64 animate-pulse rounded bg-slate-200" /><div className="h-4 w-96 animate-pulse rounded bg-slate-100" /></div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{[0, 1, 2].map((i) => <div key={i} className="h-32 animate-pulse rounded-xl bg-slate-100" />)}</div>
      <div className="h-40 animate-pulse rounded-xl bg-slate-100" />
    </div>
  );
}
