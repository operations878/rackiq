import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { Regime, RegimeConfig, Scorecard, ScorecardsResponse, Summary } from "../api/types";
import Panel from "../components/Panel";
import RegimeSelector from "../components/RegimeSelector";
import { ScorePill, Bar, ArchetypeTag } from "../lib/scoreui";
import { humanize } from "../lib/format";

const SUBSCORE_ORDER = [
  "volume_steadiness", "timing_steadiness", "price_sensitivity", "evr", "discount_efficiency",
  "market_sensitivity", "weather_sensitivity", "quote_score", "churn_risk",
];

function SubScores({ card }: { card: Scorecard }) {
  return (
    <div className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
      {SUBSCORE_ORDER.map((k) => {
        const s = card.subscores[k];
        if (!s) return null;
        const churn = k === "churn_risk";
        const collecting = s.collecting;
        return (
          <div key={k} className={s.available ? "" : "opacity-40"}>
            <div className="flex items-center justify-between text-[11px]">
              <span className="text-slate-600" title={s.reason}>{humanize(k)}</span>
              <span className="font-medium text-slate-700">{!s.available ? "—" : collecting ? "collecting" : s.value ?? "—"}</span>
            </div>
            <Bar value={s.available && !collecting ? s.value : 0} color={churn ? "bg-rose-500" : "bg-indigo-500"} />
          </div>
        );
      })}
    </div>
  );
}

function RegimeBreakdown({ card, config }: { card: Scorecard; config: RegimeConfig }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {Object.entries(card.regime_breakdown).map(([axis, m]) => {
        const tone = m > 1.02 ? "bg-emerald-50 text-emerald-700" : m < 0.98 ? "bg-rose-50 text-rose-700" : "bg-slate-100 text-slate-500";
        return (
          <span key={axis} className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${tone}`} title={config.axes[axis]?.label}>
            {config.axes[axis]?.label}: ×{m}
          </span>
        );
      })}
      <span className="rounded bg-slate-900 px-1.5 py-0.5 text-[10px] font-semibold text-white">net ×{card.regime_multiplier}</span>
    </div>
  );
}

function FullCard({ card, config }: { card: Scorecard; config: RegimeConfig }) {
  const a = card.archetype;
  const bv = card.base_value;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-bold text-slate-900">{card.name}</h3>
          <p className="text-[11px] text-slate-500">{card.home_terminal} · trend {card.trend_pct >= 0 ? "+" : ""}{card.trend_pct}% · recency {card.recency_gap}×</p>
          <div className="mt-1 flex items-center gap-2">
            <ArchetypeTag name={a.primary} secondary={a.secondary} />
            {a.ambiguous && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-700">ambiguous</span>}
            <span className="text-[10px] text-slate-400">conf {a.confidence}</span>
          </div>
        </div>
        <div className="flex items-center gap-4 text-sm">
          <div className="text-center"><div className="text-[10px] uppercase text-slate-400">VAR</div><ScorePill score={card.var.score} grade={card.var.grade} /></div>
          <div className="text-center"><div className="text-[10px] uppercase text-slate-400">Base value</div><ScorePill score={bv.score} grade={bv.grade} /></div>
          <div className="rounded-lg bg-indigo-600 px-3 py-1.5 text-center text-white">
            <div className="text-[9px] uppercase opacity-80">Regime score</div>
            <div className="text-lg font-bold leading-none">{card.regime_score ?? "—"}</div>
          </div>
        </div>
      </div>

      <RegimeBreakdown card={card} config={config} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Sub-scores</h4>
          <SubScores card={card} />
        </div>
        <div className="space-y-3">
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Why now</div>
            <p className="mt-0.5 text-[12px] text-slate-700">{card.why_now}</p>
            <div className="mt-2 text-[10px] font-semibold uppercase tracking-wide text-slate-500">Recommended action</div>
            <p className="mt-0.5 text-[13px] font-medium text-slate-800">{card.recommended_action}</p>
            <div className="mt-1 text-[11px] text-emerald-700">Expected impact: {card.expected_impact}</div>
          </div>
          <div className="grid grid-cols-1 gap-1 text-[11px] text-slate-600">
            <div><span className="text-slate-400">Pricing:</span> {a.posture?.pricing}</div>
            <div><span className="text-slate-400">Terms:</span> {a.posture?.terms}</div>
            <div><span className="text-slate-400">Allocation:</span> {a.posture?.allocation}</div>
          </div>
        </div>
      </div>

      <div className="rounded-lg border-2 border-dashed border-amber-200 bg-amber-50/60 p-3">
        <div className="text-[10px] font-semibold uppercase tracking-wide text-amber-700">Flip side — {card.flip.regime_label}</div>
        <p className="mt-1 text-[12px] text-slate-700">{card.flip.line}</p>
        <div className="mt-1 text-[11px] text-slate-500">
          Regime score {card.regime_score ?? "—"} → <span className="font-semibold text-slate-700">{card.flip.regime_score ?? "—"}</span>
          {card.flip.delta != null && <span className={`ml-1 ${card.flip.delta >= 0 ? "text-emerald-600" : "text-rose-600"}`}>({card.flip.delta >= 0 ? "+" : ""}{card.flip.delta})</span>}
          {" · "}then: {card.flip.action}
        </div>
      </div>
    </div>
  );
}

export default function Scorecards({ summary, customerId }: { summary: Summary; customerId?: string }) {
  const [config, setConfig] = useState<RegimeConfig | null>(null);
  const [regime, setRegime] = useState<Regime | null>(null);
  const [terminal, setTerminal] = useState<string | null>(null);
  const [data, setData] = useState<ScorecardsResponse | null>(null);
  const [selected, setSelected] = useState<string | null>(customerId ?? null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { api.regimeConfig().then((c) => { setConfig(c); setRegime(c.default); }).catch((e) => setError(String(e))); }, []);
  useEffect(() => { if (customerId) setSelected(customerId); }, [customerId]);

  const reload = useCallback(() => {
    if (!regime) return;
    setError(null);
    api.scorecards(regime, terminal).then((d) => {
      setData(d);
      setSelected((cur) => cur ?? d.cards[0]?.customer_id ?? null);
    }).catch((e) => setError(String(e)));
  }, [regime, terminal]);
  useEffect(reload, [reload]);

  if (!summary.connected) return <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-slate-500">Load a book to see customer scorecards.</div>;
  if (error) return <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700">{error}</div>;
  if (!config || !regime || !data) return <div className="text-sm text-slate-500">Building scorecards…</div>;

  const card = data.cards.find((c) => c.customer_id === selected) ?? data.cards[0];

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold tracking-tight text-slate-900">Customer Scorecards</h1>
          <p className="text-xs text-slate-500">
            One-page per customer · regime <span className="font-medium text-slate-700">{data.regime_label}</span> · {data.archetypes_present.length} archetypes present
          </p>
        </div>
        {data.terminals.length > 0 && (
          <div className="flex gap-1 rounded-lg bg-slate-100 p-0.5 text-xs">
            <button onClick={() => setTerminal(null)} className={`rounded-md px-2.5 py-1 font-medium ${!terminal ? "bg-white text-slate-900 shadow-sm" : "text-slate-500"}`}>All</button>
            {data.terminals.map((t) => (
              <button key={t} onClick={() => setTerminal(t)} className={`rounded-md px-2.5 py-1 font-medium ${t === terminal ? "bg-white text-slate-900 shadow-sm" : "text-slate-500"}`}>{t}</button>
            ))}
          </div>
        )}
      </div>

      <Panel title="Regime selector — scores & actions flip live">
        <RegimeSelector config={config} regime={regime} onChange={setRegime} compact />
      </Panel>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-4">
        <section className="xl:col-span-1">
          <Panel title={`Accounts (${data.cards.length})`}>
            <div className="max-h-[40rem] space-y-1 overflow-auto">
              {data.cards.map((c) => (
                <button key={c.customer_id} onClick={() => setSelected(c.customer_id)}
                  className={`flex w-full items-center justify-between rounded-lg px-2 py-1.5 text-left text-[12px] hover:bg-slate-50 ${selected === c.customer_id ? "bg-indigo-50" : ""}`}>
                  <span className="min-w-0">
                    <span className="block truncate font-medium text-slate-700">{c.name}</span>
                    <span className="text-[10px] text-slate-400">{c.archetype.primary}</span>
                  </span>
                  <span className="ml-2 shrink-0 font-semibold text-indigo-600">{c.regime_score ?? "—"}</span>
                </button>
              ))}
            </div>
          </Panel>
        </section>
        <section className="xl:col-span-3">
          <Panel title="Scorecard">
            {card ? <FullCard card={card} config={config} /> : <div className="text-sm text-slate-500">Select an account.</div>}
          </Panel>
          <div className="mt-5">
            <Panel title="Archetype coverage — one exemplar per archetype present">
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {data.exemplars.map((c) => (
                  <button key={c.customer_id} onClick={() => setSelected(c.customer_id)}
                    className="rounded-lg border border-slate-200 p-2 text-left hover:border-indigo-300 hover:bg-slate-50">
                    <ArchetypeTag name={c.archetype.primary} />
                    <div className="mt-1 truncate text-[12px] font-medium text-slate-700">{c.name}</div>
                    <div className="text-[10px] text-slate-500">Base {c.base_value.score} → regime <b className="text-indigo-600">{c.regime_score ?? "—"}</b></div>
                  </button>
                ))}
              </div>
            </Panel>
          </div>
        </section>
      </div>
    </div>
  );
}
