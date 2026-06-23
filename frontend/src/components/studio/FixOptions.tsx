import type { HygieneOptions } from "../../api/types";

function Toggle({
  label,
  hint,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className={`flex items-start gap-2 ${disabled ? "opacity-50" : ""}`}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5"
      />
      <span>
        <span className="text-sm text-slate-700">{label}</span>
        {hint && <span className="block text-[11px] text-slate-400">{hint}</span>}
      </span>
    </label>
  );
}

export default function FixOptions({
  table,
  mapping,
  options,
  onChange,
}: {
  table: string;
  mapping: Record<string, string>;
  options: HygieneOptions;
  onChange: (next: HygieneOptions) => void;
}) {
  const set = (patch: Partial<HygieneOptions>) => onChange({ ...options, ...patch });
  const mappedTargets = new Set(Object.values(mapping).filter(Boolean));
  const isLifts = table === "lifts";
  const hasGross = mappedTargets.has("gross_gallons");

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
        Auto-fix on import (with approval)
      </h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Toggle
          label="Trim whitespace"
          hint="Strip stray spaces; blank emptied strings."
          checked={options.trim_whitespace}
          onChange={(v) => set({ trim_whitespace: v })}
        />
        <Toggle
          label="Drop fully-blank rows"
          checked={options.drop_empty_rows}
          onChange={(v) => set({ drop_empty_rows: v })}
        />
        <Toggle
          label="Remove exact-duplicate rows"
          hint="Identical across every mapped column (lossless)."
          checked={options.dedupe_exact}
          onChange={(v) => set({ dedupe_exact: v })}
        />
        <Toggle
          label="Quarantine failing rows"
          hint="Hold invalid rows for review instead of dropping them."
          checked={options.quarantine_failures}
          onChange={(v) => set({ quarantine_failures: v })}
        />
        {isLifts && (
          <Toggle
            label="Quarantine duplicate lifts"
            hint="Same customer · datetime · net gallons → held for review."
            checked={options.dedupe_lifts_grain}
            onChange={(v) => set({ dedupe_lifts_grain: v })}
          />
        )}
        <Toggle
          label="Resolve customers (crosswalk)"
          hint="Rewrite variant ids to their confirmed master."
          checked={options.resolve_customers}
          onChange={(v) => set({ resolve_customers: v })}
        />
      </div>

      {/* Unit standardization */}
      <div className="mt-4 border-t border-slate-100 pt-3">
        <Toggle
          label="Standardize units (barrels → gallons)"
          hint="Multiply volume columns by 42 when the file is in barrels."
          checked={options.standardize_units}
          onChange={(v) => set({ standardize_units: v })}
        />
        {options.standardize_units && (
          <div className="mt-2 flex items-center gap-2 pl-6 text-xs text-slate-600">
            Source unit:
            <select
              value={options.source_unit}
              onChange={(e) => set({ source_unit: e.target.value })}
              className="rounded border border-slate-300 px-2 py-1 text-xs"
            >
              <option value="gallons">gallons (no change)</option>
              <option value="barrels">barrels (×42 → gallons)</option>
            </select>
          </div>
        )}
      </div>

      {/* Net-60 correction (lifts only, gated on gross) */}
      {isLifts && (
        <div className="mt-4 border-t border-slate-100 pt-3">
          <div className="text-sm font-medium text-slate-700">Net (60°F) correction</div>
          <p className="text-[11px] text-slate-400">
            {hasGross
              ? "Gross is mapped — compute ASTM D1250 net at 60°F."
              : "Map gross_gallons to enable D1250 net correction; otherwise net is used as-is."}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-3 pl-1 text-xs text-slate-600">
            <select
              value={options.net_correction}
              disabled={!hasGross}
              onChange={(e) => set({ net_correction: e.target.value })}
              className="rounded border border-slate-300 px-2 py-1 text-xs disabled:opacity-50"
            >
              <option value="auto">ASTM D1250 (gross + temp + API)</option>
              <option value="factor">Flat correction factor</option>
              <option value="gross">Proceed on gross (net = gross)</option>
              <option value="off">Off (keep provided net)</option>
            </select>
            {options.net_correction === "factor" && (
              <label className="flex items-center gap-1">
                factor
                <input
                  type="number"
                  step="0.0001"
                  value={options.net_factor ?? 1}
                  onChange={(e) => set({ net_factor: parseFloat(e.target.value) })}
                  className="w-24 rounded border border-slate-300 px-2 py-1 text-xs"
                />
              </label>
            )}
          </div>
        </div>
      )}

      {/* Fill defaults */}
      <div className="mt-4 border-t border-slate-100 pt-3">
        <Toggle
          label="Fill missing terminal / product from a default"
          checked={options.fill_defaults}
          onChange={(v) => set({ fill_defaults: v })}
        />
        {options.fill_defaults && (
          <div className="mt-2 flex flex-wrap items-center gap-3 pl-6 text-xs text-slate-600">
            <label className="flex items-center gap-1">
              terminal
              <input
                value={options.default_terminal ?? ""}
                onChange={(e) => set({ default_terminal: e.target.value || null })}
                placeholder="e.g. Linden"
                className="w-28 rounded border border-slate-300 px-2 py-1 text-xs"
              />
            </label>
            <label className="flex items-center gap-1">
              product
              <input
                value={options.default_product ?? ""}
                onChange={(e) => set({ default_product: e.target.value || null })}
                placeholder="e.g. ULSD"
                className="w-28 rounded border border-slate-300 px-2 py-1 text-xs"
              />
            </label>
          </div>
        )}
      </div>
    </div>
  );
}
