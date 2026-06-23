import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
} from "recharts";
import type { ReconTankPeriod } from "../../api/types";

/**
 * Meter-drift CONTROL CHART for one tank: loss-% of throughput per period against the network
 * routine-shrinkage control limits (centerline + UCL). Periods beyond the UCL are flagged red —
 * a tank running persistently above the limit is a drifting meter, not routine noise.
 */
function Dot(props: { cx?: number; cy?: number; payload?: ReconTankPeriod }) {
  const { cx, cy, payload } = props;
  if (cx == null || cy == null || !payload) return <g />;
  const out = payload.out_of_control;
  return <circle cx={cx} cy={cy} r={out ? 3.6 : 2} fill={out ? "#e11d48" : "#4338ca"} stroke="white" strokeWidth={out ? 1 : 0} />;
}

export default function ControlChart({
  series,
  ucl,
  center,
}: {
  series: ReconTankPeriod[];
  ucl: number;
  center: number;
}) {
  if (!series.length) return <div className="text-sm text-slate-500">No periods to chart.</div>;
  const data = series.map((p) => ({ x: p.period, loss: p.loss_pct, out_of_control: p.out_of_control }));
  return (
    <div className="h-56 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
          <XAxis dataKey="x" tick={{ fontSize: 9 }} interval={Math.max(0, Math.floor(data.length / 8))} />
          <YAxis tick={{ fontSize: 10 }} width={46} tickFormatter={(v) => `${Number(v).toFixed(2)}%`} />
          <Tooltip
            formatter={(v) => [`${Number(v).toFixed(3)}% of throughput`, "Loss"]}
            labelFormatter={(l) => `Period ${l}`}
          />
          <ReferenceLine y={center} stroke="#94a3b8" strokeDasharray="4 4"
            label={{ value: "routine", fontSize: 9, fill: "#94a3b8", position: "insideTopLeft" }} />
          <ReferenceLine y={ucl} stroke="#e11d48" strokeDasharray="5 3"
            label={{ value: "UCL (3σ)", fontSize: 9, fill: "#e11d48", position: "insideTopLeft" }} />
          <Line type="monotone" dataKey="loss" stroke="#4338ca" strokeWidth={1.5} dot={<Dot />} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
