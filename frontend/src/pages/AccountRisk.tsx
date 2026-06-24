import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { CreditResponse, Summary } from "../api/types";
import Panel from "../components/Panel";
import AccountRiskScatter, { QUAD_COLOR } from "../components/credit/AccountRiskScatter";
import { gradeTone } from "../lib/scoreui";

const WINDOW_LABEL: Record<string, string> = { "30": "30d", "90": "90d", "365": "365d", all: "All-time" };

const gal = (v: number | null | undefined) =>
  v == null ? "—" : Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(2)}MM` : Math.abs(v) >= 1e3 ? `${(v / 1e3).toFixed(0)}k` : `${Math.round(v)}`;
const usd = (v: number | null | undefined) => (v == null ? "—" : `$${Math.round(v).toLocaleString()}`);

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

function CreditPill({ score, grade }: { score: number | null; grade: string | null }) {
  if (score == null) return <span className="text-xs text-slate-400">—</span>;
  return (
    <span className="inline-flex items-center gap-1">
      <span className="font-semibold text-slate-800">{Math.round(score)}</span>
      {grade && <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${gradeTone(grade)}`}>{grade}</span>}
    </span>
  );
}

function QuadTag({ q }: { q: string | null }) {
  if (!q) return <span className="text-slate-400">—</span>;
  return (
    <span className="inline-flex items-center gap-1 text-[11px] font-medium" style={{ color: QUAD_COLOR[q] }}>
      <span className="h-2 w-2 rounded-full" style={{ background: QUAD_COLOR[q] }} />
      {q}
    </span>
  );
}

function LockState({ data }: { data: CreditResponse }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center shadow-sm">
      <div className="text-3xl">🔒</div>
      <h2 className="mt-3 text-lg font-semibold text-slate-800">Credit &amp; Account Risk is locked</h2>
      <p className="mx-auto mt-1 max-w-md text-sm text-slate-500">{data.reason}</p>
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        {(data.missing_fields ?? []).map((f) => (
          <span key={f} className="rounded-lg bg-amber-50 px-3 py-1.5 text-sm font-medium text-amber-700">
            Feed me <span className="font-mono">{f}</span>
          </span>
        ))}
      </div>
      <p className="mx-auto mt-4 max-w-md text-xs text-slate-400">
        The credit score, the VAR × credit account-risk map, and conversion targeting all run on the
        AR ledger — invoice / due / paid dates, amount, and credit limit.
      </p>
    </div>
  );
}

export default function AccountRisk({ summary, navigate }: { summary: Summary; navigate: (to: string) => void }) {
  const [window, setWindow] = useState("all");
  const [data, setData] = useState<CreditResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    setError(null);
    api.credit.get(window).then(setData).catch((e) => setError(String(e)));
  }, [window]);
  useEffect(reload, [reload]);

  if (!summary.connected) {
    return <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-slate-500">Load a book in Data Studio to see credit &amp; account risk.</div>;
  }
  if (error) return <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700">{error}</div>;
  if (!data) return <div className="text-sm text-slate-500">Loading credit &amp; account risk…</div>;
  if (!data.available) return <LockState data={data} />;

  const net = data.network!;
  const cuts = data.axis_cuts!;
  const counts = data.quadrant_counts ?? {};

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold tracking-tight text-slate-900">Credit &amp; Account Risk</h1>
          <p className="text-xs text-slate-500">{data.n_customers} customers with AR · as of {data.as_of}</p>
        </div>
        <div className="flex gap-1 rounded-lg bg-slate-100 p-0.5 text-xs">
          {Object.keys(WINDOW_LABEL).map((w) => (
            <button key={w} onClick={() => setWindow(w)} className={`rounded-md px-2.5 py-1 font-medium ${w === window ? "bg-white text-slate-900 shadow-sm" : "text-slate-500"}`}>{WINDOW_LABEL[w]}</button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-5">
        <Kpi label="Open exposure" value={usd(net.open_exposure_total)} sub="total open AR" />
        <Kpi label="Median credit" value={net.median_credit_score != null ? `${net.median_credit_score}` : "—"} sub="book midpoint (0–100, higher safer)" />
        <Kpi label="Danger accounts" value={`${net.n_danger}`} sub="erratic + slow-pay" tone="rose" />
        <Kpi label="Danger exposure" value={usd(net.danger_open_exposure)} sub="open AR in Danger cell" tone="rose" />
        <Kpi label="Over limit" value={`${net.n_over_limit}`} sub="open exposure > credit limit" tone={net.n_over_limit ? "rose" : "slate"} />
      </div>

      <Panel title="Account-risk map — VAR (supply risk) × Credit score (financial risk)" right={
        <div className="flex flex-wrap gap-2 text-[10px] text-slate-500">
          {(data.quadrant_order ?? []).map((q) => (
            <span key={q} className="rounded bg-slate-100 px-1.5 py-0.5">{q}: <b className="text-slate-700">{counts[q] ?? 0}</b></span>
          ))}
        </div>
      }>
        <AccountRiskScatter rows={data.customers ?? []} varCut={cuts.var} creditCut={cuts.credit}
          onSelect={(id) => navigate(`scorecard/${id}`)} />
        <p className="mt-2 text-[11px] text-slate-400">
          Bubble size = lifted volume. Quadrants split at the book median (VAR {cuts.var}, credit {cuts.credit}).
          Click a bubble to open the customer scorecard. Anchor = steady + pays; Danger = erratic + slow-pay.
        </p>
      </Panel>

      <Panel title="Conversion targeting — have these conversations (spot → ratable term)" right={
        !data.elasticity_available ? <span className="text-[10px] text-amber-600">elasticity still collecting — using volume + erraticness</span> : null
      }>
        {data.conversion_targets?.length ? (
          <div className="space-y-2">
            {data.conversion_targets.slice(0, 12).map((t, i) => (
              <button key={t.customer_id} onClick={() => navigate(`scorecard/${t.customer_id}`)}
                className="flex w-full items-start gap-3 rounded-lg border border-slate-100 bg-white p-2.5 text-left hover:bg-indigo-50/50">
                <span className="mt-0.5 w-5 shrink-0 text-center text-xs font-semibold text-slate-400">{i + 1}</span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-slate-800">{t.name}</span>
                    <span className="rounded bg-indigo-100 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-700">conv {Math.round(t.conversion_score)}</span>
                    <span className="text-[10px] text-slate-400">VAR {t.var_score} · credit {t.credit_score}/{t.credit_grade}</span>
                  </div>
                  <div className="mt-0.5 text-[11px] text-slate-500">{t.rationale}</div>
                </div>
              </button>
            ))}
          </div>
        ) : <div className="text-sm text-slate-500">No conversion targets in this window.</div>}
      </Panel>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Grow-me — steady, growing, good credit">
          {data.grow_me?.length ? (
            <div className="space-y-2">
              {data.grow_me.slice(0, 8).map((g) => (
                <button key={g.customer_id} onClick={() => navigate(`scorecard/${g.customer_id}`)}
                  className="block w-full rounded-lg border border-emerald-100 bg-emerald-50/40 p-2.5 text-left hover:bg-emerald-50">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-slate-800">{g.name}</span>
                    <span className="text-[10px] text-emerald-700">+{Math.round(g.trend_pct)}% · credit {g.credit_score}/{g.credit_grade}</span>
                  </div>
                  <div className="mt-0.5 text-[11px] text-slate-500">{g.rationale}</div>
                </button>
              ))}
            </div>
          ) : <div className="text-sm text-slate-500">No growing good-credit accounts in this window.</div>}
        </Panel>

        <Panel title="Revenue-at-risk — good accounts fading">
          {data.revenue_at_risk?.length ? (
            <div className="space-y-2">
              {data.revenue_at_risk.slice(0, 8).map((v) => (
                <button key={v.customer_id} onClick={() => navigate(`scorecard/${v.customer_id}`)}
                  className="block w-full rounded-lg border border-rose-100 bg-rose-50/40 p-2.5 text-left hover:bg-rose-50">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-slate-800">{v.name}</span>
                    <span className="text-[10px] text-rose-700">{Math.round(v.trend_pct)}% · {gal(v.volume_at_risk)} gal/yr at risk</span>
                  </div>
                  <div className="mt-0.5 text-[11px] text-slate-500">{v.rationale}</div>
                </button>
              ))}
            </div>
          ) : <div className="text-sm text-slate-500">No fading accounts in this window.</div>}
        </Panel>
      </div>
    </div>
  );
}
