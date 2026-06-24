import { useState } from "react";
import type { RuleResult, ValidateResponse } from "../../api/types";
import { humanize } from "../../lib/format";

function Stat({ label, value, tone }: { label: string; value: string; tone?: "ok" | "warn" | "bad" }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <div
        className={`text-xl font-semibold ${
          tone === "bad"
            ? "text-red-600"
            : tone === "warn"
              ? "text-amber-600"
              : tone === "ok"
                ? "text-emerald-600"
                : "text-slate-800"
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
  const dropped = v.dropped_rows ?? 0;
  // BOL/EDI lift imports: compartment rows are grouped & summed into lifts; show the result.
  const grouped = v.lifts_after_grouping;
  const showGrouped = grouped != null && grouped !== v.clean_rows;
  const corrections = v.corrections ?? 0;
  const nStats = 5 + (dropped > 0 ? 1 : 0) + (showGrouped ? 1 : 0) + (corrections > 0 ? 1 : 0);
  const lgColsClass =
    nStats >= 7 ? "lg:grid-cols-7" : nStats === 6 ? "lg:grid-cols-6" : "lg:grid-cols-5";

  // Required keys that explain a failing "required fields present" rule.
  const reqProblems = (v.required_status ?? []).filter((r) => !r.mapped || r.all_null);
  // Per-column parse failures, worst first.
  const parseFields = v.fields
    .filter((f) => f.parse_errors > 0)
    .sort((a, b) => b.parse_errors - a.parse_errors);
  const srcFor = (target: string) => v.fields.find((f) => f.target === target)?.source;

  return (
    <div className="space-y-4">
      <div className={`grid grid-cols-2 gap-3 sm:grid-cols-3 ${lgColsClass}`}>
        <Stat label="Rows in file" value={v.n_rows.toLocaleString()} />
        <Stat
          label={showGrouped ? "Clean rows" : "Clean → store"}
          value={v.clean_rows.toLocaleString()}
          tone={v.clean_rows ? "ok" : "bad"}
        />
        {showGrouped && (
          <Stat label="Lifts (BOL-grouped)" value={grouped!.toLocaleString()} tone="ok" />
        )}
        {corrections > 0 && (
          <Stat label="Corrections (kept)" value={corrections.toLocaleString()} tone="warn" />
        )}
        <Stat
          label="Quarantined"
          value={v.quarantine_count.toLocaleString()}
          tone={v.quarantine_count ? "warn" : undefined}
        />
        {dropped > 0 && <Stat label="Dropped" value={dropped.toLocaleString()} tone="bad" />}
        <Stat
          label="Rule warnings"
          value={String(v.rule_warnings)}
          tone={v.rule_warnings ? "warn" : undefined}
        />
        <Stat
          label="Parse errors"
          value={v.total_parse_errors.toLocaleString()}
          tone={v.total_parse_errors ? "warn" : undefined}
        />
      </div>

      {/* Why "required fields present" is failing — unmapped vs blank/unparseable keys. */}
      {reqProblems.length > 0 && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-red-700">
            Required fields need attention
          </div>
          <ul className="mt-1 space-y-0.5 text-xs text-red-700">
            {reqProblems.map((r) => (
              <li key={r.field}>
                <span className="font-mono">{humanize(r.field)}</span>{" "}
                {!r.mapped ? (
                  <>— not mapped to any column.</>
                ) : (
                  <>
                    — mapped to <span className="font-mono">{srcFor(r.field) ?? "?"}</span> but every value is
                    blank or unparseable.
                  </>
                )}
              </li>
            ))}
          </ul>
          <div className="mt-1.5 text-[11px] text-red-600/80">
            Fix the mapping on the previous step, or fill a default for terminal / product in the Clean
            step — then these rows import instead of being held.
          </div>
        </div>
      )}

      {/* Per-column parse-error breakdown, with sample failing values. */}
      {parseFields.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50/60 p-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-amber-700">
            Values that couldn&apos;t be parsed (became blank)
          </div>
          <ul className="mt-1 space-y-0.5 text-xs text-slate-600">
            {parseFields.map((f) => (
              <li key={f.target}>
                <span className="font-mono text-[11px] text-slate-500">{f.source}</span> →{" "}
                <span className="font-medium">{humanize(f.target)}</span>:{" "}
                <span className="font-medium text-amber-700">{f.parse_errors.toLocaleString()}</span> value
                {f.parse_errors === 1 ? "" : "s"}
                {f.parse_error_samples && f.parse_error_samples.length > 0 && (
                  <span className="text-slate-500">
                    {" "}
                    (e.g.{" "}
                    {f.parse_error_samples.slice(0, 4).map((s, i) => (
                      <span key={i} className="font-mono text-[11px]">
                        {i > 0 ? ", " : ""}
                        {JSON.stringify(s)}
                      </span>
                    ))}
                    )
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

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
              : `Commit ${(showGrouped ? grouped! : v.clean_rows).toLocaleString()} ${
                  showGrouped ? "lifts" : "rows"
                } → ${humanize(v.table)}`}
          </button>
        </div>
      </div>
    </div>
  );
}
