import type { Capabilities, Feature } from "../api/types";

function FeatureCard({ f }: { f: Feature }) {
  const collecting = f.kind === "feed" && f.status === "collecting";
  const tone = collecting
    ? "border-indigo-200 bg-indigo-50"
    : f.enabled
    ? "border-emerald-200 bg-emerald-50"
    : "border-slate-200 bg-slate-50";
  const barColor = collecting ? "bg-indigo-500" : "bg-emerald-500";
  return (
    <div
      className={`rounded-lg border p-3 ${tone}`}
      title={
        f.enabled
          ? `requires: ${f.required_fields.join(", ")}`
          : `missing: ${f.missing_fields.join(", ")}`
      }
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-slate-800">{f.label}</span>
        {collecting ? (
          <span className="shrink-0 rounded-full bg-indigo-100 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-indigo-700">
            collecting
          </span>
        ) : (
          <span className={`h-2 w-2 shrink-0 rounded-full ${f.enabled ? "bg-emerald-500" : "bg-slate-300"}`} />
        )}
      </div>
      <p className="mt-1 text-xs leading-snug text-slate-500">{f.description}</p>
      {f.kind === "feed" && f.collecting ? (
        <div className="mt-2">
          <div className="h-1.5 w-full overflow-hidden rounded bg-slate-200">
            <div className={`h-1.5 rounded ${barColor}`} style={{ width: `${Math.round(f.coverage * 100)}%` }} />
          </div>
          <p className={`mt-1 text-[11px] ${collecting ? "text-indigo-700" : "text-emerald-700"}`}>
            {f.collecting.label}
            {f.collecting.rejections != null && <> · {f.collecting.rejections} rejections</>}
          </p>
        </div>
      ) : f.enabled ? (
        <div className="mt-2">
          <div className="h-1.5 w-full overflow-hidden rounded bg-slate-200">
            <div className="h-1.5 rounded bg-emerald-500" style={{ width: `${Math.round(f.coverage * 100)}%` }} />
          </div>
          {f.enhanced_by.length > 0 && (
            <p className="mt-1 text-[11px] text-emerald-700">+ {f.enhanced_by.join(", ")}</p>
          )}
        </div>
      ) : (
        <p className="mt-2 text-xs text-amber-700">Needs: {f.missing_fields.join(", ")}</p>
      )}
    </div>
  );
}

export default function CapabilityGrid({ caps }: { caps: Capabilities }) {
  return (
    <div className="space-y-4">
      {caps.categories.map((cat) => {
        const feats = caps.features.filter((f) => f.category === cat);
        if (feats.length === 0) return null;
        const on = feats.filter((f) => f.enabled).length;
        return (
          <div key={cat}>
            <div className="mb-2 flex items-center gap-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-600">{cat}</h3>
              <span className="text-xs text-slate-400">
                {on}/{feats.length}
              </span>
            </div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {feats.map((f) => (
                <FeatureCard key={f.key} f={f} />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
