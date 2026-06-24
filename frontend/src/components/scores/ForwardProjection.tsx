import type { ForecastBlock } from "../../api/types";
import { fmtGal, fmtGalFull } from "../../lib/scoreui";

/** The per-customer forward projection from their lane — the plain-language headline plus the
 *  7/30/90-day expected volume and confidence band. VAR turned into a forecast. When the lane is
 *  wide relative to the expected volume the projection is flagged ROUGH — an honest "this is a
 *  range, not a firm number" rather than false precision. */
export default function ForwardProjection({ forecast }: { forecast?: ForecastBlock }) {
  if (!forecast || !forecast.available) {
    return (
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-[11px] text-slate-500">
        {forecast?.reason ?? "Not enough history to project this account forward yet."}
      </div>
    );
  }
  const byDays = (d: number) => forecast.horizons.find((h) => h.days === d);
  const order = [7, 30, 90].map(byDays).filter(Boolean) as NonNullable<ReturnType<typeof byDays>>[];
  const rough = !!forecast.rough;
  return (
    <div className={`rounded-lg border p-3 ${rough ? "border-amber-200 bg-amber-50/50" : "border-emerald-100 bg-gradient-to-br from-emerald-50 to-white"}`}>
      <div className="flex items-center justify-between gap-2">
        <span className={`text-[11px] font-semibold uppercase tracking-wide ${rough ? "text-amber-700" : "text-emerald-700"}`}>
          Forward projection
        </span>
        {rough && (
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-700"
                title="The likely range is wide relative to the expected volume — this account is choppy, so treat the forecast as a range, not a firm number.">
            rough — wide lane
          </span>
        )}
      </div>
      <p className="mt-1 text-sm leading-snug text-slate-700">{forecast.plain}</p>
      <div className="mt-2.5 grid grid-cols-3 gap-2">
        {order.map((h) => (
          <div key={h.days} className="rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-center">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">Next {h.days} days</div>
            <div className="mt-0.5 text-base font-bold tracking-tight text-slate-900" title={fmtGalFull(h.expected)}>{fmtGal(h.expected)}</div>
            <div className="text-[10px] text-slate-500" title={`Likely range: ${fmtGalFull(h.lo)} to ${fmtGalFull(h.hi)}`}>
              {fmtGal(h.lo)}–{fmtGal(h.hi)} gal
            </div>
            {h.expected_orders != null && (
              <div className="mt-0.5 text-[10px] text-slate-400">~{h.expected_orders} order{h.expected_orders === 1 ? "" : "s"}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
