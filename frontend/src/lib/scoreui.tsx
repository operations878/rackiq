/** Small shared bits of score UI reused across the Book Overview, Radar, Daily Ops,
 *  and Scorecard screens (kept dependency-free, Tailwind-only). */

export function gradeTone(g: string | null | undefined): string {
  return (
    {
      A: "bg-emerald-100 text-emerald-700",
      B: "bg-emerald-50 text-emerald-700",
      C: "bg-amber-100 text-amber-700",
      D: "bg-red-100 text-red-700",
    }[g ?? ""] ?? "bg-slate-100 text-slate-500"
  );
}

export function ScorePill({ score, grade }: { score: number | null; grade?: string | null }) {
  if (score == null) return <span className="text-xs text-slate-400">insufficient</span>;
  return (
    <span className="inline-flex items-center gap-1">
      <span className="font-semibold text-slate-800">{score}</span>
      {grade && (
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${gradeTone(grade)}`}>{grade}</span>
      )}
    </span>
  );
}

export function Bar({ value, color = "bg-indigo-500" }: { value: number | null; color?: string }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded bg-slate-200">
      <div className={`h-1.5 rounded ${color}`} style={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%` }} />
    </div>
  );
}

export function TrendArrow({ pct }: { pct: number }) {
  const up = pct >= 0;
  const flat = Math.abs(pct) < 1;
  return (
    <span className={flat ? "text-slate-400" : up ? "text-emerald-600" : "text-rose-600"}>
      {flat ? "→" : up ? "▲" : "▼"} {up ? "+" : ""}
      {pct}%
    </span>
  );
}

export function DeltaPill({ delta }: { delta: number | null }) {
  if (delta == null) return <span className="text-slate-400">—</span>;
  const up = delta > 0.05;
  const down = delta < -0.05;
  const tone = up ? "text-emerald-600" : down ? "text-rose-600" : "text-slate-400";
  return (
    <span className={`font-medium ${tone}`}>
      {up ? "▲" : down ? "▼" : "→"} {delta > 0 ? "+" : ""}
      {delta}
    </span>
  );
}

const ARCHE_TONE: Record<string, string> = {
  "Anchor Base-Load": "bg-emerald-50 text-emerald-700",
  "Strategic Platform": "bg-emerald-100 text-emerald-800",
  "Contract Candidate": "bg-teal-50 text-teal-700",
  "Premium Spot": "bg-blue-50 text-blue-700",
  "Scarcity Buyer": "bg-sky-50 text-sky-700",
  "Weather-Triggered": "bg-cyan-50 text-cyan-700",
  "Flex Buyer": "bg-indigo-50 text-indigo-700",
  "Surplus Absorber": "bg-violet-50 text-violet-700",
  "Price Shopper": "bg-amber-50 text-amber-700",
  "Backup-Only": "bg-slate-100 text-slate-600",
  "Credit Drag": "bg-red-50 text-red-700",
  "Operationally Expensive": "bg-orange-50 text-orange-700",
};

export function ArchetypeTag({ name, secondary }: { name: string; secondary?: string }) {
  return (
    <span className="inline-flex flex-col leading-tight">
      <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${ARCHE_TONE[name] ?? "bg-slate-100 text-slate-600"}`}>
        {name}
      </span>
      {secondary && <span className="mt-0.5 text-[10px] text-slate-400">+ {secondary}</span>}
    </span>
  );
}

export function fmtGal(x: number | null | undefined): string {
  if (x == null) return "—";
  if (Math.abs(x) >= 1e6) return `${(x / 1e6).toFixed(2)}MM`;
  if (Math.abs(x) >= 1e3) return `${(x / 1e3).toFixed(0)}k`;
  return `${Math.round(x)}`;
}
