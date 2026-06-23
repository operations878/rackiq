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
} from "recharts";
import type { LanePoint } from "../../api/types";

/**
 * The BASE-RANGE CHART — the single best screen for leadership. Renders, per period:
 *   • the lighter VARIABILITY range (base ± 2σ),
 *   • the shaded BASE range (base ± 1σ / ±%),
 *   • the BASE line (seasonally-aware expected volume), and
 *   • the ACTUAL lifts on top.
 * Recharts draws a band from an [lo, hi] array dataKey.
 */
export default function BaseRangeChart({ series, grain }: { series: LanePoint[]; grain: string }) {
  if (!series.length) {
    return <div className="text-sm text-slate-500">Not enough history to draw the base-range lane.</div>;
  }
  const data = series.map((p) => ({
    x: p.period_start,
    base: p.base,
    baseRange: [p.base_lo, p.base_hi] as [number, number],
    varRange: [p.var_lo, p.var_hi] as [number, number],
    actual: p.actual,
  }));
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
          <Area type="monotone" dataKey="varRange" name="Variability range (±2σ)" stroke="none" fill="#c7d2fe" fillOpacity={0.45} isAnimationActive={false} />
          <Area type="monotone" dataKey="baseRange" name="Base range (±1σ)" stroke="none" fill="#818cf8" fillOpacity={0.5} isAnimationActive={false} />
          <Line type="monotone" dataKey="base" name="Base volume" stroke="#4338ca" dot={false} strokeWidth={2} isAnimationActive={false} />
          <Line type="monotone" dataKey="actual" name={`Actual / ${unit}`} stroke="#0f172a" strokeWidth={1} dot={{ r: 1.6 }} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
