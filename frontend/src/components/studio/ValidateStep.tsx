import type { ValidateResponse } from "../../api/types";
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
        <Stat label="Importable" value={v.importable_rows.toLocaleString()} tone="ok" />
        <Stat label="Duplicates" value={String(v.duplicate_rows)} tone={v.duplicate_rows ? "warn" : undefined} />
        <Stat label="Parse errors" value={String(v.total_parse_errors)} tone={v.total_parse_errors ? "warn" : undefined} />
        <Stat label="Missing-key rows" value={String(v.droppable_rows)} tone={v.droppable_rows ? "warn" : undefined} />
      </div>

      <div className="rounded-lg border border-slate-200 bg-white p-3 text-sm">
        <span className="text-slate-500">Date range </span>
        <span className="font-medium text-slate-700">
          ({v.date_range.column ? humanize(v.date_range.column) : "—"}):
        </span>{" "}
        <span className="font-mono text-slate-700">{range}</span>
      </div>

      {/* Per-field null rates + parse errors */}
      <div className="overflow-x-auto rounded-lg border border-slate-200">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-[11px] uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-3 py-2">Source → Field</th>
              <th className="px-3 py-2 text-right">Null rate</th>
              <th className="px-3 py-2 text-right">Parse errors</th>
            </tr>
          </thead>
          <tbody>
            {v.fields.map((f) => (
              <tr key={f.target} className="border-t border-slate-100">
                <td className="px-3 py-1.5 text-slate-700">
                  <span className="text-slate-500">{f.source}</span> →{" "}
                  <span className="font-medium">{humanize(f.target)}</span>
                </td>
                <td className="px-3 py-1.5 text-right text-slate-600">{Math.round(f.null_rate * 100)}%</td>
                <td
                  className={`px-3 py-1.5 text-right ${f.parse_errors ? "font-medium text-amber-600" : "text-slate-400"}`}
                >
                  {f.parse_errors}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Errors / warnings */}
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
      {v.warnings.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
          <div className="text-xs font-semibold text-amber-700">Heads up</div>
          <ul className="mt-1 list-inside list-disc text-xs text-amber-700">
            {v.warnings.map((w, i) => (
              <li key={i}>{w}</li>
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
            {busy === "commit" ? "Importing…" : `Commit import → ${humanize(v.table)}`}
          </button>
        </div>
      </div>
    </div>
  );
}
