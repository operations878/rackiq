import { useEffect, useState } from "react";
import type { BehaviorBlock, BehaviorStats, BehaviorWindow } from "../../api/types";
import { BehaviorTag, Tip, fmtGal, fmtGalFull, freqWord, sizeWord } from "../../lib/scoreui";
import DailyBars from "./DailyBars";

const WINDOW_LABEL: Record<string, string> = { "7": "7 days", "30": "30 days", "90": "90 days", all: "All-time" };
const ORDER = ["7", "30", "90", "all"];

const pctOf = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`);
const cvPct = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`);

/** A labelled stat for the presence / size split lists. */
function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2 border-t border-slate-100 py-1 first:border-t-0">
      <span className="text-[11px] text-slate-500">{hint ? <Tip text={hint}><span className="cursor-help underline decoration-dotted underline-offset-2">{label}</span></Tip> : label}</span>
      <span className="text-[12px] font-medium tabular-nums text-slate-800">{value}</span>
    </div>
  );
}

/** The full descriptive-statistics table — size-when-present vs naive all-days, side by side, so
 *  it's always clear which view each number belongs to. Kept folded by default (support, not a wall). */
function StatTable({ size, all }: { size: BehaviorStats | null; all: BehaviorStats | null }) {
  const rows: [string, keyof BehaviorStats | "mode"][] = [
    ["Mean", "mean"], ["Median (P50)", "median"], ["Mode (bucket)", "mode"], ["Min", "min"], ["Max", "max"],
    ["Range", "range"], ["Std dev", "std"], ["CV", "cv"], ["P10", "p10"], ["P90", "p90"],
  ];
  const cell = (s: BehaviorStats | null, key: keyof BehaviorStats | "mode") => {
    if (!s) return "—";
    if (key === "mode") return s.mode ? `${fmtGal(s.mode.lo)}–${fmtGal(s.mode.hi)} (×${s.mode.count})` : "—";
    if (key === "cv") return cvPct(s.cv);
    const v = s[key] as number | null;
    return v == null ? "—" : fmtGalFull(v);
  };
  return (
    <table className="w-full text-[11px]">
      <thead>
        <tr className="text-left text-slate-400">
          <th className="py-1 font-semibold">Statistic</th>
          <th className="py-1 text-right font-semibold">
            <Tip text="Computed over ACTIVE days only — the real size of a load when they actually buy."><span className="cursor-help underline decoration-dotted underline-offset-2">When present</span></Tip>
          </th>
          <th className="py-1 text-right font-semibold">
            <Tip text="Computed over EVERY calendar day, zeros included — the naive view that can mislead."><span className="cursor-help underline decoration-dotted underline-offset-2">All days</span></Tip>
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, key]) => (
          <tr key={label} className="border-t border-slate-100">
            <td className="py-1 text-slate-500">{label}</td>
            <td className="py-1 text-right font-medium tabular-nums text-slate-800">{cell(size, key)}</td>
            <td className="py-1 text-right tabular-nums text-slate-500">{cell(all, key)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function EmptyState() {
  return (
    <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50/50 p-4 text-center text-[12px] text-slate-400">
      Not enough buying history yet to read a daily pattern.
    </div>
  );
}

export default function BehaviorProfile({ behavior }: { behavior?: BehaviorBlock }) {
  const primary = behavior?.primary_window ?? "30";
  const [win, setWin] = useState<string>(primary);
  const [showAll, setShowAll] = useState(false);
  useEffect(() => setWin(primary), [primary]);

  if (!behavior?.available || !behavior.windows) return <EmptyState />;
  const wins = behavior.windows;
  const W: BehaviorWindow | undefined = wins[win] ?? wins[primary] ?? Object.values(wins)[0];
  if (!W) return <EmptyState />;
  const pres = W.presence;
  const size = W.size_when_present;
  const all = W.all_days;
  const sev = W.misleading_severity;
  const accent = sev === "high" ? "#fb7185" : W.frequency_class === "daily" || W.frequency_class === "frequent" ? "#10b981" : "#6366f1";

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3.5">
      {/* header: title + label + window toggle */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Daily buying pattern</h4>
          <BehaviorTag label={W.label} severity={sev} />
        </div>
        <div className="flex gap-0.5 rounded-lg bg-slate-100 p-0.5 text-[11px]">
          {ORDER.filter((w) => wins[w]).map((w) => (
            <button key={w} onClick={() => setWin(w)} className={`rounded-md px-2 py-0.5 font-medium transition-colors ${w === win ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"}`}>
              {WINDOW_LABEL[w] ?? w}
            </button>
          ))}
        </div>
      </div>

      {/* plain-English headline read */}
      {W.headline && <p className="mt-2 text-[13px] leading-snug text-slate-700">{W.headline}</p>}

      {/* misleading-average flag — prominent for a genuine burst buyer, subtle for a chunky-frequent one */}
      {W.misleading_average && (
        <div className={`mt-2 rounded-lg border px-3 py-2 text-[12px] leading-snug ${sev === "high" ? "border-rose-200 bg-rose-50 text-rose-800" : "border-amber-200 bg-amber-50/70 text-amber-800"}`}>
          <span className="font-semibold">{sev === "high" ? "⚠ Average daily volume is misleading." : "Heads up — a daily average smears this account."}</span>{" "}
          Over the last {W.n_days} days the all-days <span className="font-medium">median is {fmtGal(all?.median ?? 0)}</span> while the mean is {fmtGal(all?.mean ?? 0)}/day — they lift 0 on most days, then ~{fmtGal(size?.median ?? 0)} when they buy. Plan around active-day size + frequency, not a daily rate.
        </div>
      )}

      {/* the daily bar view: presence + size at a glance */}
      <div className="mt-3">
        <DailyBars bars={W.bars} accent={accent} />
      </div>

      {/* presence-aware lane restatement */}
      {W.presence_lane?.sentence && (
        <p className="mt-2 rounded-lg bg-slate-50 px-3 py-1.5 text-[12px] leading-snug text-slate-600">
          <span className="font-semibold text-slate-700">Presence-aware lane:</span> {W.presence_lane.sentence}
        </p>
      )}

      {/* the split: presence (all days) | size (active days) */}
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-slate-100 bg-slate-50/40 p-2.5">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
            Presence / frequency <span className="font-normal normal-case text-slate-400">· over all {W.n_days} days (zeros incl.)</span>
          </div>
          <Stat label="Buying frequency" value={<span className="capitalize">{freqWord(W.frequency_class)}</span>} hint="Daily / frequent / occasional / rare — from the active-day rate." />
          <Stat label="Active-day rate" value={pctOf(pres.active_day_rate)} hint="Share of calendar days with at least one lift." />
          <Stat label="Active days / week" value={pres.active_days_per_week ?? "—"} />
          <Stat label="Typical gap" value={pres.median_gap_days != null ? `~${pres.median_gap_days}d` : "—"} hint="Median days between active days." />
          <Stat label="Longest silent stretch" value={`${pres.longest_silent_days}d`} />
          <Stat label="Lifts / week" value={pres.lifts_per_week ?? "—"} />
        </div>
        <div className="rounded-lg border border-slate-100 bg-slate-50/40 p-2.5">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
            Size when present <span className="font-normal normal-case text-slate-400">· active days only</span>
          </div>
          {size ? (
            <>
              <Stat label="Size consistency" value={<span className="capitalize">{sizeWord(W.size_class)}</span>} hint="Tight / variable / erratic — from the CV of active-day sizes." />
              <Stat label="Typical load (median)" value={fmtGalFull(size.median)} />
              <Stat label="Usual range (P10–P90)" value={`${fmtGal(size.p10)}–${fmtGal(size.p90)}`} hint="10th–90th percentile of active-day load size." />
              <Stat label="Min – Max" value={`${fmtGal(size.min)}–${fmtGal(size.max)}`} />
              <Stat label="Std dev · CV" value={`${fmtGal(size.std)} · ${cvPct(size.cv)}`} />
              <Stat label="Mode (bucket)" value={size.mode ? `${fmtGal(size.mode.lo)}–${fmtGal(size.mode.hi)}` : "—"} hint="The most common load-size bucket." />
            </>
          ) : (
            <p className="text-[11px] text-slate-400">No active days in this window.</p>
          )}
        </div>
      </div>

      {/* naive all-days, with the misleading framing made explicit */}
      {all && (
        <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded-lg border border-slate-100 px-3 py-1.5 text-[11px] text-slate-500">
          <span className="font-semibold uppercase tracking-wide text-slate-400">Naive all-days view</span>
          <span>mean <span className="font-medium tabular-nums text-slate-700">{fmtGal(all.mean)}/day</span></span>
          <span>median <span className="font-medium tabular-nums text-slate-700">{fmtGal(all.median)}/day</span></span>
          {W.misleading_average && <span className="text-rose-500">← the median 0 is why this average misleads</span>}
        </div>
      )}

      {/* full descriptive stats — folded by default */}
      <button onClick={() => setShowAll((s) => !s)} className="mt-2 text-[11px] font-medium text-indigo-600 hover:underline">
        {showAll ? "Hide full statistics" : "Show full statistics (mean · median · mode · min/max · std · CV · P10/P50/P90)"}
      </button>
      {showAll && (
        <div className="mt-2 rounded-lg border border-slate-100 p-2.5">
          <StatTable size={size} all={all} />
        </div>
      )}
    </div>
  );
}
