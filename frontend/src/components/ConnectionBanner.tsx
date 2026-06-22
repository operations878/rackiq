import type { Summary } from "../api/types";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-2xl font-semibold text-slate-900">{value}</div>
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
    </div>
  );
}

function rangeMonths(s: Summary): string {
  if (!s.date_range.start || !s.date_range.end) return "—";
  const a = new Date(s.date_range.start);
  const b = new Date(s.date_range.end);
  const m = (b.getFullYear() - a.getFullYear()) * 12 + (b.getMonth() - a.getMonth());
  return String(m + 1);
}

export default function ConnectionBanner({ summary }: { summary: Summary }) {
  const gal = (summary.total_net_gallons / 1e6).toFixed(1);
  return (
    <div className="rounded-xl border border-emerald-200 bg-white p-5 shadow-sm">
      <div className="flex items-center gap-2">
        <span className="relative flex h-3 w-3">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
          <span className="relative inline-flex h-3 w-3 rounded-full bg-emerald-500" />
        </span>
        <span className="text-sm font-medium text-emerald-700">
          Connected — {summary.customers} customers loaded
        </span>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Customers" value={String(summary.customers)} />
        <Stat label="Lifts" value={summary.lifts.toLocaleString()} />
        <Stat label="Net MM gal" value={gal} />
        <Stat label="Terminals" value={String(summary.terminals.length)} />
        <Stat label="Products" value={String(summary.products.length)} />
        <Stat label="Months" value={rangeMonths(summary)} />
      </div>

      <div className="mt-3 text-xs text-slate-400">
        {summary.date_range.start} → {summary.date_range.end}
        {summary.terminals.length > 0 && <> · {summary.terminals.join(", ")}</>}
        {summary.products.length > 0 && <> · {summary.products.join(", ")}</>}
      </div>
    </div>
  );
}
