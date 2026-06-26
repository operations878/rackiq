/**
 * INTERIM data adapters — the forward-compatibility seam for Phase 6 / Phase 7.
 *
 * Two tiles currently ride interim data sources that a later wiring pass will replace:
 *   • Opportunity / missing-volume rides the CHANNEL-MISMATCH volume (not a demand model). Phase 6
 *     swaps in modeled missing-volume (peak≈wallet). When it lands, change ONLY `opportunitySignal`.
 *   • Terminal position rides the hedging/demand net-flow forecast (not a tank gauge). Phase 7 swaps
 *     in a true days-of-cover from gauge data. When it lands, change ONLY `positionSignal`.
 *
 * Keeping the data source behind one labelled function per tile means the swap is a data change, not
 * a redesign — the tile shape and caveat label stay put. The caveat text is part of the contract:
 * never let an estimate read as ground truth.
 */
import type { ProfileCustomer, ProfileCustomerListRow, DemandCockpit } from "../api/types";

export interface OpportunitySignal {
  available: boolean;
  kind: string;
  gallons: number; // winnable (or at-risk) gal/yr
  dollars: number | null; // gallons × the existing margin ¢/gal (ranking-only)
  chaseChannel: string | null;
  note?: string;
  interim: true;
  caveat: string;
}

const OPP_CAVEAT = "Interim: channel-mismatch volume, not modeled demand (Phase 6 will replace).";

export function opportunitySignal(c: ProfileCustomer | ProfileCustomerListRow): OpportunitySignal {
  // ProfileCustomer carries the full opportunity object; the list row carries the flattened fields.
  const full = (c as ProfileCustomer).opportunity;
  if (full) {
    const gallons = full.kind === "risk" ? full.at_risk_gal_per_yr ?? 0 : full.winnable_gal_per_yr ?? 0;
    const dollars = full.kind === "risk" ? full.at_risk_dollars_per_yr ?? null : full.winnable_dollars_per_yr ?? null;
    return {
      available: full.available, kind: full.kind, gallons, dollars,
      chaseChannel: full.chase_channel ?? null, note: full.note,
      interim: true, caveat: full.interim_note ?? OPP_CAVEAT,
    };
  }
  const row = c as ProfileCustomerListRow;
  return {
    available: row.current_channel_known ?? true, kind: row.opportunity_kind,
    gallons: row.winnable_gal_per_yr ?? 0, dollars: row.winnable_dollars_per_yr ?? null,
    chaseChannel: null, interim: true, caveat: OPP_CAVEAT,
  };
}

export interface PositionSignal {
  available: boolean; // is there a position read at all (inventory connected)?
  daysOfCover: number | null;
  tight: boolean;
  headline: string | null;
  interim: true;
  caveat: string;
}

const POS_CAVEAT_LIVE = "Interim: days-of-cover from the demand-forecast burn, not a tank gauge (Phase 7 will replace).";
const POS_CAVEAT_DARK = "Position needs inventory loaded — a true gauge read lands in Phase 7.";

export function positionSignal(cockpit: DemandCockpit | null, invConnected: boolean): PositionSignal {
  const days = invConnected ? cockpit?.days_of_cover ?? null : null;
  return {
    available: invConnected && days != null,
    daysOfCover: days,
    tight: days != null && days < 5,
    headline: cockpit?.recommendation?.headline ?? null,
    interim: true,
    caveat: invConnected ? POS_CAVEAT_LIVE : POS_CAVEAT_DARK,
  };
}
