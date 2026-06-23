import type { Regime, RegimeConfig } from "../api/types";

/**
 * The REGIME SELECTOR — four segmented controls (inventory / market / capacity / credit).
 * Changing any axis updates the regime; the parent re-fetches the backend, which re-ranks
 * everything through the V1 regime-multiplier matrix.
 */
export default function RegimeSelector({
  config,
  regime,
  onChange,
  compact = false,
}: {
  config: RegimeConfig;
  regime: Regime;
  onChange: (r: Regime) => void;
  compact?: boolean;
}) {
  return (
    <div className={`grid gap-3 ${compact ? "grid-cols-2 lg:grid-cols-4" : "grid-cols-1 sm:grid-cols-2 lg:grid-cols-4"}`}>
      {Object.entries(config.axes).map(([axis, cfg]) => {
        const current = regime[axis] ?? cfg.default;
        const hint = cfg.states[current]?.hint;
        return (
          <div key={axis}>
            <div className="mb-1 flex items-baseline justify-between">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{cfg.label}</span>
            </div>
            <div className="flex flex-wrap gap-1 rounded-lg bg-slate-100 p-0.5">
              {Object.entries(cfg.states).map(([state, sc]) => (
                <button
                  key={state}
                  title={sc.hint}
                  onClick={() => onChange({ ...regime, [axis]: state })}
                  className={`rounded-md px-2 py-1 text-[11px] font-medium transition ${
                    state === current ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"
                  }`}
                >
                  {sc.label}
                </button>
              ))}
            </div>
            {!compact && hint && <p className="mt-1 text-[10px] text-slate-400">{hint}</p>}
          </div>
        );
      })}
    </div>
  );
}
