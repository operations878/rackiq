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
import type { CreditRow } from "../../api/types";

// x = VAR (supply / variability risk), y = credit score (financial risk). Higher = safer on both.
export const QUAD_COLOR: Record<string, string> = {
  Anchor: "#16a34a",
  "Watch – Supply": "#2563eb",
  "Watch – Credit": "#d97706",
  Danger: "#dc2626",
};

export default function AccountRiskScatter({
  rows,
  varCut,
  creditCut,
  onSelect,
}: {
  rows: CreditRow[];
  varCut: number;
  creditCut: number;
  onSelect?: (id: string) => void;
}) {
  const pts = rows.filter((r) => r.var_score != null && r.credit_score != null);
  if (!pts.length) {
    return <div className="text-sm text-slate-500">No customers with both a VAR and credit score yet.</div>;
  }
  const chart = pts.map((p) => ({
    ...p,
    z: Math.max(40, Math.sqrt(Math.max(0, p.total_net_gallons ?? 0))),
  }));

  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-3 text-[11px]">
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
              dataKey="var_score"
              name="VAR (supply steadiness)"
              tick={{ fontSize: 10 }}
              domain={[0, 100]}
              label={{ value: "VAR — supply steadiness →", position: "insideBottom", offset: -8, fontSize: 11 }}
            />
            <YAxis
              type="number"
              dataKey="credit_score"
              name="Credit score"
              tick={{ fontSize: 10 }}
              domain={[0, 100]}
              label={{ value: "Credit — pays well →", angle: -90, position: "insideLeft", fontSize: 11 }}
            />
            <ZAxis type="number" dataKey="z" range={[30, 320]} />
            <ReferenceLine x={varCut} stroke="#94a3b8" strokeDasharray="4 4" />
            <ReferenceLine y={creditCut} stroke="#94a3b8" strokeDasharray="4 4" />
            <Tooltip
              cursor={{ strokeDasharray: "3 3" }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload as CreditRow;
                return (
                  <div className="max-w-xs rounded border border-slate-200 bg-white p-2 text-[11px] shadow">
                    <div className="font-semibold text-slate-700">{p.name}</div>
                    <div className="text-slate-500">{p.archetype ?? "—"} · {p.home_terminal ?? "—"}</div>
                    <div className="mt-1 text-slate-600">
                      VAR {p.var_score} · Credit {p.credit_score}/{p.credit_grade}
                    </div>
                    <div className="text-slate-500">
                      DSO {p.dso_days ?? "—"}d · {p.pct_late != null ? `${Math.round(p.pct_late * 100)}% late` : "—"}
                      {p.utilization != null ? ` · ${Math.round(p.utilization * 100)}% of limit` : ""}
                    </div>
                    <div className="mt-1 font-medium" style={{ color: QUAD_COLOR[p.quadrant ?? ""] }}>{p.quadrant}</div>
                  </div>
                );
              }}
            />
            <Scatter
              data={chart}
              onClick={(e) => onSelect?.((e as unknown as CreditRow).customer_id)}
              cursor="pointer"
            >
              {chart.map((p) => (
                <Cell key={p.customer_id} fill={QUAD_COLOR[p.quadrant ?? ""] ?? "#64748b"} fillOpacity={0.8} />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
