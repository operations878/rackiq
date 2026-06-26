/**
 * The unified customer view — a DOSSIER, not a row of cards. It opens with the prescriptive
 * one-breath verdict (the spine), then facet tiles that REFERENCE EACH OTHER (channel → margin →
 * winnable $), each with a because-clause and expand-to-inputs, then scrollable drill-down depth
 * (the existing VAR lane / behavior / forecast charts, the plotted 2×2, product mix, peak-vs-actual).
 * Every number traces to its engine; every caveat (estimated-vs-contract, interim) is on screen.
 * No new math — synthesis, connection and legibility over the existing outputs.
 */
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ProfileCustomer, CustomerScoreResponse, BehaviorStats } from "../api/types";
import { fmtDate } from "../lib/format";
import {
  PageHeader, Card, FacetTile, FacetValue, ConfidencePill, ChannelChip, MismatchFlag, QuadrantChip,
  ActionChip, actionTone, Because, Inputs, InputRow, Meter, cents, gal, money, num, type Tone,
} from "../lib/ui";
import { DefTip } from "../lib/varGlossary";
import { opportunitySignal } from "../lib/adapters";
import Quadrant2x2 from "../components/converge/Quadrant2x2";
import BaseRangeChart from "../components/scores/BaseRangeChart";
import ForwardProjection from "../components/scores/ForwardProjection";
import BehaviorProfile from "../components/scores/BehaviorProfile";
import LaneBreaks from "../components/scores/LaneBreaks";

const SPINE_TONE: Record<string, Tone> = {
  CALL: "emerald", PROTECT: "indigo", FIX_PRICING: "amber", WATCH: "amber",
  DE_RISK: "rose", LEAVE: "slate", REVIEW: "slate",
};

export default function CustomerProfile({ id, navigate }: { id: string; navigate: (to: string) => void }) {
  const [c, setC] = useState<ProfileCustomer | null>(null);
  const [score, setScore] = useState<CustomerScoreResponse | null>(null);
  const [scoreLoading, setScoreLoading] = useState(true);
  const [marginOn, setMarginOn] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setC(null); setScore(null); setScoreLoading(true); setError(null);
    api.profile.customer(id).then((r) => { setC(r.customer); setMarginOn(r.margin_available); })
      .catch((e) => setError(String(e)));
    api.scores.customer(id, "all")
      .then(setScore).catch(() => setScore(null)).finally(() => setScoreLoading(false));
  }, [id]);

  if (error) return <div className="text-sm text-rose-600">Could not load customer: {error}</div>;
  if (!c) return <div className="text-sm text-slate-400">Loading customer…</div>;

  const opp = opportunitySignal(c);
  const sc = score?.customer;
  const ci = c.cadence_inputs ?? {};
  const si = c.size_inputs ?? {};
  const tone = SPINE_TONE[c.action] ?? "indigo";

  // closed-loop dollar phrase for the channel tile
  const winGal = c.opportunity.winnable_gal_per_yr || 0;
  const winDol = c.opportunity.winnable_dollars_per_yr;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeader
        back={{ label: "All customers", onClick: () => navigate("customers") }}
        title={c.name}
        right={<ConfidencePill tier={c.confidence_tier} flag={c.confidence_flag} />}
        subtitle={
          <span className="flex flex-wrap gap-x-4 gap-y-1">
            <span>{c.n_lifts.toLocaleString()} lifts over {Math.round((c.span_days ?? 0) / 30)} months</span>
            <span>·</span><span>{c.primary_terminal ?? "—"}</span>
            <span>·</span><span>{c.top_product ?? "—"}</span>
            <span>·</span><span>{gal(c.total_net_gallons)} all-time</span>
            {c.last_lift && <><span>·</span><span>last lift {fmtDate(c.last_lift)}</span></>}
          </span>
        }
      />

      {/* THE SPINE — the prescriptive one-breath verdict, the first thing read */}
      <div className={`rounded-2xl border-l-4 px-6 py-5 ${spineBar(tone)}`}>
        <div className="mb-2 flex items-center gap-2">
          <ActionChip action={c.action} />
          <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">the verdict</span>
        </div>
        <p className="text-[19px] font-medium leading-snug text-slate-800">{c.headline}</p>
      </div>

      {/* CONNECTED FACET TILES — each references the others, each says why */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {/* STEADINESS */}
        <FacetTile title="Steadiness" defKey={c.quadrant !== "insufficient" ? c.quadrant : "quadrant"}
          accent={c.quadrant === "metronome" ? "emerald" : c.quadrant === "unpredictable" ? "rose" : "amber"}
          available={c.quadrant !== "insufficient"} unavailableNote="Too new to read a buying pattern yet.">
          <div className="space-y-3">
            <QuadrantChip quadrant={c.quadrant} label={c.quadrant_label} />
            <div className="space-y-2">
              <AxisRow label="Cadence (when)" defKey="cadence" value={c.cadence_consistency} />
              <AxisRow label="Size (how much)" defKey="size" value={c.size_consistency}
                weatherAdjusted={c.size_weather_adjusted} />
            </div>
            <Because>
              {regularityWord(c.cadence_consistency)} timing, {sizeWord(c.size_consistency)} loads
              {c.size_weather_adjusted ? " (size measured on the HDD residual, not raw)" : ""}.
              {c.behavior_label ? ` Daily pattern: ${c.behavior_label}.` : ""}
            </Because>
            <Inputs>
              <InputRow k="Gap CV (timing regularity)" v={fmt(ci.gap_cv)} hint="Lower = more regular. 1.0 ≈ random." />
              <InputRow k="Active days / week" v={fmt(ci.active_days_per_week)} />
              <InputRow k="Size CV (active days)" v={fmt(si.cv)} hint="Spread of per-lift load size on the days they lift." />
              <InputRow k="Load P10 / P50 / P90" v={`${gal(si.p10)} · ${gal(si.p50)} · ${gal(si.p90)}`} />
              {c.weather_sensitive && <InputRow k="HDD→size β" v={fmt(c.weather_beta)} hint="Gallons per heating-degree-day; the size axis is measured net of this." />}
            </Inputs>
          </div>
        </FacetTile>

        {/* MARGIN */}
        <FacetTile title="Margin" defKey="margin" accent="indigo"
          available={marginOn && c.margin?.book_cents_gal != null}
          unavailableNote={<span>Connect the <b>price &amp; cost grid</b> to value this account on contract terms.</span>}>
          {c.margin && (
            <div>
              <FacetValue value={cents(c.margin.book_cents_gal)}
                tone={(c.margin_pctile ?? 0) >= 0.75 ? "emerald" : (c.margin_pctile ?? 1) <= 0.34 ? "amber" : "neutral"}
                caption={<>
                  {money(c.margin.book_margin_dollars)} earned
                  {c.margin.rank_by_margin ? <> · <DefTip k="value_rank"><span className="cursor-help underline decoration-dotted">value rank #{c.margin.rank_by_margin}</span></DefTip></> : null}
                </>} />
              <Because>
                {marginBasisNote(marginOn)} {pctWord(c.margin_pctile)} of the book by margin.
              </Because>
              <Inputs>
                <InputRow k="Book ¢/gal (inventory basis)" v={cents(c.margin.book_cents_gal)} />
                <InputRow k="Replacement ¢/gal (latest barge)" v={cents(c.margin.repl_cents_gal)} />
                <InputRow k="Rank by margin $ / by volume" v={`#${c.margin.rank_by_margin ?? "—"} / #${c.margin.rank_by_volume ?? "—"}`}
                  hint="Higher on margin than volume = a fat-margin account punching above its gallons." />
                {c.margin_note && <div className="pt-1 text-amber-700">{c.margin_note.replace(" [ranking note only]", "")}</div>}
              </Inputs>
            </div>
          )}
        </FacetTile>

        {/* CHANNEL — the closed loop lives here */}
        <FacetTile title="Channel" defKey="channel" accent={c.mismatch ? "amber" : "indigo"}
          available={c.current_channel_known || c.recommended_channel != null} unavailableNote="No channel read yet.">
          <div className="space-y-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <ChannelChip rec={c.recommended_channel} label={c.channel_label} />
              {c.mismatch && <MismatchFlag direction={c.mismatch_direction} strength={c.mismatch_strength} />}
            </div>
            <div className="text-xs text-slate-500">
              {c.current_channel_known
                ? <>On <span className="font-medium text-slate-700">{c.current_channel_label}</span> today{c.mismatch ? " — mismatch." : " — a match."}</>
                : <span className="text-slate-400">No deal book — can't compare to the current channel.</span>}
            </div>
            {/* the so-what: channel × margin × opportunity, on one line */}
            {c.mismatch && c.mismatch_direction === "upgrade_to_rack" && winGal > 0 && (
              <div className="rounded-lg bg-emerald-50 px-2.5 py-2 text-[11px] leading-snug text-emerald-800">
                On spot but behaves like rack — {c.margin_cents_gal != null ? <>~{cents(c.margin_cents_gal)} margin on </> : ""}
                <b>{gal(winGal)}/yr</b>{winDol ? <> (~{money(winDol)}/yr)</> : ""} you could lock onto rack/term.
              </div>
            )}
            <Because>Set by steadiness + confidence only. Margin is a ranking note — it never moves the channel.</Because>
            <Inputs label="the 2×2">
              <Quadrant2x2 cadence={c.cadence_consistency} size={c.size_consistency} />
            </Inputs>
          </div>
        </FacetTile>

        {/* MISSING / WINNABLE VOLUME (INTERIM — Phase 6) */}
        <FacetTile title="Missing volume" defKey={c.opportunity.kind === "risk" ? "at_risk_volume" : "winnable_volume"}
          accent={c.opportunity.kind === "win" ? "emerald" : c.opportunity.kind === "risk" ? "rose" : "slate"}
          available={opp.available} unavailableNote={<span>{c.opportunity.reason ?? "No comparison available."}</span>}>
          {opp.kind === "win" || opp.kind === "win_stale" ? (
            <FacetValue value={<>{gal(opp.gallons)}<span className="text-sm font-normal text-slate-400">/yr</span></>} tone="emerald"
              caption={<>{opp.dollars ? <><b>≈ {money(opp.dollars)}/yr</b> at current margin · </> : ""}chase via {opp.chaseChannel}.</>} />
          ) : opp.kind === "risk" ? (
            <FacetValue value={<>{gal(opp.gallons)}<span className="text-sm font-normal text-slate-400">/yr</span></>} tone="rose"
              caption={<>{opp.dollars ? <><b>≈ {money(opp.dollars)}/yr</b> committed · </> : ""}move to spot to de-risk.</>} />
          ) : (
            <FacetValue value="None to chase" tone="slate" caption={opp.note} />
          )}
          <Because>{opp.note}</Because>
          <p className="mt-1.5 rounded bg-slate-50 px-2 py-1 text-[10px] text-slate-400">{opp.caveat}</p>
        </FacetTile>

        {/* WEATHER (heating fuels only) */}
        {c.weather_sensitive && (
          <FacetTile title="Weather sensitivity" defKey="weather_adjust" accent="indigo">
            <FacetValue value={c.size_weather_adjusted ? "Cold-snap-adjusted" : "Weather-driven"} tone="neutral"
              caption={c.size_weather_adjusted
                ? <>A heating fuel — steadiness above is measured net of weather (β ≈ {fmt(c.weather_beta)} gal/HDD), so cold-snap swings aren't misread as inconsistency.</>
                : "A heating fuel — demand rises with cold. Load HDD to measure steadiness cold-snap-adjusted."} />
          </FacetTile>
        )}

        {/* COMMITMENT */}
        <FacetTile title="Commitment" defKey="current_channel" accent="slate"
          available={c.commitment_available} unavailableNote="No commitment on file — load the deal book.">
          <FacetValue value={<span className="text-base font-medium text-slate-700">{c.commitment_label}</span>}
            caption="From the deal book — what they're contracted for today." />
        </FacetTile>
      </div>

      {/* ───── DRILL-DOWN DOSSIER ───── */}
      <SectionTitle>Steadiness — how they buy</SectionTitle>
      <Card className="p-5">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-medium text-slate-700">Normal lane &amp; forecast</h3>
          <span className="text-[11px] text-slate-400">base ±1σ usual · ±2σ wider · dots = lifts · dotted = forecast</span>
        </div>
        {scoreLoading && !sc ? <ChartSkeleton /> :
          <BaseRangeChart series={sc?.lane_series ?? []} grain={sc?.grain ?? "weekly"}
            forecast={sc?.forecast_series} anchorDate={score?.forecast_anchor} />}
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-5">
          <h3 className="mb-2 text-sm font-medium text-slate-700">Where they sit — the planning 2×2</h3>
          <Quadrant2x2 cadence={c.cadence_consistency} size={c.size_consistency} label={c.name.split(" ")[0]} />
        </Card>
        <Card className="p-5">{sc?.excursions ? <LaneBreaks excursions={sc.excursions} /> :
          <div className="text-xs text-slate-400">Lane-break detail loads with the customer's history.</div>}</Card>
      </div>
      {sc?.behavior && <Card className="p-5"><BehaviorProfile behavior={sc.behavior} /></Card>}

      {/* MARGIN detail — product mix (volume only; per-product margin isn't computed, never implied) */}
      {(c.product_mix?.length ?? 0) > 0 && (
        <>
          <SectionTitle>Margin — what they're worth</SectionTitle>
          <Card className="p-5">
            <h3 className="mb-3 text-sm font-medium text-slate-700">Volume by product</h3>
            <div className="space-y-2">
              {c.product_mix!.map((p) => (
                <div key={p.product} className="flex items-center gap-3">
                  <span className="w-20 shrink-0 text-xs font-medium text-slate-600">{p.product}</span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                    <div className="h-2 rounded-full bg-indigo-400" style={{ width: `${Math.round(p.share * 100)}%` }} />
                  </div>
                  <span className="w-28 shrink-0 text-right text-xs text-slate-500">{gal(p.gallons)} · {Math.round(p.share * 100)}%</span>
                </div>
              ))}
            </div>
            <p className="mt-3 text-[11px] text-slate-400">Volume split only — margin is rolled up at the account level, so we don't imply a per-product ¢/gal we don't measure.</p>
          </Card>
        </>
      )}

      {/* WHAT'S NEXT — forecast + the peak-vs-actual (interim peak≈wallet hint for Phase 6) */}
      <SectionTitle>What's next</SectionTitle>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-5">{scoreLoading && !sc ? <div className="text-xs text-slate-400">Loading forecast…</div> :
          <ForwardProjection forecast={sc?.forecast} />}</Card>
        <Card className="p-5"><PeakVsActual stats={peakStats(sc)} /></Card>
      </div>

      <div className="pt-2 text-[11px] text-slate-400">Internal id {c.customer_id} · every number above traces to its engine; estimates are labelled.</div>
    </div>
  );
}

// ---- helpers ---------------------------------------------------------------------
function spineBar(tone: Tone): string {
  return ({
    emerald: "border-emerald-400 bg-emerald-50/50", indigo: "border-indigo-400 bg-indigo-50/50",
    amber: "border-amber-400 bg-amber-50/50", rose: "border-rose-400 bg-rose-50/50",
    slate: "border-slate-300 bg-slate-50", neutral: "border-slate-300 bg-slate-50",
  } as Record<string, string>)[tone];
}
function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="pt-3 text-sm font-semibold text-slate-700">{children}</h2>;
}
function ChartSkeleton() {
  return <div className="flex h-72 items-center justify-center rounded-lg bg-slate-50 text-xs text-slate-400">Loading the lane…</div>;
}
function fmt(x: number | string | null | undefined): string {
  if (x == null) return "—";
  if (typeof x === "string") return x;
  return Math.abs(x) < 10 ? x.toFixed(2).replace(/\.?0+$/, "") : num(x);
}
function regularityWord(cad: number | null): string {
  if (cad == null) return "—";
  return cad >= 75 ? "Very regular" : cad >= 60 ? "Regular" : cad >= 40 ? "Loose" : "Irregular";
}
function sizeWord(s: number | null): string {
  if (s == null) return "—";
  return s >= 75 ? "very consistent" : s >= 65 ? "consistent" : s >= 45 ? "variable" : "erratic";
}
function pctWord(p: number | null | undefined): string {
  if (p == null) return "Margin rank not available";
  if (p >= 0.75) return "Top quartile";
  if (p >= 0.5) return "Upper half";
  if (p >= 0.34) return "Lower half";
  return "Bottom third";
}
function marginBasisNote(on: boolean): string {
  return on ? "Valued against landed cost." : "Estimated from lift invoice prices (no sell grid loaded).";
}
function AxisRow({ label, defKey, value, weatherAdjusted }: {
  label: string; defKey: string; value: number | null; weatherAdjusted?: boolean;
}) {
  const tone: Tone = value == null ? "slate" : value >= 65 ? "emerald" : value >= 45 ? "amber" : "rose";
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-[11px]">
        <DefTip k={defKey}>
          <span className="cursor-help text-slate-500 underline decoration-slate-300 decoration-dotted underline-offset-2">{label}</span>
        </DefTip>
        <span className="font-medium text-slate-700">
          {value == null ? "—" : Math.round(value)}
          {weatherAdjusted && <span className="ml-1 text-[9px] text-indigo-400">wx-adj</span>}
        </span>
      </div>
      <Meter value={value} tone={tone} />
    </div>
  );
}

function peakStats(sc: CustomerScoreResponse["customer"] | undefined): BehaviorStats | null {
  const w = sc?.behavior?.windows?.all ?? sc?.behavior?.windows?.["90"];
  return w?.size_when_present ?? null;
}
function PeakVsActual({ stats }: { stats: BehaviorStats | null }) {
  if (!stats) return <div className="text-xs text-slate-400">Active-day load detail loads with history.</div>;
  const typical = stats.median ?? stats.p50;
  const peak = stats.p90;
  const headroom = typical > 0 ? peak / typical : null;
  return (
    <div>
      <h3 className="mb-2 text-sm font-medium text-slate-700">Typical vs peak load (active days)</h3>
      <div className="flex items-end gap-6">
        <div>
          <div className="text-[11px] text-slate-400">typical</div>
          <div className="text-xl font-semibold text-slate-700">{gal(typical)}</div>
        </div>
        <div>
          <div className="text-[11px] text-slate-400">peak (P90)</div>
          <div className="text-xl font-semibold text-indigo-700">{gal(peak)}</div>
        </div>
        {headroom && headroom > 1.3 && (
          <div className="ml-auto text-right">
            <div className="text-[11px] text-slate-400">peak ≈ wallet</div>
            <div className="text-sm font-medium text-emerald-700">{headroom.toFixed(1)}× headroom</div>
          </div>
        )}
      </div>
      <p className="mt-3 rounded bg-slate-50 px-2 py-1 text-[10px] text-slate-400">
        Interim peak≈wallet read from active-day sizes — a hint at untapped volume. Phase 6 replaces
        this with a modeled missing-volume estimate.
      </p>
    </div>
  );
}
