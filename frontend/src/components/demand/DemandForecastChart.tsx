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
import type { DemandHistoryPoint, DemandForecastPoint } from "../../api/types";

/**
 * The DEMAND FORECAST chart — trailing actual weekly volume, then the rolled-up
 * P10/P50/P90 forecast band. A reference line marks where history ends and the forecast
 * begins; the band is drawn from an [p10, p90] array dataKey (Recharts band convention).
 */
export default function DemandForecastChart({
  history,
  forecast,
}: {
  history: DemandHistoryPoint[];
  forecast: DemandForecastPoint[];
}) {
  if (!history.length && !forecast.length) {
    return <div className="text-sm text-slate-500">Not enough history to forecast.</div>;
  }

  const lastActual = history.length ? history[history.length - 1].actual : null;
  const boundary = forecast.length ? forecast[0].period_start : null;

  const data = [
    ...history.map((h) => ({ x: h.period_start, actual: h.actual })),
    // Anchor the forecast line/band to the last actual so they connect visually.
    ...(lastActual != null && boundary
      ? [{ x: history[history.length - 1].period_start, p50: lastActual, band: [lastActual, lastActual] as [number, number] }]
      : []),
    ...forecast.map((f) => ({ x: f.period_start, p50: f.p50, band: [f.p10, f.p90] as [number, number] })),
  ];

  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 8, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
          <XAxis dataKey="x" tick={{ fontSize: 9 }} interval={Math.max(0, Math.floor(data.length / 9))} />
          <YAxis tick={{ fontSize: 10 }} width={52} tickFormatter={(v) => `${(Number(v) / 1000).toFixed(0)}k`} />
          <Tooltip
            formatter={(v, name) => {
              if (Array.isArray(v)) return [`${Number(v[0]).toLocaleString()}–${Number(v[1]).toLocaleString()} gal`, name];
              return [`${Number(v).toLocaleString()} gal`, name];
            }}
            labelFormatter={(l) => `Week of ${l}`}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {boundary && <ReferenceLine x={boundary} stroke="#94a3b8" strokeDasharray="4 3" label={{ value: "forecast →", fontSize: 10, fill: "#64748b", position: "insideTopRight" }} />}
          <Area type="monotone" dataKey="band" name="P10–P90 band" stroke="none" fill="#a5f3fc" fillOpacity={0.55} isAnimationActive={false} connectNulls />
          <Line type="monotone" dataKey="p50" name="P50 forecast" stroke="#0891b2" strokeWidth={2} dot={false} isAnimationActive={false} connectNulls />
          <Line type="monotone" dataKey="actual" name="Actual / wk" stroke="#0f172a" strokeWidth={1.4} dot={{ r: 1.5 }} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
