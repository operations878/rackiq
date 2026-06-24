# RackIQ

**Customer demand & margin intelligence for wholesale fuel terminals.**

RackIQ ingests a terminal company's lift/sales book (and, when available, AR, physical
inventory, and market prices) and surfaces demand, margin, receivables, inventory, and
market analytics. It is built for a multi-terminal wholesale fuel marketer (e.g. Soundview
Energy) that sells refined products — **no blending operations**.

> **Core principle — capabilities flex with the data provided.**
> There is one canonical schema. Only `customer_id`, `lift_datetime`, and `net_gallons`
> are required; everything else is optional. A **capability matrix** inspects which
> canonical fields are actually populated and enables/disables features accordingly. The
> matrix is the single source of truth and is exposed over the API for the UI to read.

---

## Architecture

Monorepo with a Python backend and a React frontend.

```
            ┌──────────────────────────────────────────────────────────┐
            │  backend/  (FastAPI + DuckDB)                             │
            │                                                          │
  generator.py ──drop+recreate+bulk insert──▶  DuckDB (data/rackiq.duckdb)
   ingest.py ─▶ hygiene.py ─▶ canonical tables ──▶      │               │
            │                                          │               │
            │   schema.py  ── single source of truth ──┤               │
            │   capabilities.py ── reads non-null cols ┘               │
            │        │                                                 │
            │   api/routes.py  (reads)   api/studio.py (uploads/writes)│
            │        └──────────────▶ /api/* JSON ◀─────────┘          │
            └────────────────────────────────────┬─────────────────────┘
                                                  │  (Vite dev proxy /api → :8000)
            ┌─────────────────────────────────────▼────────────────────┐
            │  frontend/ (Vite + React + TS + Tailwind v4 + Recharts)   │
            │  Dashboard  ·  Data Studio (upload → map → validate → go) │
            └──────────────────────────────────────────────────────────┘
```

- **Storage:** a single DuckDB file at `backend/data/rackiq.duckdb` (gitignored,
  regenerable). Because **Data Studio writes while the server is live**, the API process
  holds ONE long-lived **read/write** connection (`db.get_shared_connection()`) guarded by
  a process-wide lock (`db.lock()`); all reads and writes go through it. DuckDB is
  single-writer *per process*, so while the server runs it holds the file lock — use the
  UI's **Load demo / Reset** (or stop the server) instead of the CLI generator against the
  served file.
- **First run is empty:** with no DuckDB file, the shared connection initializes empty
  canonical tables. The app boots to a "no data — open Data Studio" state; you feed it via
  upload or the demo button. Nothing needs to be generated up front.
- **Single source of truth:** `backend/app/schema.py` declares every canonical field once.
  DDL, the generator, capability detection, the Data Studio import targets, and the API all
  derive from it.

---

## Canonical schema

**45 canonical fields = 3 required + 42 optional**, organized into seven canonical data
tables plus a `customers` dimension. Defined in `backend/app/schema.py`.

| Table | Grain | Fields |
|---|---|---|
| **lifts** | one lift/load event | **`customer_id`\***, **`lift_datetime`\***, **`net_gallons`\***, `terminal`, `product`, `gross_gallons`, `observed_temp`, `api_gravity`, `unit_price`, `unit_cost` (+ optional key `bol_number`) |
| **inventory_snapshots** | terminal × product × tank × time | `tank_id`, `tank_capacity`, `min_heel`, `inventory_snapshot`, `physical_inventory`, `receipts` (+ keys `snapshot_datetime`, `terminal`, `product`) |
| **invoices** | one invoice (AR) | `invoice_date`, `due_date`, `paid_date` (NULL = open), `invoice_amount`, `credit_limit` (+ key `customer_id`) |
| **market_prices** | price_date × product × terminal | `market_price`, `nyh_basis`, `street_rack`, `committed_buys`, `committed_sells`, `rack_benchmark` |
| **quotes** *(early feed)* | one quote given | `quoted_price`, `market_price_at_quote`, `inventory_state`, `capacity_state`, `competitor_context`, `outcome` (accept/reject/no_response), `time_to_decision`, `final_gallons` (+ keys `customer_id`, `quote_time`, `product`) |
| **receipts** *(early feed)* | one receipt landed | `receipt_source` (marine/pipeline/truck), `receipt_gross_gallons`, `receipt_net_gallons`, `measurement_basis` (shore_tank/ship_meter/pipeline_meter/truck_meter), `bl_vs_received_variance` (signed) (+ keys `receipt_datetime`, `terminal`, `product`) |
| **bol_compartments** *(P8 reconciliation)* | one BOL compartment (rack/truck loading) | `compartment_gross_gallons`, `compartment_net_gallons` (billed/metered), `compartment_temp`, `compartment_api`, `compartment_unit_cost` (+ keys `bol_number`, `bol_datetime`, `terminal`, `product`, `tank_id`, `meter_id`, `customer_id`, `compartment_id`) |
| **customers** *(dimension)* | one customer | `customer_id`, `name`, `archetype`, `home_terminal` |

\* = required core field. `terminal`/`product` are detected for presence on **lifts** (their
primary home); their copies on inventory/market/quotes/receipts/bol_compartments are dimensional keys.
A **disbursement** is one `bol_number` (sum its compartment rows) — never a single compartment.
`bol_number` is also an **optional key on `lifts`**: a wide BOL/EDI export lists each metered
compartment of a load on its own row, all sharing one BOL number. On a lifts import those rows are
**grouped by `bol_number` and summed** (gross + net) into a single lift at commit — they are never
treated as standalone lifts. It is nullable: a lift with no BOL still imports.

### Early data feeds — start collecting now, modules consume later

Three feeds let history accumulate before the analytics that read them ship. They are wired
through the **same** column-mapping + hygiene + capability pipeline as everything else:

1. **`rack_benchmark`** (on `market_prices`) — the daily street/OPIS rack reference. Logged via a
   quick **daily-entry form** (date · terminal · product · price) *or* CSV/OPIS import. Powers the
   Pricing Sandbox + elasticity models.
2. **`quotes`** — the **elasticity training set**: every quote outcome incl. **rejections** (the
   point). Logged via a fast in-app form (customer resolved through the crosswalk) *or* bulk CSV.
3. **`receipts`** — receipt detail (source / gross+net gallons / measurement basis / BL-vs-received
   variance). Optional, capability-gated for **P8**. Imported via the wizard.

These surface as **feed capabilities** that are *never hard-locked*: they report
`status: "collecting"` with `collecting: {count, target, unit, label}` (e.g. "collecting — N days
logged") and flip to `enabled` once they cross their target. Running counts also appear on the
**Data Health** dashboard. Quick-entry endpoints: `POST /api/studio/rack-benchmark`, `POST
/api/studio/quote` (both append through the hygiene pipeline).

**Derived concepts** (computed from stored columns; nothing is discarded):
net-vs-gross / VCF shrinkage ← `gross_gallons`,`net_gallons`(+`observed_temp`,`api_gravity`);
DSO & aging buckets ← invoice dates + amount; days-of-supply ← inventory + capacity + heel;
gain/loss ← `physical_inventory` vs `inventory_snapshot`; net position ← `committed_buys` − `committed_sells`.

---

## Capability matrix

`backend/app/capabilities.py` declares **21 features** (18 analysis + 3 *feed*). Each feature
lists the canonical fields it `requires` (and optional fields that `enhance` it). At runtime:

- A field is **present** if it has ≥1 non-null value in its primary table.
- `coverage` = non-null ÷ that table's own row count (an empty sibling table never dilutes
  another table's coverage).
- An **analysis** feature is **enabled** iff all its required fields are present (else `locked`).
- A **feed** feature (`kind:"feed"`) is *never hard-locked*: it reports `status:"collecting"` with
  `collecting:{count,target,unit,label}` and flips to `enabled` once its count crosses the target.
  `compute_capabilities` also returns a `feeds` block with the raw running counts.

Served at **`GET /api/capabilities`**:

```jsonc
{
  "profile": "full",
  "categories": ["Demand","Margin","Receivables","Inventory","Market","Pricing"],
  "fields":   { "unit_cost": {"present":true,"nonnull":6541,"applicable":6541,"coverage":1.0}, ... },
  "features": [ { "key":"margin_analysis","enabled":true,"missing_fields":[],
                  "enhanced_by":["product","terminal"],"coverage":1.0, ... } ],
  "summary":  { "enabled": 21, "total": 21 }
}
```

| Category | Features (required fields) |
|---|---|
| **Demand** | demand_ranking (customer_id, net_gallons) · lift_cadence (customer_id, lift_datetime) · archetype_detection (core 3) · demand_forecast (core 3) · product_mix (net_gallons, product) · terminal_breakdown (net_gallons, terminal) |
| **Margin** | net_vs_gross (net_gallons, gross_gallons) · margin_analysis (unit_price, unit_cost, net_gallons) · revenue (net_gallons, unit_price) |
| **Receivables** | ar_aging (invoice_date, due_date, invoice_amount) · dso (invoice_date, paid_date, invoice_amount) · credit_risk_late_payers (due_date, paid_date) |
| **Inventory** | inventory_days_of_supply (inventory_snapshot, tank_capacity, min_heel) · gain_loss_reconciliation (physical_inventory, inventory_snapshot) · tank_utilization (inventory_snapshot, tank_capacity) · **reconciliation** (physical_inventory, receipt_source — the P8 loss-control module; enhanced by bol_compartments) · **receipt_detail** *(feed: receipt_source; target 20 receipts)* |
| **Market** | basis_tracking (market_price, nyh_basis) · position_committed (committed_buys, committed_sells) |
| **Pricing** | **pricing_sandbox** *(feed: rack_benchmark; target 30 days)* · **quote_elasticity** *(feed: quoted_price + outcome; target 50 quotes)* |

### Data profiles make the matrix flex

The generator can omit optional field groups, so you can watch capabilities turn on/off
from the **same code** on different data:

| Profile | Populated | Enabled features |
|---|---|---|
| `core` | only the 3 required fields (no inventory/invoices/market/quotes/receipts) | **4** |
| `lite` | core + `terminal` + `product` on lifts | **6** |
| `full` | every canonical field (incl. rack_benchmark, quotes, receipts, bol_compartments) | **21** |

```
rackiq-generate --profile core   #  capabilities enabled: 4/21
rackiq-generate --profile lite   #  capabilities enabled: 6/21
rackiq-generate --profile full   #  capabilities enabled: 21/21
```

(The 3 feed capabilities count toward "enabled" only once their history crosses the target;
the `full` book generates enough rack-benchmark days / quotes / receipts to mature all three.)

---

## Data Studio — the front door for feeding RackIQ

Data Studio is how a real book gets in: upload a CSV/Excel file, map its columns to canonical
fields, preview validation, and commit. Capabilities then flex from the fields actually present.

**Backend modules**
- `app/ingest.py` — parse (CSV/TSV/Excel; delimiter auto-detected for text, Excel cells read as
  typed values — dates as dates, numbers as numbers; arbitrary column count tolerated, unmapped
  columns ignored), **fuzzy header matching** (curated synonyms incl. BOL/EDI aliases like
  *Consignee Number*→`customer_id` + string-similarity + token overlap), per-table mapping
  suggestions with a **two-tier threshold** (required keys match generously; optional fields need
  high confidence so a loose header never auto-fills a numeric field with junk), target-table
  inference, column inspection, type **coercion** (mixed-format date salvage + **Excel serial
  dates** — `45474` → 2024-07-01 — applied ONLY inside date coercion, so a numeric *non-date*
  column like a customer number is never reinterpreted as a date), and mapping **validation**.
  Parsed uploads are cached in-process (bounded) keyed by an `upload_id`.
- `app/profiling.py` — the **data-quality scorecard**: per column type, null %, distinct count,
  min/max, sample values, outlier counts (IQR fences), and quality flags (mixed-type, high-null,
  negatives, unparsed-dates, whitespace, constant) + an overall 0–100 score.
- `app/crosswalk.py` — the **Customer Master crosswalk** (entity resolution / de-duplication):
  fuzzy-clusters customer key variants into proposed merge groups with a confidence score,
  persists confirm/reject decisions, and rewrites variant ids → master id on every commit.
- `app/validation.py` — the **rule engine**: required-present, **edi-control-row** (junk), dates-
  parseable, dates-in-range, **volume-corrections**, value-bounds, duplicate-lifts, price≥cost —
  each with a severity, a count, and **drill-down rows**; rules with `action="quarantine"` feed the
  quarantine index. **Required-only gating:** the *only* rules that quarantine a lift/BOL row are
  required-present (a missing/unparseable `customer_id`/`lift_datetime`/`net_gallons`) and edi-
  control-row (`bol_number`=0 **and** gross=0 **and** net=0 — EDI heartbeat junk, often product
  `ZZZ`). A blank/unused **optional** column — however many — never quarantines a row. Negative
  gross/net are legitimate **reversals/corrections**: kept, tagged, and listed (never quarantined).
  Date rules run ONLY on the date target (e.g. `lift_datetime`), never on numeric columns.
- `app/hygiene.py` — the **configurable cleaning pipeline** (`HygieneOptions` → `apply_fixes`):
  trim (auto-trims surrounding whitespace on text fields with an audit line — whitespace never
  quarantines), drop-empty, **unit standardization** (bbl→gal ×42), **default fill**, **ASTM D1250
  net(60°F) correction** (`vcf(api, temp, product)`), and **crosswalk resolution**, plus
  **`group_by_bol`** (collapse compartment rows sharing a `bol_number` into one lift — gross & net
  summed, every other field first-non-null; run by the caller on the *clean* rows after the
  quarantine split, so junk never lands in a group). Each step emits a human report line and a
  structured audit entry. `run_pipeline(df, table)` is kept as
  the conservative lossless default.
- `app/data_health.py` — the **standing health report**: composite quality score
  (completeness · validity · consistency · resolution) + drift alerts (un-mapped/variant customer
  codes, volume out of historical pattern) + quarantine/crosswalk/audit summaries.
- `app/api/studio.py` — the `/api/studio/*` endpoints, orchestrating profile → map → fix →
  validate → **quarantine split** → write → audit → recompute capabilities on every write.

**Import targets.** A file targets exactly one canonical table; its columns map to that table's
*import targets* = structural keys (grain/foreign keys) + that table's canonical fields. Required
mappings per table (must be set to commit): lifts → `customer_id, lift_datetime, net_gallons`
(everything else — `terminal`, `product`, `gross_gallons`, `bol_number`, temp/gravity, price/cost —
is optional and never required to commit; a wide BOL/EDI export thus needs only those three mapped,
and the matcher auto-fills them by header incl. *Consignee Number*→`customer_id`);
invoices → `customer_id`; inventory → `snapshot_datetime, terminal, product`; market →
`price_date, product`; quotes → `customer_id, quote_time, product, quoted_price, outcome`;
receipts → `receipt_datetime, terminal, product, receipt_source`; bol_compartments →
`bol_number, bol_datetime, compartment_net_gallons` (terminal/product/tank_id are optional,
defaultable dimensional keys — a partial BOL feed still imports). Derived in
`schema.import_targets(table)` from the single source of truth.

**Wizard flow** (`POST` unless noted):

| Step | Endpoint | What it does |
|---|---|---|
| Inspect | `/api/studio/inspect` (multipart) | parse + stash; return the **profiling scorecard** (columns + samples + null rates + distinct + min/max + outliers + flags + score), suggested table, per-table fuzzy suggestions, mappable targets, matched profile, crosswalk size |
| Validate | `/api/studio/validate` | apply the chosen **hygiene fixes**, then run the **rule engine**: returns `rules` (with drill-down rows), `fixes_preview`, `quarantine_count` (+ `quarantine_reasons` breakdown), `clean_rows`, `corrections`, `lifts_after_grouping` (post-BOL-grouping lift count), plus the mapping-level `can_commit` |
| Commit | `/api/studio/commit` | coerce → `apply_fixes` → run rules → **split quarantine** → **group by BOL** (clean rows) → write (replace/append) → derive `customers` (names from crosswalk) → log audit → recompute capabilities; returns `rows_in_file`, `clean_rows`, `lifts_after_grouping`/`rows_written`, `corrections`, `quarantined` + `quarantine_reasons`, hygiene report |
| Targets | `GET /api/studio/targets` | static registry powering the mapping dropdowns (+ `customer_key_column`, `defaultable_fields`) |
| Crosswalk | `POST …/crosswalk/propose`, `…/crosswalk/confirm`, `GET …/crosswalk`, `DELETE …/crosswalk/{key}`, `POST …/crosswalk/clear` | propose merge groups, persist confirm/reject, browse/edit the master crosswalk |
| Quarantine | `GET …/quarantine`, `POST …/quarantine/reimport`, `…/quarantine/discard` | review held rows, fix-and-re-import (with edits), or discard |
| Data health | `GET …/data-health` | the standing quality-score + drift report |
| Audit | `GET …/audit` | recent hygiene transformations |
| Profiles | `GET/POST /api/studio/profiles`, `DELETE …/{name}` | save/list/delete named **cleaning profiles** (mapping **+ hygiene options**); a re-uploaded file whose columns satisfy a profile auto-applies its mapping *and* its fix settings |
| History | `GET /api/studio/history` | recent imports (table, filename, rows, mode) |
| Quick feeds | `/api/studio/rack-benchmark`, `/api/studio/quote` | append a daily rack benchmark / a single quote (resolved via crosswalk) through the hygiene pipeline; bumps the "collecting" counters live |
| Demo / Reset | `/api/studio/load-demo`, `/api/studio/reset` | load the synthetic book (`core`/`lite`/`full`) or clear the store |

Saved profiles, the import log, the **customer crosswalk**, the **hygiene audit log**, and the
**quarantine queue** live in dedicated tables (`import_profiles`, `import_log`,
`customer_crosswalk`, `hygiene_audit`, `quarantine`) that **survive** demo regeneration / reset on
purpose — merge decisions and held rows are never lost when the book is reloaded.

**Frontend** (`pages/DataStudio.tsx` + `components/studio/*`): a **5-step** wizard — **Upload**,
**Map Columns**, **Clean** (`CleanStep` = `ProfilingScorecard` + `CustomerMasterPanel` +
`FixOptions`), **Validate** (stat cards + fixes preview + rule cards with row-level drill-down),
and **Commit** (rows written + hygiene report + quarantine link). A live **Data Capability** panel
sits alongside, unlocking features the instant data lands, plus a **Quick Feeds** panel
(`components/studio/QuickFeeds.tsx`) with the rack-benchmark daily-entry and quote-logger forms.

---

## Data Hygiene Studio — clean before it lands

The Hygiene Studio runs on **every upload, before the canonical write**. It is the **Clean** step
of the wizard plus the standing **Data Health** page, and covers eight jobs:

1. **Profiling scorecard** (`profiling.py`) — per-column type, null %, distinct, min/max, samples,
   outlier count, and quality flags; an overall 0–100 score shown on upload.
2. **Customer Master / de-duplication** (`crosswalk.py`) — *the most important job*. Fuzzy-clusters
   the distinct customer keys (optionally aided by a name column) into **merge groups** with a
   confidence score. You confirm or reject each (and may edit membership / master id+name). Decisions
   persist in the **`customer_crosswalk`** table; `apply_to_frame` rewrites every variant → master id
   on **every future commit**, so all downstream metrics read one resolved entity. Rejected keys are
   pinned as singletons and never re-proposed.
3. **Validation rules** (`validation.py`) — **required-only gating**: a lift/BOL row is quarantined
   ONLY for a missing/unparseable required field (`customer_id`/`lift_datetime`/`net_gallons`) or a
   genuine **EDI control row** (`bol_number`=0 **and** gross=0 **and** net=0). The other rules —
   dates-parseable, dates-in-range, **volume-corrections** (negatives kept & tagged, never
   quarantined), value-bounds (highs only for volumes), duplicate-lifts (customer·datetime·net,
   opt-in), price≥cost — are advisory (`action="none"`). A blank/unused **optional** column never
   quarantines a row. Each failure carries a **drill-down** to the offending rows.
4. **Auto-fix with approval + audit** (`hygiene.apply_fixes`) — trim whitespace (always, with an
   audit line), **standardize units** (barrels→gallons), parse mixed/serial date formats, **fill
   terminal/product defaults**, resolve customers, and **group compartment rows by BOL** into one
   lift (gross & net summed). Toggled per import; every transformation is written to **`hygiene_audit`**.
5. **Net (60°F) correction** — when `gross_gallons` is mapped, compute net via an **ASTM D1250-style
   VCF** (`hygiene.vcf(api, temp, product)`); modes: `auto` (D1250 where temp+API exist), `factor`
   (flat user factor), `gross` (net = gross), `off`. Gated on field availability.
6. **Quarantine + re-import** — rows failing a hard rule (missing required field, EDI control row,
   opted-in duplicate lifts) are diverted to the **`quarantine`** table instead of being dropped
   (negatives are NOT a hard rule — they pass through as corrections). The Data
   Health page lets you edit the held values and **fix-and-re-import**, re-import all (re-run the
   rules), or discard.
7. **Reusable cleaning profiles** — saved profiles store the **mapping + hygiene options** together,
   so a repeat upload is one click and consistent. The crosswalk is global, so merge decisions apply
   regardless of profile.
8. **Standing Data-Health dashboard** (`data_health.py`, `pages/DataHealth.tsx`) — composite quality
   score with component bars, **drift alerts** (new/likely-variant customer codes, monthly volume
   outside ±2σ of history), the quarantine queue, the crosswalk browser, and the audit log.

**Net-60 correction (ASTM D1250-style).** `VCF = exp(−α·ΔT·(1 + 0.8·α·ΔT))`, `ΔT = T − 60°F`,
`α = (K0 + K1·ρ₆₀)/ρ₆₀²` with `ρ₆₀ = (141.5/(131.5+API))·999.016 kg/m³` and product-group constants
(gasoline / distillate / crude). `vcf=1.0` exactly at 60°F; hot → shrink, cold → expand.

**Sample files.** `uv run rackiq-export-samples` writes `samples/{lifts,invoices,market_prices,
inventory_snapshots,quotes,receipts}_sample.csv` (+ `lifts_sample.xlsx`) with *friendly* headers
(e.g. "Customer", "Lift Date", "Sell Price", "Posted Rack", "OPIS Rack", "Quoted Price", "Outcome")
so the fuzzy matcher has something to chew on. Importing them walks the capability matrix up. It also writes **deliberately-dirty** Hygiene Studio
demo files: `samples/lifts_dirty.csv` (customer NAMES with spelling/ID variants of several
customers, mixed/bad dates, negative volumes, exact-duplicate rows, stray whitespace) and
`samples/lifts_barrels.csv` (volumes in barrels, for the unit-standardization toggle). `--no-dirty`
skips them. Worked screenshots of the merge + fix flow live in `docs/hygiene-studio/`.

---

## Synthetic data generator

`backend/app/generator.py` builds a realistic, deterministic-per-seed "Soundview" book:
~40 customers across 3 terminals (Linden / Providence / Albany), products RBOB / ULSD /
ULSHO, ~21 months, plus matching AR, inventory snapshots, and daily market prices. In `full`
it also generates the early feeds: a daily `rack_benchmark`, a **quote log** (accepts +
rejections with a recoverable per-archetype price elasticity), and **receipt detail** derived
from inventory replenishments (source / measurement basis / BL variance).

**Customer archetypes** (default mix sums to 40, scales with `--n-customers`):

| Archetype | n | Behavior |
|---|---|---|
| `ratable` | 12 | steady base-load, near-constant cadence, low variance |
| `weather_distillate` | 9 | ULSHO/ULSD; volume & frequency ∝ heating-degree-days → winter spikes |
| `price_chaser` | 8 | lifts only when posted rack is below a personal threshold; erratic |
| `marine` | 4 | a few very large, irregular parcels; long quiet stretches |
| `cstore_chain` | 7 | frequent small RBOB lifts, weekday-skewed |

**Realism details:** daily ambient temperature drives an HDD seasonality curve; market
prices are per-product geometric random walks with winter drift for distillates; NYH basis
is mean-reverting (OU); posted `street_rack` = market + basis + per-terminal markup + noise;
net = gross × VCF where `VCF = 1 − α(api)·(observed_temp − 60°F)`; invoices derive from
lifts with per-customer terms (net-10/15/30), a subset of **chronically late payers**, and
recent invoices left **open** (`paid_date` NULL).

**Inventory + BOL disbursements (P8 reconciliation, `full` only).** Each lift explodes into a
**bill of lading** with 1–N metered **compartments** (`bol_compartments`) whose billed net sums to
the lift; gross is back-computed from an independent ASTM D1250 net so the engine's recompute
recovers the truth. The book inventory rolls on **billed** disbursements; `physical_inventory`
rolls on **true** disbursements minus seeded shrink — so book-vs-physical reveals real losses.
The generator deliberately seeds, deterministically over the sorted tanks: a **bad-VCF lane**
(one meter's temperature probe reads hot → billed net runs ~0.4–0.6% under recompute,
temperature-correlated), two **meter-drift tanks** (totalizer reads progressively low → loss-%
trends up out of control), one **high-evaporation tank** (elevated physical shrink), and routine
~0.05% shrink + gauge noise elsewhere. Receipts carry a gross-vs-net thermal gap and a
source-biased B/L variance (marine **VEF** / pipeline shrink).

**Parameters:** `--seed --n-customers --months --terminals --products --profile {core,lite,full}
--end-date --db`. Regeneration drops and recreates all tables, deterministic per seed.

---

## Customer scoring — VAR lane · sub-scores · base value · archetypes

`backend/app/scoring.py` (engine) + `scoring_config.py` (**every weight/threshold/window is a
config parameter**) read the **resolved** customer master (ids already rewritten to master at
commit), compute everything over rolling **30/90/365-day windows + all-time**, flag
**data-sufficiency** per customer, and **capability-gate every metric** (each carries
`available: true/false + reason`). DuckDB views back the SQL-friendly facts
(`v_customer_facts`); Python (pandas/numpy/statsmodels/scipy) does STL, regressions, and
percentile ranking. Results persist to `customer_scores` + `customer_lane`.

- **Part 1 — VAR base-range (lane) model** on net volume (weekly buckets; monthly for sporadic
  accounts). *Base volume* = seasonally-aware STL trend+seasonal fitted value (robust
  seasonal-median fallback for short history). *Base range* = base ± 1 robust σ of the
  de-seasonalized residual (or a fixed `±%`). *Variability range* = base ± 2σ. **VAR score** =
  `0.45·in_band + 0.35·tightness + 0.20·(1 − excursion)` (weights configurable),
  grade A≥80/B/C/D, guard ≥8 lifts over ≥12 weeks. A **cadence lane** scores inter-lift timing;
  headline VAR blends volume/cadence **70/30**. *(This VARIABILITY score is distinct from any
  financial VaR — never conflated.)* The per-period base / base-range / variability-range series
  is persisted and drawn as the **base-range chart** (the leadership screen).
- **Part 2 — Layer-1 behavioral facts**: order size mean/median/CV, monthly volume, frequency,
  days-between mean & CV, margin/gal mean & CV, days-since-last, product mix + HHI,
  rush/split/small-order/cancel rates + friction-tag count, payment terms, days-to-pay mean & CV,
  credit utilization.
- **Part 3 — Layer-2 sub-scores** (0–100, percentile-ranked across the active book unless noted):
  Volume/Timing Steadiness (= VAR lanes), Price Sensitivity (β of accept-incidence vs price−reference,
  gated on quotes/rack benchmark), **EVR** (demand model vs naive-calendar baseline — the
  useful-vs-dangerous separator), Discount Efficiency (`incremental_GP / GP_given_up`), Market
  Sensitivity (signed corr profile), Weather Sensitivity (HDD/CDD β; NOAA fetch pending → seasonal
  proxy), Quote Score (accept/negotiate/latency/lowest-only), Churn Risk. Plus the **Variability
  Quality Quadrant** (Explainability = EVR × Profitability) → Strategic Lever / Premium Spot /
  Managed Cost / Dangerous Noise.
- **Part 4 — Layer-3 Base Value**: `EGP = annual_gal·margin`; friction & credit costs; `RFAP =
  EGP − friction − credit`; profit per gallon/rack-hour/credit-$/order; strategic uplift
  (0.8–1.5); **Base Value** = `100·[0.50·pct(RFAP) + 0.30·pct(profit-per-constraint) +
  0.20·pct(strategic)] × uplift`, grade A/B/C/D.
- **Part 5 — Archetype classifier**: a **primary + secondary** of the 12 archetypes from
  *sub-score signatures* (not hard-coded names), each with a confidence and the standing posture
  (pricing/terms/allocation) it triggers; ambiguous cases (small top-1/top-2 gap) are flagged.
- Plus **Account Value** (volume×margin×VAR/100, percentile), **Recency gap** (days since last ÷
  base cadence), and a **backtest** helper (per-customer one-step MAE by method: naive-last,
  seasonal, lane-base).

## Reconciliation & loss control (P8) — book vs physical · mechanism split · meter drift

`backend/app/reconciliation.py` (engine) + `reconciliation_config.py` (**every threshold is a
config parameter**) compute terminal gain/loss per **tank · terminal · product · period** (monthly
or weekly). **Gated on `physical_inventory` + `receipt_source`** (clear lock + "feed me X"
otherwise); uses the Hygiene Studio's ASTM D1250 `vcf()` for the independent net recompute. Live-
computed over the shared connection with a data-signature cache (`api/reconciliation.py`).

- **Part 1 — Book vs physical** (GROSS & NET): `opening_physical + receipts − BOL_disbursements −
  closing_physical = gain/loss`. **Disbursements are grouped by `bol_number` and summed across
  compartments** — a compartment row is never a standalone lift (raw rows from `bol_compartments`).
  Each tank's first period only seeds the opening gauge.
- **Part 2 — Net-recon cross-check**: where a BOL carries a billed net AND temp+gravity allow an
  independent ASTM D1250 recompute, the two are compared and **systematic divergence flagged by
  lane/meter/terminal** (probe calibration / VCF mismatch — disagreement is signal, not noise). The
  billed net is **never overwritten**; the delta + a cause hint (totalizer drift vs VCF/probe) is
  reported.
- **Part 3 — Loss-mechanism split**: each tank's loss separates into **(a) temperature/volumetric**
  = `(disb_gross − disb_recompute) − (receipt_gross − receipt_net)` (benign — nets out under VCF),
  **(b) measurement** = `recomputed_net − billed_net` (the cross-check; meter drift / gauging), and
  **(c) physical** = residual (evaporation / line-fill / theft). `measurement + physical` = net
  loss; all three sum to the gross gap.
- **Part 4 — Receipt measurement basis**: marine vessel **B/L-vs-shore (VEF)** and pipeline
  **B/L-vs-received shrink** surfaced as their own line items (source · basis · gross/net · variance).
- **Part 5 — Loss tracking**: loss-% of throughput over time per tank/network, routine shrinkage vs
  anomalies (above the control limit).
- **Part 6 — Meter-drift detection**: control-chart logic — each tank's loss-% vs the **network
  routine distribution** (robust center ± k·σ); tanks running persistently beyond the UCL (or a long
  run above center, Western-Electric style) are flagged and **ranked by severity** (+ trend).
- **Part 7 — Dollarize**: losses valued at `compartment_unit_cost` (fallback lift cost / default),
  ranked (e.g. *"Tank 4 ULSD 0.18% vs 0.05% network avg ≈ $X/yr"*), with a **network recoverable**
  total (loss above routine shrink).

## API endpoints

All return JSON over the shared connection (`db.lock()` serializes access). Read endpoints
live in `api/routes.py`; `/api/studio/*` write/upload endpoints in `api/studio.py`; the
`/api/scores/*` scoring endpoints in `api/scores.py`, `/api/reconciliation/*` in
`api/reconciliation.py` (both live-compute with a data-signature cache), the
**daily-operating / regime / scorecard / playbook** endpoints in `api/daily.py`, and the
**Demand Cockpit** endpoints in `api/demand.py` (heavy forecast cached per scope, the
service-level slider re-derives only the cheap action).

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | liveness + active profile |
| `GET /api/summary` | counts, terminals, products, date range, total net gallons (drives the banner) |
| `GET /api/schema` | canonical field registry joined with live coverage |
| `GET /api/capabilities` | the capability matrix (above) |
| `GET /api/customers` | per-customer rollups; `avg_margin_per_gal`/`dso_days` are `null` when those capabilities are off — the API itself honors the matrix |
| `GET /api/market-prices?product=ULSD` | market vs street-rack time series (`available:false` when absent) |
| `GET /api/monthly-volume` | monthly net gallons (needs only required fields; survives `core`) |
| `GET /api/scores?window=all` | ranked customers (VAR+grade, base value+grade, archetype, volume, trend) + per-metric `availability` |
| `GET /api/scores/customer/{id}?window=` | drill-down: lane series for the base-range chart, VAR explanation, sub-scores, base value, archetype posture |
| `GET /api/scores/quadrant?window=` | Explainability × Profitability scatter points with archetype tags |
| `GET /api/scores/backtest` | per-customer one-step forecast error by method |
| `GET /api/scores/config` | the scoring config (every weight) + windows + archetypes/posture |
| `POST /api/scores/recompute` | recompute all windows (+ optional `{overrides}`) and write `customer_scores`/`customer_lane` |
| `GET /api/reconciliation?period=month` | the full loss-control payload: network totals + mechanism split, ranked worst-offender tanks (control chart series), net-recon by meter/terminal, receipt basis, loss-tracking series, meter-drift ranking (`available:false` + missing feeds when locked) |
| `GET /api/reconciliation/config` | the reconciliation config (control limits, thresholds) + period grains |
| `POST /api/reconciliation/recompute` | recompute with optional `{overrides, period}` (busts the cache) |
| `GET /api/regime/config` | the regime axes + states + the full **V1 regime-multiplier matrix** + posture (the frontend mirrors this) |
| `GET /api/daily?terminal=&inventory=&market=&capacity=&credit=&window=` | the **nine ranked panels** for one terminal under a regime (Blueprint C) |
| `POST /api/daily/persist` | recompute every terminal under a regime and write the `daily_recommendations` table (§14) |
| `GET /api/daily/recommendations?run_date=&terminal=` | read back the persisted §14 worklist |
| `GET /api/scorecards?terminal=&<regime axes>&window=` | per-customer **scorecards** with the regime-adjusted score + the **flip-side** line (Blueprint E) |
| `GET /api/playbook?terminal=&window=` | the **Sales Playbook**: per-archetype plays + regime cheat-sheets + morning routine (Blueprint G) |
| `GET /api/demand/cockpit?terminal=&product=&window=&service_level=&lead_time_days=&lot_size=` | the **Demand Cockpit**: per-customer→terminal P10/P50/P90 forecast band, days-of-cover + burn-down (gated on inventory), the recommended buy action at a service level, and the accuracy strip |
| `POST /api/demand/persist` | recompute & write the per-customer + terminal forecast distributions (`demand_forecast_customer`/`demand_forecast_terminal`) — the P6/P7/P10 read contract |
| `GET /api/demand/forecasts?terminal=&product=&level=&window=` | read back the persisted forecast distributions (terminal or customer level) |
| `GET /api/demand/config` | the demand-cockpit config (horizon, service-level / lead-time defaults, band weights) |

Interactive docs at `http://localhost:8000/docs`.

---

## Daily operating layer — regime re-ranking · nine panels · scorecards · playbook

On top of the standing scores sits the **operating layer** people live in day-to-day:

- **`backend/app/regime_config.py`** — the **V1 regime-multiplier matrix**. A *regime* is the
  day's operating context on four axes: **inventory** (long/balanced/tight/tank-constrained),
  **market** (rising/falling/flat/volatile), **capacity** (ample/normal/constrained), **credit**
  (easy/normal/tight). `REGIME_MULTIPLIER[archetype][axis][state]` (neutral 1.0) feeds
  `regime_score = clamp(base_value · Π_axis multiplier, 0, 100)`. Every multiplier is a config
  number (mirrors `scoring_config`). The frontend re-implements the same math in `lib/regime.ts`'s
  spirit via the matrix it fetches, but live re-ranking calls the backend.
- **`backend/app/regime.py`** — builds the **nine ranked, actionable panels** per terminal
  (Today's Actions · Customer Rankings · Inventory Actions · Pricing Opportunities · Credit Alerts
  · Churn Alerts · Contract Candidates · Discount Opportunities · Strategic Accounts). Every row
  carries an **action**, a one-line **why-now**, and an **expected impact**. `persist_daily` writes
  the `daily_recommendations` table (§14: `run_date · terminal · regime · panel · rank · customer ·
  action · why_now · expected_impact · base_value · regime_score`). `scorecards` returns one-page
  per-customer cards including the **flip side** (how score + action change under the *opposite*
  inventory/market regime, via `regime_config.opposite_regime`).
- **`backend/app/playbook.py`** — one source of truth for the **Sales Playbook** (Blueprint G):
  per-archetype plays (what to say / when to call / what to quote / what terms / what NOT to do),
  regime cheat-sheets, and the morning routine. Powers both `GET /api/playbook` and the generated
  `docs/playbook.md` (`uv run rackiq-export-playbook`).
- **`backend/app/api/daily.py`** — the endpoints above. `daily_recommendations` is a derived cache
  (created by `regime.ensure_tables`, like `customer_scores`), not a canonical table.

---

## Demand Cockpit — the per-terminal operating forecast

`backend/app/demand.py` is the per-terminal demand-planning view. For one `terminal × product`
(product `(all)` aggregates a terminal's whole book):

- **Per-customer forecast → terminal band.** Each customer's weekly series (Mon-start buckets over
  the active span; a trailing **partial week is dropped** so it can't drag the model) is forecast by
  **per-account model selection**: the lowest-backtest-error of **Holt-Winters seasonal** (≥2 weekly
  cycles), **Holt's linear trend** (`holt_linear`), **seasonal-naive**, or **flat** — so a ratable
  gasoline account lands on Holt while a weather-driven distillate lands on seasonal-naive, *by
  skill*. A reliability **shrinkage** blends the path toward the recent run-rate (trusting the model
  less when its backtest error is high) to curb thin-series overforecasting. Forecasts are summed to
  the terminal **P50**; the **P10/P90 band** is *derived from historical one-step forecast error*
  and is **VAR-weighted** — erratic (low-VAR) accounts widen the band via `1 + λ·(1 − VAR/100)`,
  combined as `σ_terminal = √(Σ σ_i²)` and grown ∝√h (plateauing after `sigma_growth_cap_periods`).
- **Days of cover + burn-down** (capability-gated on `inventory_days_of_supply`): the latest book
  inventory / `tank_capacity` / `min_heel` give **days-of-cover** = on-hand-above-heel ÷ near-term
  daily P50, and a daily **burn-down** projecting inventory at the P50 rate with a fast(P90)/slow(P10)
  cone vs. the heel & capacity lines (the fast-path heel crossing is the conservative reorder day).
- **Recommended action** = a plain-English **order-up-to** plan at a chosen **service level** (z via
  `scipy.stats.norm.ppf`): reorder point `s = μ_d·L + z·σ_d·√L`, order-up-to `S = μ_d·(L+R) + z·σ_d·√(L+R)`
  (above heel), → "**buy ~X gal by &lt;date&gt; to hold a 95% service level**", rounded to **lot size**
  and capped at tank ullage. With **no supply constraints** it degrades to a **target carry** and
  notes the gap. Service level / lead time / lot size are live inputs; the heavy forecast is cached
  per scope so only this cheap step re-runs.
- **Accuracy strip** = recent **MAPE / bias** from a terminal-level one-step backtest, with the
  selected model vs. naive-last / seasonal-naive baselines.
- **Persistence (the P6/P7/P10 contract).** `persist` writes the **per-customer** and **terminal**
  forecast distributions to `demand_forecast_customer` / `demand_forecast_terminal` for every
  `terminal × product` (+ the all-products rollup) so downstream phases (P6 allocation, P7 pricing,
  P10 S&OP) read one canonical forecast. Like `customer_scores` / `daily_recommendations` these are
  derived caches created by `demand.ensure_tables` (NOT `init_db`), so they **survive** demo reload /
  reset. Every horizon / weight / planning constant lives in `demand.DemandConfig`.

---

## Frontend

Vite + React 19 + TypeScript + **Tailwind v4 (CSS-first)** + Recharts. A **left-nav dashboard
shell** (`App.tsx`) switches between modules via a tiny dependency-free hash router
(`lib/useHashRoute.ts`), grouped into **Operate** / **Analyze** / **Data**. The app **HOME** is
the Daily Operating Dashboard.

**Operate**
- **Daily Operating Dashboard** (`pages/DailyOps.tsx`, route `""`/home) — Blueprint C. One view per
  terminal, the **nine ranked panels** (lists, not charts), and the **regime selector**
  (`components/RegimeSelector.tsx`) that re-ranks everything live by calling `/api/daily`. A
  "Persist worklist (§14)" button writes `daily_recommendations`. Rows deep-link to the scorecard.
- **Demand Cockpit** (`pages/DemandCockpit.tsx`, route `demand`) — the per-terminal operating
  forecast. Terminal / product / window selectors; a **P10/P50/P90 forecast-band chart**
  (`components/demand/DemandForecastChart.tsx`, history → forecast with a boundary line); an
  **inventory burn-down** vs. heel/capacity (`components/demand/BurnDownChart.tsx`, greyed with a
  gap-note when inventory is absent); a **days-of-cover** stat; a **Recommended Action** panel with a
  **service-level slider** + lead-time / lot-size inputs (re-derives the buy-by date live); and a
  **forecast-accuracy strip** (MAPE / bias vs. baselines). A "Persist (P6/P7/P10)" button writes the
  forecast distributions.
- **Scorecards** (`pages/Scorecards.tsx`, routes `scorecards` / `scorecard/{id}`) — Blueprint E.
  One-page per-customer cards: sub-scores, Base Value, today's Regime-Adjusted Score (+ per-axis
  multiplier breakdown), archetype(s), why-now, recommended action, posture, expected impact, and
  the **flip-side** panel. An exemplar gallery covers every archetype present.
- **Sales Playbook** (`pages/Playbook.tsx`) — Blueprint G. The morning routine, regime cheat-sheets,
  and per-archetype plays (toggle to only archetypes in the current book).

**Analyze**
- **Book Overview** (`pages/BookOverview.tsx`) — the sortable/filterable customer table (VAR, Base
  Value, archetypes, volume, trend arrow, margin & Account Value greyed when unavailable, recency
  gap, churn flag, credit/quadrant — credit greyed until **P9**). Filter by terminal/product/grade/
  archetype. Row → drill-down with the **base-range chart**, in-band rate, base volume & cadence,
  recency, and an auto-generated **scouting note**.
- **Early-Warning Radar** (`pages/Radar.tsx`) — a ranked worklist: **Overdue** (recency > 1.5×
  cadence), **Fading** (volume trend ≤ −12%), **Erratic** (VAR dropped ≥ 8 vs all-time, 90-day vs
  all-time). Shows why each is flagged, sorts by **volume-at-risk**, and **exports CSV**.
- **Scores & Quadrant** (`pages/Scores.tsx`) — the original ranked table + quadrant + drill-down.
- **Capabilities** (`pages/Dashboard.tsx`) — the live
  **capability-matrix grid** (enabled = green with coverage bar; disabled = grey with the missing
  fields; *feed* features show an indigo "collecting — N logged" pill), a monthly-volume bar chart,
  a market-price line chart, and a top-customers table (margin/DSO columns appear only when
  enabled). With no data it shows an empty state that points to Data Studio. (The **Scores &
  Quadrant** page above renders the window selector + recompute, the metric-availability strip, the
  Explainability × Profitability scatter (`components/scores/QuadrantScatter.tsx`), and the
  base-range drill-down — `components/scores/BaseRangeChart.tsx`.)
- **Reconciliation** (`pages/Reconciliation.tsx`) — the P8 loss-control screen: network KPIs (net &
  gross loss, $ loss & recoverable/yr, tanks out of control), the **loss-mechanism split** bar
  (`components/reconciliation/MechanismBar.tsx`), a ranked **worst-offenders** table, a **meter-drift**
  control-chart list, a per-tank drill-down with the **control chart**
  (`components/reconciliation/ControlChart.tsx`), the **loss-tracking** trend
  (`components/reconciliation/LossTrendChart.tsx`), the **net-recon cross-check** table, and the
  **receipt measurement basis** (VEF / shrink). A clear lock + "Feed me &lt;field&gt;" when gated.

**Data**
- **Data Studio** (`pages/DataStudio.tsx`) — the upload → map → **clean** → validate → commit
  wizard with its live "Feed me &lt;field&gt;" capability panel (see **Data Studio** above).
- **Data Health** (`pages/DataHealth.tsx`) — the standing quality score + drift alerts + quarantine
  review + crosswalk browser + audit log. The nav shows a quarantine-count badge when rows are held.

Shared score UI (pills, grade tones, bars, archetype tags, trend arrows) lives in
`lib/scoreui.tsx`. `App.tsx` owns `summary` + `capabilities`; Data Studio returns fresh copies on
every write so the sidebar badge and panels update without a reload.

Tailwind v4 is wired via `@tailwindcss/vite`; `src/index.css` is just `@import "tailwindcss";`
— there is intentionally **no** `tailwind.config.js` or `postcss.config.js`.

---

## Run it

Prereqs: Python ≥ 3.11, `uv`, Node ≥ 20, `npm`.

### Backend
```bash
cd backend
uv sync                                   # install deps into .venv
uv run rackiq-serve                       # FastAPI on http://localhost:8000
# First run boots EMPTY — feed it from Data Studio (upload or "Load demo data").
# Optional: pre-seed the book from the CLI before serving:
uv run rackiq-generate --seed 42 --profile full
uv run rackiq-export-samples              # write sample CSV/XLSX (+ dirty demo files) into ../samples/
uv run rackiq-export-playbook             # (re)generate ../docs/playbook.md from the archetype plays
uv run pytest                             # run the test suite (units + e2e API flow)
# rackiq-info  -> print row counts + enabled capability count
```

### Frontend
```bash
cd frontend
npm install
npm run dev                               # http://localhost:5173 (proxies /api → :8000)
# npm run build  -> type-check + production build into dist/
```

Open **http://localhost:5173**. Either click **Data Studio → Load demo data** (`core`/`lite`/
`full`) or upload `samples/*.csv` and map the columns, and watch the capability grid flex.

---

## Project layout

```
backend/
  pyproject.toml            # uv project; scripts + [dependency-groups] dev (pytest, httpx) + pytest cfg
  app/
    schema.py               # ★ canonical field registry + DDL + import targets + hygiene metadata
    db.py                   # DuckDB lifecycle, shared r/w connection + lock, studio + crosswalk/audit/quarantine tables
                            #   (scoring caches customer_scores/customer_lane are managed in scoring.ensure_tables)
    capabilities.py         # ★ FEATURES registry + runtime matrix (incl. "feed" collecting state)
    scoring.py              # ★ scoring engine: VAR lane, sub-scores, base value, archetypes, backtest
    scoring_config.py       # ★ ScoringConfig — every weight/threshold/window as a parameter
    reconciliation.py       # ★ P8 loss-control engine: book vs physical, BOL-grouped disbursements,
                            #   net-recon cross-check, mechanism split, meter-drift control charts, $loss
    reconciliation_config.py# ★ ReconConfig — control limits / thresholds / period grain as parameters
    regime.py               # ★ daily operating engine: regime re-rank + the nine ranked panels + scorecards
    regime_config.py        # ★ the V1 regime-multiplier matrix (axes/states/multipliers — every value a param)
    playbook.py             # ★ Sales Playbook source (archetype plays + regime cheat-sheets + routine) + md render
    demand.py               # ★ Demand Cockpit: per-customer HW/seasonal-naive forecast → terminal P10/P50/P90 band, days-of-cover/burn-down, order-up-to action, persisted distributions (DemandConfig)
    generator.py            # parameterized Soundview synthetic data + profiles (+ BOL/seeded losses)
    ingest.py               # Data Studio: parse, fuzzy mapping (BOL/EDI aliases, 2-tier threshold), inspect (+profiling), validate, coerce (mixed + Excel-serial dates)
    profiling.py            # data-quality scorecard (type/null/distinct/min-max/outliers/flags + score)
    crosswalk.py            # ★ Customer Master crosswalk — fuzzy merge groups, confirm/reject, apply
    validation.py           # rule engine: required-only gating (+ EDI-control-row junk), negatives-as-corrections, drill-down + quarantine index
    hygiene.py              # configurable cleaning pipeline (HygieneOptions, apply_fixes, group_by_bol, ASTM D1250 vcf)
    data_health.py          # standing quality score + drift alerts + quarantine/crosswalk/audit summary
    cli.py                  # rackiq-generate / -serve / -info / -export-samples (+dirty) / -export-playbook
    config.py               # settings (db path, CORS, host/port)
    main.py                 # FastAPI app factory (routes + studio + scores + reconciliation + daily + demand routers)
    api/{routes,queries}.py # read endpoints + SQL
    api/studio.py           # /api/studio/* inspect / crosswalk / validate / commit / quarantine / data-health / feeds
    api/scores.py           # /api/scores/* ranked / customer drill-down / quadrant / backtest / config / recompute
    api/reconciliation.py   # /api/reconciliation/* loss-control payload / config / recompute (cached)
    api/daily.py            # /api/daily, /api/regime/config, /api/scorecards, /api/playbook (Blueprints C/E/G)
    api/demand.py           # /api/demand/cockpit / persist / forecasts / config (the Demand Cockpit)
  tests/                    # pytest: test_hygiene_studio + test_studio_api + test_data_studio_robustness + test_bol_ingest + test_early_feeds + test_scoring + test_regime + test_reconciliation + test_demand
  data/rackiq.duckdb        # runtime store, gitignored (regenerable / re-feedable)
samples/                    # sample CSV/XLSX incl. lifts_dirty.csv / lifts_barrels.csv (rackiq-export-samples)
docs/hygiene-studio/        # worked screenshots of the merge + fix flow and Data Health page
docs/playbook.md            # generated Sales Playbook (rackiq-export-playbook)
frontend/
  vite.config.ts            # react + tailwindcss plugins; /api dev proxy
  src/
    App.tsx, main.tsx, index.css       # App.tsx = the left-nav dashboard shell (Operate/Analyze/Data)
    lib/{useHashRoute,format}.ts, lib/scoreui.tsx
    api/{client,types}.ts
    pages/{DailyOps,DemandCockpit,Scorecards,Playbook,BookOverview,Radar,Scores,Reconciliation,Dashboard,DataStudio,DataHealth}.tsx
    components/{ConnectionBanner,ProfileBadge,CapabilityGrid,VolumeChart,MarketPriceChart,Panel,DataCapabilityPanel,RegimeSelector}.tsx
    components/scores/{BaseRangeChart,QuadrantScatter}.tsx
    components/demand/{DemandForecastChart,BurnDownChart}.tsx
    components/reconciliation/{MechanismBar,ControlChart,LossTrendChart}.tsx
    components/studio/{Stepper,UploadStep,MappingStep,CleanStep,ProfilingScorecard,CustomerMasterPanel,FixOptions,ValidateStep,DoneStep,QuickFeeds}.tsx
CLAUDE.md
```

## Notes & gotchas
- **numpy < 2.5** on Python 3.11 (2.5 requires 3.12); pinned in `pyproject.toml`. The scoring
  engine adds **statsmodels** (STL) + **scipy** (rank percentiles) — installed by `uv sync`.
- **Scoring is capability-gated end-to-end**: each metric returns `available + reason`; gated
  sub-scores (margin, elasticity, EVR, market, quotes, credit) report `available:false` on a thin
  book and the UI greys them out. `customer_scores`/`customer_lane` are *derived caches* recomputed
  from canonical data (live-computed on read with a data-signature cache; persisted by `recompute`).
- **Regime re-ranking is config-driven**: `regime_config.REGIME_MULTIPLIER[archetype][axis][state]`
  (neutral 1.0) → `regime_score = clamp(base_value · Π multipliers, 0, 100)`. The matrix is exposed at
  `/api/regime/config` so the selector re-ranks live (the backend recomputes via the scoring cache).
  The scorecard **flip side** uses `opposite_regime` (inverts inventory + market). `daily_recommendations`
  is a derived cache created by `regime.ensure_tables` (NOT in `init_db`), so it survives like
  `customer_scores`; `persist_daily` rewrites the current `run_date`.
- **Demand Cockpit forecasting** is at a **weekly** terminal grain (so per-customer forecasts align
  to sum); a trailing **partial week is dropped** before modeling. Method tiering counts **active
  (non-zero) weeks** — zero-padding a sparse account's span must not promote it to a trend model;
  the model is **picked per account by backtest** (Holt-Winters seasonal/linear vs. seasonal-naive
  vs. flat). The P10/P90 band comes from the **historical one-step error** (not a model's own CI) and
  is **VAR-weighted**. `demand_forecast_customer`/`demand_forecast_terminal` are derived caches created
  by `demand.ensure_tables` (NOT `init_db`), so they **survive reset/demo** like `customer_scores`;
  the heavy forecast is cached per `(data-sig, terminal, product, window)` so the service-level slider
  re-runs only the cheap order-up-to action. Days-of-cover / burn-down / buy-by-date are gated on the
  `inventory_days_of_supply` capability (else a target carry + gap note).
- **`window` is a reserved word in DuckDB** (window functions) — the scoring tables use
  `score_window` for the column (the JSON/API still exposes `window`), like the `at`→`ts` rule.
- The **VARIABILITY** "VAR" score (steadiness, 0–100) is deliberately distinct from any financial
  **VaR** — they are never conflated in code or UI.
- **Reconciliation loss sign:** positive = product *missing* (`opening + receipts − disbursements −
  closing`). **Disbursements are grouped by `bol_number`** (sum compartments) — never a standalone
  compartment row. The **net basis removes temperature** (it nets out under VCF), so `net loss =
  measurement + physical`; temperature is reported as the gross-vs-net bridge (the three sum to the
  gross gap). The billed net is **never overwritten** — the net-recon delta is *reported*, since
  systematic billed-vs-recompute divergence is the calibration signal. Control limits come from the
  **network** routine distribution (not a tank's own series), so a drifting tank can't hide itself.
- DuckDB bulk insert casts each column to its declared schema type, so pandas
  datetime → DATE/TIMESTAMP and `NaT` → NULL are handled in `db.insert_df`.
- Coverage is measured against each field's **own** table row count.
- The live server holds the DuckDB file **read/write** (one shared connection). Don't run the
  CLI `rackiq-generate`/`rackiq-info` against the served file while it's up — use the UI's
  **Load demo / Reset**, or stop the server (or target a separate `--db` path).
- **Hygiene fixes are opt-in and ordered** (trim → drop-empty → units → defaults → net-60 →
  resolve-customers); exact-duplicate removal is lossless, grain-aware duplicate *lifts* are
  quarantined (not dropped) when that toggle is on.
- **Coercion is forgiving (use everything, quarantine little).** `ingest.coerce_column` treats
  textual missing-value tokens (`N/A`, `-`, `TBD`, Excel `#REF!`/`#VALUE!` …) as blanks → NULL,
  **not** parse errors, and recovers decorated numbers (thousands separators, `$`/`%`,
  accounting negatives `(123)`, a Unicode minus, the Excel text-number apostrophe). A *parse
  error* is only a value with real content that still won't coerce; `validate` returns per-field
  `parse_error_samples` + a `required_status` (mapped? all-null?) so the UI explains a failing
  "required present" rule instead of a bare ∅.
- **Validate counts reconcile:** `clean_rows + quarantine_count + dropped_rows == rows_after_fixes`
  (compartment-row level). `lifts_after_grouping` (≤ `clean_rows`) is the count AFTER BOL grouping,
  i.e. the lifts actually written. Failing rows are HELD (quarantined) by default; only with
  `quarantine_failures` off are they dropped — surfaced as `dropped_rows`/`dropped`, never a silent 0/0.
- **Wide BOL/EDI exports are first-class lift sources.** The only required mappings are
  `customer_id`/`lift_datetime`/`net_gallons`; `bol_number` is an optional lifts key. Rows sharing a
  BOL number are **grouped & summed** into one lift at commit (`hygiene.group_by_bol`, after the
  quarantine split). The matcher knows BOL/EDI aliases (`Consignee Number`→`customer_id`) and uses a
  stricter threshold for optional targets so a stray admin header (`Rack Driver ID`) is NOT auto-mapped
  into `unit_price`. **Negatives are reversals/corrections** — kept, tagged (`volume_corrections`),
  listed — never quarantined; they sum correctly under grouping. Only `bol_number`=0 ∧ gross=0 ∧
  net=0 (**EDI control/heartbeat** rows, often product `ZZZ`) are held as junk (`edi_control_row`).
- **Excel serial dates** (`45474`→2024-07-01) are parsed in date coercion via the 1899-12-30 epoch.
  This runs ONLY for DATE/TIMESTAMP targets, so a numeric *non-date* column (a customer number like
  `42023`, a dollar amount) is never misread as a date; date *rules* likewise run only on the date
  target column.
- **`at` is a reserved word in DuckDB** — the audit/quarantine tables use `ts` for the timestamp
  column (the JSON still exposes `at`).
- **Crosswalk resolution happens at commit** (variant ids are rewritten to master ids before the
  write), so downstream queries need no crosswalk awareness; re-importing more data auto-resolves.
- **Net-60 `auto` recomputes net from gross** wherever temp+API exist (it overwrites a provided
  net with the corrected value); quarantine **re-import uses `net_correction="off"`** so hand-fixed
  values are respected.
- The studio persistence tables (`import_profiles`, `import_log`, `customer_crosswalk`,
  `hygiene_audit`, `quarantine`) **survive reset/demo** by design; init runs idempotent
  `ALTER TABLE … ADD COLUMN IF NOT EXISTS` migrations (`import_profiles.hygiene`; `lifts.bol_number`)
  for pre-existing stores.
- Uploads are cached in-process by `upload_id`; a server restart between map/commit means
  re-uploading the file (the UI surfaces this as "upload expired").
- **Tests:** `uv run pytest` (dev group adds `pytest` + `httpx`); covers VCF, profiling, crosswalk,
  validation, the hygiene pipeline, scoring, the reconciliation engine (BOL grouping, mechanism
  split, net-recon, meter drift, dollarize), and the full API flow against a throwaway DuckDB.
