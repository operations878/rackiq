import type { VarTrendComparison } from "../../api/types";

/** Is the customer's lane TIGHTENING (more reliable) or WIDENING (a developing problem)?
 *  A compact arrow + tone for the table, with an optional one-line note. */

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
  insufficient: "— new",
};

export default function VarTrendBadge({ trend, showNote = false }: { trend?: VarTrendComparison; showNote?: boolean }) {
  const dir = trend?.direction ?? "insufficient";
  return (
    <span className="inline-flex flex-col gap-0.5">
      <span className={`inline-block w-fit rounded px-1.5 py-0.5 text-[10px] font-semibold ${TONE[dir]}`}>
        {LABEL[dir]}
        {trend?.delta != null && dir !== "insufficient" && (
          <span className="ml-1 font-normal opacity-80">{trend.delta > 0 ? "+" : ""}{trend.delta}</span>
        )}
      </span>
      {showNote && trend?.note && dir !== "insufficient" && (
        <span className="text-[10px] text-slate-400">{trend.note}</span>
      )}
    </span>
  );
}
