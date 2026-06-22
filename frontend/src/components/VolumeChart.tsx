import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";
import type { MonthlyVolumePoint } from "../api/types";

export default function VolumeChart({ points }: { points: MonthlyVolumePoint[] }) {
  const data = points.map((p) => ({
    month: p.month,
    mmgal: Number((p.net_gallons / 1e6).toFixed(2)),
  }));

  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
          <XAxis dataKey="month" tick={{ fontSize: 10 }} interval={2} />
          <YAxis tick={{ fontSize: 11 }} width={40} />
          <Tooltip formatter={(v) => [`${Number(v).toFixed(2)} MM gal`, "Net volume"]} />
          <Bar dataKey="mmgal" fill="#2563eb" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
