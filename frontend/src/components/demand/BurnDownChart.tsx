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
import type { DemandBurndown } from "../../api/types";

/**
 * The INVENTORY BURN-DOWN — projects book inventory forward at the P50 demand rate, with a
 * fast (P90 demand) / slow (P10 demand) cone, against the tank's min-heel floor and shell
 * capacity. The fast-path heel crossing is the conservative "must reorder by" day.
 */
export default function BurnDownChart({ burndown }: { burndown: DemandBurndown }) {
  const data = burndown.series.map((p) => ({
    x: p.date,
    cone: [p.fast, p.slow] as [number, number],
    p50: p.p50,
    heel: p.heel,
    capacity: p.capacity,
  }));
  const breach = burndown.breach_day != null ? burndown.series[burndown.breach_day]?.date : null;

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
            labelFormatter={(l) => `${l}`}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Area type="monotone" dataKey="cone" name="Fast–slow demand" stroke="none" fill="#fde68a" fillOpacity={0.5} isAnimationActive={false} />
          <Line type="monotone" dataKey="capacity" name="Tank capacity" stroke="#cbd5e1" strokeWidth={1} dot={false} isAnimationActive={false} />
          <Line type="monotone" dataKey="p50" name="Projected (P50)" stroke="#0891b2" strokeWidth={2} dot={false} isAnimationActive={false} />
          <Line type="monotone" dataKey="heel" name="Min heel" stroke="#dc2626" strokeWidth={1.2} strokeDasharray="5 3" dot={false} isAnimationActive={false} />
          {breach && <ReferenceLine x={breach} stroke="#dc2626" strokeDasharray="3 3" label={{ value: "heel risk", fontSize: 10, fill: "#dc2626", position: "insideTopRight" }} />}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
