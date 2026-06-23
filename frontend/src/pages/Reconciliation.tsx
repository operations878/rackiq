import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { Reconciliation as Recon, ReconTank, Summary } from "../api/types";
import Panel from "../components/Panel";
import MechanismBar from "../components/reconciliation/MechanismBar";
import ControlChart from "../components/reconciliation/ControlChart";
import LossTrendChart from "../components/reconciliation/LossTrendChart";
import { humanize } from "../lib/format";

const PERIODS: Record<string, string> = { month: "Monthly", week: "Weekly" };

function gal(v: number | null | undefined): string {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e6) return `${(v / 1e6).toFixed(2)} MM`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
  return `${Math.round(v)}`;
}
const usd = (v: number | null | undefined) => (v == null ? "—" : `$${Math.round(v).toLocaleString()}`);
const pct = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(2)}%`);

function Kpi({ label, value, sub, tone = "slate" }: { label: string; value: string; sub?: string; tone?: string }) {
  const toneCls = { rose: "text-rose-600", emerald: "text-emerald-600", indigo: "text-indigo-700", slate: "text-slate-800" }[tone] ?? "text-slate-800";
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`mt-1 text-xl font-bold ${toneCls}`}>{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

const MECH_TONE: Record<string, string> = {
  measurement: "bg-indigo-100 text-indigo-700",
  physical: "bg-rose-100 text-rose-700",
};
function TrendArrow({ trend }: { trend: string }) {
  if (trend === "rising") return <span title="rising" className="text-rose-600">↑</span>;
  if (trend === "falling") return <span title="falling" className="text-emerald-600">↓</span>;
  return <span title="flat" className="text-slate-400">→</span>;
}

function LockState({ data }: { data: Recon }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center shadow-sm">
      <div className="text-3xl">🔒</div>
      <h2 className="mt-3 text-lg font-semibold text-slate-800">Reconciliation &amp; Loss Control is locked</h2>
      <p className="mx-auto mt-1 max-w-md text-sm text-slate-500">{data.reason}</p>
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        {(data.missing_fields ?? []).map((f) => (
          <span key={f} className="rounded-lg bg-amber-50 px-3 py-1.5 text-sm font-medium text-amber-700">
            Feed me <span className="font-mono">{f}</span>
          </span>
        ))}
      </div>
      <p className="mx-auto mt-4 max-w-md text-xs text-slate-400">
        Book-vs-physical gain/loss runs on physically-gauged inventory plus receipt detail; BOL
        compartment rows enable the net-recon cross-check and the meter-drift control charts.
      </p>
    </div>
  );
}

function TankDrill({ tank, ucl, center }: { tank: ReconTank; ucl: number; center: number }) {
  const c = tank.control;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 className="text-base font-bold text-slate-900">
            {tank.tank_id} · {tank.product}
            {c.persistent_out && (
              <span className="ml-2 rounded bg-rose-100 px-1.5 py-0.5 text-[10px] font-semibold text-rose-700">out of control</span>
            )}
          </h3>
          <p className="text-[11px] text-slate-500">{tank.vs_network}</p>
        </div>
        <div className="flex items-center gap-4 text-sm">
          <div className="text-center"><div className="text-[10px] uppercase text-slate-400">Loss</div><span className="font-semibold text-rose-600">{pct(tank.loss_pct)}</span></div>
          <div className="text-center"><div className="text-[10px] uppercase text-slate-400">$/yr</div><span className="font-semibold text-slate-800">{usd(tank.dollar_loss_per_yr)}</span></div>
          <div className="text-center"><div className="text-[10px] uppercase text-slate-400">Severity</div><span className="font-semibold text-slate-800">{c.severity}σ</span></div>
        </div>
      </div>
      <ControlChart series={tank.series} ucl={ucl} center={center} />
      <div>
        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Loss-mechanism split</div>
        <MechanismBar mech={tank.mechanism} />
        <p className="mt-1.5 text-[11px] text-slate-400">
          Measurement + Physical = net loss; Temperature is the gross-vs-net thermal bridge (vanishes under VCF correction).
          Recoverable ≈ {usd(tank.recoverable_dollar_per_yr)}/yr at {usd(tank.unit_cost)}·/gal.
        </p>
      </div>
    </div>
  );
}

export default function Reconciliation({ summary, navigate }: { summary: Summary; navigate: (to: string) => void }) {
  const [period, setPeriod] = useState("month");
  const [data, setData] = useState<Recon | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    setError(null);
    api.reconciliation
      .get(period)
      .then((r) => {
        setData(r);
        setSelected((cur) => cur ?? r.tanks[0]?.tank_id ?? null);
      })
      .catch((e) => setError(String(e)));
  }, [period]);

  useEffect(reload, [reload]);

  if (!summary.connected) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-slate-500">
        Load a book in Data Studio to run reconciliation. <button onClick={() => navigate("studio")} className="ml-1 underline">Open Data Studio →</button>
      </div>
    );
  }
  if (error) return <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700">{error}</div>;
  if (!data) return <div className="text-sm text-slate-500">Reconciling book vs physical…</div>;

  const PeriodToggle = (
    <div className="flex gap-1 rounded-lg bg-slate-100 p-0.5 text-xs">
      {Object.entries(PERIODS).map(([k, label]) => (
        <button key={k} onClick={() => setPeriod(k)} className={`rounded-md px-2.5 py-1 font-medium ${k === period ? "bg-white text-slate-900 shadow-sm" : "text-slate-500"}`}>{label}</button>
      ))}
    </div>
  );

  if (!data.available) {
    return (
      <div className="space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-bold tracking-tight text-slate-900">Reconciliation &amp; Loss Control</h1>
            <p className="text-xs text-slate-500">Book-vs-physical gain/loss · loss-mechanism split · meter drift · dollarized loss</p>
          </div>
          {PeriodToggle}
        </div>
        <LockState data={data} />
      </div>
    );
  }

  const net = data.network!;
  const selectedTank = data.tanks.find((t) => t.tank_id === selected) ?? data.tanks[0] ?? null;
  const mech = net.mechanism;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold tracking-tight text-slate-900">Reconciliation &amp; Loss Control</h1>
          <p className="text-xs text-slate-500">
            {net.n_tanks} tanks · {net.n_bols.toLocaleString()} BOLs reconciled · {net.horizon_days}-day horizon · as of {data.as_of}
            {!data.has_bol && <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-amber-700">no BOL detail — disbursements from lifts</span>}
          </p>
        </div>
        {PeriodToggle}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Kpi label="Net loss" value={`${gal(net.net_loss_gal)} gal`} sub={pct(net.loss_pct) + " of throughput"} tone="rose" />
        <Kpi label="Gross gap" value={`${gal(net.gross_loss_gal)} gal`} sub="incl. temperature" />
        <Kpi label="Loss value" value={`${usd(net.dollar_loss_per_yr)}/yr`} tone="rose" />
        <Kpi label="Recoverable" value={`${usd(net.recoverable_dollar_per_yr)}/yr`} sub="above routine shrink" tone="emerald" />
        <Kpi label="Throughput" value={`${gal(net.throughput_gal)} gal`} />
        <Kpi label="Out of control" value={`${data.meter_drift.n_out_of_control}`} sub={`of ${net.n_tanks} tanks`} tone="indigo" />
      </div>

      {mech && (
        <Panel title="Loss-mechanism decomposition (network)">
          <MechanismBar mech={mech} />
          <p className="mt-2 text-[11px] text-slate-500">
            Of the gross book-to-physical gap, <b className="text-amber-700">{mech.temperature_pct}%</b> is temperature/volumetric
            (benign — vanishes under VCF), <b className="text-indigo-700">{mech.measurement_pct}%</b> is measurement (meter drift /
            gauging — the net-recon cross-check), and <b className="text-rose-700">{mech.physical_pct}%</b> is physical
            (evaporation / line-fill / theft). Measurement + Physical is the real net loss to chase.
          </p>
        </Panel>
      )}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-5">
        <section className="xl:col-span-3">
          <Panel title="Worst offenders — by recoverable loss value">
            <div className="max-h-[26rem] overflow-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-white text-left text-[10px] uppercase tracking-wide text-slate-400">
                  <tr>
                    <th className="pb-2">Tank · Product</th>
                    <th className="pb-2 text-right">Loss %</th>
                    <th className="pb-2 text-right">$/yr</th>
                    <th className="pb-2">Driver</th>
                    <th className="pb-2">Split</th>
                  </tr>
                </thead>
                <tbody>
                  {data.tanks.map((t) => (
                    <tr key={t.tank_id} onClick={() => setSelected(t.tank_id)}
                      className={`cursor-pointer border-t border-slate-100 hover:bg-slate-50 ${selected === t.tank_id ? "bg-indigo-50" : ""}`}>
                      <td className="py-1.5">
                        <div className="font-medium text-slate-700">{t.tank_id}</div>
                        <div className="text-[11px] text-slate-400">{t.terminal} · {t.product}{t.control.persistent_out && <span className="ml-1 text-rose-500">● out of control</span>}</div>
                      </td>
                      <td className="py-1.5 text-right font-medium text-slate-700">{pct(t.loss_pct)}</td>
                      <td className="py-1.5 text-right text-slate-700">{usd(t.dollar_loss_per_yr)}</td>
                      <td className="py-1.5">
                        {t.dominant_mechanism && (
                          <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${MECH_TONE[t.dominant_mechanism] ?? "bg-slate-100 text-slate-500"}`}>
                            {t.dominant_mechanism}
                          </span>
                        )}
                        {" "}<TrendArrow trend={t.control.trend} />
                      </td>
                      <td className="w-28 py-1.5"><MechanismBar mech={t.mechanism} compact /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </section>

        <section className="xl:col-span-2">
          <Panel title="Meter-drift — control-chart offenders">
            {data.meter_drift.ranked.length === 0 ? (
              <div className="text-sm text-slate-500">All tanks inside control limits.</div>
            ) : (
              <div className="space-y-2">
                {data.meter_drift.ranked.slice(0, 8).map((d) => (
                  <button key={d.tank_id} onClick={() => setSelected(d.tank_id)}
                    className={`flex w-full items-center justify-between rounded-lg border p-2 text-left text-xs ${d.persistent_out ? "border-rose-200 bg-rose-50" : "border-slate-200 bg-slate-50"}`}>
                    <div>
                      <div className="font-semibold text-slate-700">{d.tank_id} · {d.product} <TrendArrow trend={d.trend} /></div>
                      <div className="text-[11px] text-slate-500">
                        mean {pct(d.mean_pct)} vs UCL {pct(d.ucl_pct)} · {d.n_out} periods out
                        {d.dominant_mechanism && <> · {d.dominant_mechanism}</>}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="font-bold text-slate-800">{d.severity}σ</div>
                      {d.persistent_out && <div className="text-[10px] font-semibold text-rose-600">persistent</div>}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </Panel>
        </section>
      </div>

      <Panel title="Tank drill-down — book vs physical control chart">
        {selectedTank ? <TankDrill tank={selectedTank} ucl={net.control.ucl_pct} center={net.control.center_pct} /> : <div className="text-sm text-slate-500">Select a tank.</div>}
      </Panel>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-5">
        <section className="xl:col-span-3">
          <Panel title="Loss tracking — % of throughput over time">
            <LossTrendChart series={data.loss_tracking.network_series} ucl={net.control.ucl_pct} />
            <p className="mt-1 text-[11px] text-slate-400">
              Routine shrinkage stays under the upper control limit; red points are anomalies above it.
            </p>
          </Panel>
        </section>
        <section className="xl:col-span-2">
          <Panel title="Receipt measurement basis">
            {data.receipts.by_source.length === 0 ? (
              <div className="text-sm text-slate-500">No receipt detail.</div>
            ) : (
              <div className="space-y-2">
                {data.receipts.by_source.map((s) => (
                  <div key={s.source} className="rounded-lg border border-slate-200 p-2.5">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium capitalize text-slate-700">{s.source}</span>
                      <span className={`text-xs font-semibold ${s.bl_variance_pct < 0 ? "text-rose-600" : "text-slate-500"}`}>
                        B/L vs received {pct(s.bl_variance_pct)}
                      </span>
                    </div>
                    <div className="mt-0.5 text-[11px] text-slate-500">{s.label}</div>
                    <div className="mt-1 grid grid-cols-3 gap-1 text-[11px] text-slate-600">
                      <div>net <b>{gal(s.net_gal)}</b></div>
                      <div>variance <b className={s.bl_variance_gal < 0 ? "text-rose-600" : ""}>{gal(s.bl_variance_gal)}</b></div>
                      <div>basis <b>{s.measurement_basis ?? "—"}</b></div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </section>
      </div>

      <Panel title="Net-recon cross-check — billed net vs independent ASTM D1250 recompute">
        {data.net_recon.available ? (
          <div className="overflow-x-auto">
            <p className="mb-2 text-[11px] text-slate-500">
              Disbursements grouped by BOL number ({data.net_recon.checked_bols?.toLocaleString()} BOLs over{" "}
              {data.net_recon.checked_compartments?.toLocaleString()} compartments). The billed net is never overwritten —
              a systematic gap by lane/meter is the signal (probe calibration / VCF mismatch).
            </p>
            <table className="w-full text-sm">
              <thead className="text-left text-[10px] uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="pb-2">Meter · Lane</th>
                  <th className="pb-2 text-right">BOLs</th>
                  <th className="pb-2 text-right">Billed net</th>
                  <th className="pb-2 text-right">Recomputed</th>
                  <th className="pb-2 text-right">Δ</th>
                  <th className="pb-2">Flag</th>
                </tr>
              </thead>
              <tbody>
                {data.net_recon.by_meter.map((m, i) => (
                  <tr key={i} className={`border-t border-slate-100 ${m.systematic ? "bg-amber-50/60" : ""}`}>
                    <td className="py-1.5 font-medium text-slate-700">{m.meter_id} <span className="text-[11px] text-slate-400">{m.product}</span></td>
                    <td className="py-1.5 text-right text-slate-600">{m.n_bols.toLocaleString()}</td>
                    <td className="py-1.5 text-right text-slate-600">{gal(m.billed_net)}</td>
                    <td className="py-1.5 text-right text-slate-600">{gal(m.recomputed_net)}</td>
                    <td className={`py-1.5 text-right font-medium ${Math.abs(m.delta_pct) >= 0.15 ? "text-rose-600" : "text-slate-500"}`}>
                      {pct(m.delta_pct)} <TrendArrow trend={m.trend} />
                    </td>
                    <td className="py-1.5 text-[11px]">
                      {m.systematic ? (
                        <span className="text-amber-700" title={m.flag_label ?? ""}>⚑ systematic</span>
                      ) : (
                        <span className="text-slate-400">ok</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.net_recon.by_meter.filter((m) => m.systematic && m.flag_label).slice(0, 3).map((m, i) => (
              <p key={i} className="mt-2 rounded bg-amber-50 px-2 py-1 text-[11px] text-amber-800">⚑ {m.meter_id}: {m.flag_label}</p>
            ))}
          </div>
        ) : (
          <div className="text-sm text-slate-500">{data.net_recon.reason ?? humanize("needs BOL compartment detail")}</div>
        )}
      </Panel>
    </div>
  );
}
