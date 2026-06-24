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

/**
 * The total-margin-vs-spread curve for the Pricing Sandbox. The X axis is the posted
 * "our rack vs. street" spread (in cents/gal); the line is the aggregated book margin across the
 * toggled-in customers. Reference lines mark the margin-MAXIMIZING post (green), the current
 * book spread (slate), and the spread the slider is parked on (indigo).
 */
export default function MarginCurveChart({
  curve,
  optimalSpread,
  currentSpread,
  selectedSpread,
}: {
  curve: { spread: number; margin: number | null }[];
  optimalSpread: number | null;
  currentSpread: number | null;
  selectedSpread: number | null;
}) {
  if (!curve.length) return <div className="text-sm text-slate-500">No margin curve.</div>;
  const data = curve
    .filter((p) => p.margin != null)
    .map((p) => ({ x: +(p.spread * 100).toFixed(3), margin: p.margin as number }));
  const cents = (s: number | null) => (s == null ? null : +(s * 100).toFixed(3));
  const usd = (v: number) =>
    Math.abs(v) >= 1e6 ? `$${(v / 1e6).toFixed(2)}MM` : Math.abs(v) >= 1e3 ? `$${(v / 1e3).toFixed(0)}k` : `$${Math.round(v)}`;

  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 14, bottom: 18, left: 6 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
          <XAxis
            dataKey="x"
            type="number"
            domain={["dataMin", "dataMax"]}
            tick={{ fontSize: 10 }}
            tickFormatter={(v) => `${v > 0 ? "+" : ""}${Number(v).toFixed(1)}¢`}
            label={{ value: "our rack vs. street (¢/gal)", fontSize: 10, fill: "#64748b", position: "insideBottom", offset: -8 }}
          />
          <YAxis tick={{ fontSize: 10 }} width={54} tickFormatter={(v) => usd(Number(v))} />
          <Tooltip
            formatter={(v) => [usd(Number(v)), "Total margin/yr"]}
            labelFormatter={(l) => `Spread ${Number(l) > 0 ? "+" : ""}${Number(l).toFixed(2)}¢`}
          />
          {cents(currentSpread) != null && (
            <ReferenceLine x={cents(currentSpread)!} stroke="#94a3b8" strokeDasharray="4 3"
              label={{ value: "current", fontSize: 9, fill: "#64748b", position: "top" }} />
          )}
          {cents(optimalSpread) != null && (
            <ReferenceLine x={cents(optimalSpread)!} stroke="#059669" strokeWidth={1.5}
              label={{ value: "max margin", fontSize: 9, fill: "#059669", position: "top" }} />
          )}
          {cents(selectedSpread) != null && (
            <ReferenceLine x={cents(selectedSpread)!} stroke="#4f46e5" strokeDasharray="2 2" />
          )}
          <Line type="monotone" dataKey="margin" name="Total margin" stroke="#0891b2" strokeWidth={2}
            dot={false} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
