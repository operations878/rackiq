import type { ReconMechanism } from "../../api/types";

/**
 * The loss-mechanism split — a single stacked bar showing how a gross book-to-physical gap
 * decomposes into temperature/volumetric (benign, nets out under VCF), measurement (meter
 * drift / gauging — the net-recon cross-check), and physical (evaporation/line-fill/theft).
 * measurement + physical = NET loss; + temperature = the gross gap.
 */
const PARTS = [
  { key: "temperature_gal", label: "Temperature", color: "bg-amber-400", text: "text-amber-700" },
  { key: "measurement_gal", label: "Measurement", color: "bg-indigo-500", text: "text-indigo-700" },
  { key: "physical_gal", label: "Physical", color: "bg-rose-500", text: "text-rose-700" },
] as const;

function fmt(v: number): string {
  const a = Math.abs(v);
  const s = a >= 1000 ? `${(v / 1000).toFixed(1)}k` : `${Math.round(v)}`;
  return `${v >= 0 ? "" : "−"}${s.replace("-", "")} gal`;
}

export default function MechanismBar({ mech, compact = false }: { mech: ReconMechanism; compact?: boolean }) {
  if (mech.temperature_gal == null) {
    return <div className="text-xs text-slate-400">Needs BOL compartment detail (gross + temp + gravity) to split.</div>;
  }
  const vals = PARTS.map((p) => Math.abs((mech[p.key] as number) ?? 0));
  const total = vals.reduce((a, b) => a + b, 0) || 1;
  return (
    <div className={compact ? "" : "space-y-2"}>
      <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-slate-100">
        {PARTS.map((p, i) => (
          <div key={p.key} className={p.color} style={{ width: `${(vals[i] / total) * 100}%` }} title={`${p.label}: ${fmt((mech[p.key] as number) ?? 0)}`} />
        ))}
      </div>
      {!compact && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
          {PARTS.map((p) => (
            <span key={p.key} className="inline-flex items-center gap-1.5">
              <span className={`inline-block h-2 w-2 rounded-sm ${p.color}`} />
              <span className="text-slate-500">{p.label}</span>
              <span className={`font-semibold ${p.text}`}>{fmt((mech[p.key] as number) ?? 0)}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
