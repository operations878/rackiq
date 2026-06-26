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
  margin: {
    term: "Margin (¢/gal)",
    short: "What you actually earn per gallon — sell minus landed cost — and where it ranks in the book.",
    what: "Realized margin per gallon, valued against actual landed cost (the barge cost), not a list price.",
    how: "Per BOL lift: sell price (deal → grid → invoice) minus landed cost (barge trips running cost → invoice). Rolled up per customer as ¢/gal and total $. Shown two ways: BOOK (vs your inventory cost basis) and replacement (vs the latest barge).",
    meaning: "Ranks the desk by VALUE, not volume — a high-volume thin-margin account and a low-volume fat-margin one are both visible. It NEVER changes how steady a customer is or which channel they belong on.",
  },
  value_rank: {
    term: "Value rank",
    short: "Where a customer sits when you rank the book by margin dollars instead of gallons.",
    what: "The customer's rank by total margin $ earned, contrasted with their rank by volume.",
    how: "Rank every account by book margin $ (1 = most valuable). 'Ranks higher on margin than volume' is the fat-margin tell; the reverse is a high-volume, thin account.",
    meaning: "Tells you who's actually worth the most — not just who lifts the most gallons.",
  },
  winnable_volume: {
    term: "Winnable volume",
    short: "Steady volume currently bought on spot that you could lock onto a rack/term deal.",
    what: "Volume on the table — a plannable account buying spot when it belongs on rack/term.",
    how: "Taken straight from the channel mismatch: a metronome / predictable account whose current channel is spot. The figure is that customer's annualized lift volume. Accounts gone quiet (stale) are excluded so you don't chase dead volume.",
    threshold: "Excludes accounts with no lift in ~90 days.",
    meaning: "Your 'sell more / commit it' worklist — capture the volume on a deal before a competitor does.",
  },
  at_risk_volume: {
    term: "At-risk committed volume",
    short: "Committed volume riding on an account too erratic to plan — exposure if they don't lift.",
    what: "The risk side of a channel mismatch: an unpredictable account that's term-committed.",
    how: "An account in the unpredictable quadrant whose current channel is a term/forward contract. Their annualized volume is the exposure.",
    meaning: "Move it to spot to cut volume risk — you're committed to product an erratic buyer may not take.",
  },
  days_of_cover: {
    term: "Days of cover",
    short: "How many days the tank holds at the expected burn rate before it hits the heel.",
    what: "Inventory above the minimum heel divided by near-term expected daily demand.",
    how: "Latest book inventory − min heel, divided by the forecast P50 daily demand. Needs inventory loaded; without it, the view shows a target carry instead and says so.",
    meaning: "Low days of cover at a terminal = you're tight and may need to nominate a barge soon.",
  },
  demand_band: {
    term: "Expected demand band (P10 / P50 / P90)",
    short: "The range of likely demand over the next few working days — floor, middle, and high case.",
    what: "A terminal's near-term expected demand as a range, not a single number.",
    how: "Each customer's forecast is summed to the terminal P50; the P10–P90 band comes from historical forecast error, widened for erratic accounts and for correlated cold-weather demand.",
    meaning: "Stage to the middle (P50) and carry a buffer toward P90 for the accounts that drive the surprise.",
  },
  committed_vs_spot: {
    term: "Committed vs spot",
    short: "How much of a terminal's demand is locked on contracts vs bought opportunistically.",
    what: "The split of expected/served volume between committed (term + forward) and spot.",
    how: "Summed from the deal book per terminal: term + forward committed gallons vs realized spot gallons.",
    meaning: "Committed volume is must-serve; if you're tight, that's the volume (and margin) at risk first.",
  },
  barge_cure: {
    term: "Barge-nomination cost (the cure)",
    short: "What it costs per gallon to bring in a barge to cover a shortfall.",
    what: "The landed cost of nominating product to cover a demand gap at a terminal.",
    how: "Read from the barge Trips landed cost for that terminal × product. The margin-priced gap splits the volume into must-serve (committed) margin vs spot upside.",
    meaning: "If a terminal is tight, this is the price of the fix — weighed against the committed margin you'd protect.",
  },
};

/** Inline tooltip that pulls its text from the shared glossary by key. */
export function DefTip({ k, children }: { k: keyof typeof DEFS | string; children: ReactNode }) {
  const d = DEFS[k];
  if (!d) return <>{children}</>;
  return <Tip text={`${d.term} — ${d.short}`}>{children}</Tip>;
}
