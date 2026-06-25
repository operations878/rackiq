import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Summary, CalendarResponse, RhythmGroup } from "../api/types";
import Panel from "../components/Panel";

const DAY_TONE: Record<string, string> = {
  full: "bg-emerald-500",
  low: "bg-amber-500",
  nonlifting: "bg-slate-300",
};

function RhythmBars({ g }: { g: RhythmGroup }) {
  const max = Math.max(...g.by_weekday.map((d) => d.lift_share), 0.001);
  return (
    <div>
      <div className="flex items-end gap-2" style={{ height: 120 }}>
        {g.by_weekday.map((d) => (
          <div key={d.dow} className="flex flex-1 flex-col items-center justify-end">
            <span className="mb-1 text-[9px] text-slate-400">{Math.round(d.lift_share * 100)}%</span>
            <div
              className={`w-full rounded-t ${DAY_TONE[d.day_type]}`}
              style={{ height: `${(d.lift_share / max) * 90}%` }}
              title={`${d.weekday}: ${d.lifts} lifts · ${d.occurrences} days · activity index ${d.activity_index ?? "—"}`}
            />
            <span className="mt-1 text-[10px] font-medium text-slate-600">{d.weekday}</span>
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-slate-500">
        <span className="flex items-center gap-1"><i className="inline-block h-2 w-2 rounded-sm bg-emerald-500" /> Full working day (×1.0)</span>
        <span className="flex items-center gap-1"><i className="inline-block h-2 w-2 rounded-sm bg-amber-500" /> Low-activity (Sat ×{g.saturday_weight})</span>
        <span className="flex items-center gap-1"><i className="inline-block h-2 w-2 rounded-sm bg-slate-300" /> Non-lifting (excluded)</span>
      </div>
    </div>
  );
}

export default function Calendar({ summary, navigate }: { summary: Summary; navigate: (to: string) => void }) {
  const [data, setData] = useState<CalendarResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState<string>("network");

  useEffect(() => {
    if (!summary.connected) return;
    api.calendar().then(setData).catch((e) => setError(String(e)));
  }, [summary.connected]);

  if (!summary.connected) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-slate-500">
        Load a book in <button onClick={() => navigate("studio")} className="font-medium text-indigo-600 underline">Data Studio</button> to measure the working-day calendar.
      </div>
    );
  }
  if (error) return <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700">{error}</div>;
  if (!data) return <div className="text-sm text-slate-500">Measuring the day-of-week rhythm…</div>;
  if (!data.available || !data.network) {
    return <div className="rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center text-sm text-slate-500">No lifts to measure a rhythm from yet.</div>;
  }

  const scopes = ["network", ...data.terminal_names];
  const g: RhythmGroup = scope === "network" ? data.network : (data.terminals[scope] ?? data.network);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold tracking-tight text-slate-900">Working-Day Calendar</h1>
          <p className="text-xs text-slate-500">
            Day-type model learned from your book — Sundays &amp; US holidays excluded, Saturdays
            weighted by their real activity, Mon–Fri full. Today {data.today}.
          </p>
        </div>
        <div className="flex flex-wrap gap-1 rounded-lg bg-slate-100 p-0.5 text-xs">
          {scopes.map((s) => (
            <button key={s} onClick={() => setScope(s)}
              className={`rounded-md px-2.5 py-1 font-medium ${s === scope ? "bg-white text-slate-900 shadow-sm" : "text-slate-500"}`}>
              {s === "network" ? "All terminals" : s}
            </button>
          ))}
        </div>
      </div>

      {/* Saturday-weight strip per terminal */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {Object.entries(data.saturday_weights).map(([name, w]) => (
          <div key={name} className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">{name === "network" ? "Network" : name}</div>
            <div className="mt-1 text-2xl font-bold text-amber-600">{w == null ? "—" : `×${w}`}</div>
            <div className="text-[10px] text-slate-500">Saturday weight</div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Panel title={`Measured day-of-week rhythm — ${scope === "network" ? "all terminals" : scope}`}>
            <RhythmBars g={g} />
            <p className="mt-3 text-[11px] text-slate-400">
              Bars show each weekday's share of lifts over {g.first_lift} → {g.last_lift} ({g.n_lifts.toLocaleString()} lifts).
              The Saturday weight = its activity per occurrence ÷ a full weekday's
              ({g.saturday_measured ? "measured from data" : "default — too few Saturdays to measure"}).
              {g.exception_lifts > 0 && ` ${g.exception_lifts} lifts (${Math.round(g.exception_share * 100)}%) landed on Sundays/holidays — kept as volume but treated as exceptions, not counted against presence.`}
            </p>
          </Panel>
        </div>

        <div className="space-y-5">
          <Panel title="Upcoming non-lifting days (excluded)">
            {data.upcoming_exclusions.length === 0 ? (
              <div className="text-xs text-slate-500">No Sundays or holidays in the next three weeks.</div>
            ) : (
              <div className="space-y-1.5">
                {data.upcoming_exclusions.map((e) => (
                  <div key={e.date} className="flex items-center justify-between text-xs">
                    <span className="text-slate-700">{e.date} <span className="text-slate-400">({e.weekday})</span></span>
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">{e.reason}</span>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel title="Holidays in your data span">
            {data.holidays_in_span.length === 0 ? (
              <div className="text-xs text-slate-500">No holidays detected in the loaded span.</div>
            ) : (
              <div className="max-h-56 space-y-1 overflow-y-auto">
                {data.holidays_in_span.map((hd) => (
                  <div key={hd.date} className="flex items-center justify-between text-xs">
                    <span className="text-slate-500">{hd.date}</span>
                    <span className="text-slate-700">{hd.name}</span>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </div>
      </div>

      <p className="text-[11px] text-slate-400">
        This corrected calendar feeds presence/steadiness, cadence, "days since last lift", and the{" "}
        <button onClick={() => navigate("hedging")} className="font-medium text-indigo-600 underline">Demand Hedging</button>{" "}
        buffer — so a customer who lifts every weekday and skips weekends reads as fully steady, and a
        Fri→Mon gap isn't mistaken for a long silence.
      </p>
    </div>
  );
}
