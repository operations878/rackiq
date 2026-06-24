import {
  ResponsiveContainer,
  ComposedChart,
  Area,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
  ReferenceArea,
} from "recharts";
import type { LanePoint, LaneForecastPoint } from "../../api/types";
import { fmtMonthYear, fmtDate } from "../../lib/format";
import { fmtGal, fmtGalFull } from "../../lib/scoreui";

/**
 * The BASE-RANGE CHART — the signature screen for leadership. Renders, per period:
 *   • the lighter VARIABILITY range (base ± 2σ) — their wider envelope,
 *   • the shaded BASE range (base ± 1σ) — where a normal order lands,
 *   • the BASE line (seasonally-aware expected volume), and
 *   • the ACTUAL lifts as distinct dots on top.
 * When a `forecast` series is passed, the lane continues FORWARD as a dotted projection over a
 * lightly-shaded "forecast" region past a boundary line — VAR turned into a forecast.
 * A small always-visible legend (below) explains every element in plain words.
 */

// One shared palette so the chart and the legend never drift apart.
const C = {
  base: "#4338ca", // indigo-700 — the base line
  band1: "#818cf8", // indigo-400 — base range (±1σ)
  band2: "#c7d2fe", // indigo-200 — variability range (±2σ)
  actual: "#0f172a", // slate-900 — actual lifts
  boundary: "#94a3b8", // slate-400 — the forecast boundary
  wash: "#eef2ff", // indigo-50 — the forecast region wash
};

function LegendItem({ swatch, label, title }: { swatch: React.ReactNode; label: string; title: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[10px] text-slate-500" title={title}>
      {swatch}
      {label}
    </span>
  );
}

function EmptyLane({ message }: { message: string }) {
  return (
    <div className="flex h-72 w-full flex-col items-center justify-center rounded-lg border border-dashed border-slate-200 bg-slate-50/50 text-center">
      <div className="text-sm font-medium text-slate-500">Base-range lane not available</div>
      <div className="mt-1 max-w-xs text-[11px] text-slate-400">{message}</div>
    </div>
  );
}

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
    return (
      <EmptyLane message="We need ≥8 lifts over ≥12 weeks to fit a customer's normal lane. This account doesn't have enough history yet." />
    );
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
  const hasForecast = hist.length > 0 && fc.length > 0;
  if (hasForecast) {
    const last = hist[hist.length - 1];
    last.fbase = last.base;
    last.fbaseRange = last.baseRange;
    last.fvarRange = last.varRange;
  }
  const data: Row[] = [...hist, ...fc];
  const lastX = data[data.length - 1]?.x ?? boundary;
  const perLabel = grain === "monthly" ? "month" : "week";

  return (
    <div className="w-full">
      <div className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 10, right: 14, bottom: 4, left: 6 }}>
            {/* a soft wash behind the forecast region so the projection reads as "future" */}
            {hasForecast && <ReferenceArea x1={boundary} x2={lastX} fill={C.wash} fillOpacity={0.6} ifOverflow="extendDomain" />}
            <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" vertical={false} />
            <XAxis dataKey="x" tick={{ fontSize: 9, fill: "#94a3b8" }} tickFormatter={fmtMonthYear} interval={Math.max(0, Math.floor(data.length / 8))} tickMargin={6} />
            <YAxis tick={{ fontSize: 10, fill: "#94a3b8" }} width={48} tickFormatter={(v) => fmtGal(Number(v))} />
            <Tooltip
              formatter={(v, name) => {
                if (Array.isArray(v)) return [`${Math.round(Number(v[0])).toLocaleString()}–${Math.round(Number(v[1])).toLocaleString()} gal`, name];
                return [fmtGalFull(Number(v)), name];
              }}
              labelFormatter={(l) => fmtDate(String(l))}
              contentStyle={{ fontSize: 11, borderRadius: 8, border: "1px solid #e2e8f0" }}
            />
            {/* history bands (outer first, inner on top) */}
            <Area type="monotone" dataKey="varRange" name="Wider range (±2σ)" stroke="none" fill={C.band2} fillOpacity={0.5} isAnimationActive={false} />
            <Area type="monotone" dataKey="baseRange" name="Usual range (±1σ)" stroke="none" fill={C.band1} fillOpacity={0.5} isAnimationActive={false} />
            {/* forward projection (dotted, fainter) */}
            {hasForecast && (
              <>
                <Area type="monotone" dataKey="fvarRange" name="Projected ±2σ" stroke="none" fill={C.band2} fillOpacity={0.28} isAnimationActive={false} connectNulls />
                <Area type="monotone" dataKey="fbaseRange" name="Projected ±1σ" stroke="none" fill={C.band1} fillOpacity={0.3} isAnimationActive={false} connectNulls />
                <ReferenceLine x={boundary} stroke={C.boundary} strokeDasharray="3 3" label={{ value: "Forecast →", position: "insideTopRight", fontSize: 9, fill: "#64748b" }} />
                <Line type="monotone" dataKey="fbase" name="Projected base" stroke={C.base} strokeDasharray="5 4" strokeWidth={2.5} dot={false} isAnimationActive={false} connectNulls />
              </>
            )}
            <Line type="monotone" dataKey="base" name="Normal volume" stroke={C.base} dot={false} strokeWidth={2.5} isAnimationActive={false} />
            <Line type="monotone" dataKey="actual" name="Actual lifts" stroke="transparent" strokeWidth={0} dot={{ r: 2.4, fill: C.actual, stroke: "#fff", strokeWidth: 1 }} activeDot={{ r: 3.4 }} isAnimationActive={false} connectNulls={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      {/* always-visible plain-language legend */}
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded-lg bg-slate-50 px-3 py-2">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">How to read it</span>
        <LegendItem
          title="Their seasonally-aware expected volume each period — the middle of the lane."
          label={`Normal volume / ${perLabel}`}
          swatch={<span className="inline-block h-0.5 w-4 rounded" style={{ background: C.base }} />}
        />
        <LegendItem
          title="Where a normal order lands most of the time (base ± 1 standard deviation)."
          label="Usual range"
          swatch={<span className="inline-block h-2.5 w-4 rounded-sm" style={{ background: C.band1, opacity: 0.6 }} />}
        />
        <LegendItem
          title="Their wider envelope — almost every lift lands inside this (base ± 2 standard deviations). Outside it is a genuine surprise."
          label="Wider range"
          swatch={<span className="inline-block h-2.5 w-4 rounded-sm" style={{ background: C.band2, opacity: 0.7 }} />}
        />
        <LegendItem
          title="Each actual lift volume for the period."
          label="Actual lifts"
          swatch={<span className="inline-block h-2 w-2 rounded-full border border-white" style={{ background: C.actual }} />}
        />
        {hasForecast && (
          <LegendItem
            title="The lane projected forward — a dotted continuation past the 'Forecast →' line, shaded as the future."
            label="Forecast (projected)"
            swatch={<span className="inline-block h-0 w-4 border-t-2 border-dashed" style={{ borderColor: C.base }} />}
          />
        )}
      </div>
    </div>
  );
}
