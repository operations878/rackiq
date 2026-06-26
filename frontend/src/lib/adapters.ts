/**
 * Data adapters — thin readers that normalize an engine payload into the shape a tile draws.
 *
 * Both tiles now read their REAL merged engine (the Phase-6/7 wiring pass landed):
 *   • `opportunitySignal` reads the REAL modeled missing-volume facet (peak ≈ wallet) carried on
 *     every profile customer — winnable gal/$, a 0–100 winnability, and the shrunk-vs-under-served
 *     split. A low-winnability account reads "looks shrunk, not winnable", never a bare tempting
 *     gallons number. Always labelled MODELED.
 *   • `positionSignal` reads the REAL Phase-7 position / days-of-cover engine (`/api/position`):
 *     gauge-anchored vs net-flow proxy (never conflated), days-of-cover in WORKING days, and the
 *     "nominate a barge" cure when short.
 *
 * Each returns `available: false` (degrade honestly to "not enough data") rather than a fabricated
 * zero when its engine returned nothing. The caveat text is part of the contract.
 */
import type { ProfileCustomer, ProfileCustomerListRow, PositionResponse, PositionCell } from "../api/types";

const MODELED_CAVEAT =
  "Modeled estimate (peak ≈ wallet) — opportunity, not measured demand.";

export interface OpportunitySignal {
  available: boolean;
  kind: string; // win | shrunk | matched | unknown
  modeled: boolean;
  gallons: number; // winnable gal/yr (the headline number)
  dollars: number | null; // gallons × the existing margin ¢/gal (ranking-only)
  gapGallons: number | null; // the full modeled gap (winnable is the winnable slice of it)
  winnability: number | null; // 0–100
  flag: string | null; // under_served | shrunk | thin_per_product | insufficient
  chaseChannel: string | null;
  note?: string;
  caveat: string;
}

/** Read the REAL modeled opportunity facet off a profile customer (full record or slim list row). */
export function opportunitySignal(c: ProfileCustomer | ProfileCustomerListRow): OpportunitySignal {
  const full = (c as ProfileCustomer).opportunity;
  if (full) {
    return {
      available: !!full.available,
      kind: full.kind,
      modeled: full.modeled ?? true,
      gallons: full.winnable_gal_per_yr ?? 0,
      dollars: full.winnable_dollars_per_yr ?? null,
      gapGallons: full.gap_gal_per_yr ?? null,
      winnability: full.winnability ?? null,
      flag: full.winnability_flag ?? null,
      chaseChannel: full.chase_channel ?? null,
      note: full.note,
      caveat: full.caveat ?? full.interim_note ?? MODELED_CAVEAT,
    };
  }
  // slim list row — the flattened facet fields the /profile/customers route adds
  const row = c as ProfileCustomerListRow;
  return {
    available: row.opportunity_available ?? (row.opportunity_kind !== "unknown"),
    kind: row.opportunity_kind,
    modeled: true,
    gallons: row.winnable_gal_per_yr ?? 0,
    dollars: row.winnable_dollars_per_yr ?? null,
    gapGallons: row.gap_gal_per_yr ?? null,
    winnability: row.winnability ?? null,
    flag: row.winnability_flag ?? null,
    chaseChannel: row.chase_channel ?? null,
    note: row.opportunity_note ?? undefined,
    caveat: MODELED_CAVEAT,
  };
}

export interface PositionSignal {
  available: boolean; // is there a real position read at all?
  daysOfCover: number | null; // working days (the tightest cell)
  tight: boolean; // short or watch
  status: "short" | "watch" | "ok" | "unknown" | null;
  mode: "gauge" | "proxy" | null;
  modeLabel: string | null; // "gauge-verified" | "net-flow proxy"
  isProxy: boolean;
  headline: string | null; // the plain-English facet sentence (carries the cure when short)
  cell: PositionCell | null; // the representative (tightest) cell
  caveat: string | null; // proxy honesty note, when applicable
}

const POS_DARK = "Days-of-cover needs inbound supply + lifts loaded (load the Trips report or receipts).";

/** Read the REAL position engine: pick the tightest cell as the terminal's representative read. */
export function positionSignal(pos: PositionResponse | null): PositionSignal {
  const cells = pos?.positions ?? [];
  if (!pos?.availability?.available || cells.length === 0) {
    return {
      available: false, daysOfCover: null, tight: false, status: null, mode: null,
      modeLabel: null, isProxy: false, headline: null, cell: null,
      caveat: pos?.inbound?.connected === false ? POS_DARK : (pos?.availability?.reason ?? POS_DARK),
    };
  }
  // tightest cover first (engine already sorts this way; be defensive)
  const withCover = cells.filter((c) => c.days_of_cover != null);
  const cell = (withCover.length ? withCover : cells)
    .slice()
    .sort((a, b) => (a.days_of_cover ?? Infinity) - (b.days_of_cover ?? Infinity))[0];
  const isProxy = cell.mode === "proxy";
  return {
    available: cell.days_of_cover != null,
    daysOfCover: cell.days_of_cover,
    tight: cell.status === "short" || cell.status === "watch",
    status: cell.status,
    mode: cell.mode,
    modeLabel: cell.facet?.mode_label ?? (isProxy ? "net-flow proxy" : "gauge-verified"),
    isProxy,
    headline: cell.facet?.sentence ?? null,
    cell,
    caveat: isProxy ? (cell.proxy_note ?? null) : null,
  };
}
