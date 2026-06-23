import { useRef, useState } from "react";
import type { SavedProfile } from "../../api/types";
import { humanize } from "../../lib/format";

export default function UploadStep({
  onFile,
  onLoadDemo,
  onReset,
  profiles,
  onDeleteProfile,
  busy,
}: {
  onFile: (file: File) => void;
  onLoadDemo: (profile: string) => void;
  onReset: () => void;
  profiles: SavedProfile[];
  onDeleteProfile: (name: string) => void;
  busy: string | null;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [demoProfile, setDemoProfile] = useState("full");

  return (
    <div className="space-y-5">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const f = e.dataTransfer.files?.[0];
          if (f) onFile(f);
        }}
        className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-12 text-center transition ${
          dragOver ? "border-blue-400 bg-blue-50" : "border-slate-300 bg-slate-50"
        }`}
      >
        <div className="text-3xl">📄</div>
        <p className="mt-3 text-sm font-medium text-slate-700">
          Drop a CSV or Excel file here
        </p>
        <p className="mt-1 text-xs text-slate-500">.csv · .tsv · .xlsx — lifts, AR, inventory, or market prices</p>
        <button
          onClick={() => inputRef.current?.click()}
          disabled={!!busy}
          className="mt-4 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {busy === "inspect" ? "Inspecting…" : "Choose file"}
        </button>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.tsv,.txt,.xlsx,.xls"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onFile(f);
            e.target.value = "";
          }}
        />
      </div>

      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-white p-4">
        <span className="text-xs font-medium text-slate-600">No file handy?</span>
        <select
          value={demoProfile}
          onChange={(e) => setDemoProfile(e.target.value)}
          className="rounded border border-slate-300 px-2 py-1 text-xs"
        >
          <option value="core">core (4 features)</option>
          <option value="lite">lite (6 features)</option>
          <option value="full">full (21 features)</option>
        </select>
        <button
          onClick={() => onLoadDemo(demoProfile)}
          disabled={!!busy}
          className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          {busy === "demo" ? "Loading…" : "Load demo data"}
        </button>
        <span className="text-slate-300">|</span>
        <button
          onClick={onReset}
          disabled={!!busy}
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-50"
        >
          {busy === "reset" ? "Clearing…" : "Reset to empty"}
        </button>
      </div>

      {profiles.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Saved mapping profiles
          </h3>
          <ul className="space-y-1.5">
            {profiles.map((p) => (
              <li key={p.name} className="flex items-center justify-between text-sm">
                <span className="text-slate-700">
                  {p.name}{" "}
                  <span className="text-xs text-slate-400">
                    → {humanize(p.target_table)} · {p.source_columns.length} cols
                  </span>
                </span>
                <button
                  onClick={() => onDeleteProfile(p.name)}
                  className="text-xs text-slate-400 hover:text-red-600"
                >
                  delete
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[11px] text-slate-400">
            Re-upload a file with matching columns and its profile is applied automatically.
          </p>
        </div>
      )}
    </div>
  );
}
