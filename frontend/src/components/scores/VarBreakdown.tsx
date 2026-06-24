import { useState } from "react";
import type { VarBlock } from "../../api/types";
import { fmtGal } from "../../lib/scoreui";

/** The VAR transparency + statistics layer: the three score components, the base / range /
 *  variability numbers, the cadence lane, the steadiness drift test, and (folded away) the
 *  advanced statistics. Every figure carries a plain-language label. */

function p0(x: number | null | undefined): string {
  return x == null ? "—" : `${Math.round(x * 100)}%`;
}

/** Format a raw float for the advanced-stats grid without false precision (0.73841 → "0.74"). */
function fx(x: number | null | undefined, dp = 2): string {
  if (x == null || !isFinite(x)) return "—";
  return `${Number(x.toFixed(dp))}`;
}

function StatRow({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="text-[11px] text-slate-500" title={hint}>{label}</span>
      <span className="text-right text-[11px] font-medium text-slate-800">{value}</span>
    </div>
  );
}

const DIR_TONE: Record<string, string> = {
  improving: "bg-emerald-100 text-emerald-700",
  deteriorating: "bg-rose-100 text-rose-700",
  steady: "bg-slate-100 text-slate-600",
  insufficient: "bg-slate-100 text-slate-400",
};
const DIR_LABEL: Record<string, string> = {
  improving: "▲ Getting steadier",
  deteriorating: "▼ Getting choppier",
  steady: "→ Holding steady",
  insufficient: "— not enough history",
};

export default function VarBreakdown({ v, grain }: { v: VarBlock; grain: string }) {
  const [showStats, setShowStats] = useState(false);
  const unit = grain === "monthly" ? "mo" : "wk";
  const d = v.diagnostics;
  const st = v.steadiness;
  // With too little history the lane is degenerate (σ≈0 → zero-width ranges). Don't show
  // false-precision numbers like "6.2k–6.2k"; say plainly that the lane isn't fitted yet.
  const ok = v.status === "ok" && v.score != null;

  return (
    <div className="space-y-4 text-slate-700">
      {/* The three components that make the score */}
      <div>
        <h4 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          What makes up the score
        </h4>
        <div className="space-y-2">
          {(v.components ?? []).map((c) => (
            <div key={c.key}>
              <div className="flex items-center justify-between text-[11px]">
                <span className="font-medium text-slate-700">{c.label}</span>
                <span className="text-slate-500">
                  {p0(c.value)} <span className="text-slate-300">·</span>{" "}
                  <span className="text-slate-400">{Math.round(c.weight * 100)}% weight</span>
                </span>
              </div>
              <div className="mt-0.5 h-1.5 w-full overflow-hidden rounded bg-slate-200">
                <div className="h-1.5 rounded bg-indigo-500" style={{ width: `${Math.max(0, Math.min(100, (c.value ?? 0) * 100))}%` }} />
              </div>
              <p className="mt-0.5 text-[10px] leading-tight text-slate-400">{c.description}</p>
            </div>
          ))}
          {!v.components && <p className="text-[11px] text-slate-400">Not enough history to break down the score.</p>}
        </div>
      </div>

      {/* The lane numbers */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-2.5">
          <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Their normal lane</h4>
          {!ok ? (
            <p className="py-1.5 text-[11px] leading-snug text-slate-400">
              Not enough history yet to map their normal lane — it firms up once they have ≥8 lifts over ≥12 weeks.
            </p>
          ) : (
            <>
              <StatRow label={`Base volume / ${unit}`} value={`${fmtGal(v.base_level)} gal`}
                       hint="Seasonally-aware expected volume per period." />
              <StatRow label="Usual range (±1σ)" value={v.base_range ? `${fmtGal(v.base_range[0])}–${fmtGal(v.base_range[1])} gal` : "—"}
                       hint="Where a normal order lands most of the time (base ± 1 standard deviation)." />
              <StatRow label="Wider range (±2σ)" value={v.variability_range ? `${fmtGal(v.variability_range[0])}–${fmtGal(v.variability_range[1])} gal` : "—"}
                       hint="Almost every lift lands inside this; outside it is a genuine surprise (base ± 2 standard deviations)." />
              {d?.base_ci && (
                <StatRow label={`${Math.round((d.base_ci.ci ?? 0.9) * 100)}% confidence on base`}
                         value={`${fmtGal(d.base_ci.lo)}–${fmtGal(d.base_ci.hi)} gal`}
                         hint="Residual-bootstrap confidence interval on the base volume." />
              )}
            </>
          )}
        </div>
        <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-2.5">
          <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Their rhythm (cadence)</h4>
          <StatRow label="Typical gap between lifts"
                   value={v.cadence?.base_cadence_days != null ? `~${Math.round(v.cadence.base_cadence_days)} ${Math.round(v.cadence.base_cadence_days) === 1 ? "day" : "days"}` : "—"}
                   hint="Median days between one lift and the next." />
          <StatRow label="Timing consistency" value={p0(v.cadence?.in_band_rate)}
                   hint="How often the gap stays inside their usual rhythm." />
          <StatRow label="Cadence steadiness" value={v.cadence?.score != null ? Math.round(v.cadence.score) : "—"}
                   hint="The cadence lane's own 0–100 score (30% of the headline VAR)." />
          <div className="mt-1.5 flex items-center justify-between">
            <span className="text-[11px] text-slate-500">Trend in steadiness</span>
            <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${DIR_TONE[st?.direction ?? "insufficient"]}`}>
              {DIR_LABEL[st?.direction ?? "insufficient"]}
            </span>
          </div>
          {st && st.direction !== "insufficient" && (
            <p className="mt-0.5 text-[10px] leading-tight text-slate-400">
              In-band {p0(st.in_band_prior)} → {p0(st.in_band_recent)} (recent half vs prior
              {st.p_value != null && <>, p={fx(st.p_value, 3)}</>}).
            </p>
          )}
        </div>
      </div>

      {/* Advanced statistics — folded away so it never clutters the glance-able view */}
      {d && (
        <div className="rounded-lg border border-slate-200">
          <button
            onClick={() => setShowStats((s) => !s)}
            className="flex w-full items-center justify-between px-3 py-2 text-[11px] font-semibold text-slate-600 hover:bg-slate-50"
          >
            <span>📐 Advanced statistics (the math)</span>
            <span className="text-slate-400">{showStats ? "▲ hide" : "▼ show"}</span>
          </button>
          {showStats && (
            <div className="grid grid-cols-1 gap-x-6 gap-y-0.5 border-t border-slate-100 px-3 py-2 sm:grid-cols-2">
              <StatRow label="Forecastability" value={d.forecastability != null ? `${d.forecastability}/100` : "—"}
                       hint="1 − normalized spectral entropy: how concentrated the demand cycle is. Higher = more forecastable." />
              <StatRow label="Predictability (skill vs naïve)"
                       value={d.skill ? `${d.skill.predictability}/100` : "—"}
                       hint="1 − model error / naïve-last error, one-step. Higher = the lane beats a naïve guess." />
              <StatRow label="Forecast error (model / naïve)"
                       value={d.skill ? `${fmtGal(d.skill.mae_model)} / ${fmtGal(d.skill.mae_naive)} gal` : "—"}
                       hint="Mean absolute one-step error of the seasonal lane vs naïve-last." />
              <StatRow label="Trend test (Mann–Kendall)"
                       value={d.trend_test ? `${d.trend_test.direction} (τ=${fx(d.trend_test.tau)}, p=${fx(d.trend_test.p_value, 3)})` : "—"}
                       hint="Non-parametric monotonic-trend test on the demand series." />
              <StatRow label="Lane fit R²" value={fx(d.r2)}
                       hint="Share of demand variance the seasonal lane explains." />
              <StatRow label="Coefficient of variation" value={fx(d.coef_variation)}
                       hint="σ ÷ mean — relative scatter (lower = tighter)." />
              <StatRow label="Residuals are white noise"
                       value={d.residuals.white_noise == null ? "—" : d.residuals.white_noise ? `yes (Ljung–Box p=${fx(d.residuals.ljung_box_p, 3)})` : `no (p=${fx(d.residuals.ljung_box_p, 3)})`}
                       hint="If yes, the lane captured the structure — what's left is irreducible noise." />
              <StatRow label="Residual autocorrelation (lag-1)" value={fx(d.residuals.acf1)}
                       hint="Leftover period-to-period pattern the lane misses." />
              {d.stl && (
                <>
                  <StatRow label="Trend strength" value={fx(d.stl.trend_strength)}
                           hint="STL feature: 0 (no trend) → 1 (dominant trend)." />
                  <StatRow label="Seasonal strength" value={fx(d.stl.seasonal_strength)}
                           hint="STL feature: 0 (no seasonality) → 1 (dominant seasonality)." />
                </>
              )}
              <StatRow label="Outliers beyond ±3σ" value={d.n_outliers_3sigma}
                       hint="Periods that swung far outside the variability range." />
              <StatRow label="Periods analyzed" value={d.n_periods} hint={`History length in ${unit} buckets.`} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
