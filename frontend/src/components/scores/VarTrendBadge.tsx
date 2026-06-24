import type { VarTrendComparison } from "../../api/types";

/** Is the customer's lane TIGHTENING (more reliable) or WIDENING (a developing problem)?
 *  A compact arrow + tone for the table, with an optional one-line note. Colour meaning matches
 *  the rest of the app: emerald = good (tightening), rose = concern (widening). */

const TONE: Record<string, string> = {
  tightening: "bg-emerald-100 text-emerald-700",
  widening: "bg-rose-100 text-rose-700",
  steady: "bg-slate-100 text-slate-500",
  insufficient: "bg-slate-100 text-slate-400",
};
const LABEL: Record<string, string> = {
  tightening: "▲ Tightening",
  widening: "▼ Widening",
  steady: "→ Steady",
  insufficient: "— not rated",
};
const HELP: Record<string, string> = {
  tightening: "Their buying is getting more predictable than a quarter ago — the lane is narrowing.",
  widening: "Their buying is getting less predictable than a quarter ago — the lane is widening.",
  steady: "Their predictability is about the same as a quarter ago.",
  insufficient: "Not enough history yet to compare against a quarter ago.",
};

export default function VarTrendBadge({ trend, showNote = false }: { trend?: VarTrendComparison; showNote?: boolean }) {
  const dir = trend?.direction ?? "insufficient";
  const delta = trend?.delta;
  return (
    <span className="inline-flex flex-col gap-0.5">
      <span className={`inline-block w-fit cursor-help rounded px-1.5 py-0.5 text-[10px] font-semibold ${TONE[dir]}`} title={HELP[dir]}>
        {LABEL[dir]}
        {delta != null && dir !== "insufficient" && dir !== "steady" && (
          <span className="ml-1 font-normal opacity-80">{delta > 0 ? "+" : ""}{Number(delta.toFixed(1))} pts</span>
        )}
      </span>
      {showNote && trend?.note && dir !== "insufficient" && (
        <span className="text-[10px] text-slate-400">{trend.note}</span>
      )}
    </span>
  );
}
