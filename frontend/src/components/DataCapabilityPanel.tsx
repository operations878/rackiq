import type { Capabilities, Feature } from "../api/types";
import { humanize, pct } from "../lib/format";

/**
 * Live "Data Capability" panel for Data Studio: every feature is either unlocked (green,
 * with coverage) or locked with an actionable "feed me <field>" hint. Reads the same
 * capability matrix the dashboard does, so feeding data lights features up in real time.
 */
function Row({ f }: { f: Feature }) {
  return (
    <div
      className={`flex items-start justify-between gap-3 rounded-md border px-2.5 py-1.5 ${
        f.enabled ? "border-emerald-200 bg-emerald-50" : "border-slate-200 bg-slate-50"
      }`}
    >
      <div className="min-w-0">
        <div className="flex items-center gap-1.5">
          <span className={`text-xs ${f.enabled ? "text-emerald-600" : "text-slate-400"}`}>
            {f.enabled ? "✓" : "🔒"}
          </span>
          <span className="truncate text-xs font-medium text-slate-700">{f.label}</span>
        </div>
        {!f.enabled && (
          <p className="mt-0.5 text-[11px] text-amber-700">
            Feed me: {f.missing_fields.map(humanize).join(", ")}
          </p>
        )}
      </div>
      {f.enabled && (
        <span className="shrink-0 text-[10px] font-medium text-emerald-700">{pct(f.coverage)}</span>
      )}
    </div>
  );
}

export default function DataCapabilityPanel({ caps }: { caps: Capabilities }) {
  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between">
        <span className="text-xs uppercase tracking-wide text-slate-500">Data Capability</span>
        <span className="text-xs font-semibold text-slate-700">
          {caps.summary.enabled}/{caps.summary.total} unlocked
        </span>
      </div>
      {caps.categories.map((cat) => {
        const feats = caps.features.filter((f) => f.category === cat);
        if (feats.length === 0) return null;
        const on = feats.filter((f) => f.enabled).length;
        return (
          <div key={cat}>
            <div className="mb-1 flex items-center gap-2">
              <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{cat}</h3>
              <span className="text-[11px] text-slate-400">
                {on}/{feats.length}
              </span>
            </div>
            <div className="space-y-1">
              {feats.map((f) => (
                <Row key={f.key} f={f} />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
