/**
 * Single source of truth for every spot/rack term — surfaced BOTH as inline tooltips (DefTip) and on
 * the Glossary page. Each definition states: what it measures, how it's computed (in words), the
 * threshold used, and what the label means for the desk. Anyone can read WHY a customer got their rec.
 */
import type { ReactNode } from "react";
import { Tip } from "./scoreui";

export interface Def {
  term: string;
  short: string; // one-liner for the hover tooltip
  what: string; // what it measures
  how: string; // how it's computed, in words
  threshold?: string; // the cutoff/boundary used
  meaning: string; // what the label means for the desk
}

export const DEFS: Record<string, Def> = {
  cadence: {
    term: "Cadence consistency",
    short: "How regularly they show up — regular weekly/biweekly counts, daily not required.",
    what: "How predictably a customer SHOWS UP, regardless of how often.",
    how: "From the regularity of the gap between lifts (measured in working days, so weekends/holidays don't count as silence): cadence = 100·(0.72·regularity + 0.28·presence), where regularity = 1 − (gap CV). A perfectly regular lifter earns ~72 from regularity alone, at ANY frequency.",
    threshold: "≥ 60 ⇒ REGULAR timing (the quadrant's timing axis).",
    meaning: "High = you can plan WHEN they'll lift. A clockwork weekly buyer scores high here even though they're not 'frequent'.",
  },
  size: {
    term: "Size consistency",
    short: "How alike each load is, on the days they actually lift (zeros never dilute it).",
    what: "When they DO lift, how alike each load size is.",
    how: "Computed on ACTIVE-day per-lift gallons only (silent days are never averaged in). size = 100·(1 − size CV). For heating fuels it's measured on the HDD residual (see Weather adjustment).",
    threshold: "≥ 65 ⇒ CONSISTENT size (active-day size CV ≤ 0.35).",
    meaning: "High = you can plan HOW MUCH. A 2×+ swing in load size reads as variable — and is never smoothed away.",
  },
  quadrant: {
    term: "Quadrant (2×2)",
    short: "Regular-timing? × Consistent-size? → metronome / predictable-timing / predictable-size / unpredictable.",
    what: "The planning quadrant, from the two axes crossed.",
    how: "regular_timing (cadence ≥ 60) × consistent_size (size ≥ 65).",
    meaning: "Names exactly what you can and can't plan for a customer — and sets the channel.",
  },
  metronome: {
    term: "Metronome",
    short: "Regular timing AND consistent size — the rack/term anchor.",
    what: "A customer plannable on BOTH timing and size.",
    how: "cadence ≥ 60 AND size ≥ 65.",
    meaning: "Channel: RACK / TERM. Rack-price them and commit the volume on a term deal. Worked example: lifts ~8,000 gal every Tuesday like clockwork.",
  },
  predictable_timing: {
    term: "Predictable timing, variable size",
    short: "Regular when, unpredictable how much — rack, but don't commit firm volume.",
    what: "Shows up regularly, but the load size swings.",
    how: "cadence ≥ 60 AND size < 65.",
    meaning: "Channel: capped RACK or SPOT. Serve them on rack (you can plan to be ready) but spot the size swings rather than term-committing them. Worked example: lifts every week, but anywhere 2,000–14,000 gal.",
  },
  predictable_size: {
    term: "Predictable size, irregular timing",
    short: "Identical loads, unpredictable when — rack-eligible, watch the calendar.",
    what: "When they come it's a known number, but the timing is irregular.",
    how: "cadence < 60 AND size ≥ 65.",
    meaning: "Channel: RACK-eligible (term on the known quantity); flag the irregular timing. Worked example: always 5,000 gal, but anywhere from 4 to 40 days apart.",
  },
  unpredictable: {
    term: "Unpredictable",
    short: "Irregular timing AND variable size — price it on spot.",
    what: "Hard to plan either way.",
    how: "cadence < 60 AND size < 65.",
    meaning: "Channel: SPOT. Neither timing nor size is plannable — quote opportunistically. Worked example: random gaps, loads anywhere 800–15,000 gal.",
  },
  channel: {
    term: "Recommended channel",
    short: "Rack/Term vs Spot — set by the quadrant + confidence ONLY (margin never moves it).",
    what: "Where this customer should be priced: rack/term (planned) or spot (opportunistic).",
    how: "Read straight off the quadrant. Confidence only flags the rec as provisional — it never changes it. Margin is a ranking note, never a channel mover.",
    meaning: "The headline action. Metronome/predictable → rack/term; unpredictable → spot.",
  },
  confidence: {
    term: "Confidence tier",
    short: "How much history backs the rec — High / Medium / Low. Annotates trust, never the rec.",
    what: "How much to trust the recommendation, from lift count + history span.",
    how: "High = ≥ 200 lifts over ≥ 365 days. Medium = ≥ 100 lifts over ≥ 180 days. Low = below that. Absolute (a thin account is low-confidence in any book).",
    threshold: "High ≥ 200 lifts / 365 d · Medium ≥ 100 lifts / 180 d · else Low.",
    meaning: "Low-confidence accounts STILL get a rec, flagged 'provisional — based on only N lifts'. A ~5,800-lift account is High; an ~88-lift account is Low. Confidence never changes the quadrant.",
  },
  current_channel: {
    term: "Current channel",
    short: "What the deal book says they're on TODAY — term/forward contract, spot, or mixed.",
    what: "The channel a customer is actually on now, from the deal book.",
    how: "term or forward-fixed deals ⇒ contract; spot deals only ⇒ spot; both ⇒ mixed; no deals ⇒ unknown.",
    meaning: "Compared against the recommended channel to surface mismatches.",
  },
  mismatch: {
    term: "Channel mismatch",
    short: "Recommended ≠ current — a steady account stuck on spot (upside) or an erratic one over-committed (risk).",
    what: "Where today's channel disagrees with the recommendation.",
    how: "Strong when a metronome/predictable-size account is on spot (move to rack), or an unpredictable account is term-committed (move to spot). Borderline cases are flagged 'soft'.",
    meaning: "The worklist: upgrade steady buyers to rack/term (capture upside), pull erratic buyers off firm commitments (cut risk).",
  },
  weather_adjust: {
    term: "Weather adjustment (heating fuels)",
    short: "Heating-fuel size measured on the HDD residual — cold-snap swings aren't misread as inconsistency.",
    what: "For heating fuels (ULSHO/#2/HO4), removes the weather-driven part of size variation.",
    how: "Regress lift size on Heating Degree Days (HDD) → slope β. Adjusted size = size − β·(HDD − average HDD), keeping the level. Kept ONLY if it lowers the size CV (never manufactures steadiness); gasoline is never touched.",
    meaning: "A dealer who only swings on cold snaps reads steady-underneath — without flattening genuine non-weather lumpiness.",
  },
  beta: {
    term: "HDD→demand β",
    short: "Gallons of extra demand per heating-degree-day — must be positive (cold → more).",
    what: "How strongly demand rises with cold weather, per terminal × heating-product.",
    how: "Regress working-day-aggregated demand on HDD: demand = baseload + β·HDD. Reported with in-sample R² and an out-of-sample check vs a weather-blind baseline. A wrong-sign (≤0) β is flagged, never used.",
    meaning: "Sanity-checked against the BX HO SOLD series before it's trusted. Drives the size adjustment and the forward demand view.",
  },
  margin_note: {
    term: "Margin note (ranking only)",
    short: "A human-judgment flag when margin and channel are in tension — it never moves the channel.",
    what: "Where the Phase-2 margin rank disagrees with the channel.",
    how: "E.g. a rack-recommended account that earns thin margin, or a spot account that earns fat margin ('earns more on spot today'). Margin RANKS the book; it never sets the channel.",
    meaning: "A prompt for a human call, not an automatic action. Channel stays set by variability + confidence.",
  },
};

/** Inline tooltip that pulls its text from the shared glossary by key. */
export function DefTip({ k, children }: { k: keyof typeof DEFS | string; children: ReactNode }) {
  const d = DEFS[k];
  if (!d) return <>{children}</>;
  return <Tip text={`${d.term} — ${d.short}`}>{children}</Tip>;
}
