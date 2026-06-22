import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
} from "recharts";
import type { MarketPrices } from "../api/types";

export default function MarketPriceChart({
  data,
  onSelect,
}: {
  data: MarketPrices;
  onSelect: (product: string) => void;
}) {
  if (!data.available) {
    return (
      <div className="text-sm text-slate-500">
        Market price data is not present in this profile — feature disabled.
      </div>
    );
  }

  // Thin to ~every 3rd day to keep the line readable.
  const chart = data.points
    .filter((_, i) => i % 3 === 0)
    .map((p) => ({ date: p.date, market: p.market_price, rack: p.street_rack }));

  return (
    <div>
      <div className="mb-2 flex gap-2">
        {data.products.map((p) => (
          <button
            key={p}
            onClick={() => onSelect(p)}
            className={`rounded px-2 py-0.5 text-xs ${
              p === data.product ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-600"
            }`}
          >
            {p}
          </button>
        ))}
      </div>
      <div className="h-56 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chart} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
            <XAxis dataKey="date" tick={{ fontSize: 9 }} interval={29} />
            <YAxis
              tick={{ fontSize: 11 }}
              width={48}
              domain={["auto", "auto"]}
              tickFormatter={(v) => `$${Number(v).toFixed(2)}`}
            />
            <Tooltip formatter={(v) => `$${Number(v).toFixed(4)}`} />
            <Legend />
            <Line type="monotone" dataKey="market" name="Market" stroke="#2563eb" dot={false} strokeWidth={1.5} />
            <Line type="monotone" dataKey="rack" name="Street rack" stroke="#16a34a" dot={false} strokeWidth={1.5} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
