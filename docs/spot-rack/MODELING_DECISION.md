# Heating-fuel weather + Spot/Rack rebuild — modeling decision

This is the written record of the sequenced build that (0) made the HDD/weather and price/cost books
re-uploadable Data Studio sources, (1) modeled HDD→demand and **rewrote the heating-fuel variability
axes** on the weather residual, and (2) **rebuilt the spot-vs-rack recommendation** on the now-final
axes. The order is load-bearing: weather changes *how the size axis is measured*, spot/rack *reads*
that axis — so weather settles the axis values before spot/rack calibrates against them.

---

## Stage 0 — ingestion (everything this build reads is re-uploadable)

- **HDD / weather** (`weather_hdd.py`, table `weather_hdd` + `hdd_demand_anchor`): the "HDD'S" sheet
  has a messy multi-row header, so the parser finds the header row + date/year axis **empirically**
  and self-reports what it mapped. It handles both a **tidy** real-date layout and a **by-year
  matrix** (melted to dated observations), lands `station × day → HDD (+ Normal/5-yr/10-yr baselines)`
  and the **BX HO SOLD** anchor (`station × month → ho_sold`, paired with monthly HDD). Idempotent
  upsert on `(station, day)`; HDD ≡ `max(0, 65 − mean_temp)` is verified when a mean-temp column is
  present. Surfaced at `/api/weather/hdd/*`, `rackiq-load-hdd`, and the Data Studio "Re-uploadable
  sources" panel.
- **Price/cost grid**: Phase-2 already created `price_grid` / `landed_costs` and a re-upload path
  (`/api/margin/upload`). We **reused** it as-is and only surfaced it as a first-class Data Studio
  source — no duplicate store, no schema change.

HDD has no customer/product names to resolve; the price grid resolves customers through the existing
`customer_crosswalk` and products through `dealbook.product_family` (blend numbers are product
attributes, e.g. GEC 10/20 → GEC @ B10/B20), with unmapped grid names surfaced for the crosswalk.

---

## Stage 1 — weather (`weather_model.py`)

**Heating fuels only** (`ULSHO` / #2 / `HO4`). Gasoline, RD-99, ethanol are never touched.

- **Station map.** Each terminal maps to a station (NY/Bronx/Brooklyn → LGA; Newark → EWR; Baltimore
  → BWI; Pennsauken/Port Reading → PHL). HDD is read from the uploaded `weather_hdd` for the
  terminal's **own** station (`coverage: modeled`); a terminal without an uploaded station falls back
  to the Open-Meteo / climatology proxy (`coverage: proxy`). **LGA is never applied to Baltimore** —
  step 1 only matches a terminal's own station, so cross-application can't happen; the label is
  honest everywhere.
- **Demand β.** Per terminal × heating-product, `demand = baseload + β·HDD` on working-day-aggregated
  (weekly) demand, with in-sample R² and an **out-of-sample** check vs a weather-blind baseline. A
  wrong-sign (β ≤ 0) fit is flagged and never used; thin lanes inherit the terminal β. On the demo
  book: Linden ULSHO β≈2516 (R² 0.67, OOS beats blind +29%), Albany ULSHO β≈1227 (R² 0.77, +46%).
- **BX HO SOLD anchor.** When the HDD book carries HO SOLD, we regress monthly HO sold on monthly HDD
  and check it **agrees in sign** with the BOL-derived β before trusting it (the anchor and the BOL
  book are different volume universes, so we check agreement, not equality).
- **The axis rewrite (the load-bearing part).** For a heating-fuel customer with a stable positive
  per-lift HDD→size β (its own, else the terminal pool), the per-lift size used by the variability
  score becomes the **residual** `size − β·(HDD − HDD̄)`, re-centred to keep the level. Kept **only**
  when it lowers the size CV, so it can never manufacture steadiness; non-heating customers are
  untouched. On the demo book, weather-driven distillate accounts move toward steady (e.g. size CV
  0.34 → 0.25) while genuinely lumpy heating accounts keep their raw size (no over-smoothing).
- **Forward HDD seam.** `forward_hdd` returns a Normal/5-yr baseline curve now, labelled
  `is_live: false` — a pluggable seam for a live NOAA/CPC feed, baseline-vs-live labelled everywhere.
- **Forecast.** The weather-aware demand projection is kept only where it beats weather-blind
  out-of-sample; partial wins are reported honestly rather than forced.

---

## Stage 2 — spot vs rack (rebuilt on the now-final axes, in `variability.py`)

### Root cause of the all-spot bug (stated before any change)

The 2×2 timing axis used the behavioral **frequency class** (`active-day rate over all working days`,
zeros included) instead of cadence **regularity**. Real wholesale customers lift weekly/biweekly, so
none clear "frequent" → every regular weekly lifter bunched into the infrequent rows → spot. The size
axis was already correctly active-day (size scores spread 42–100); the dilution was on the **timing**
axis. Empirically (weekly-lifter fixture): "Weekly Steady" read cadence **78** (regular, gap CV 0) yet
`frequency=occasional` → it landed `infrequent_identical` → spot. The cadence *score* was right; the
quadrant just wasn't reading it.

### The four fixes

1. **Quadrants on the two SCORES** with tunable, principled cutoffs (config, not magic numbers):
   `regular_timing = cadence ≥ 60` (a perfectly regular lifter earns ~72 from the regularity term
   alone, at any frequency) × `consistent_size = size ≥ 65` (active-day size CV ≤ 0.35). →
   **metronome** / **predictable_timing** / **predictable_size** / **unpredictable**, each with a
   written definition and a channel (metronome → RACK/TERM; predictable_size → RACK, watch timing;
   predictable_timing → capped RACK or SPOT; unpredictable → SPOT). Calibrated until the real-book
   distribution spreads across all four (the demo book: 19 / 0 / 15 / 6; the weekly fixture: all four).
2. **Confidence tier** from lift count + span (High ≥ 200 lifts/365 d · Medium ≥ 100 lifts/180 d ·
   else Low) — absolute, so a ~5,800-lift account is High and an ~88-lift account is Low. Low-
   confidence accounts **still get a rec**, flagged "provisional — based on only N lifts"; never
   suppressed. Confidence annotates trust; it never changes the quadrant.
3. **Margin is ranking only.** Channel is set by variability + confidence **only**. The Phase-2 margin
   rank attaches a human-judgment **note** where margin and channel are in tension ("steady, but earns
   more on spot today") — it can never move a customer between rack and spot. Enforced in code (the
   recommended channel is read straight off the quadrant) and audited in the validation readout
   (`channels_flipped_by_margin` must be 0).
4. **Current-vs-recommended mismatch** (the headline): each customer's channel **today** is read from
   the deal book (term/forward = contract, spot, or mixed); the rebuilt rec is compared to it; the top
   mismatches each direction are surfaced with reasons + confidence — steady metronomes stuck on spot
   (upside) and irregular accounts term-committed (risk).

### Definitions

Every metric, axis, cutoff, quadrant, confidence tier, β, weather adjustment, and channel is defined
in plain English in one place (`frontend/src/lib/varGlossary.tsx`), surfaced **both** as inline hover
tooltips throughout the Spot-vs-Rack page **and** on the dedicated **Glossary** page (with worked
examples for the four quadrants and the confidence tiers).
