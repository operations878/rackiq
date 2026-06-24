import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Summary, DemandCockpit as Cockpit } from "../api/types";
import Panel from "../components/Panel";
import DemandForecastChart from "../components/demand/DemandForecastChart";
import BurnDownChart from "../components/demand/BurnDownChart";
import { fmtGal } from "../lib/scoreui";

const WINDOW_LABEL: Record<string, string> = { all: "All-time", "365": "365d", "90": "90d" };
const METHOD_LABEL: Record<string, string> = {
  holt_winters_seasonal: "Holt-Winters (seasonal)",
  holt_linear: "Holt-Winters (trend)",
  seasonal_naive: "Seasonal-naive",
  flat: "Flat level",
};
const ALL_PRODUCTS = "(all)";

function Seg({ options, value, onChange, labels }: {
  options: string[]; value: string; onChange: (v: string) => void; labels?: Record<string, string>;
}) {
  return (
    <div className="flex flex-wrap gap-1 rounded-lg bg-slate-100 p-0.5 text-xs">
      {options.map((o) => (
        <button
          key={o}
          onClick={() => onChange(o)}
          className={`rounded-md px-2.5 py-1 font-medium ${o === value ? "bg-white text-slate-900 shadow-sm" : "text-slate-500"}`}
        >
          {labels?.[o] ?? o}
        </button>
      ))}
    </div>
  );
}

function Stat({ label, value, sub, tone = "text-slate-900" }: {
  label: string; value: string; sub?: string; tone?: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`mt-1 text-2xl font-bold ${tone}`}>{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

function coverTone(days: number | null): string {
  if (days == null) return "text-slate-400";
  if (days <= 7) return "text-red-600";
  if (days <= 14) return "text-amber-600";
  return "text-emerald-600";
}

export default function DemandCockpit({ summary, navigate }: { summary: Summary; navigate: (to: string) => void }) {
  const [terminal, setTerminal] = useState<string | null>(null);
  const [product, setProduct] = useState<string>(ALL_PRODUCTS);
  const [window, setWindow] = useState("all");
  const [serviceLevel, setServiceLevel] = useState(0.95);
  const [leadTime, setLeadTime] = useState(5);
  const [lotSize, setLotSize] = useState<number | "">("");

  const [data, setData] = useState<Cockpit | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [persisted, setPersisted] = useState<string | null>(null);
  const [persisting, setPersisting] = useState(false);
  const firstLoad = useRef(true);

  useEffect(() => {
    if (!summary.connected) return;
    const handle = setTimeout(() => {
      setPending(true);
      setError(null);
      api.demand
        .cockpit({
          terminal,
          product: product === ALL_PRODUCTS ? null : product,
          window,
          serviceLevel,
          leadTimeDays: leadTime,
          lotSize: lotSize === "" ? null : Number(lotSize),
        })
        .then((d) => {
          setData(d);
          if (firstLoad.current) {
            if (!terminal && d.terminal) setTerminal(d.terminal);
            firstLoad.current = false;
          }
        })
        .catch((e) => setError(String(e)))
        .finally(() => setPending(false));
    }, 180); // debounce so dragging the slider doesn't spam the API
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summary.connected, terminal, product, window, serviceLevel, leadTime, lotSize]);

  async function persist() {
    setPersisting(true);
    setPersisted(null);
    try {
      const r = await api.demand.persist(window);
      setPersisted(`Persisted ${r.terminal_rows} terminal + ${r.customer_rows} customer rows.`);
    } catch (e) {
      setError(String(e));
    } finally {
      setPersisting(false);
    }
  }

  if (!summary.connected) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-slate-500">
        Load a book in <button onClick={() => navigate("studio")} className="font-medium text-indigo-600 underline">Data Studio</button> to open the Demand Cockpit.
      </div>
    );
  }
  if (error) return <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700">{error}</div>;
  if (!data) return <div className="text-sm text-slate-500">Building the demand forecast…</div>;

  const coverOn = data.availability.inventory_cover.available;
  const rec = data.recommendation;
  const acc = data.accuracy;
  const next = data.forecast[0];

  return (
    <div className="space-y-5">
      {/* Header + selectors */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold tracking-tight text-slate-900">Demand Cockpit</h1>
          <p className="text-xs text-slate-500">
            Per-terminal {data.grain} forecast · {data.n_customers} accounts ·{" "}
            <span className="font-medium text-slate-700">{data.terminal ?? "all lifts"}</span>
            {" · "}{data.product}{" · "}as of {data.as_of}
            {pending && <span className="ml-2 text-slate-400">updating…</span>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {data.terminals.length > 0 && (
            <Seg options={data.terminals} value={data.terminal ?? ""} onChange={setTerminal} />
          )}
          {data.products.length > 0 && (
            <Seg options={[ALL_PRODUCTS, ...data.products]} value={product} onChange={setProduct} />
          )}
          <Seg options={Object.keys(WINDOW_LABEL)} value={window} onChange={setWindow} labels={WINDOW_LABEL} />
        </div>
      </div>

      {/* Stat strip */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Days of cover"
          value={coverOn && data.days_of_cover != null ? `${data.days_of_cover}` : "—"}
          sub={coverOn ? (data.inventory ? `${fmtGal(data.inventory.inventory)} on hand · heel ${fmtGal(data.inventory.min_heel)}` : "") : "needs inventory + tank_capacity"}
          tone={coverTone(coverOn ? data.days_of_cover : null)}
        />
        <Stat
          label="Next week (P50)"
          value={next ? fmtGal(next.p50) : "—"}
          sub={next ? `P10 ${fmtGal(next.p10)} · P90 ${fmtGal(next.p90)}` : ""}
        />
        <Stat
          label="Forecast MAPE"
          value={acc.mape != null ? `${acc.mape}%` : "—"}
          sub={acc.bias != null ? `bias ${acc.bias > 0 ? "+" : ""}${acc.bias}% · ${acc.method ? METHOD_LABEL[acc.method] ?? acc.method : ""}` : "recent one-step backtest"}
          tone={acc.mape == null ? "text-slate-400" : acc.mape <= 20 ? "text-emerald-600" : acc.mape <= 40 ? "text-amber-600" : "text-red-600"}
        />
        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">Persist forecast</div>
          <button
            onClick={persist}
            disabled={persisting}
            className="mt-1.5 w-full rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            {persisting ? "Writing…" : "Persist (P6/P7/P10)"}
          </button>
          <div className="mt-1 text-[10px] text-slate-400">{persisted ?? "writes the forecast distributions"}</div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        {/* Left: charts */}
        <div className="space-y-5 lg:col-span-2">
          <Panel title="Demand forecast — P10 / P50 / P90 (VAR-weighted rollup)">
            <DemandForecastChart history={data.history} forecast={data.forecast} />
            <p className="mt-2 text-[11px] text-slate-400">
              Per-customer Holt-Winters / seasonal-naive forecasts, summed to the terminal. The band
              is derived from historical one-step forecast error and widened for erratic (low-VAR)
              accounts.
            </p>
          </Panel>

          <Panel title="Inventory burn-down vs. tank">
            {coverOn && data.burndown ? (
              <>
                <BurnDownChart burndown={data.burndown} />
                <p className="mt-2 text-[11px] text-slate-400">
                  Book inventory projected at the P50 demand rate, with a fast (P90) / slow (P10)
                  cone against the min-heel floor. {data.burndown.breach_day != null
                    ? `Fast-demand path risks the heel in ~${data.burndown.breach_day} days.`
                    : "No heel risk within the horizon."}
                </p>
              </>
            ) : (
              <div className="rounded-lg border-2 border-dashed border-amber-200 bg-amber-50/60 p-4 text-sm text-amber-800">
                {data.availability.inventory_cover.reason} Map <code>inventory_snapshot</code>,{" "}
                <code>tank_capacity</code>, and <code>min_heel</code> in{" "}
                <button onClick={() => navigate("studio")} className="font-medium underline">Data Studio</button>{" "}
                to see days-of-cover and the burn-down.
              </div>
            )}
          </Panel>
        </div>

        {/* Right: recommendation + accuracy + accounts */}
        <div className="space-y-5">
          <Panel title="Recommended action">
            <div className="space-y-3">
              <div className="rounded-lg bg-slate-50 p-3">
                <div className="mb-2 flex items-baseline justify-between">
                  <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Service level</span>
                  <span className="text-sm font-bold text-cyan-700">{Math.round(serviceLevel * 100)}%</span>
                </div>
                <input
                  type="range" min={0.5} max={0.99} step={0.01} value={serviceLevel}
                  onChange={(e) => setServiceLevel(Number(e.target.value))}
                  className="w-full accent-cyan-600"
                />
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <label className="text-[11px] text-slate-500">
                    Lead time (days)
                    <input
                      type="number" min={0} value={leadTime}
                      onChange={(e) => setLeadTime(Math.max(0, Number(e.target.value)))}
                      className="mt-0.5 w-full rounded border border-slate-300 px-2 py-1 text-xs text-slate-800"
                    />
                  </label>
                  <label className="text-[11px] text-slate-500">
                    Lot size (gal)
                    <input
                      type="number" min={0} placeholder="optional" value={lotSize}
                      onChange={(e) => setLotSize(e.target.value === "" ? "" : Math.max(0, Number(e.target.value)))}
                      className="mt-0.5 w-full rounded border border-slate-300 px-2 py-1 text-xs text-slate-800"
                    />
                  </label>
                </div>
              </div>

              {rec && (
                <>
                  <p className="text-sm leading-relaxed text-slate-800">{rec.headline}</p>
                  {rec.mode === "buy" && (
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <Mini label="Buy qty" value={fmtGal(rec.buy_quantity ?? null)} strong />
                      <Mini label="By date" value={rec.buy_by_date ?? "—"} strong />
                      <Mini label="Safety stock" value={fmtGal(rec.safety_stock)} />
                      <Mini label="Reorder point" value={fmtGal(rec.reorder_point_above_heel)} />
                      <Mini label="Daily demand" value={`${fmtGal(rec.daily_demand_p50)}/d`} />
                      <Mini label="± daily σ" value={fmtGal(rec.daily_demand_sigma)} />
                    </div>
                  )}
                  {rec.mode === "target_only" && (
                    <div className="space-y-2">
                      <div className="grid grid-cols-2 gap-2 text-xs">
                        <Mini label="Target carry" value={fmtGal(rec.target_inventory ?? null)} strong />
                        <Mini label="Safety stock" value={fmtGal(rec.safety_stock)} />
                      </div>
                      <div className="rounded-lg border border-amber-200 bg-amber-50/60 p-2 text-[11px] text-amber-800">
                        {rec.gap_note}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          </Panel>

          <Panel title="Forecast accuracy">
            {acc.mape == null ? (
              <div className="text-xs text-slate-500">Not enough history for a backtest yet.</div>
            ) : (
              <div className="space-y-2 text-xs">
                <div className="flex justify-between"><span className="text-slate-500">Recent MAPE</span><span className="font-semibold text-slate-800">{acc.mape}% ({acc.n} wks)</span></div>
                <div className="flex justify-between"><span className="text-slate-500">Bias</span><span className={`font-semibold ${Math.abs(acc.bias ?? 0) < 10 ? "text-emerald-600" : "text-amber-600"}`}>{(acc.bias ?? 0) > 0 ? "+" : ""}{acc.bias}%</span></div>
                {Object.keys(acc.by_method).length > 0 && (
                  <div className="mt-2 border-t border-slate-100 pt-2">
                    <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">vs. baselines (lower is better)</div>
                    {Object.entries(acc.by_method).map(([m, v]) => (
                      <div key={m} className="flex items-center justify-between py-0.5">
                        <span className="text-slate-500">{m === "model" ? "Selected model" : m === "seasonal_naive" ? "Seasonal-naive" : "Naive (last)"}</span>
                        <span className={`font-medium ${m === "model" ? "text-cyan-700" : "text-slate-600"}`}>{v}%</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </Panel>

          <Panel title="Top accounts (next-week P50)">
            <div className="space-y-1.5">
              {data.customer_forecasts.slice(0, 8).map((c) => (
                <div key={c.customer_id} className="flex items-center justify-between gap-2 text-xs">
                  <span className="min-w-0 flex-1 truncate text-slate-700">{c.name}</span>
                  <span className="text-[10px] text-slate-400">{METHOD_LABEL[c.method]?.split(" ")[0] ?? c.method}</span>
                  <span className="w-16 text-right font-medium text-slate-800">{fmtGal(c.next_p50)}</span>
                </div>
              ))}
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}

function Mini({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="rounded-lg bg-slate-50 p-2">
      <div className="text-[10px] uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`mt-0.5 ${strong ? "text-sm font-bold text-slate-900" : "text-xs font-medium text-slate-700"}`}>{value}</div>
    </div>
  );
}
