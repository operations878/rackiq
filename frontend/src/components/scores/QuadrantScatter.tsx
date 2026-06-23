import {
  ResponsiveContainer,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  ZAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
  Cell,
} from "recharts";
import type { QuadrantPoint, QuadrantResponse } from "../../api/types";

const QUAD_COLOR: Record<string, string> = {
  "Strategic Lever": "#16a34a",
  "Premium Spot": "#2563eb",
  "Managed Cost": "#d97706",
  "Dangerous Noise": "#dc2626",
};

function median(xs: number[]): number {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

export default function QuadrantScatter({
  data,
  onSelect,
}: {
  data: QuadrantResponse;
  onSelect?: (id: string) => void;
}) {
  const pts = data.points;
  if (!pts.length) {
    return <div className="text-sm text-slate-500">No scored customers with explainability + profitability yet.</div>;
  }
  const mx = median(pts.map((p) => p.explainability));
  const my = median(pts.map((p) => p.profitability));
  const chart = pts.map((p) => ({ ...p, z: Math.max(40, Math.sqrt(p.total_net_gallons)) }));

  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-2 text-[11px]">
        {Object.entries(QUAD_COLOR).map(([k, c]) => (
          <span key={k} className="inline-flex items-center gap-1">
            <span className="h-2 w-2 rounded-full" style={{ background: c }} /> {k}
          </span>
        ))}
      </div>
      <div className="h-80 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 10, right: 16, bottom: 20, left: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
            <XAxis
              type="number"
              dataKey="explainability"
              name="Explainability (EVR)"
              tick={{ fontSize: 10 }}
              domain={[0, "dataMax"]}
              label={{ value: "Explainability (EVR) →", position: "insideBottom", offset: -8, fontSize: 11 }}
            />
            <YAxis
              type="number"
              dataKey="profitability"
              name="Profitability"
              tick={{ fontSize: 10 }}
              domain={[0, 100]}
              label={{ value: "Profitability →", angle: -90, position: "insideLeft", fontSize: 11 }}
            />
            <ZAxis type="number" dataKey="z" range={[30, 320]} />
            <ReferenceLine x={mx} stroke="#94a3b8" strokeDasharray="4 4" />
            <ReferenceLine y={my} stroke="#94a3b8" strokeDasharray="4 4" />
            <Tooltip
              cursor={{ strokeDasharray: "3 3" }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload as QuadrantPoint;
                return (
                  <div className="rounded border border-slate-200 bg-white p-2 text-[11px] shadow">
                    <div className="font-semibold text-slate-700">{p.name}</div>
                    <div className="text-slate-500">{p.primary_archetype}</div>
                    <div className="mt-1 text-slate-600">
                      EVR {p.explainability} · Profit pct {p.profitability}
                    </div>
                    <div className="text-slate-500">
                      VAR {p.var_score ?? "—"} · Base value {p.base_value}
                    </div>
                    <div className="font-medium" style={{ color: QUAD_COLOR[p.quadrant] }}>{p.quadrant}</div>
                  </div>
                );
              }}
            />
            <Scatter
              data={chart}
              onClick={(e) => onSelect?.((e as unknown as QuadrantPoint).customer_id)}
              cursor="pointer"
            >
              {chart.map((p) => (
                <Cell key={p.customer_id} fill={QUAD_COLOR[p.quadrant] ?? "#64748b"} fillOpacity={p.data_sufficient ? 0.85 : 0.35} />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
