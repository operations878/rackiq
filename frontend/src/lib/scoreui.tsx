/** Small shared bits of score UI reused across the VAR Home, Book Overview, Radar, Daily Ops,
 *  and Scorecard screens (kept dependency-free, Tailwind-only).
 *
 *  Colour meaning is consistent everywhere: emerald = steady / predictable / good,
 *  amber = caution, rose = a developing problem, slate = neutral / not-enough-data. */

/** Plain-language colour ramp for the A/B/C/D grades — green (good) → amber → rose (concern). */
export function gradeTone(g: string | null | undefined): string {
  return (
    {
      A: "bg-emerald-100 text-emerald-800",
      B: "bg-emerald-50 text-emerald-700",
      C: "bg-amber-100 text-amber-800",
      D: "bg-rose-100 text-rose-700",
    }[g ?? ""] ?? "bg-slate-100 text-slate-500"
  );
}

/** What a grade means in plain words (for tooltips / the at-a-glance label). */
export function gradeWord(g: string | null | undefined): string {
  return (
    {
      A: "steady and very predictable",
      B: "steady and fairly predictable",
      C: "somewhat erratic — harder to plan around",
      D: "erratic and hard to plan around",
    }[g ?? ""] ?? "not enough history yet to rate"
  );
}

/** Expand a VAR score + grade into the full hover sentence, e.g.
 *  "Variability score 71 of 100 — steady and fairly predictable." */
export function varMeaning(score: number | null | undefined, grade: string | null | undefined): string {
  if (score == null)
    return "Variability score — not enough history yet to rate how predictable their buying is.";
  return `Variability score ${Math.round(score)} of 100 — ${gradeWord(grade)}. Higher = steadier, more forecastable buying.`;
}

/** A lightweight, dependency-free hover tooltip. Renders above its child; pair it with a native
 *  `title` on dense/﻿clipping rows where an absolutely-positioned bubble could be cut off. */
export function Tip({ text, children }: { text: string; children: React.ReactNode }) {
  return (
    <span className="group relative inline-flex cursor-help items-center">
      {children}
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-30 mb-1.5 w-max max-w-[18rem] -translate-x-1/2 whitespace-normal rounded-md bg-slate-800 px-2.5 py-1.5 text-left text-[11px] font-normal normal-case leading-snug tracking-normal text-white opacity-0 shadow-lg transition-opacity duration-100 group-hover:opacity-100"
      >
        {text}
      </span>
    </span>
  );
}

export function ScorePill({
  score,
  grade,
  hint,
}: {
  score: number | null;
  grade?: string | null;
  /** When provided, the pill becomes hoverable (native title — never clipped in tables). */
  hint?: string;
}) {
  if (score == null)
    return (
      <span className="text-xs text-slate-400" title={hint}>
        — <span className="text-[10px]">no rating</span>
      </span>
    );
  return (
    <span className={`inline-flex items-center gap-1 ${hint ? "cursor-help" : ""}`} title={hint}>
      <span className="font-semibold text-slate-800">{Math.round(score)}</span>
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

export function TrendArrow({ pct }: { pct: number | null }) {
  if (pct == null || !isFinite(pct)) return <span className="text-slate-400">—</span>;
  const flat = Math.abs(pct) < 1;
  const up = pct > 0;
  const tone = flat ? "text-slate-400" : up ? "text-emerald-600" : "text-rose-600";
  return (
    <span className={tone}>
      {flat ? "→" : up ? "▲" : "▼"} {up && !flat ? "+" : ""}
      {Math.round(pct)}%
    </span>
  );
}

export function DeltaPill({ delta }: { delta: number | null }) {
  if (delta == null || !isFinite(delta)) return <span className="text-slate-400">—</span>;
  const up = delta > 0.05;
  const down = delta < -0.05;
  const tone = up ? "text-emerald-600" : down ? "text-rose-600" : "text-slate-400";
  const shown = Number(delta.toFixed(1));
  return (
    <span className={`font-medium ${tone}`}>
      {up ? "▲" : down ? "▼" : "→"} {shown > 0 ? "+" : ""}
      {shown}
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

/** Compact gallons for tables/cards: a smooth ramp with no false precision and no float junk.
 *  9,054 → "9.1k" · 56,186 → "56k" · 1,234,567 → "1.2MM". Callers add the " gal" unit. */
export function fmtGal(x: number | null | undefined): string {
  if (x == null || !isFinite(x)) return "—";
  const a = Math.abs(x);
  if (a >= 1e7) return `${(x / 1e6).toFixed(0)}MM`;
  if (a >= 1e6) return `${(x / 1e6).toFixed(1)}MM`;
  if (a >= 1e4) return `${Math.round(x / 1e3)}k`;
  if (a >= 1e3) return `${(x / 1e3).toFixed(1)}k`;
  return `${Math.round(x)}`;
}

/** Exact gallons with thousands separators for tooltips / hover detail: 56,186 → "56,186 gal". */
export function fmtGalFull(x: number | null | undefined): string {
  if (x == null || !isFinite(x)) return "—";
  return `${Math.round(x).toLocaleString()} gal`;
}
