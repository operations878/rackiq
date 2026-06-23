import { useState } from "react";
import type { InspectResponse, SourceColumn } from "../../api/types";

function scoreTone(score: number): string {
  if (score >= 90) return "text-emerald-600";
  if (score >= 75) return "text-amber-600";
  return "text-red-600";
}

function FlagChip({ level, message }: { level: string; message: string }) {
  const cls = level === "warn"
    ? "bg-amber-100 text-amber-700"
    : "bg-slate-100 text-slate-500";
  return <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${cls}`}>{message}</span>;
}

function Row({ col }: { col: SourceColumn }) {
  const flags = col.flags ?? [];
  const range =
    col.min !== null && col.min !== undefined
      ? `${col.min} … ${col.max}`
      : "—";
  return (
    <tr className="border-t border-slate-100 align-top">
      <td className="px-3 py-2">
        <div className="font-medium text-slate-700">{col.name}</div>
        <div className="text-[11px] text-slate-400">{col.dtype_guess}</div>
      </td>
      <td className="px-3 py-2 text-right text-xs text-slate-600">{col.distinct ?? "—"}</td>
      <td className="px-3 py-2 text-right text-xs text-slate-600">{Math.round(col.null_rate * 100)}%</td>
      <td className="px-3 py-2 text-xs text-slate-600">{range}</td>
      <td className="px-3 py-2 text-right text-xs text-slate-600">{col.outliers ?? 0}</td>
      <td className="px-3 py-2">
        <div className="flex flex-wrap gap-1">
          {flags.length === 0 && <span className="text-[11px] text-emerald-600">✓ clean</span>}
          {flags.map((f, i) => (
            <FlagChip key={i} level={f.level} message={f.message} />
          ))}
        </div>
      </td>
    </tr>
  );
}

export default function ProfilingScorecard({ inspect }: { inspect: InspectResponse }) {
  const [open, setOpen] = useState(true);
  const score = inspect.profile?.score ?? 100;
  const flagged = inspect.profile?.n_flagged_columns ?? 0;

  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-2.5"
      >
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Data-quality scorecard
          </span>
          <span className="text-[11px] text-slate-400">
            {inspect.n_columns} cols · {flagged} flagged
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-sm font-bold ${scoreTone(score)}`}>{score}</span>
          <span className="text-[11px] text-slate-400">/100</span>
          <span className="text-slate-300">{open ? "▾" : "▸"}</span>
        </div>
      </button>
      {open && (
        <div className="overflow-x-auto border-t border-slate-100">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-[11px] uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-3 py-2">Column</th>
                <th className="px-3 py-2 text-right">Distinct</th>
                <th className="px-3 py-2 text-right">Null</th>
                <th className="px-3 py-2">Min … Max</th>
                <th className="px-3 py-2 text-right">Outliers</th>
                <th className="px-3 py-2">Flags</th>
              </tr>
            </thead>
            <tbody>
              {inspect.columns.map((c) => (
                <Row key={c.name} col={c} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
