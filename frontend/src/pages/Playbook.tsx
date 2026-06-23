import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { PlaybookResponse, Summary } from "../api/types";
import Panel from "../components/Panel";
import { ArchetypeTag } from "../lib/scoreui";

function Play({ label, value }: { label: string; value?: string }) {
  if (!value) return null;
  const danger = label === "What NOT to do";
  return (
    <div className="text-[12px]">
      <span className={`font-semibold ${danger ? "text-rose-600" : "text-slate-600"}`}>{label}: </span>
      <span className="text-slate-700">{value}</span>
    </div>
  );
}

export default function Playbook({ summary }: { summary: Summary }) {
  const [data, setData] = useState<PlaybookResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [onlyPresent, setOnlyPresent] = useState(true);

  useEffect(() => { api.playbook().then(setData).catch((e) => setError(String(e))); }, []);

  if (error) return <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700">{error}</div>;
  if (!data) return <div className="text-sm text-slate-500">Loading playbook…</div>;

  const archetypes = onlyPresent ? data.archetypes.filter((a) => a.present) : data.archetypes;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold tracking-tight text-slate-900">Sales Playbook</h1>
          <p className="text-xs text-slate-500">Per-archetype plays · regime cheat-sheets · the morning routine</p>
        </div>
        {summary.connected && (
          <label className="flex items-center gap-1.5 text-xs text-slate-600">
            <input type="checkbox" checked={onlyPresent} onChange={(e) => setOnlyPresent(e.target.checked)} />
            Only archetypes in this book ({data.present_archetypes.length})
          </label>
        )}
      </div>

      <Panel title="Morning routine — work the day in six moves">
        <ol className="space-y-2">
          {data.morning_routine.map((s, i) => (
            <li key={i} className="flex gap-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-indigo-600 text-[11px] font-bold text-white">{i + 1}</span>
              <div className="text-[12px]"><b className="text-slate-800">{s.step}.</b> <span className="text-slate-600">{s.detail}</span></div>
            </li>
          ))}
        </ol>
      </Panel>

      <Panel title="Regime cheat-sheets — when X, do Y">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {data.regime_cheatsheet.map((axis) => (
            <div key={axis.axis}>
              <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">{axis.label}</h3>
              <div className="space-y-2">
                {axis.states.map((st) => (
                  <div key={st.state} className="rounded-lg border border-slate-200 p-2">
                    <div className="text-[12px] font-semibold text-slate-800">{st.label} <span className="font-normal text-slate-400">— {st.hint}</span></div>
                    {st.do && <div className="mt-0.5 text-[11px] text-emerald-700"><b>Do:</b> {st.do}</div>}
                    {st.dont && st.dont !== "—" && <div className="text-[11px] text-rose-600"><b>Don't:</b> {st.dont}</div>}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title={`Archetype plays (${archetypes.length})`}>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {archetypes.map((a) => (
            <div key={a.archetype} className="rounded-xl border border-slate-200 p-4">
              <div className="mb-2 flex items-center justify-between">
                <ArchetypeTag name={a.archetype} />
                {!a.present && <span className="text-[10px] text-slate-400">not in current book</span>}
              </div>
              <div className="space-y-1.5">
                <Play label="What to say" value={a.play.say} />
                <Play label="When to call" value={a.play.call_when} />
                <Play label="What to quote" value={a.play.quote} />
                <Play label="Terms to require" value={a.play.terms} />
                <Play label="What NOT to do" value={a.play.avoid} />
              </div>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
