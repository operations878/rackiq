import {
  ResponsiveContainer,
  ComposedChart,
  Area,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
  ReferenceLine,
} from "recharts";
import type { LanePoint, LaneForecastPoint } from "../../api/types";

/**
 * The BASE-RANGE CHART — the single best screen for leadership. Renders, per period:
 *   • the lighter VARIABILITY range (base ± 2σ),
 *   • the shaded BASE range (base ± 1σ / ±%),
 *   • the BASE line (seasonally-aware expected volume), and
 *   • the ACTUAL lifts on top.
 * When a `forecast` series is passed, the lane is continued FORWARD as a dotted projection
 * (same bands, no actuals yet) past a "forecast →" boundary line — VAR turned into a forecast.
 * Recharts draws a band from an [lo, hi] array dataKey.
 */
export default function BaseRangeChart({
  series,
  grain,
  forecast,
}: {
  series: LanePoint[];
  grain: string;
  forecast?: LaneForecastPoint[];
}) {
  if (!series.length) {
    return <div className="text-sm text-slate-500">Not enough history to draw the base-range lane.</div>;
  }
  type Row = {
    x: string;
    base?: number | null;
    baseRange?: [number, number] | null;
    varRange?: [number, number] | null;
    actual?: number | null;
    fbase?: number | null;
    fbaseRange?: [number, number] | null;
    fvarRange?: [number, number] | null;
  };
  const hist: Row[] = series.map((p) => ({
    x: p.period_start,
    base: p.base,
    baseRange: [p.base_lo, p.base_hi],
    varRange: [p.var_lo, p.var_hi],
    actual: p.actual,
  }));
  const fc: Row[] = (forecast ?? []).map((p) => ({
    x: p.period_start,
    fbase: p.base,
    fbaseRange: [p.base_lo, p.base_hi],
    fvarRange: [p.var_lo, p.var_hi],
  }));
  // bridge the boundary so the dotted projection connects to the end of the history
  const boundary = series[series.length - 1].period_start;
  if (hist.length && fc.length) {
    const last = hist[hist.length - 1];
    last.fbase = last.base;
    last.fbaseRange = last.baseRange;
    last.fvarRange = last.varRange;
  }
  const data: Row[] = [...hist, ...fc];
  const unit = grain === "monthly" ? "mo" : "wk";

  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 8, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
          <XAxis dataKey="x" tick={{ fontSize: 9 }} interval={Math.max(0, Math.floor(data.length / 8))} />
          <YAxis tick={{ fontSize: 10 }} width={52} tickFormatter={(v) => `${(Number(v) / 1000).toFixed(0)}k`} />
          <Tooltip
            formatter={(v, name) => {
              if (Array.isArray(v)) return [`${Number(v[0]).toLocaleString()}–${Number(v[1]).toLocaleString()} gal`, name];
              return [`${Number(v).toLocaleString()} gal`, name];
            }}
            labelFormatter={(l) => `Period ${l}`}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {/* history */}
          <Area type="monotone" dataKey="varRange" name="Variability range (±2σ)" stroke="none" fill="#c7d2fe" fillOpacity={0.45} isAnimationActive={false} />
          <Area type="monotone" dataKey="baseRange" name="Base range (±1σ)" stroke="none" fill="#818cf8" fillOpacity={0.5} isAnimationActive={false} />
          {/* forward projection (dotted) */}
          {fc.length > 0 && (
            <>
              <Area type="monotone" dataKey="fvarRange" name="Projected ±2σ" stroke="none" fill="#c7d2fe" fillOpacity={0.25} isAnimationActive={false} connectNulls />
              <Area type="monotone" dataKey="fbaseRange" name="Projected ±1σ" stroke="none" fill="#818cf8" fillOpacity={0.28} isAnimationActive={false} connectNulls />
              <ReferenceLine x={boundary} stroke="#94a3b8" strokeDasharray="2 2" label={{ value: "forecast →", position: "insideTopRight", fontSize: 9, fill: "#64748b" }} />
              <Line type="monotone" dataKey="fbase" name="Projected base" stroke="#4338ca" strokeDasharray="4 3" strokeWidth={2} dot={false} isAnimationActive={false} connectNulls />
            </>
          )}
          <Line type="monotone" dataKey="base" name="Base volume" stroke="#4338ca" dot={false} strokeWidth={2} isAnimationActive={false} />
          <Line type="monotone" dataKey="actual" name={`Actual / ${unit}`} stroke="#0f172a" strokeWidth={1} dot={{ r: 1.6 }} isAnimationActive={false} connectNulls={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
