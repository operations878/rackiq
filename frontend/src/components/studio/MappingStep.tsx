import type { InspectResponse } from "../../api/types";
import { humanize } from "../../lib/format";

const SKIP = "";

export default function MappingStep({
  inspect,
  table,
  mapping,
  onChangeTable,
  onChangeMapping,
  saveProfileName,
  onChangeSaveProfileName,
  onBack,
  onValidate,
  busy,
}: {
  inspect: InspectResponse;
  table: string;
  mapping: Record<string, string>;
  onChangeTable: (table: string) => void;
  onChangeMapping: (source: string, target: string) => void;
  saveProfileName: string;
  onChangeSaveProfileName: (name: string) => void;
  onBack: () => void;
  onValidate: () => void;
  busy: string | null;
}) {
  const targets = inspect.targets_by_table[table] ?? [];
  const required = inspect.required_keys[table] ?? [];
  const usedTargets = new Set(Object.values(mapping).filter(Boolean));
  const mappedRequired = required.filter((r) => usedTargets.has(r));
  const missingRequired = required.filter((r) => !usedTargets.has(r));
  const canValidate = missingRequired.length === 0 && !busy;

  return (
    <div className="space-y-4">
      {/* Target table selector */}
      <div>
        <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          What kind of data is this?
        </label>
        <div className="mt-2 flex flex-wrap gap-2">
          {inspect.targets_by_table &&
            Object.keys(inspect.targets_by_table).map((t) => (
              <button
                key={t}
                onClick={() => onChangeTable(t)}
                className={`rounded-lg border px-3 py-1.5 text-xs font-medium ${
                  t === table
                    ? "border-slate-900 bg-slate-900 text-white"
                    : "border-slate-300 bg-white text-slate-600 hover:bg-slate-50"
                }`}
              >
                {inspect.table_labels[t] ?? humanize(t)}
                {t === inspect.suggested_table && t !== table && (
                  <span className="ml-1 text-emerald-600">• suggested</span>
                )}
              </button>
            ))}
        </div>
      </div>

      {/* Required-field checklist */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
        <span className="text-xs font-medium text-slate-600">Required:</span>
        {required.map((r) => {
          const ok = mappedRequired.includes(r);
          return (
            <span
              key={r}
              className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                ok ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700"
              }`}
            >
              {ok ? "✓ " : "• "}
              {humanize(r)}
            </span>
          );
        })}
        {required.length === 0 && <span className="text-xs text-slate-400">none</span>}
      </div>

      {/* Column mapping table */}
      <div className="overflow-x-auto rounded-lg border border-slate-200">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-[11px] uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-3 py-2">Source column</th>
              <th className="px-3 py-2">Sample values</th>
              <th className="px-3 py-2 text-right">Null</th>
              <th className="px-3 py-2">Maps to canonical field</th>
            </tr>
          </thead>
          <tbody>
            {inspect.columns.map((col) => {
              const value = mapping[col.name] ?? SKIP;
              const suggestion = inspect.suggestions_by_table[table]?.[col.name];
              const isAuto = !!suggestion && value === suggestion.target;
              return (
                <tr key={col.name} className="border-t border-slate-100 align-top">
                  <td className="px-3 py-2">
                    <div className="font-medium text-slate-700">{col.name}</div>
                    <div className="text-[11px] text-slate-400">{col.dtype_guess}</div>
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {col.samples.slice(0, 3).map((s, i) => (
                        <span key={i} className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">
                          {s.length > 22 ? s.slice(0, 22) + "…" : s}
                        </span>
                      ))}
                      {col.samples.length === 0 && <span className="text-[11px] text-slate-300">(empty)</span>}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-right text-xs text-slate-500">
                    {Math.round(col.null_rate * 100)}%
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-2">
                      <select
                        value={value}
                        onChange={(e) => onChangeMapping(col.name, e.target.value)}
                        className="w-56 rounded border border-slate-300 px-2 py-1 text-xs"
                      >
                        <option value={SKIP}>— skip —</option>
                        {targets.map((t) => {
                          const takenElsewhere = usedTargets.has(t.name) && value !== t.name;
                          return (
                            <option key={t.name} value={t.name} disabled={takenElsewhere} title={t.description}>
                              {humanize(t.name)}
                              {t.required ? " *" : ""}
                              {t.canonical ? "" : " (key)"}
                              {takenElsewhere ? " — used" : ""}
                            </option>
                          );
                        })}
                      </select>
                      {isAuto && suggestion && (
                        <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-600">
                          auto {Math.round(suggestion.confidence * 100)}%
                        </span>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Save profile + actions */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="flex items-center gap-2 text-xs text-slate-600">
          Save mapping as profile:
          <input
            value={saveProfileName}
            onChange={(e) => onChangeSaveProfileName(e.target.value)}
            placeholder="(optional name)"
            className="rounded border border-slate-300 px-2 py-1 text-xs"
          />
        </label>
        <div className="flex gap-2">
          <button
            onClick={onBack}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
          >
            ← Back
          </button>
          <button
            onClick={onValidate}
            disabled={!canValidate}
            title={missingRequired.length ? `Map: ${missingRequired.map(humanize).join(", ")}` : ""}
            className="rounded-lg bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-40"
          >
            Next: Clean →
          </button>
        </div>
      </div>
    </div>
  );
}
