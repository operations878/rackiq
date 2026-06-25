import { useMemo } from "react";
import type { ScoreCustomer } from "../../api/types";
import { Tip, fmtGal } from "../../lib/scoreui";

/** The 2-axis behavioral map: FREQUENCY (how often they buy) × SIZE-CONSISTENCY (how consistent the
 *  load is when they do). Drop every customer into a cell so you can instantly see who's baseload
 *  (top-left, steady & frequent) vs. who's a buffer-risk burst buyer (bottom-right, rare & erratic).
 *  Dot area ∝ volume; a rose ring marks a misleading-average (silent-most-days) account. */
const FREQS = ["daily", "frequent", "occasional", "rare"] as const;
const SIZES = ["tight", "variable", "erratic"] as const;
const FREQ_LABEL: Record<string, string> = { daily: "Daily", frequent: "Frequent", occasional: "Occasional", rare: "Rare" };
const SIZE_LABEL: Record<string, string> = { tight: "Tight", variable: "Variable", erratic: "Erratic" };

/** Tint a cell by "plan-ability": top-left (daily+tight) green → bottom-right (rare+erratic) rose. */
function cellTone(fi: number, si: number): string {
  const s = fi + si; // 0 (daily+tight) … 5 (rare+erratic)
  if (s <= 1) return "bg-emerald-50/70";
  if (s <= 2) return "bg-teal-50/60";
  if (s <= 3) return "bg-amber-50/60";
  if (s <= 4) return "bg-orange-50/70";
  return "bg-rose-50/80";
}

export default function BehaviorMap({
  customers,
  selected,
  onPick,
}: {
  customers: ScoreCustomer[];
  selected?: string | null;
  onPick: (id: string) => void;
}) {
  const { grid, unclassified, maxVol } = useMemo(() => {
    const g: Record<string, ScoreCustomer[]> = {};
    const un: ScoreCustomer[] = [];
    let mv = 1;
    for (const c of customers) {
      const f = c.behavior?.frequency_class;
      const s = c.behavior?.size_class;
      mv = Math.max(mv, c.total_net_gallons || 0);
      if (f && s && (FREQS as readonly string[]).includes(f) && (SIZES as readonly string[]).includes(s)) {
        (g[`${f}|${s}`] ??= []).push(c);
      } else if (c.behavior?.available) {
        un.push(c);
      }
    }
    for (const k in g) g[k].sort((a, b) => (b.total_net_gallons || 0) - (a.total_net_gallons || 0));
    return { grid: g, unclassified: un, maxVol: mv };
  }, [customers]);

  const dot = (c: ScoreCustomer) => {
    const vol = c.total_net_gallons || 0;
    const r = 6 + 16 * Math.sqrt(vol / maxVol); // area ∝ volume
    const high = c.behavior?.misleading_severity === "high";
    const isSel = selected === c.customer_id;
    return (
      <Tip key={c.customer_id} text={`${c.name} — ${c.behavior?.label ?? ""} · ${fmtGal(vol)} gal${high ? " · avg misleading" : ""}`}>
        <button
          onClick={() => onPick(c.customer_id)}
          className={`rounded-full transition-transform hover:scale-110 ${isSel ? "ring-2 ring-indigo-500 ring-offset-1" : high ? "ring-1 ring-rose-400" : ""}`}
          style={{
            width: r,
            height: r,
            backgroundColor: high ? "#fb7185" : "#6366f1",
            opacity: isSel ? 1 : 0.78,
          }}
          aria-label={c.name}
        />
      </Tip>
    );
  };

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-slate-400">
        <span className="inline-flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full bg-indigo-500/80" /> a customer (size ∝ volume)</span>
        <span className="inline-flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full bg-rose-400 ring-1 ring-rose-400" /> avg-misleading burst buyer</span>
        <span className="ml-auto font-medium text-emerald-600">↖ baseload</span>
        <span className="font-medium text-rose-500">buffer-risk bursts ↘</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[34rem] border-separate border-spacing-1">
          <thead>
            <tr>
              <th className="w-16" />
              {FREQS.map((f) => (
                <th key={f} className="pb-1 text-center text-[10px] font-semibold uppercase tracking-wide text-slate-500">{FREQ_LABEL[f]}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {SIZES.map((s, si) => (
              <tr key={s}>
                <td className="pr-1 text-right align-middle text-[10px] font-semibold uppercase tracking-wide text-slate-500">{SIZE_LABEL[s]}</td>
                {FREQS.map((f, fi) => {
                  const cell = grid[`${f}|${s}`] ?? [];
                  return (
                    <td key={f} className={`h-20 rounded-lg align-top ${cellTone(fi, si)}`}>
                      <div className="flex h-full flex-wrap content-start items-center gap-1 p-1.5">
                        {cell.slice(0, 14).map(dot)}
                        {cell.length > 14 && <span className="text-[10px] text-slate-400">+{cell.length - 14}</span>}
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-1.5 flex items-center justify-between text-[10px] text-slate-400">
        <span>← buys more often · less often →</span>
        <span>↑ more consistent load · less consistent ↓</span>
      </div>
      {unclassified.length > 0 && (
        <p className="mt-1.5 text-[10px] text-slate-400">{unclassified.length} account{unclassified.length === 1 ? "" : "s"} too new/sparse to place yet.</p>
      )}
    </div>
  );
}
