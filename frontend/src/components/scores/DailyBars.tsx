import type { BehaviorBar } from "../../api/types";
import { fmtGal, fmtGalFull } from "../../lib/scoreui";
import { fmtDate } from "../../lib/format";

/** Daily presence + size bar view. Each calendar day is one bar; silent days are empty (a faint
 *  baseline tick), active days a column ∝ that day's gallons. A steady daily buyer reads as a row
 *  of similar bars; a silent-then-spiky buyer reads as mostly-empty with occasional towers — they
 *  look visibly different at a glance. */
export default function DailyBars({ bars, accent = "#6366f1" }: { bars: BehaviorBar[]; accent?: string }) {
  if (!bars.length) return <div className="text-[11px] text-slate-400">No days to show in this window.</div>;
  const max = Math.max(1, ...bars.map((b) => b.gallons));
  const nActive = bars.filter((b) => b.lifts > 0).length;
  const first = bars[0]?.date;
  const last = bars[bars.length - 1]?.date;
  // keep individual bars legible but let a long ('all') window scroll rather than vanish
  const dense = bars.length > 60;

  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-[10px] text-slate-400">
        <span>Daily volume — each bar is one calendar day</span>
        <span>
          peak <span className="font-medium text-slate-500">{fmtGal(max)}</span> · {nActive}/{bars.length} days active
        </span>
      </div>
      <div className={`flex h-28 items-end gap-px rounded-lg border border-slate-100 bg-slate-50/60 px-1.5 py-1 ${dense ? "overflow-x-auto" : ""}`}>
        {bars.map((b, i) => {
          const active = b.lifts > 0;
          const h = active ? Math.max(7, (b.gallons / max) * 100) : 0;
          return (
            <div
              key={i}
              className={`relative flex h-full flex-1 items-end ${dense ? "min-w-[3px]" : "min-w-[4px]"}`}
              title={`${fmtDate(b.date)} · ${active ? `${fmtGalFull(b.gallons)} (${b.lifts} ${b.lifts === 1 ? "lift" : "lifts"})` : "no lift"}`}
            >
              {active ? (
                <div className="w-full rounded-t-[2px]" style={{ height: `${h}%`, backgroundColor: accent }} />
              ) : (
                <div className="h-[2px] w-full rounded bg-slate-200" />
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-slate-400">
        <span>{first ? fmtDate(first) : ""}</span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-flex items-center gap-0.5"><span className="inline-block h-2 w-1.5 rounded-sm" style={{ backgroundColor: accent }} /> active day</span>
          <span className="inline-flex items-center gap-0.5"><span className="inline-block h-[2px] w-2 rounded bg-slate-300" /> silent day</span>
        </span>
        <span>{last ? fmtDate(last) : ""}</span>
      </div>
    </div>
  );
}
