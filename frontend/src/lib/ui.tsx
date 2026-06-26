/**
 * The convergence visual system — one calm, flat, coherent look shared by every unified view
 * (Home, Customers, a Customer, Terminals, Opportunity, Data). Deliberately NOT five dashboards:
 * a restrained palette (slate + one indigo accent; emerald/amber/rose reserved for status), sentence
 * case, generous whitespace, rounded cards, numbers rounded with no false precision.
 *
 * Inline definitions are sourced from the EXISTING glossary (DefTip / DEFS) — never re-defined here.
 */
import type { ReactNode } from "react";
import { Tip } from "./scoreui";
import { DefTip } from "./varGlossary";

// ---- formatting -----------------------------------------------------------------
/** Compact gallons: 1,095,271 → "1.1M gal". Callers pass the raw number; unit included. */
export function gal(x: number | null | undefined): string {
  if (x == null || !isFinite(x)) return "—";
  const a = Math.abs(x);
  if (a >= 1e9) return `${(x / 1e9).toFixed(1)}B gal`;
  if (a >= 1e6) return `${(x / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M gal`;
  if (a >= 1e3) return `${Math.round(x / 1e3)}k gal`;
  return `${Math.round(x)} gal`;
}
/** Compact dollars: 1_234_567 → "$1.2M", 9_400 → "$9.4k", 320 → "$320". */
export function money(x: number | null | undefined): string {
  if (x == null || !isFinite(x)) return "—";
  const s = x < 0 ? "-" : "";
  const a = Math.abs(x);
  if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(1)}M`;
  if (a >= 1e4) return `${s}$${Math.round(a / 1e3)}k`;
  if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(1)}k`;
  return `${s}$${Math.round(a)}`;
}
/** Cents per gallon: 8.42 → "8.4¢/gal"; sub-cent shows one decimal honestly. */
export function cents(x: number | null | undefined): string {
  if (x == null || !isFinite(x)) return "—";
  return `${x.toFixed(1)}¢/gal`;
}
export function num(x: number | null | undefined): string {
  if (x == null || !isFinite(x)) return "—";
  return Math.round(x).toLocaleString();
}

// ---- tone vocabulary (status colour means the same thing everywhere) -------------
export type Tone = "neutral" | "emerald" | "amber" | "rose" | "indigo" | "slate";
export const toneText: Record<Tone, string> = {
  neutral: "text-slate-800", slate: "text-slate-500",
  emerald: "text-emerald-700", amber: "text-amber-700", rose: "text-rose-700", indigo: "text-indigo-700",
};
export const toneBg: Record<Tone, string> = {
  neutral: "bg-slate-100 text-slate-700", slate: "bg-slate-100 text-slate-500",
  emerald: "bg-emerald-100 text-emerald-800", amber: "bg-amber-100 text-amber-800",
  rose: "bg-rose-100 text-rose-700", indigo: "bg-indigo-100 text-indigo-800",
};

// ---- page scaffolding -----------------------------------------------------------
export function PageHeader({ title, subtitle, right, back }: {
  title: ReactNode; subtitle?: ReactNode; right?: ReactNode; back?: { label: string; onClick: () => void };
}) {
  return (
    <div className="mb-6">
      {back && (
        <button onClick={back.onClick}
          className="mb-2 inline-flex items-center gap-1 text-xs font-medium text-slate-500 hover:text-indigo-600">
          <span aria-hidden>←</span> {back.label}
        </button>
      )}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{title}</h1>
          {subtitle && <p className="mt-1 max-w-2xl text-sm text-slate-500">{subtitle}</p>}
        </div>
        {right && <div className="shrink-0">{right}</div>}
      </div>
    </div>
  );
}

export function Card({ children, className = "", onClick, hover }: {
  children: ReactNode; className?: string; onClick?: () => void; hover?: boolean;
}) {
  return (
    <div
      onClick={onClick}
      className={`rounded-xl border border-slate-200 bg-white shadow-sm ${
        hover ? "cursor-pointer transition hover:border-indigo-300 hover:shadow-md" : ""
      } ${className}`}
    >
      {children}
    </div>
  );
}

// ---- home: headline stat tile ---------------------------------------------------
export function StatTile({ label, value, unit, sub, tone = "neutral", onClick, muted, modeled }: {
  label: string; value: ReactNode; unit?: string; sub?: string; tone?: Tone;
  onClick?: () => void; muted?: boolean; modeled?: boolean;
}) {
  return (
    <Card hover={!!onClick && !muted} onClick={muted ? undefined : onClick}
      className={`group p-5 ${muted ? "opacity-70" : ""}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{label}</div>
        {modeled && !muted && <ProvenanceTag kind="modeled" />}
      </div>
      <div className="mt-2 flex items-baseline gap-1.5">
        <span className={`tnum text-[2rem] font-semibold leading-none tracking-tight ${muted ? "text-slate-400" : toneText[tone]}`}>{value}</span>
        {unit && <span className="text-xs font-medium text-slate-400">{unit}</span>}
      </div>
      {sub && <div className="mt-2 text-xs leading-snug text-slate-500">{sub}</div>}
      {onClick && !muted && (
        <div className="mt-2 text-[11px] font-medium text-indigo-400 opacity-0 transition group-hover:opacity-100">View →</div>
      )}
    </Card>
  );
}

// ---- customer facet tile (one engine's answer, on the unified page) -------------
export function FacetTile({ title, defKey, available = true, unavailableNote, children, accent }: {
  title: string; defKey?: string; available?: boolean; unavailableNote?: ReactNode;
  children: ReactNode; accent?: Tone;
}) {
  return (
    <Card className="flex flex-col p-4">
      <div className="mb-2 flex items-center gap-1.5">
        {accent && <span className={`h-2 w-2 rounded-full ${dotTone[accent]}`} />}
        {defKey ? (
          <DefTip k={defKey}>
            <h3 className="cursor-help text-xs font-semibold uppercase tracking-wide text-slate-500 underline decoration-slate-300 decoration-dotted underline-offset-2">
              {title}
            </h3>
          </DefTip>
        ) : (
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
        )}
      </div>
      {available ? (
        <div className="flex-1">{children}</div>
      ) : (
        <div className="flex flex-1 items-center rounded-lg bg-slate-50 px-3 py-4 text-xs text-slate-400">
          {unavailableNote ?? "Not enough data yet."}
        </div>
      )}
    </Card>
  );
}

const dotTone: Record<Tone, string> = {
  neutral: "bg-slate-300", slate: "bg-slate-300", emerald: "bg-emerald-500",
  amber: "bg-amber-500", rose: "bg-rose-500", indigo: "bg-indigo-500",
};

/** The big number + small caption inside a facet. */
export function FacetValue({ value, caption, tone = "neutral" }: {
  value: ReactNode; caption?: ReactNode; tone?: Tone;
}) {
  return (
    <div>
      <div className={`tnum text-[1.65rem] font-semibold leading-tight tracking-tight ${toneText[tone]}`}>{value}</div>
      {caption && <div className="mt-1 text-xs leading-snug text-slate-500">{caption}</div>}
    </div>
  );
}

// ---- status chips ---------------------------------------------------------------
export function ConfidencePill({ tier, flag, small }: {
  tier: "High" | "Medium" | "Low" | null | undefined; flag?: string | null; small?: boolean;
}) {
  const tone: Tone = tier === "High" ? "emerald" : tier === "Medium" ? "amber" : tier === "Low" ? "rose" : "slate";
  const size = small ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-0.5 text-xs";
  return (
    <DefTip k="confidence">
      <span className={`inline-flex cursor-help items-center gap-1 rounded-full font-semibold ${size} ${toneBg[tone]}`}>
        {tier ?? "—"} confidence{flag && !small ? " ·" : ""}
        {flag && !small && <span className="font-normal opacity-80">{flag.replace("provisional — ", "")}</span>}
      </span>
    </DefTip>
  );
}

/** RACK / SPOT recommendation chip. */
export function ChannelChip({ rec, label, small }: {
  rec: "RACK" | "SPOT" | null | undefined; label?: string; small?: boolean;
}) {
  const tone: Tone = rec === "RACK" ? "indigo" : rec === "SPOT" ? "amber" : "slate";
  const size = small ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-0.5 text-xs";
  return (
    <DefTip k="channel">
      <span className={`inline-flex cursor-help items-center rounded-full font-semibold ${size} ${toneBg[tone]}`}>
        {label ?? (rec === "RACK" ? "Rack / Term" : rec === "SPOT" ? "Spot" : "—")}
      </span>
    </DefTip>
  );
}

/** A loud little flag for a channel mismatch (the worklist signal). */
export function MismatchFlag({ direction, strength, small }: {
  direction: string | null | undefined; strength?: string; small?: boolean;
}) {
  if (!direction) return null;
  const up = direction === "upgrade_to_rack";
  const tone: Tone = up ? "emerald" : "rose";
  const txt = up ? "Move to rack/term" : "Move to spot";
  const size = small ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-0.5 text-xs";
  return (
    <DefTip k="mismatch">
      <span className={`inline-flex cursor-help items-center gap-1 rounded-full font-semibold ${size} ${toneBg[tone]}`}>
        {strength === "strong" ? "▲" : "△"} {txt}
      </span>
    </DefTip>
  );
}

const QUAD_TONE: Record<string, Tone> = {
  metronome: "emerald", predictable_size: "emerald", predictable_timing: "amber",
  unpredictable: "rose", insufficient: "slate",
};
export function QuadrantChip({ quadrant, label, small }: {
  quadrant: string; label: string; small?: boolean;
}) {
  const tone = QUAD_TONE[quadrant] ?? "slate";
  const size = small ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-0.5 text-xs";
  return (
    <DefTip k={quadrant in QUAD_TONE && quadrant !== "insufficient" ? quadrant : "quadrant"}>
      <span className={`inline-flex cursor-help items-center rounded-full font-semibold ${size} ${toneBg[tone]}`}>
        {label}
      </span>
    </DefTip>
  );
}

// ---- small inline label : value -------------------------------------------------
export function Stat({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  const inner = (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-slate-400">{label}</div>
      <div className="tnum mt-0.5 text-sm font-medium text-slate-800">{children}</div>
    </div>
  );
  return hint ? <Tip text={hint}>{inner}</Tip> : inner;
}

// ---- the plain-English summary banner (the thing read first) --------------------
export function SummaryBanner({ children, tone = "indigo" }: { children: ReactNode; tone?: Tone }) {
  const bar: Record<string, string> = {
    indigo: "border-indigo-200 bg-indigo-50/60", emerald: "border-emerald-200 bg-emerald-50/60",
    amber: "border-amber-200 bg-amber-50/60", rose: "border-rose-200 bg-rose-50/60",
    neutral: "border-slate-200 bg-slate-50", slate: "border-slate-200 bg-slate-50",
  };
  return (
    <div className={`rounded-xl border ${bar[tone]} px-5 py-4`}>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">In plain English</div>
      <p className="mt-1 text-[15px] leading-relaxed text-slate-700">{children}</p>
    </div>
  );
}

// ---- the prescriptive action chip (the verb in the verdict) ---------------------
const ACTION: Record<string, { tone: Tone; verb: string }> = {
  CALL: { tone: "emerald", verb: "Call" },
  DE_RISK: { tone: "rose", verb: "De-risk" },
  FIX_PRICING: { tone: "amber", verb: "Fix pricing" },
  WATCH: { tone: "amber", verb: "Watch" },
  PROTECT: { tone: "indigo", verb: "Protect" },
  LEAVE: { tone: "slate", verb: "Leave as-is" },
  REVIEW: { tone: "slate", verb: "Review" },
};
export function ActionChip({ action, small }: { action: string | null | undefined; small?: boolean }) {
  const a = ACTION[action ?? ""] ?? { tone: "slate" as Tone, verb: action ?? "—" };
  const size = small ? "px-1.5 py-0.5 text-[10px]" : "px-2.5 py-1 text-xs";
  return <span className={`inline-flex items-center rounded-full font-semibold uppercase tracking-wide ${size} ${toneBg[a.tone]}`}>{a.verb}</span>;
}
export function actionTone(action: string | null | undefined): Tone {
  return (ACTION[action ?? ""]?.tone) ?? "slate";
}

// ---- "why am I seeing this number" — a because-clause + expand-to-inputs ---------
export function Because({ children }: { children: ReactNode }) {
  return <p className="mt-2 text-[11px] leading-snug text-slate-400">{children}</p>;
}
export function Inputs({ label = "the inputs", children }: { label?: string; children: ReactNode }) {
  return (
    <details className="group mt-2">
      <summary className="cursor-pointer list-none text-[11px] font-medium text-indigo-500 hover:text-indigo-700">
        <span className="inline-block transition group-open:rotate-90">▸</span> Show {label}
      </summary>
      <div className="mt-2 space-y-1 rounded-lg bg-slate-50 p-2.5 text-[11px] text-slate-600">{children}</div>
    </details>
  );
}
export function InputRow({ k, v, hint }: { k: ReactNode; v: ReactNode; hint?: string }) {
  const row = (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-slate-500">{k}</span>
      <span className="tnum font-medium text-slate-700">{v}</span>
    </div>
  );
  return hint ? <Tip text={hint}>{row}</Tip> : row;
}

// ---- a thin labeled meter (for cadence/size axes) -------------------------------
export function Meter({ value, tone = "indigo" }: { value: number | null; tone?: Tone }) {
  const fill: Record<string, string> = {
    indigo: "bg-indigo-500", emerald: "bg-emerald-500", amber: "bg-amber-500",
    rose: "bg-rose-500", neutral: "bg-slate-400", slate: "bg-slate-400",
  };
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
      <div className={`h-1.5 rounded-full transition-all ${fill[tone]}`}
        style={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%` }} />
    </div>
  );
}

// ---- provenance: the modeled-vs-measured / estimated-vs-contract / gauge-vs-proxy marker --------
// One consistent vocabulary so a viewer always knows how solid a number is. Never let an estimate
// read as ground truth: modeled & estimated are violet/amber and hover-explain; verified is emerald.
type Provenance = "modeled" | "estimated" | "measured" | "verified" | "proxy" | "contract";
const PROV: Record<Provenance, { label: string; cls: string; tip: string }> = {
  modeled:   { label: "modeled", cls: "bg-violet-50 text-violet-700 ring-violet-200",
               tip: "A modeled estimate (peak ≈ wallet), not measured demand." },
  estimated: { label: "estimated", cls: "bg-amber-50 text-amber-700 ring-amber-200",
               tip: "Estimated from lift invoice prices — not your contract terms / sell grid." },
  measured:  { label: "measured", cls: "bg-slate-100 text-slate-600 ring-slate-200",
               tip: "Measured directly from the loaded book." },
  verified:  { label: "gauge-verified", cls: "bg-emerald-50 text-emerald-700 ring-emerald-200",
               tip: "Anchored to a verified physical tank gauge — a true level." },
  proxy:     { label: "net-flow proxy", cls: "bg-amber-50 text-amber-700 ring-amber-200",
               tip: "Cumulative inbound − outbound — a flow delta, not a tank gauge level." },
  contract:  { label: "contract", cls: "bg-indigo-50 text-indigo-700 ring-indigo-200",
               tip: "From the loaded deal book — actual contract terms." },
};
export function ProvenanceTag({ kind, small }: { kind: Provenance; small?: boolean }) {
  const p = PROV[kind];
  const size = small ? "px-1.5 py-0 text-[9px]" : "px-2 py-0.5 text-[10px]";
  return (
    <Tip text={p.tip}>
      <span className={`inline-flex cursor-help items-center gap-1 rounded-full font-semibold uppercase tracking-wide ring-1 ring-inset ${size} ${p.cls}`}>
        <span aria-hidden className="text-[0.85em] leading-none opacity-70">◇</span>{p.label}
      </span>
    </Tip>
  );
}

// ---- a small, consistent caveat line (the always-present honesty note) --------------------------
export function Caveat({ children, tone = "slate" }: { children: ReactNode; tone?: Tone }) {
  const bar: Record<string, string> = {
    slate: "border-slate-200 text-slate-400", neutral: "border-slate-200 text-slate-400",
    amber: "border-amber-200 text-amber-600", rose: "border-rose-200 text-rose-600",
    indigo: "border-indigo-200 text-indigo-500", emerald: "border-emerald-200 text-emerald-600",
  };
  return (
    <p className={`mt-2 border-l-2 pl-2 text-[10.5px] leading-snug ${bar[tone]}`}>{children}</p>
  );
}

// ---- the prescriptive verdict banner — the SPINE, the first thing read on a profile -------------
const VERDICT_BAR: Record<Tone, string> = {
  emerald: "border-emerald-400 bg-gradient-to-r from-emerald-50/80 to-transparent",
  indigo: "border-indigo-400 bg-gradient-to-r from-indigo-50/80 to-transparent",
  amber: "border-amber-400 bg-gradient-to-r from-amber-50/80 to-transparent",
  rose: "border-rose-400 bg-gradient-to-r from-rose-50/80 to-transparent",
  slate: "border-slate-300 bg-gradient-to-r from-slate-50 to-transparent",
  neutral: "border-slate-300 bg-gradient-to-r from-slate-50 to-transparent",
};
export function Verdict({ action, tone, children, meta, caveat }: {
  action?: ReactNode; tone: Tone; children: ReactNode; meta?: ReactNode; caveat?: ReactNode;
}) {
  return (
    <div className={`riq-rise rounded-2xl border-l-4 px-6 py-5 ${VERDICT_BAR[tone]}`}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        {action}
        <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">the verdict</span>
        {meta && <span className="ml-auto">{meta}</span>}
      </div>
      <p className="text-[1.25rem] font-medium leading-snug tracking-[-0.01em] text-slate-800">{children}</p>
      {caveat && <div className="mt-2 text-[11px] text-slate-400">{caveat}</div>}
    </div>
  );
}

// ---- a section heading with an optional one-line note (drill-down dossier rhythm) ---------------
export function SectionHeading({ children, note }: { children: ReactNode; note?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 pt-2">
      <h2 className="text-sm font-semibold tracking-tight text-slate-700">{children}</h2>
      {note && <span className="text-[11px] text-slate-400">{note}</span>}
    </div>
  );
}

// ---- the "so-what" link line inside a tile (the closed loop made visible) -----------------------
export function SoWhat({ tone = "emerald", children }: { tone?: Tone; children: ReactNode }) {
  const bg: Record<string, string> = {
    emerald: "bg-emerald-50 text-emerald-800", amber: "bg-amber-50 text-amber-800",
    rose: "bg-rose-50 text-rose-700", indigo: "bg-indigo-50 text-indigo-800",
    slate: "bg-slate-50 text-slate-600", neutral: "bg-slate-50 text-slate-600",
  };
  return <div className={`rounded-lg px-2.5 py-2 text-[11px] leading-snug ${bg[tone]}`}>{children}</div>;
}
