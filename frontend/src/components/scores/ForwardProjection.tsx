import type { ForecastBlock } from "../../api/types";
import { fmtGal, fmtGalFull } from "../../lib/scoreui";

/** The per-customer forward projection — a REAL forecast (not a flat run-rate). Shows the
 *  plain-language headline, the 7/30/90-day expected volume + honest band, AND which model the
 *  engine chose for this customer with its backtested accuracy ("seasonal model · ±12% typical
 *  error"). Flags low-predictability (no model beat naive), a slowdown (silent past their
 *  cadence), and rough (wide-lane) forecasts — honest ranges over false precision. All horizons
 *  are anchored to today; a data-recency gap note appears when the book is behind today. */
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
  const lowPred = !!forecast.low_predictability;
  const tone = lowPred ? "rose" : rough ? "amber" : "emerald";
  const toneCls: Record<string, string> = {
    emerald: "border-emerald-100 bg-gradient-to-br from-emerald-50 to-white",
    amber: "border-amber-200 bg-amber-50/50",
    rose: "border-rose-200 bg-rose-50/50",
  };
  const headCls: Record<string, string> = {
    emerald: "text-emerald-700", amber: "text-amber-700", rose: "text-rose-700",
  };
  return (
    <div className={`rounded-lg border p-3 ${toneCls[tone]}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className={`text-[11px] font-semibold uppercase tracking-wide ${headCls[tone]}`}>
          Forward forecast
        </span>
        <div className="flex items-center gap-1.5">
          {/* Chosen model + its backtested typical error */}
          {forecast.model_label && (
            <span
              className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[10px] font-medium text-slate-600"
              title={`The model the engine picked for this customer by backtested accuracy${forecast.model_blurb ? ` — ${forecast.model_blurb}` : ""}.`}
            >
              {forecast.model_label}
              {forecast.mape != null && <span className="text-slate-400"> · ±{Math.round(forecast.mape)}% typ. error</span>}
            </span>
          )}
          {lowPred ? (
            <span className="rounded-full bg-rose-100 px-2 py-0.5 text-[10px] font-semibold text-rose-700"
                  title="No model beat a naive guess on this customer's history — their buying isn't reliably predictable, so treat the numbers as a rough guess.">
              low predictability
            </span>
          ) : forecast.slowing ? (
            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-700"
                  title={`Silent ${forecast.days_silent ?? ""} days — past their usual rhythm. The forecast is trimmed for a possible slowdown / churn.`}>
              slowing — {forecast.days_silent}d quiet
            </span>
          ) : rough ? (
            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-700"
                  title="The likely range is wide relative to the expected volume — this account is choppy, so treat the forecast as a range, not a firm number.">
              rough — wide lane
            </span>
          ) : null}
        </div>
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
      {forecast.gap_note && (
        <p className="mt-2 rounded-md bg-amber-50 px-2 py-1.5 text-[10px] leading-snug text-amber-700"
           title="Forecasts are measured from today's real date, not the last date in the data.">
          ⏱ {forecast.gap_note}
        </p>
      )}
    </div>
  );
}
