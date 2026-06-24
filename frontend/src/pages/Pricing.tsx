import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { PricingResponse, Regime, RegimeConfig, Summary } from "../api/types";
import Panel from "../components/Panel";
import RegimeSelector from "../components/RegimeSelector";
import MarginCurveChart from "../components/pricing/MarginCurveChart";
import { ArchetypeTag } from "../lib/scoreui";

const WINDOW_LABEL: Record<string, string> = { all: "All-time", "365": "365d", "90": "90d" };

const usd = (v: number | null | undefined) =>
  v == null ? "—" : Math.abs(v) >= 1e6 ? `$${(v / 1e6).toFixed(2)}MM` : Math.abs(v) >= 1e3 ? `$${(v / 1e3).toFixed(0)}k` : `$${Math.round(v)}`;
const gal = (v: number | null | undefined) =>
  v == null ? "—" : Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(2)}MM` : Math.abs(v) >= 1e3 ? `${(v / 1e3).toFixed(0)}k` : `${Math.round(v)}`;
const cents = (s: number | null | undefined) => (s == null ? "—" : `${s > 0 ? "+" : ""}${(s * 100).toFixed(2)}¢`);
const price = (p: number | null | undefined) => (p == null ? "—" : `$${p.toFixed(3)}`);

const CLASS_TONE: Record<string, string> = {
  price_driven: "bg-rose-100 text-rose-700",
  captive: "bg-emerald-100 text-emerald-700",
  mixed: "bg-slate-100 text-slate-500",
};
const CLASS_LABEL: Record<string, string> = { price_driven: "price-driven", captive: "captive", mixed: "mixed" };

function Seg({ options, value, onChange, labels }: {
  options: string[]; value: string; onChange: (v: string) => void; labels?: Record<string, string>;
}) {
  return (
    <div className="flex flex-wrap gap-1 rounded-lg bg-slate-100 p-0.5 text-xs">
      {options.map((o) => (
        <button key={o} onClick={() => onChange(o)}
          className={`rounded-md px-2.5 py-1 font-medium ${o === value ? "bg-white text-slate-900 shadow-sm" : "text-slate-500"}`}>
          {labels?.[o] ?? o}
        </button>
      ))}
    </div>
  );
}

function Stat({ label, value, sub, tone = "text-slate-900" }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`mt-1 text-2xl font-bold ${tone}`}>{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

function LockState({ data }: { data: PricingResponse }) {
  const av = data.availability;
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center shadow-sm">
      <div className="text-3xl">🔒</div>
      <h2 className="mt-3 text-lg font-semibold text-slate-800">Pricing Sandbox &amp; Engine is locked</h2>
      <p className="mx-auto mt-1 max-w-md text-sm text-slate-500">{av.reason}</p>
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        {av.missing_fields.map((f) => (
          <span key={f} className="rounded-lg bg-amber-50 px-3 py-1.5 text-sm font-medium text-amber-700">
            Feed me <span className="font-mono">{f}</span>
          </span>
        ))}
      </div>
      <div className="mx-auto mt-5 flex max-w-md flex-wrap justify-center gap-2">
        {Object.entries(av.collecting).map(([k, c]) => (
          <span key={k} className={`rounded-lg px-3 py-1.5 text-xs font-medium ${c.matured ? "bg-emerald-50 text-emerald-700" : "bg-indigo-50 text-indigo-700"}`}>
            {c.matured ? "✓" : "collecting —"} {c.count}/{c.target} {c.unit} {k === "rack_benchmark" ? "rack benchmark" : "quotes"}
          </span>
        ))}
      </div>
      <p className="mx-auto mt-4 max-w-md text-xs text-slate-400">
        The sandbox needs realized price (unit_price) and the street/OPIS rack benchmark; the engine's
        acceptance model sharpens as the quote log accumulates rejections.
      </p>
    </div>
  );
}

export default function Pricing({ summary, navigate }: { summary: Summary; navigate: (to: string) => void }) {
  const [config, setConfig] = useState<RegimeConfig | null>(null);
  const [regime, setRegime] = useState<Regime | null>(null);
  const [terminal, setTerminal] = useState<string | null>(null);
  const [window, setWindow] = useState("all");
  const [data, setData] = useState<PricingResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);

  useEffect(() => {
    api.regimeConfig().then((c) => { setConfig(c); setRegime(c.default); }).catch((e) => setError(String(e)));
  }, []);

  const reload = useCallback(() => {
    if (!regime || !summary.connected) return;
    setPending(true);
    setError(null);
    api.pricing.get({ terminal, window, regime })
      .then((d) => { setData(d); setSelectedIdx(null); })
      .catch((e) => setError(String(e)))
      .finally(() => setPending(false));
  }, [regime, terminal, window, summary.connected]);
  useEffect(reload, [reload]);

  const sb = data?.sandbox ?? null;

  // Client-side aggregate over the toggled-in customers (book-level sensitivity, no refetch).
  const agg = useMemo(() => {
    if (!sb) return null;
    const incl = sb.customers.filter((c) => !excluded.has(c.customer_id));
    const grid = sb.grid;
    const margin = grid.map((_, i) => incl.reduce((a, c) => a + (c.margin_curve[i] ?? 0), 0));
    const volume = grid.map((_, i) => incl.reduce((a, c) => a + (c.volume_curve[i] ?? 0), 0));
    const realized = incl.reduce((a, c) => a + (c.margin_per_gal != null ? c.base_annual_gallons * c.margin_per_gal : 0), 0);
    let optIdx = 0;
    for (let i = 1; i < margin.length; i++) if (margin[i] > margin[optIdx]) optIdx = i;
    const curIdx = grid.reduce((best, s, i) => Math.abs(s - sb.current_spread) < Math.abs(grid[best] - sb.current_spread) ? i : best, 0);
    return { incl, grid, margin, volume, realized, optIdx, curIdx,
             curve: grid.map((s, i) => ({ spread: s, margin: margin[i] })) };
  }, [sb, excluded]);

  const selIdx = selectedIdx ?? agg?.optIdx ?? 0;

  if (!summary.connected) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-slate-500">
        Load a book in <button onClick={() => navigate("studio")} className="font-medium text-indigo-600 underline">Data Studio</button> to open the Pricing Sandbox.
      </div>
    );
  }
  if (error) return <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700">{error}</div>;
  if (!config || !regime || !data) return <div className="text-sm text-slate-500">Building the pricing model…</div>;

  const TerminalWindow = (
    <div className="flex flex-wrap items-center gap-2">
      {data.terminals.length > 0 && (
        <div className="flex gap-1 rounded-lg bg-slate-100 p-0.5 text-xs">
          <button onClick={() => setTerminal(null)} className={`rounded-md px-2.5 py-1 font-medium ${!terminal ? "bg-white text-slate-900 shadow-sm" : "text-slate-500"}`}>All</button>
          {data.terminals.map((t) => (
            <button key={t} onClick={() => setTerminal(t)} className={`rounded-md px-2.5 py-1 font-medium ${t === terminal ? "bg-white text-slate-900 shadow-sm" : "text-slate-500"}`}>{t}</button>
          ))}
        </div>
      )}
      <Seg options={Object.keys(WINDOW_LABEL)} value={window} onChange={setWindow} labels={WINDOW_LABEL} />
    </div>
  );

  const Header = (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-lg font-bold tracking-tight text-slate-900">Pricing Sandbox &amp; Engine</h1>
        <p className="text-xs text-slate-500">
          Rack-vs-street what-if + GP-maximizing quote recommendations ·{" "}
          <span className="font-medium text-slate-700">{terminal ?? "all terminals"}</span>
          {data.acceptance && <> · acceptance: {data.acceptance.source === "quote_model" ? "quote-log logistic" : "elasticity proxy"}</>}
          {data.as_of && <> · as of {data.as_of}</>}
          {pending && <span className="ml-2 text-slate-400">updating…</span>}
        </p>
      </div>
      {TerminalWindow}
    </div>
  );

  if (!data.available || !sb || !agg) {
    return <div className="space-y-5">{Header}<LockState data={data} /></div>;
  }

  const rec = data.recommendations!;
  const selMargin = agg.margin[selIdx];
  const optMargin = agg.margin[agg.optIdx];
  const uplift = optMargin - agg.realized;

  return (
    <div className="space-y-5">
      {Header}

      {/* ---- The Sandbox ---- */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Margin-maximizing post" value={cents(sb.grid[agg.optIdx])}
          sub={`${usd(optMargin)}/yr at the optimal book spread`} tone="text-emerald-600" />
        <Stat label="Uplift vs. current" value={usd(uplift)}
          sub={`current book spread ${cents(sb.current_spread)} → ${usd(agg.realized)}/yr`} tone={uplift >= 0 ? "text-emerald-600" : "text-rose-600"} />
        <Stat label="At selected spread" value={cents(sb.grid[selIdx])}
          sub={`${usd(selMargin)}/yr · ${gal(agg.volume[selIdx])} gal`} tone="text-indigo-700" />
        <Stat label="Elasticity mix" value={`${sb.n_price_driven} / ${sb.n_captive}`}
          sub={`price-driven / captive · ${agg.incl.length} accounts in`} />
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Panel title="Total margin vs. our rack-vs-street spread">
            <MarginCurveChart curve={agg.curve} optimalSpread={sb.grid[agg.optIdx]}
              currentSpread={sb.current_spread} selectedSpread={sb.grid[selIdx]} />
            <div className="mt-3 rounded-lg bg-slate-50 p-3">
              <div className="mb-1 flex items-baseline justify-between">
                <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Our rack vs. street</span>
                <span className="text-sm font-bold text-indigo-700">{cents(sb.grid[selIdx])}/gal</span>
              </div>
              <input type="range" min={0} max={sb.grid.length - 1} step={1} value={selIdx}
                onChange={(e) => setSelectedIdx(Number(e.target.value))} className="w-full accent-indigo-600" />
              <div className="mt-1 flex justify-between text-[10px] text-slate-400">
                <span>{cents(sb.grid[0])}</span>
                <button onClick={() => setSelectedIdx(agg.optIdx)} className="font-medium text-emerald-600 hover:underline">
                  jump to max-margin post ({cents(sb.grid[agg.optIdx])})
                </button>
                <span>{cents(sb.grid[sb.grid.length - 1])}</span>
              </div>
            </div>
            <p className="mt-2 text-[11px] text-slate-400">
              Each customer's expected volume shifts with the posted spread via its elasticity β (the
              quote-log acceptance model); total margin = Σ volume × (spread − cost). Toggle accounts
              below to see book-level sensitivity — the curve and the optimum re-solve instantly.
            </p>
          </Panel>
        </div>

        <Panel title="Acceptance model">
          {data.acceptance ? (
            <div className="space-y-2 text-xs">
              <div className="flex justify-between"><span className="text-slate-500">Source</span>
                <span className="font-semibold text-slate-800">{data.acceptance.source === "quote_model" ? "Quote-log logistic" : "Elasticity proxy"}</span></div>
              <div className="flex justify-between"><span className="text-slate-500">Quotes (accepts)</span>
                <span className="font-medium text-slate-700">{data.acceptance.n_quotes.toLocaleString()} ({data.acceptance.n_accept.toLocaleString()})</span></div>
              {data.acceptance.b_spread != null && (
                <div className="flex justify-between"><span className="text-slate-500">Spread coef (b)</span>
                  <span className={`font-medium ${data.acceptance.b_spread < 0 ? "text-emerald-600" : "text-rose-600"}`}>{data.acceptance.b_spread.toFixed(2)}/$</span></div>
              )}
              <div className="mt-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">Per-segment fits</div>
              <div className="max-h-40 space-y-1 overflow-auto">
                {Object.entries(data.acceptance.segments).map(([a, s]) => (
                  <div key={a} className="flex items-center justify-between rounded bg-slate-50 px-2 py-1">
                    <span className="truncate text-[11px] text-slate-600">{a}</span>
                    <span className="text-[10px] text-slate-400">n={s.n} · b={s.b_spread.toFixed(1)}</span>
                  </div>
                ))}
                {Object.keys(data.acceptance.segments).length === 0 && (
                  <div className="text-[11px] text-slate-400">Pooled model (thin per-segment data).</div>
                )}
              </div>
              <p className="mt-1 text-[10px] text-slate-400">
                P(accept) = logistic(a + b·spread + c·size + d·regime). A negative b means accepts fall as
                the quote climbs over the rack — the recoverable price elasticity.
              </p>
            </div>
          ) : <div className="text-xs text-slate-500">No acceptance model.</div>}
        </Panel>
      </div>

      <Panel title="Customer sensitivity — toggle accounts in / out">
        <div className="max-h-[24rem] overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-white text-left text-[10px] uppercase tracking-wide text-slate-400">
              <tr>
                <th className="pb-2">In</th>
                <th className="pb-2">Customer</th>
                <th className="pb-2">Class</th>
                <th className="pb-2 text-right">β</th>
                <th className="pb-2 text-right">Cur spread</th>
                <th className="pb-2 text-right">Vol @ sel</th>
                <th className="pb-2 text-right">Margin @ sel</th>
              </tr>
            </thead>
            <tbody>
              {sb.customers.map((c) => {
                const isIn = !excluded.has(c.customer_id);
                return (
                  <tr key={c.customer_id} className={`border-t border-slate-100 ${isIn ? "" : "opacity-40"}`}>
                    <td className="py-1.5">
                      <input type="checkbox" checked={isIn} className="accent-indigo-600"
                        onChange={() => setExcluded((prev) => {
                          const next = new Set(prev);
                          if (next.has(c.customer_id)) next.delete(c.customer_id); else next.add(c.customer_id);
                          return next;
                        })} />
                    </td>
                    <td className="py-1.5">
                      <div className="font-medium text-slate-700">{c.name}</div>
                      <div className="text-[10px] text-slate-400">{c.archetype} · {c.product ?? "—"} · {c.terminal ?? "—"}</div>
                    </td>
                    <td className="py-1.5">
                      <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${CLASS_TONE[c.elasticity_class]}`}>{CLASS_LABEL[c.elasticity_class]}</span>
                    </td>
                    <td className="py-1.5 text-right text-slate-600">{c.beta.toFixed(2)}</td>
                    <td className="py-1.5 text-right text-slate-600">{cents(c.current_spread)}</td>
                    <td className="py-1.5 text-right text-slate-600">{gal(c.volume_curve[selIdx])}</td>
                    <td className="py-1.5 text-right font-medium text-slate-700">{usd(c.margin_curve[selIdx])}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Panel>

      {/* ---- The Engine ---- */}
      <Panel title="Pricing engine — regime selector re-prices live">
        <RegimeSelector config={config} regime={regime} onChange={setRegime} compact />
      </Panel>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Current book GP" value={`${usd(rec.current_gp_per_yr)}/yr`} sub="at realized prices" />
        <Stat label="Optimized GP" value={`${usd(rec.optimized_gp_per_yr)}/yr`} sub="at recommended quotes" tone="text-emerald-600" />
        <Stat label="GP uplift" value={`${usd(rec.gp_uplift_per_yr)}/yr`} sub={`${rec.n_underpriced} underpriced accounts`} tone="text-emerald-600" />
        <Stat label="Shadow price" value={`${cents(rec.shadow_price)}/gal`}
          sub={rec.shadow_price > 0 ? "binding — no discounts below street" : "product not constrained"}
          tone={rec.shadow_price > 0 ? "text-rose-600" : "text-slate-900"} />
      </div>

      <Panel title="Today's pricing opportunities — underpriced vs. demonstrated willingness">
        {rec.top_underpriced.length === 0 ? (
          <div className="text-sm text-slate-500">No underpriced accounts under this regime — prices are at or above the GP-maximizing point.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-[10px] uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="pb-2">Customer</th>
                  <th className="pb-2">Class</th>
                  <th className="pb-2 text-right">Current → Recommended</th>
                  <th className="pb-2 text-right">Gap</th>
                  <th className="pb-2 text-right">P(accept)</th>
                  <th className="pb-2 text-right">+GP/yr</th>
                </tr>
              </thead>
              <tbody>
                {rec.top_underpriced.slice(0, 20).map((r) => (
                  <tr key={r.customer_id} className="border-t border-slate-100 hover:bg-slate-50">
                    <td className="py-1.5">
                      <div className="font-medium text-slate-700">{r.name}</div>
                      <div className="mt-0.5"><ArchetypeTag name={r.archetype} /></div>
                    </td>
                    <td className="py-1.5">
                      <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${CLASS_TONE[r.elasticity_class]}`}>{CLASS_LABEL[r.elasticity_class]}</span>
                    </td>
                    <td className="py-1.5 text-right text-slate-700">
                      <span className="text-slate-400">{price(r.current_price)}</span> → <span className="font-semibold">{price(r.recommended_price)}</span>
                    </td>
                    <td className="py-1.5 text-right font-medium text-emerald-700">+{(r.price_gap * 100).toFixed(2)}¢</td>
                    <td className="py-1.5 text-right text-slate-600">{(r.accept_prob * 100).toFixed(0)}%</td>
                    <td className="py-1.5 text-right font-semibold text-slate-800">{usd(r.gp_uplift)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-[11px] text-slate-400">
              Recommended price maximizes (price − cost) × expected gallons × P(accept | price, regime),
              floored by the shadow price of the binding constraint. "Underpriced" = the GP-maximizing
              quote sits above today's realized price (room to raise without losing the account).
            </p>
          </div>
        )}
      </Panel>
    </div>
  );
}
