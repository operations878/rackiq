import { useState } from "react";
import type { RuleResult, ValidateResponse } from "../../api/types";
import { humanize } from "../../lib/format";

function Stat({ label, value, tone }: { label: string; value: string; tone?: "ok" | "warn" }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <div
        className={`text-xl font-semibold ${
          tone === "warn" ? "text-amber-600" : tone === "ok" ? "text-emerald-600" : "text-slate-800"
        }`}
      >
        {value}
      </div>
      <div className="text-[11px] uppercase tracking-wide text-slate-400">{label}</div>
    </div>
  );
}

function sevDot(sev: string, passed: boolean): string {
  if (passed) return "bg-emerald-500";
  return sev === "error" ? "bg-red-500" : "bg-amber-500";
}

function RuleCard({ r }: { r: RuleResult }) {
  const [open, setOpen] = useState(false);
  const cols = r.rows[0] ? Object.keys(r.rows[0].values) : [];
  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <button
        onClick={() => r.count > 0 && setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2 text-left"
      >
        <div className="flex items-center gap-2">
          <span className={`h-2 w-2 rounded-full ${sevDot(r.severity, r.passed)}`} />
          <span className="text-sm text-slate-700">{r.label}</span>
          {r.action === "quarantine" && r.count > 0 && (
            <span className="rounded bg-red-50 px-1.5 py-0.5 text-[10px] font-medium text-red-600">
              → quarantine
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-xs ${r.passed ? "text-emerald-600" : "text-slate-600"}`}>
            {r.passed ? "pass" : `${r.count} row${r.count === 1 ? "" : "s"}`}
          </span>
          {r.count > 0 && <span className="text-slate-300">{open ? "▾" : "▸"}</span>}
        </div>
      </button>
      {open && r.rows.length > 0 && (
        <div className="overflow-x-auto border-t border-slate-100">
          <table className="w-full text-xs">
            <thead className="bg-slate-50 text-left text-[10px] uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-2 py-1">Row</th>
                {cols.map((c) => (
                  <th key={c} className="px-2 py-1">
                    {humanize(c)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {r.rows.map((row, i) => (
                <tr key={i} className="border-t border-slate-100">
                  <td className="px-2 py-1 font-mono text-slate-400">{row.row}</td>
                  {cols.map((c) => (
                    <td key={c} className="px-2 py-1 text-slate-700">
                      {row.values[c] === null ? <span className="text-red-400">∅</span> : String(row.values[c])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {r.count > r.rows.length && (
            <div className="px-2 py-1 text-[10px] text-slate-400">
              showing {r.rows.length} of {r.count} offending rows
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ValidateStep({
  validation,
  mode,
  onChangeMode,
  onBack,
  onCommit,
  busy,
}: {
  validation: ValidateResponse;
  mode: string;
  onChangeMode: (mode: string) => void;
  onBack: () => void;
  onCommit: () => void;
  busy: string | null;
}) {
  const v = validation;
  const range = v.date_range.start ? `${v.date_range.start} → ${v.date_range.end}` : "—";

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="Rows in file" value={v.n_rows.toLocaleString()} />
        <Stat label="Clean → store" value={v.clean_rows.toLocaleString()} tone="ok" />
        <Stat
          label="Quarantined"
          value={String(v.quarantine_count)}
          tone={v.quarantine_count ? "warn" : undefined}
        />
        <Stat
          label="Rule warnings"
          value={String(v.rule_warnings)}
          tone={v.rule_warnings ? "warn" : undefined}
        />
        <Stat label="Parse errors" value={String(v.total_parse_errors)} tone={v.total_parse_errors ? "warn" : undefined} />
      </div>

      <div className="rounded-lg border border-slate-200 bg-white p-3 text-sm">
        <span className="text-slate-500">Date range </span>
        <span className="font-medium text-slate-700">
          ({v.date_range.column ? humanize(v.date_range.column) : "—"}):
        </span>{" "}
        <span className="font-mono text-slate-700">{range}</span>
      </div>

      {/* What the approved fixes will do */}
      {v.fixes_preview.length > 0 && (
        <div className="rounded-lg border border-blue-200 bg-blue-50/60 p-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-blue-700">
            Hygiene fixes to apply
          </div>
          <ul className="mt-1 space-y-0.5 text-xs text-slate-600">
            {v.fixes_preview.map((s, i) => (
              <li key={i}>
                <span className="font-mono text-[11px] text-slate-500">{s.step}</span> — {s.detail}
                {s.rows_affected > 0 && (
                  <span className="ml-1 font-medium text-blue-600">({s.rows_affected})</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Validation rules with drill-down */}
      <div>
        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Validation rules — click a failing rule to drill down
        </div>
        <div className="space-y-1.5">
          {v.rules.map((r) => (
            <RuleCard key={r.key} r={r} />
          ))}
        </div>
      </div>

      {/* Mapping errors block commit */}
      {v.errors.length > 0 && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3">
          <div className="text-xs font-semibold text-red-700">Must fix before importing</div>
          <ul className="mt-1 list-inside list-disc text-xs text-red-700">
            {v.errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Mode + actions */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3 text-xs text-slate-600">
          <span className="font-medium">Write mode:</span>
          <label className="flex items-center gap-1">
            <input type="radio" checked={mode === "replace"} onChange={() => onChangeMode("replace")} />
            Replace {humanize(v.table)}
          </label>
          <label className="flex items-center gap-1">
            <input type="radio" checked={mode === "append"} onChange={() => onChangeMode("append")} />
            Append
          </label>
        </div>
        <div className="flex gap-2">
          <button
            onClick={onBack}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
          >
            ← Back
          </button>
          <button
            onClick={onCommit}
            disabled={!v.can_commit || !!busy}
            className="rounded-lg bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-40"
          >
            {busy === "commit"
              ? "Importing…"
              : `Commit ${v.clean_rows.toLocaleString()} rows → ${humanize(v.table)}`}
          </button>
        </div>
      </div>
    </div>
  );
}
