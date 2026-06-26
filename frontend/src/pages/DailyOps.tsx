import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { DailyResponse, DailyPanel, DailyRow, Regime, RegimeConfig, Summary } from "../api/types";
import Panel from "../components/Panel";
import RegimeSelector from "../components/RegimeSelector";
import { ArchetypeTag, DeltaPill } from "../lib/scoreui";

const WINDOW_LABEL: Record<string, string> = { "30": "30d", "90": "90d", "365": "365d", all: "All-time" };

const PANEL_ACCENT: Record<string, string> = {
  today_actions: "border-t-indigo-500",
  customer_rankings: "border-t-slate-400",
  inventory_actions: "border-t-violet-500",
  pricing_opportunities: "border-t-blue-500",
  credit_alerts: "border-t-red-500",
  churn_alerts: "border-t-rose-500",
  contract_candidates: "border-t-teal-500",
  discount_opportunities: "border-t-amber-500",
  strategic_accounts: "border-t-emerald-500",
};

function ScoreFlip({ row }: { row: DailyRow }) {
  return (
    <div className="flex shrink-0 items-center gap-1 text-[11px]">
      <span className="text-slate-400">{row.base_value}</span>
      <span className="text-slate-300">→</span>
      <span className="font-semibold text-slate-800">{row.regime_score ?? "—"}</span>
      <DeltaPill delta={row.regime_delta} />
    </div>
  );
}

function PanelCard({
  panel,
  onSelect,
}: {
  panel: DailyPanel;
  onSelect: (id: string) => void;
}) {
  return (
    <div className={`flex flex-col rounded-xl border border-slate-200 border-t-4 bg-white shadow-sm ${PANEL_ACCENT[panel.key] ?? "border-t-slate-300"}`}>
      <div className="border-b border-slate-100 px-4 py-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-800">{panel.label}</h3>
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500">
            {panel.total}
          </span>
        </div>
        <p className="mt-0.5 text-[11px] text-slate-400">{panel.description}</p>
      </div>
      <div className="flex-1 divide-y divide-slate-50">
        {panel.rows.length === 0 && (
          <div className="px-4 py-6 text-center text-[11px] text-slate-400">Nothing flagged in this regime.</div>
        )}
        {panel.rows.map((row, i) => (
          <button
            key={`${row.customer_id}-${i}`}
            onClick={() => onSelect(row.customer_id)}
            className="block w-full px-4 py-2.5 text-left hover:bg-slate-50"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="truncate text-[13px] font-medium text-slate-800">{row.name}</span>
                  {row.source && (
                    <span className="shrink-0 rounded bg-indigo-50 px-1 text-[9px] font-medium text-indigo-600">
                      {row.source}
                    </span>
                  )}
                </div>
                <div className="mt-0.5 text-[12px] font-medium text-slate-700">{row.action}</div>
                <div className="text-[11px] text-slate-500">{row.why_now}</div>
              </div>
              <ScoreFlip row={row} />
            </div>
            <div className="mt-1 flex items-center justify-between">
              <ArchetypeTag name={row.archetype} />
              <span className="text-[11px] font-semibold text-emerald-700">{row.expected_impact}</span>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

export default function DailyOps({
  summary,
  navigate,
}: {
  summary: Summary;
  navigate: (to: string) => void;
}) {
  const [config, setConfig] = useState<RegimeConfig | null>(null);
  const [regime, setRegime] = useState<Regime | null>(null);
  const [terminal, setTerminal] = useState<string | null>(null);
  const [window, setWindow] = useState("all");
  const [data, setData] = useState<DailyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [persisting, setPersisting] = useState(false);
  const [persisted, setPersisted] = useState<string | null>(null);

  useEffect(() => {
    api
      .regimeConfig()
      .then((c) => {
        setConfig(c);
        setRegime(c.default);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const reload = useCallback(() => {
    if (!regime) return;
    setError(null);
    api
      .daily(regime, terminal, window)
      .then((d) => {
        setData(d);
        if (!terminal && d.terminal) setTerminal(d.terminal);
      })
      .catch((e) => setError(String(e)));
  }, [regime, terminal, window]);

  useEffect(reload, [reload]);

  async function persist() {
    if (!regime) return;
    setPersisting(true);
    setPersisted(null);
    try {
      const r = await api.dailyPersist(regime, window);
      setPersisted(`Wrote ${r.rows_written} rows to daily_recommendations (${r.run_date}).`);
    } catch (e) {
      setError(String(e));
    } finally {
      setPersisting(false);
    }
  }

  if (!summary.connected) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-slate-500">
        Load a book in <button onClick={() => navigate("studio")} className="font-medium text-indigo-600 underline">Data Studio</button> to build today's worklist.
      </div>
    );
  }
  if (error) return <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700">{error}</div>;
  if (!config || !regime || !data) return <div className="text-sm text-slate-500">Building today's worklist…</div>;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold tracking-tight text-slate-900">Daily Operating Dashboard</h1>
          <p className="text-xs text-slate-500">
            Nine ranked worklists · regime <span className="font-medium text-slate-700">{data.regime_label}</span> · as of {data.as_of}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {data.terminals.length > 0 && (
            <div className="flex gap-1 rounded-lg bg-slate-100 p-0.5 text-xs">
              {data.terminals.map((t) => (
                <button
                  key={t}
                  onClick={() => setTerminal(t)}
                  className={`rounded-md px-2.5 py-1 font-medium ${t === data.terminal ? "bg-white text-slate-900 shadow-sm" : "text-slate-500"}`}
                >
                  {t}
                </button>
              ))}
            </div>
          )}
          <div className="flex gap-1 rounded-lg bg-slate-100 p-0.5 text-xs">
            {Object.keys(WINDOW_LABEL).map((w) => (
              <button
                key={w}
                onClick={() => setWindow(w)}
                className={`rounded-md px-2.5 py-1 font-medium ${w === window ? "bg-white text-slate-900 shadow-sm" : "text-slate-500"}`}
              >
                {WINDOW_LABEL[w]}
              </button>
            ))}
          </div>
        </div>
      </div>

      <Panel
        title="Regime selector — re-ranks everything live"
        right={
          <div className="flex items-center gap-2">
            {persisted && <span className="text-[11px] text-emerald-600">{persisted}</span>}
            <button
              onClick={persist}
              disabled={persisting}
              className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50"
            >
              {persisting ? "Writing…" : "Persist worklist (§14)"}
            </button>
          </div>
        }
      >
        <RegimeSelector config={config} regime={regime} onChange={setRegime} />
        <p className="mt-2 text-[11px] text-slate-400">
          Each customer's standing Base Value is multiplied by the V1 regime matrix
          (multiplier per archetype × regime state) and clamped 0–100 to get today's Regime Score.
          {" "}<span className="text-slate-500">{data.n_customers}</span> accounts at {data.terminal}.
        </p>
      </Panel>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {data.panels.map((p) => (
          <PanelCard key={p.key} panel={p} onSelect={(id) => navigate(`scorecard/${encodeURIComponent(id)}`)} />
        ))}
      </div>
    </div>
  );
}
