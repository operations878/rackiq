import type { CommitResponse } from "../../api/types";
import { humanize } from "../../lib/format";

export default function DoneStep({
  result,
  onImportAnother,
  onGoDashboard,
}: {
  result: CommitResponse;
  onImportAnother: () => void;
  onGoDashboard: () => void;
}) {
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-5 text-center">
        <div className="text-3xl">✅</div>
        <h2 className="mt-2 text-lg font-semibold text-emerald-800">
          Imported {result.rows_written.toLocaleString()} rows into {humanize(result.table)}
        </h2>
        <p className="mt-1 text-sm text-emerald-700">
          {result.capabilities.summary.enabled}/{result.capabilities.summary.total} capabilities now
          unlocked
          {result.saved_profile && <> · saved profile “{result.saved_profile}”</>}
        </p>
      </div>

      <div className="rounded-lg border border-slate-200 bg-white p-4">
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Hygiene Studio pipeline
        </h3>
        <ul className="space-y-1 text-sm">
          {result.hygiene.map((s, i) => (
            <li key={i} className="flex items-center justify-between">
              <span className="text-slate-700">
                <span className="font-mono text-xs text-slate-500">{s.step}</span> — {s.detail}
              </span>
              {s.rows_affected > 0 && (
                <span className="text-xs font-medium text-amber-600">{s.rows_affected} rows</span>
              )}
            </li>
          ))}
        </ul>
        <p className="mt-2 text-[11px] text-slate-400">
          {result.rows_in_file.toLocaleString()} rows in file →{" "}
          {result.rows_written.toLocaleString()} written after cleaning.
        </p>
      </div>

      <div className="flex justify-center gap-3">
        <button
          onClick={onImportAnother}
          className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          Import another file
        </button>
        <button
          onClick={onGoDashboard}
          className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
        >
          Go to Dashboard →
        </button>
      </div>
    </div>
  );
}
