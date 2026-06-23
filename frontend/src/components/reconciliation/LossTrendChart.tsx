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
} from "recharts";
import type { ReconNetSeriesPoint } from "../../api/types";

/**
 * LOSS TRACKING — network loss-% of throughput over time, separating routine shrinkage (under
 * the control limit) from anomalies (over it, drawn red). The shaded band is throughput so the
 * eye can weight a loss-% spike by how much volume actually moved that period.
 */
function Dot(props: { cx?: number; cy?: number; payload?: ReconNetSeriesPoint }) {
  const { cx, cy, payload } = props;
  if (cx == null || cy == null || !payload) return <g />;
  return <circle cx={cx} cy={cy} r={payload.anomaly ? 3.6 : 2} fill={payload.anomaly ? "#e11d48" : "#0f172a"} stroke="white" strokeWidth={payload.anomaly ? 1 : 0} />;
}

export default function LossTrendChart({ series, ucl }: { series: ReconNetSeriesPoint[]; ucl: number }) {
  if (!series.length) return <div className="text-sm text-slate-500">No periods to chart.</div>;
  const data = series.map((p) => ({ x: p.period, loss: p.loss_pct, throughput: p.throughput, anomaly: p.anomaly }));
  return (
    <div className="h-60 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
          <XAxis dataKey="x" tick={{ fontSize: 9 }} interval={Math.max(0, Math.floor(data.length / 9))} />
          <YAxis yAxisId="l" tick={{ fontSize: 10 }} width={46} tickFormatter={(v) => `${Number(v).toFixed(2)}%`} />
          <YAxis yAxisId="t" orientation="right" tick={{ fontSize: 9 }} width={42} tickFormatter={(v) => `${(Number(v) / 1e6).toFixed(1)}M`} />
          <Tooltip
            formatter={(v, name) =>
              name === "Throughput"
                ? [`${(Number(v) / 1e6).toFixed(2)} MM gal`, name]
                : [`${Number(v).toFixed(3)}%`, "Loss %"]}
            labelFormatter={(l) => `Period ${l}`}
          />
          <Area yAxisId="t" type="monotone" dataKey="throughput" name="Throughput" stroke="none" fill="#e2e8f0" fillOpacity={0.7} isAnimationActive={false} />
          <ReferenceLine yAxisId="l" y={ucl} stroke="#e11d48" strokeDasharray="5 3"
            label={{ value: "UCL", fontSize: 9, fill: "#e11d48", position: "insideTopRight" }} />
          <Line yAxisId="l" type="monotone" dataKey="loss" name="Loss %" stroke="#0f172a" strokeWidth={1.5} dot={<Dot />} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
