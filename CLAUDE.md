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

`backend/app/capabilities.py` declares **22 features** (19 analysis + 3 *feed*). Each feature
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
  "summary":  { "enabled": 22, "total": 22 }
}
```

| Category | Features (required fields) |
|---|---|
| **Demand** | demand_ranking (customer_id, net_gallons) · lift_cadence (customer_id, lift_datetime) · archetype_detection (core 3) · demand_forecast (core 3) · product_mix (net_gallons, product) · terminal_breakdown (net_gallons, terminal) |
| **Margin** | net_vs_gross (net_gallons, gross_gallons) · margin_analysis (unit_price, unit_cost, net_gallons) · revenue (net_gallons, unit_price) |
| **Receivables** | ar_aging (invoice_date, due_date, invoice_amount) · dso (invoice_date, paid_date, invoice_amount) · credit_risk_late_payers (due_date, paid_date) |
| **Inventory** | inventory_days_of_supply (inventory_snapshot, tank_capacity, min_heel) · gain_loss_reconciliation (physical_inventory, inventory_snapshot) · tank_utilization (inventory_snapshot, tank_capacity) · **reconciliation** (physical_inventory, receipt_source — the P8 loss-control module; enhanced by bol_compartments) · **receipt_detail** *(feed: receipt_source; target 20 receipts)* |
| **Market** | basis_tracking (market_price, nyh_basis) · position_committed (committed_buys, committed_sells) |
| **Pricing** | **pricing_engine** (unit_price, rack_benchmark — the Sandbox + Engine, Blueprint I; enhanced by quoted_price/outcome/unit_cost) · **pricing_sandbox** *(feed: rack_benchmark; target 30 days)* · **quote_elasticity** *(feed: quoted_price + outcome; target 50 quotes)* |

### Data profiles make the matrix flex

The generator can omit optional field groups, so you can watch capabilities turn on/off
from the **same code** on different data:

| Profile | Populated | Enabled features |
|---|---|---|
| `core` | only the 3 required fields (no inventory/invoices/market/quotes/receipts) | **4** |
| `lite` | core + `terminal` + `product` on lifts | **6** |
| `full` | every canonical field (incl. rack_benchmark, quotes, receipts, bol_compartments) | **22** |

```
rackiq-generate --profile core   #  capabilities enabled: 4/22
rackiq-generate --profile lite   #  capabilities enabled: 6/22
rackiq-generate --profile full   #  capabilities enabled: 22/22
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
  persists confirm/reject decisions, and rewrites variant ids → master id on every commit. Also
  **`load_name_map`** — load a hand-built **raw BOL account name → coded (master) name** CSV as
  `status='confirmed', source='name_map'` entries that are the human source of truth and **override
  any fuzzy/auto merge** for the same key (variant_key = raw name; master_id = master_name = the
  coded name, so all raw spellings collapse into one customer **shown by the coded name**). Paired
  with **`db.reapply_crosswalk`**, which re-resolves the *already-loaded* store (lifts / invoices /
  quotes / BOLs) to master ids and rebuilds the customers dim — so a name-map upload regroups +
  renames history too (not just future imports). `db.unmapped_customers` lists raw names still
  unmapped (name == id and not a confirmed master).
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
| Name map | `POST …/crosswalk/upload-names` (multipart), `GET …/unmapped-customers` | upload a hand-built two-column **raw→coded** CSV as confirmed masters (overrides fuzzy), then **re-apply across the whole store** (regroup + rename, busts the score/forecast caches) and return the still-**unmapped** raw names. Re-uploadable to keep extending the map |
| Product map | `POST …/product-map/upload` (multipart), `GET …/unmapped-products` | upload a hand-built two-column **raw product code → standardized code** chart as confirmed entries, then **re-apply across the whole store** (restate the `product` column on lifts/inventory/market/quotes/receipts/BOLs, busts the caches) and return still-**unmapped** raw codes. The product analogue of the name map; re-uploadable |
| Quarantine | `GET …/quarantine`, `POST …/quarantine/reimport`, `…/quarantine/discard` | review held rows, fix-and-re-import (with edits), or discard |
| Data health | `GET …/data-health` | the standing quality-score + drift report |
| Audit | `GET …/audit` | recent hygiene transformations |
| Profiles | `GET/POST /api/studio/profiles`, `DELETE …/{name}` | save/list/delete named **cleaning profiles** (mapping **+ hygiene options**); a re-uploaded file whose columns satisfy a profile auto-applies its mapping *and* its fix settings |
| History | `GET /api/studio/history` | recent imports (table, filename, rows, mode) |
| Quick feeds | `/api/studio/rack-benchmark`, `/api/studio/quote` | append a daily rack benchmark / a single quote (resolved via crosswalk) through the hygiene pipeline; bumps the "collecting" counters live |
| Demo / Reset | `/api/studio/load-demo`, `/api/studio/reset` | load the synthetic book (`core`/`lite`/`full`) or clear the store |

Saved profiles, the import log, the **customer crosswalk**, the **product reference chart**, the
**hygiene audit log**, and the **quarantine queue** live in dedicated tables (`import_profiles`,
`import_log`, `customer_crosswalk`, `product_crosswalk`, `hygiene_audit`, `quarantine`) that
**survive** demo regeneration / reset on purpose — merge decisions, product standardization, and
held rows are never lost when the book is reloaded.

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
- **Part 1b — VAR transparency + statistics layer** (diagnostics; these **never change the score**,
  which is frozen). Every customer's `var` block carries: the **base volume**, **base range (±1σ)**
  and **variability range (±2σ)** as concrete numbers; the **three score components broken out**
  (in-band rate · tightness · excursion control, each with value, weight, and contribution); the
  **cadence lane** (typical days-between, timing consistency, its own CV); a **steadiness-trend
  test** (two-proportion z-test on in-band rate, recent half vs prior → improving / steady /
  deteriorating + p-value); a **plain-English read** (e.g. *"7 Oil buys about 8,400 gal every ~6
  days and stays within their usual range 82% of the time — very predictable"*) and a two-word
  `descriptor`; plus an **advanced diagnostics** bundle: **forecastability** (1 − normalized
  spectral entropy), **predictability** (one-step skill score vs naïve-last), **Mann–Kendall** trend
  test (τ + p), lane **R²** and **CV**, residual **white-noise** test (lag-1 ACF + Ljung–Box),
  a **bootstrap confidence interval** on the base volume, and Hyndman **STL trend/seasonal
  strengths**. All guarded (`_safe`) so a degenerate series never breaks scoring; every parameter
  (`var_bootstrap_iters`, `var_steadiness_delta_band`, `var_trend_sig_p`, …) lives in `ScoringConfig`.
  The ranked `/api/scores` list ships a **slim** var (the table fields); the full layer rides on
  `/api/scores/customer/{id}`.
- **Part 1c — VAR AS A FORECAST** (the core purpose — turns the lane from a description of the past
  into a forward tool; the VAR math is untouched). Four pieces, all config-driven
  (`forecast_*`, `excursion_*`, `var_trend_*`):
  1. **Per-customer forward forecast** — a **real forecasting engine** (`forecasting.py`,
     `forecast_customer`), NOT a flat run-rate. For each master customer it **backtests multiple
     candidate models walk-forward and selects the lowest-error one *for that customer***: a
     month-of-year **seasonal** model (robust to sparse history — month medians × recency scale, so
     a winter-heavy distillate account forecasts high in DJF and ~0 in summer and **never collapses
     to zero**), a Holt-Winters **seasonal** model (≥2 cycles), a Holt **trend** model, a **cadence**
     (inter-order interval) model for clockwork buyers, a **recency-weighted** average, and a **flat**
     mean — with a pure-persistence **naive-last** baseline as the bar every model must clear. The
     winner is **reliability-shrunk** toward the recent run-rate (trusting the model less when its
     backtest error is high) so the engine is never much worse than a flat average while keeping a
     good seasonal shape. The **band comes from that customer's OWN backtested forecast error**
     (steady → tight, erratic → wide), VAR-weighted and growing ∝√h. **Anchored to TODAY:** the
     7/30/90-day horizons, the forward curve, and every period label are measured from the real
     calendar date at request time (`compute_scores(..., today=)`, default `datetime.now()`), NOT the
     last data date — each future period's volume is **prorated by its overlap with `[today, today+H]`**
     so a forecast made in June covers late-June→late-July, never April. The **data-recency gap** is
     surfaced honestly (`data_through`/`forecast_anchor`/`data_lag_days`/`recency_note`, plus a
     per-customer `gap_note`) and projected across; a customer **silent past their own cadence** is
     damped + flagged `slowing` (a possible slowdown/churn). Surfaced as a plain sentence naming the
     **chosen model + its backtested accuracy** (*"…using a seasonal model (±12% typical error)"*), a
     **dotted forward continuation** of the base-range chart (`forecast_series`, now the real possibly-
     non-flat model curve with a **"Today"** marker over the gap), `model`/`mape`/`skill_vs_naive`
     fields, and honesty flags: `low_predictability` (no model beat naive — *"treat this as a rough
     guess"*) and `rough` (wide band relative to expected). **Proof:** `forecast_backtest`
     (`GET /api/scores/forecast-backtest`) compares the new engine vs the **old flat run-rate** vs
     **naive-last** per customer (out-of-sample walk-forward) and reports the aggregate improvement —
     on the demo book the engine beats both on median MAPE (~+30% vs naive, ~+39% vs old), mean/median
     MAE, and per-customer win rate (27/30 beat naive), flagging the rest honestly.
  2. **Book-level bottom-up forecast** (`aggregate_book_forecast` → `GET /api/scores/book-forecast`):
     sums every customer's projection into a total expected-demand band (variances add,
     `band = z·√Σσ²`), **filterable by terminal/product** via each customer's `tp_share` volume mix.
  3. **Excursion explanation** (`_excursions`): every lift outside the variability range tagged
     **spike / shortfall / no-show** with the **weather that period** (HDD/CDD), then a pattern note
     (*"3 of 4 lane breaks landed on cold-snap weeks — looks weather-driven, not random"*) that
     **separates predictable-looking-erratic from truly random**. Weather comes from `weather.py`
     (live NOAA/ERA5 auto-fetch, cached, seasonal-proxy fallback). The bulk list computes breaks on
     the free proxy; the per-customer detail (`scoring.customer_excursions`) re-runs with the live
     fetch for just that terminal.
  4. **VAR trend over time** (`_var_trend`): re-fits the (cheap, diagnostics-free) lane at an earlier
     as-of and compares the VAR score — **tightening** (more reliable) vs **widening** (a developing
     problem), this month-vs-prior and quarter-vs-prior — driving the home-page **movers** list and
     the **forecastability** headline (A/B-steady vs C/D-erratic share of forecast volume, with its
     quarter-over-quarter trend).
- **Part 1f — DAILY PRESENCE-AWARE BEHAVIORAL PROFILE** (`backend/app/behavioral.py`, a layer ON TOP
  of the frozen VAR lane — it never changes the score). Fixes the *"average hides the pattern"* trap:
  a steady daily buyer ("Taylor": ~39k most days) and a silent-then-spiky buyer ("Super Quality":
  0,0,60k,0,50k) can share a weekly total yet are obviously different — the naive ~22k daily average
  is meaningless for Super Quality (they never lift 22k). The core move is to **split PRESENCE from
  SIZE** at **daily** resolution over rolling calendar windows (`behavior_windows` = 7/30/90 + all,
  anchored to the last data date, clipped at first-active so a new account isn't charged for
  pre-existence):
  1. **Presence / frequency** — over the **weighted working days** (Phase-1 calendar: Sun/holidays
     excluded, Saturday a data-driven partial weight), **zeros included**: active-day rate, median gap,
     longest silent stretch, lifts/active-days per week — all in working days (a Fri→Mon gap is ~1, not 3).
  2. **Size-when-present** — over ACTIVE days only: mean · median · **mode (bucketed)** · min · max ·
     range · std · CV · P10·P50·P90 (the real load size when they buy; exception lifts included).
  3. **Naive all-days** — mean & median over the counted (working) days; the **misleading-average detector** fires when
     the all-days **median = 0 while mean > 0** (`intermittent`/`misleading_average`), with a
     `misleading_severity` (high for occasional/rare burst buyers, moderate for chunky-but-frequent).
  Each customer is classified on **FREQUENCY** (daily/frequent/occasional/rare) × **SIZE-CONSISTENCY**
  (tight/variable/erratic), with a **timing-regularity** tiebreaker (gap CV) splitting predictable
  bursts from sporadic ones → a plain **label**: *Steady Daily · Steady Frequent · Steady Intermittent
  · Erratic Frequent · Rare but Regular · Sporadic/Bursty · New/Sparse* + a plain-English **headline**
  ("…they lift 0 most days, then ~55k when they buy — their 22k average is misleading") and a
  **presence-aware lane** restatement ("their lane is ~55k on the ~2 days/week they lift, not a
  22k/day average"). Each window is self-describing (its own stats + headline + daily **bars**).
  Resolves per **master** customer (ids rewritten at commit) using real ship dates. Every threshold is
  a `ScoringConfig` `behavior_*` parameter. Surfaced as `customer["behavior"]` — a **slim** copy
  (label + axes + the primary-window presence/size summary) rides on `/api/scores`; the **full** block
  (all windows + bars) rides on `/api/scores/customer/{id}`.
- **Part 2 — Layer-1 behavioral facts**: order size mean/median/CV, monthly volume, frequency,
  days-between mean & CV, margin/gal mean & CV, days-since-last, product mix + HHI,
  rush/split/small-order/cancel rates + friction-tag count, payment terms, days-to-pay mean & CV,
  credit utilization.
- **Part 3 — Layer-2 sub-scores** (0–100, percentile-ranked across the active book unless noted):
  Volume/Timing Steadiness (= VAR lanes), Price Sensitivity (β of accept-incidence vs price−reference,
  gated on quotes/rack benchmark), **EVR** (demand model vs naive-calendar baseline — the
  useful-vs-dangerous separator), Discount Efficiency (`incremental_GP / GP_given_up`), Market
  Sensitivity (signed corr profile), Weather Sensitivity (HDD/CDD β on the seasonal proxy; live
  NOAA/ERA5 weather powers the lane-break explanations), Quote Score (accept/negotiate/latency/
  lowest-only), Churn Risk. Plus the **Variability
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

## Deal book & two-axis variability (Phase 1) — the real-book score

The standing VAR grade collapsed on the *real* book (everyone graded B/C, ~half labelled
"Sporadic/Bursty") because one number conflated two unrelated things. **`backend/app/variability.py`**
(engine, `VariabilityConfig`) replaces it with **two independent axes**, scored and displayed
separately (a combined roll-up exists only as a secondary convenience):

- **AXIS 1 — cadence consistency** (`0–100`): how predictably a customer *shows up*, over **working
  days** (reuse the calendar; zeros are data). `= 0.72·regularity + 0.28·presence`, where
  `regularity = clamp(1 − gap_cv)` (gap_cv = 1.0 is the random/Poisson reference) and `presence` is a
  gentle active-days/week term. Regularity dominates so a strictly-periodic buyer scores high at ANY
  frequency, and a frequent-but-erratic one **cannot** buy steadiness with frequency alone.
- **AXIS 2 — size consistency** (`0–100`): when they DO lift, how alike each **per-lift** load is —
  on active lifts only (never diluted by silent days). `= 100·clamp(1 − size_cv/1.0)` over the raw
  per-BOL net gallons + P10/P50/P90. **Reported raw and on its own**, so a steady-cadence /
  variable-size account reads "steady cadence, *variable loads*" — size swings are never smoothed away.
  A **weather-adjustment SEAM** (`_weather_residual_sizes`) is built for heating fuels (ULSHO/HO4):
  raw size CV now, residual-after-β·HDD when the weather layer lands. Nothing is touched for
  non-heating products.

The frequency × size **2×2** names the planning quadrant: **metronome** (plannable both ways) ·
**shows up steadily, size unpredictable** (plan presence, not size) · **infrequent but identical**
(plan the quantity, watch timing) · **sporadic / bursty** (honest low-confidence). Reuses
`behavioral.daily_profile` (presence/size split, intermittency `median-0/mean>0`), guarded by a
data-sufficiency floor. On the real Soundview book both axes **spread** (cadence std ≈ 24 vs the old
VAR's ≈ 11; size std ≈ 17) and the textbook cases separate: a daily-clockwork lifter → *metronome*, a
daily-but-lumpy account (Taylor) → *shows up steadily, size unpredictable*, a rare-but-identical
account → *infrequent but identical*. Validation gate at `GET /api/variability/validation` (`rackiq-variability`).

**The deal book ANNOTATES, never grades.** `backend/app/dealbook.py` ingests the three deal sources
into the canonical **`deals`** table (the spine P2/P3 read) via three **format-aware parsers** (no
generic mapper): **term** (`deals_summary.xlsx` "Term" — month-pivot blocked by product, col0 product
forward-filled, col1 customer, alternating volume/basis-price rows; a basis-only row is NOT read as
gallons), **forward-fixed** (`forward_fixed_price_sales.xlsx` "Active Deals" — month headers row 1,
col0 customer-or-"Approved" forward-filled, col3 deal date, col4 locked $/gal, orphan rows excluded,
REMAINING ignored), and **spot** (monthly sheets; the date column is titled "SPOT DEALS"). Grain =
master customer × product family × terminal × month, tagged `term|forward_fixed|spot`, with
committed/realized gallons, basis/locked/realized price, `commitment_type` (firm|requirements),
`volume_basis` (net|gross|unknown). Re-uploadable Data Studio **"Deals"** source with **idempotent**
upsert (scope-replace on (source, month); dedupe on the stable `deal_key` = customer×product×terminal×
month×source×deal_date). Product families via `product_family()` (ULSD/ULSHO/DYED/HO4/RD; blend
numbers like B5/B10/B20/B99 are a sub-attribute, NOT a separate family; **"GEC 10"/"GEC 20" → GEC**).

### Weather + spot/rack rebuild (heating-fuel variability)

**The 2×2 is read off the two SCORES, not the frequency class — and that was the all-spot bug.** The
timing axis used the behavioral `frequency_class` (active-day RATE over all working days, zeros
included), so on a real book where customers lift weekly/biweekly NOBODY cleared "frequent" → every
regular lifter bunched into the infrequent rows → spot. The fix: build the quadrant on
`regular_timing = cadence_consistency ≥ cadence_regular_cutoff` (60) × `consistent_size = size ≥
size_consistent_cutoff` (65) — both `VariabilityConfig` params. A perfectly regular lifter earns ~72
from the regularity term alone, so it's a metronome at ANY frequency. Quadrants →
metronome/predictable_timing/predictable_size/unpredictable → **channel** (rack/term vs spot). Don't
revert the timing axis to a frequency measure.

**Channel is set by quadrant + confidence ONLY — margin is a ranking note that NEVER moves a channel.**
`variability.channel_recommendation` reads `recommended_channel` straight off the quadrant; the
`confidence` tier (High ≥200 lifts/365 d · Medium ≥100 lifts/180 d · else Low) only flags a rec as
`provisional` (low-confidence accounts are STILL recommended, never suppressed). The Phase-2 margin
rank attaches a `margin_note` where margin and channel are in tension; `validation_readout.margin_audit`
asserts `channels_flipped_by_margin == 0`. The **current channel** comes from the deal book
(term/forward = contract, spot, mixed); the current-vs-recommended **mismatch** is the headline.

**Heating-fuel SIZE axis is measured on the HDD residual (`weather_model.py`).** For ULSHO/#2/HO4 with
a stable positive per-lift HDD→size β (its own, else the terminal pool), the size axis uses
`size − β·(HDD − HDD̄)` — kept ONLY if it lowers the size CV (no over-smoothing; gasoline never
touched). The β is anchored against **BX HO SOLD** (sign agreement) before it's trusted, and the demand
β per terminal×heating-product is reported with in-sample R² **and** out-of-sample vs weather-blind. HDD
comes from the uploaded `weather_hdd` for a terminal's OWN station (**modeled**) or the Open-Meteo/
climatology proxy (**proxy**) — **LGA is never cross-applied to Baltimore**. Forward HDD is a Normal/
5-yr baseline seam labelled `is_live:false` (swappable for a live NOAA/CPC feed). Weather **must settle
the size axis before** spot/rack reads it (the build order is load-bearing).

**The join is the whole ball game.** `backend/app/bookload.py` loads the **Account Reference Chart**
(`raw BOL account → coded master`) as the customer crosswalk, the raw **BOLs** into `lifts` (group
compartments by BOL → one lift, sum gross+net, drop 0/0/0 control rows, **Ship Date**, product→family,
consignee name → coded master by normalized match), and the deal book. The deal-book→master **bridge**
(`dealbook.bridge_candidates`) resolves deal names to BOL masters where confirmed, **proposes** fuzzy
candidates for the rest (staged in the extend-crosswalk panel — **never auto-merged**; "KW Rastall" vs
"Rastall" stay candidates), and reports the **match rate** (what share of committed volume bridges to a
real BOL customer) loudly. The commitment annotation attaches ONLY to masters that bridge; everything
else shows "no commitment data". On the real book the chart maps ~100% of BOL volume; ~60% of committed
deal volume auto-bridges, the rest staged for confirmation.

## Margin layer (Phase 2) — rank by VALUE · index-on-index margins · mark-to-market · price the gap

A layer ON TOP of the Phase-1 book that ranks the desk by **value, not volume**, marks the
**forward-fixed book to market**, and prices any **demand gap in dollars** (the helper Phase-3's hedge
calls). It **reads** the `deals` spine (term/forward/spot as deal-TYPE metadata for margin math — NOT a
scoring split) and the BOL `lifts` (the volume spine); it **never** touches the VAR score, ingestion,
inventory/position (Phase 3), or `hedging.py`, and **never imports `hedging`** (one-way dependency:
hedge → margin). Self-contained modules so it and Phase-3 run in parallel without fighting shared files.
The full written modeling decision lives in **`docs/margin/MODELING_DECISION.md`**.

- **`backend/app/pricegrid.py`** — the SELL + COST ingestion (the two operator workbooks). **Format-
  aware parsers** (no generic mapper): the wholesale grid's **Matrix** sheet (`PRODUCT+CUSTOMER` concat
  keys with no delimiter — split by the longest known product prefix; an unmatched key is **flagged**,
  never guessed), the **per-terminal sheets** (multi-row header: a weekday-number row, then a
  `Customer`+dates row — cleaner, so PREFERRED; Matrix only fills gaps), the **Benchmarks** named
  differentials (DD/RACK/GEC/ASHBY by blend), and the barge **Trips report** (per-gallon logistics legs,
  `Product Vol` barrels→gallons ×42 with an **"mb" magnitude heuristic**, and the **cargo-flat sanity
  gate**: `Estimated Trip Value ÷ gal` is trusted as an all-in flat only inside a plausible $/gal band,
  else logistics-only + a flagged gap). Three idempotent stores (`price_grid`, `landed_costs`,
  `price_differentials`) created by `ensure_tables` and surviving reset/demo like `deals`; grid customers
  resolve through the **same** `customer_crosswalk`, products via `dealbook.product_family` (blends are
  attributes, not identity). Re-uploadable & idempotent (delete-then-insert on a stable key).
- **`backend/app/margin.py`** + **`margin_config.py`** — the engine. Margin is computed **per BOL lift**
  with sell/cost sourced by a **priority chain that records provenance + confidence** (sell: deal price →
  grid → lift `unit_price`; cost: Trips running WAC → lift `unit_cost`), so it runs on BOTH the real book
  and the synthetic/sample book. Two cost views everywhere: **BOOK** (vs the running volume-weighted
  landed cost of recent barges = inventory cost basis at *t*) and **REPLACEMENT** (vs the most-recent
  landed cost). **Deal-type margins** respect index-on-index physics: **TERM** = `sell_diff − cargo_diff
  − logistics − basis` (the flat **cancels** — recoverable with NO market level; `basis` defaults 0 =
  same index and is flagged), **FORWARD** = `locked − landed`, **SPOT** = `realized − landed`. Roll-ups
  to customer / product family / terminal (¢/gal + $) **explicitly contrast the margin rank vs the volume
  rank** (a high-volume/thin-margin and a low-volume/fat-margin account are both visible — this is a
  VALUE ranking, it never alters the VAR score). **Forward MTM** marks every OPEN (future-month) locked
  deal to current replacement cost → $ exposure + underwater/thin flags. **`margin_for_gap(con,
  terminal, product, quantity)`** returns the $ margin at stake split **committed/must-serve vs spot
  upside** — the Phase-3 contract. **Coverage** reports % of lifted volume with a defensible margin vs
  flagged incomplete; a **plausibility gate** flags the "$1/gal" units/basis bug (margins read
  single-to-low-double-digit ¢/gal) instead of shipping it.
- **`backend/app/api/margin.py`** — the `/api/margin/*` endpoints + the re-uploadable price/cost Data
  Studio source. Live-computed with a data-signature cache (heavy per-lift base cached per
  `(data-sig, window, terminal)`). Self-describes availability + coverage rather than going through the
  canonical field matrix (its sell/cost stores aren't canonical fields). CLI: **`rackiq-load-prices`**
  (load the grid + Trips) and **`rackiq-margin`** (print the readout).

## Missing volume & opportunity (Phase 6) — peak ≈ wallet · winnability · three rankings

`backend/app/opportunity.py` (engine, `OpportunityConfig`) estimates, per **master customer × product
family**, the GAP between what a customer *could* pull from us and what they actually pull — the **real
modeled engine** the convergence layer's INTERIM opportunity tile (which rides the channel-mismatch
volume in `api/profile._opportunity`) will later swap to. A SCORING-side layer that **reuses, never
re-derives**: the Phase-1 two-axis **quadrant + channel** (`variability`), the heating-fuel **β·HDD
residual** (`weather_model`), and the Phase-2 **margin ¢/gal** (`margin`). Gallons are canonical (no
barrels). Master-keyed (ids already resolved at commit). Backend/API only — no frontend page.

> **PREMISE — surfaced everywhere, never presented as fact:** True demand ≈ a customer's
> weather-normalized PEAK with us ("peak ≈ wallet"). Every output is labelled **MODELED**, not measured.

- **True-demand proxy** (per customer × family). On **ACTIVE days only** (never zero-diluted — that was
  the all-spot bug), take the **top decile** of highest-volume days (floored to 2 for thin lifters so an
  ~18-lift account still yields a *guarded* read, capped for frequent ones), **weather-adjust** them with
  `weather_model.adjusted_sizes` (the re-centred β·HDD residual — a cold-snap peak doesn't overstate true
  demand; heating families `ULSHO/HO4` only, others kept raw), and average → the proxy.
- **Gap.** Actual = the normal weather-adjusted average active-day volume; gap = proxy − actual, scaled
  per active day and **annualized** (per family AND total). A typical day within `min_gap_frac` of the
  peak (0.30, or 0.38 for a consistent-size account where the spread is mostly sampling noise) is the
  **NOISE FLOOR** — below it we claim no missing volume (keeps a steady metronome from phantom upside).
- **Winnability** (the load-bearing judgment) — `0–100` = `0.5·trend_freshness + 0.5·peak_freshness`,
  splitting **shrunk** (declining **year-over-year** — seasonally fair — AND a stale peak: the wallet
  really shrank → down-weighted, *never silently suppressed*; the facet says "looks shrunk, not winnable")
  from **under-served** (steady/growing but consistently below their own weather-adjusted peak → the real
  upside). A flag + plain reason come out; **low confidence FLAGS** (provisional), never changes the call.
- **Three independent rankings:** (1) raw **gallons** (size of gap), (2) **gap × margin** (dollar value;
  margin is **ranking-only**, honors whatever basis the margin engine reports incl. lift-price fallback,
  **never flips a channel**), (3) **gap × winnability** (realistic). Each row is **tagged spot/rack** via
  the reused quadrant. Anchored to the **data date** (not `today`) so a uniformly-stale book doesn't make
  everyone look shrunk.
- **Facet-ready.** Each customer carries a `facet` that is a **drop-in superset** of the interim
  `api/profile._opportunity` shape (`available · kind · winnable_gal_per_yr · winnable_dollars_per_yr ·
  chase_channel · note · interim_note · …`) so the fan-out can pull it and the interim tile can swap data
  source without a redesign; `opportunity.facets_by_master` returns `{master_id: facet}` for that pull.
- **Validated on SYNTHETIC data** (real Excel is local-only): gut-checks **FuelExpress Retail** (steady
  metronome → *near-peak, no winnable upside*) and **Narragansett Marine Fuels** (18 lifts → *Low-
  confidence, flagged, not suppressed*). Real-book confirmation (Rastall / Super Quality / Van Varick) is
  a separate local run. CLI **`rackiq-opportunity`**; validation gate at `GET /api/opportunity/validation`.

## API endpoints

All return JSON over the shared connection (`db.lock()` serializes access). Read endpoints
live in `api/routes.py`; `/api/studio/*` write/upload endpoints in `api/studio.py`; the
`/api/scores/*` scoring endpoints in `api/scores.py`, `/api/reconciliation/*` in
`api/reconciliation.py` (both live-compute with a data-signature cache), the
**daily-operating / regime / scorecard / playbook** endpoints in `api/daily.py`, and the
**Demand Cockpit** endpoints in `api/demand.py` (heavy forecast cached per scope, the
service-level slider re-derives only the cheap action), and the **Pricing Sandbox + Engine**
endpoints in `api/pricing.py` (the per-customer base + acceptance model cached per scope; the
spread slider, customer toggles, and regime selector re-derive only the cheap parts).

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | liveness + active profile |
| `GET /api/summary` | counts, terminals, products, date range, total net gallons (drives the banner) |
| `GET /api/schema` | canonical field registry joined with live coverage |
| `GET /api/capabilities` | the capability matrix (above) |
| `GET /api/customers` | per-customer rollups; `avg_margin_per_gal`/`dso_days` are `null` when those capabilities are off — the API itself honors the matrix |
| `GET /api/market-prices?product=ULSD` | market vs street-rack time series (`available:false` when absent) |
| `GET /api/monthly-volume` | monthly net gallons (needs only required fields; survives `core`) |
| `GET /api/scores?window=all` | ranked customers (VAR+grade, base value+grade, archetype, volume, trend, **per-customer forecast incl. chosen model + backtested accuracy**, **slim daily behavioral profile** — label + frequency/size axes + primary-window presence/size summary + intermittency flag) + per-metric `availability` + the **data-recency block** (`data_through`/`forecast_anchor`/`data_lag_days`/`recency_note`) |
| `GET /api/scores/customer/{id}?window=` | drill-down: lane series for the base-range chart, the full **VAR statistics layer** (base/variability ranges, score-component breakdown, cadence lane, steadiness-trend test, plain-English read, advanced diagnostics), the full **daily presence-aware behavioral profile** (`behavior`: per-window 7/30/90/all presence + size-when-present + naive all-days stats, misleading-average flag, FREQUENCY×SIZE label + headline, presence-aware lane, and the daily **bars**), the **forward forecast** (real per-customer engine: chosen model + backtested ±MAPE, 7/30/90-day band anchored to today, dotted `forecast_series` model curve, `low_predictability`/`slowing`/`gap_note`), the **lane-break list** (excursions tagged spike/shortfall/no-show + live-weather pattern), the **VAR trend** (tightening/widening), sub-scores, base value, archetype posture |
| `GET /api/scores/book-forecast?window=&terminal=&product=` | the **bottom-up book demand forecast** (7/30/90-day expected band summed from every customer's per-customer forecast, anchored to today, filterable by terminal/product) + the **forecastability** split (A/B-steady vs C/D-erratic volume share, with the quarter-over-quarter trend) + the data-recency block |
| `POST /api/studio/crosswalk/upload-names` · `GET /api/studio/unmapped-customers` | upload the hand-built raw→coded name map (confirmed; re-applied across the store) · list raw names still unmapped |
| `POST /api/studio/product-map/upload` · `GET /api/studio/unmapped-products` | upload the hand-built raw→standardized **product** chart (confirmed; re-applied across the store) · list raw product codes still unmapped |
| `GET /api/scores/quadrant?window=` | Explainability × Profitability scatter points with archetype tags |
| `GET /api/scores/backtest` | per-customer one-step forecast error by method (naive_last / seasonal / lane_base) |
| `GET /api/scores/forecast-backtest` | **the proof** — per-customer out-of-sample walk-forward comparison of the **new forecasting engine** vs the **old flat run-rate** vs **naive-last**, with the aggregate accuracy improvement (median MAPE, mean/median MAE) and how many customers the engine beats each baseline on |
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
| `GET /api/pricing?terminal=&window=&<regime axes>` | the **Pricing Sandbox + Engine** (Blueprint I): availability/lock + acceptance-model summary + the sandbox (per-customer volume/margin response curves over the spread grid, the margin-maximizing post, price-driven vs. captive flags) + the regime-aware GP-maximizing recommendations (`available:false` + missing feeds when locked) |
| `GET /api/pricing/recommendations?terminal=&window=&<regime axes>` | per-customer GP-maximizing quote price + accept-prob + expected GP + today's ranked underpriced accounts (regime-aware; surfaced inline on each scorecard) |
| `GET /api/pricing/config` | the pricing config (spread/price grids, shadow-price schedule, acceptance-model priors) |
| `POST /api/pricing/recompute` | recompute the full payload with `{overrides, window, terminal, regime}` (busts the cache) |
| `GET /api/calendar` | the **working-day calendar** (Phase 1): the measured per-terminal + network **day-of-week rhythm**, the data-driven **Saturday weights**, the holidays in the data span, and the upcoming non-lifting (Sunday/holiday) days the model excludes |
| `GET /api/calendar/config` · `POST /api/calendar/recompute` | the calendar config (holiday country/subdiv, Saturday default/min-obs, weights) · recompute with `{overrides}` |
| `GET /api/hedging?terminal=&window=&service_level=` | the **operational demand-hedging readout** (Phase 2): per-terminal expected demand band (P10/P50/P90) over the next 3 & 5 **working** days, the FLOOR vs UPSIDE split, the **behavior-aware dynamic buffer** (statistical safety + overdue-burst coil), the risk watch-list, the morning readout, and the operational customer view (honest "inventory not connected" note when absent) |
| `GET /api/hedging/overview` · `GET /api/hedging/config` · `POST /api/hedging/recompute` | every terminal's readout (shared scoring) · the hedging config · recompute with `{overrides, window, terminal, service_level}` |
| `GET /api/position?terminal=&product=` | the **Phase-7 position & days-of-cover** readout: per terminal×product (family) **running net position** (gallons) + **mode** (gauge-anchored "verified" vs net-flow proxy, honestly labeled), **days-of-cover in WORKING days** (+ the exposed trailing window), the drawdown **trend**, the **nominate-a-barge cure** (gallons short / implied **bbl** / nominate-by date) when short, and a **facet** tile (headline · mode label · cover · plain-English sentence) per cell for the converged terminal view |
| `GET /api/position/summary` · `GET /api/position/config` · `POST /api/position/recompute` | the inbound barge-supply store counts + which inbound source is in use (barges → receipts → inventory) · the position config (cover thresholds / lookback / nominate target) · recompute with `{overrides, terminal, product}` |
| `POST /api/position/upload` (multipart) · `POST /api/position/load-samples` | re-uploadable **Trips report** (inbound barge supply; barrels→gallons **×42** once, VEF/transit gain-loss applied, idempotent on a stable key) · load any Trips report from `sample_data/deals/` (no-op on the synthetic cloud DB) |
| `GET /api/variability` | the **two-axis variability score** + the **rebuilt spot/rack channel rec** per master: **cadence consistency** + **size consistency** (separate; size weather-adjusted for heating fuels), the regularity×size **2×2** quadrant, the **recommended channel** (rack/term vs spot — set by quadrant + confidence ONLY), the **confidence tier** (High/Med/Low; low = provisional, never suppressed), the **current channel** (from the deal book) + **mismatch**, a **margin ranking note** (never moves a channel), the commitment **annotation**, `channel_summary`, `mismatches`, and per-axis distributions + coverage |
| `GET /api/variability/validation` | the **real-book validation gate**: both axis histograms + spread verdict, the **all-spot fix proof** (post-fix quadrant spread), the **four-quadrant walk** (one named account per quadrant, end-to-end), confidence distribution + a low-confidence exemplar, the **channel mismatch** headline, the **margin-never-flipped audit**, the weather raw-vs-adjusted summary, annotation sanity, coverage, and the deal-book→master bridge match rate |
| `GET /api/variability/customer/{id}` · `GET /api/variability/config` | one customer's two axes + channel rec + full behavioral drill-down (all windows + daily bars) · the variability config |
| `GET /api/weather` | the **Stage-1 weather model**: station coverage (modeled/proxy), per terminal×heating-product **HDD→demand β** + baseload + R² + **out-of-sample** vs weather-blind, the **BX HO SOLD anchor** agreement, and the **raw-vs-weather-adjusted size axis** per heating customer |
| `GET /api/weather/hdd/summary` · `POST /api/weather/hdd/upload` (multipart) · `POST /api/weather/hdd/load-samples` | re-uploadable **HDD** source (the "HDD'S" sheet → `station × day → HDD` + Normal/5yr/10yr baselines + the BX HO SOLD anchor; empirical header/axis detection; idempotent on station×day) |
| `GET /api/deals/summary` · `GET /api/deals/bridge` | deal-book row/master counts by source · the **crosswalk bridge** (mapped / fuzzy candidates / unmapped + the committed-volume match rate) |
| `POST /api/deals/upload` (multipart) · `POST /api/deals/bridge/confirm` · `POST /api/deals/load-samples` | re-uploadable **Deals** source (term/forward/spot, auto-detected, idempotent) · confirm staged bridges (never auto-merged) · load the bundled real book (chart→BOLs→deals) |
| `GET /api/margin?terminal=&window=` | the **Phase-2 margin layer** (rank by VALUE not volume): per-master **BOOK & REPLACEMENT** margin (¢/gal + $) with the **margin rank vs volume rank** contrast, by product family / terminal, the **deal-type margins** (term flat-cancel / forward locked−landed / spot realized−landed), the **forward mark-to-market**, **coverage** (% of volume with a defensible margin), a **plausibility gate** (¢/gal sanity; the "$1/gal" units bug is flagged), and a **worked one-customer example** (`available:false` + missing sell/cost when locked) |
| `GET /api/margin/customers` · `GET /api/margin/mtm` · `GET /api/margin/coverage` | the margin-ranked customer list + value-vs-volume movers · the forward-fixed **mark-to-market** on the open committed book ($ exposure, underwater/thin) · the coverage + plausibility + worked-example honesty block |
| `GET /api/margin/gap?terminal=&product=&quantity=` | the **margin-priced gap helper** — \$ margin at stake for a demand quantity, split **committed/must-serve** vs **spot upside** (the contract Phase-3's hedge calls in-process via `margin.margin_for_gap`) |
| `GET /api/margin/config` · `POST /api/margin/recompute` | the margin config (cost-basis window, units heuristics, plausibility gate, term basis assumption) · recompute with `{overrides, window, terminal}` (busts the cache) |
| `POST /api/margin/upload` (multipart) · `GET /api/margin/unmapped-customers` · `POST /api/margin/load-samples` | re-uploadable **price/cost** source (kind=prices\|trips, auto-detected, idempotent) · raw grid customer names not yet mapped to a master · load the bundled wholesale grid + Trips report |
| `GET /api/opportunity` | the **Phase-6 modeled missing-volume layer**: per master customer **total + per-product** demand gap (peak ≈ wallet, MODELED), the **winnability** score/flag + reason (shrunk vs under-served), the **spot/rack tag** (reused quadrant), **three rankings** (raw gallons · gap × margin · gap × winnability), a per-customer **facet** (drop-in for the interim opportunity tile), + the premise/margin/weather honesty blocks |
| `GET /api/opportunity/rankings` · `GET /api/opportunity/validation` | the three ranked worklists + headline summary (lighter payload) · the gut-check gate (synthetic-honest; real-book is a separate local run) |
| `GET /api/opportunity/customer/{id}` · `GET /api/opportunity/config` | one customer's full modeled gap + per-product breakdown + winnability + headline · the opportunity config (top-decile/floor, noise floor, winnability weights, trend/stale thresholds) |

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

## Pricing Sandbox + Engine (Blueprint I) — what-if · acceptance model · GP-maximizing quote

`backend/app/pricing.py` (engine) + `pricing_config.py` (**every grid/weight/threshold is a config
parameter**) turn the rack-vs-street spread into both an interactive what-if and a concrete
recommendation. **Gated on `unit_price` + `rack_benchmark`** (clear lock + the rack/quote
"collecting" counters otherwise). It **reads P3's elasticity β** (`scoring` price-sensitivity) and
**P5's per-customer forecast** (`demand`'s persisted distributions, falling back to the scoring
lane's annualized base volume). Live-computed over the shared connection with a data-signature cache
(`api/pricing.py`); the heavy per-customer base + acceptance fit are cached, so the spread slider,
customer toggles, and regime selector re-derive only the cheap parts.

Everything is computed in **contemporaneous spread space** — each lift measured against the rack
benchmark *on its own date* (`cost_rel = vol-weighted(unit_cost − rack_at_lift)`,
`current_spread = vol-weighted(unit_price − rack_at_lift)`), so margin at a posted spread `s` is
simply `s − cost_rel`. This cancels the absolute street level and its seasonal trend (distillate
rack drifts up in winter) and is robust to multi-product customers; `ref_today` (latest rack)
restates spreads as absolute quote prices for display.

- **The Sandbox** (interactive what-if). One book-wide **"our rack vs. street" spread** lever. For
  each customer, expected **volume** scales with the spread via its acceptance curve
  (`volume(s) = base_gal · clamp(P(accept|s) / P(accept|current))`) and **margin** = `volume·(s −
  cost_rel)`; summed to a **total-margin-vs-spread curve** with the **margin-maximizing post**
  marked. Each customer is flagged **price-driven** (high |β| percentile + thin margin) vs.
  **captive** (β ≈ 0). The payload returns per-customer volume/margin **curves over the grid** so the
  frontend toggles accounts in/out and re-solves the optimum client-side (book-level sensitivity).
- **The Engine** (the recommendation). A per-segment **acceptance model** fit from the quote log:
  `P(accept) = logistic(a + b·price_spread + c·customer_size + d·regime)` — a self-contained ridge
  IRLS, **per-archetype** where there's enough data (`min_quotes_segment`), else a **pooled** model,
  else an **elasticity proxy** from β (so the engine still runs on price+rack alone). The regime's
  inventory/capacity states feed the `d·regime` term. For each account it searches the price grid for
  the **GP-maximizing quote**: `argmax (price − cost) · expected_gallons · P(accept | price, regime)`,
  with the **shadow price** of the binding constraint (`shadow_price(regime)`, positive when supply /
  capacity is tight) as a **floor** — the objective is shadow-adjusted and **no discount below the
  street is ever recommended when the shadow price is positive**. Surfaced inline on each scorecard
  (recommended price · accept-probability · expected GP) and as a ranked **underpriced-accounts**
  worklist (the GP-maximizing quote sits above today's realized price — room to raise).

---

## Working-day calendar (Phase 1) — day-type model · data-driven Saturday weight

`backend/app/calendar_days.py` replaces "count every calendar day" with a real **three-day-type
model** so daily presence / cadence / gap math stops being corrupted by non-lifting days. It is
self-contained (numpy / pandas / the offline `holidays` library only — nothing from `scoring`) so the
import graph stays acyclic; behavioral / scoring / forecasting / hedging all consume it.

- **NON-LIFTING** — Sundays **and** US bank/federal holidays (via `holidays.country_holidays`,
  configurable country/subdivision, with observed shifts). **Weight 0** — excluded from the working-day
  denominator and from gap/silence counting; a customer is not "absent" on a Sunday/holiday. A real
  lift that lands on one is an **exception**: its volume is kept (size stats / `n_lifts` include it) but
  the day adds 0 to presence/gaps.
- **LOW-ACTIVITY** — Saturdays. A **partial day** whose weight is **measured per terminal from the
  data** (its real Saturday activity ÷ a full weekday's, clamped, with a min-observations fallback to
  `saturday_default_weight`). Not fully excluded (keeps real Saturday lifts), not a full day (doesn't
  make everyone look less steady).
- **FULL** — Mon–Fri non-holiday, full weight.

`WorkingCalendar` learns the rhythm per terminal (`from_lifts` / `from_connection` → `(calendar,
rhythm_report)`), exposes `weight` / `weights_for_index` / `day_type`, and counts working days in O(1)
via a lazily-built per-terminal cumulative-weight series (`cumulative_at` vectorizes the per-lift gaps;
`working_days_between` does a `(a, b]` span; `window_working_days` a `[start, end)` window;
`add_working_days` turns a working-day horizon into a real "by &lt;date&gt;"). Every knob lives in
`CalendarConfig`. The measured **day-of-week rhythm** + Saturday weights + exclusions are served at
`GET /api/calendar` (the Working-Day Calendar page renders them).

**Propagation (the corrected calendar everywhere).** `behavioral.daily_profile` computes presence /
active-day rate / cadence / longest-silence / the median-0/mean&gt;0 **intermittency** flag over
**weighted working days** (a Mon–Fri buyer who skips Sat/Sun now reads fully *Steady Daily*).
`scoring._customer_core` measures the inter-lift **gaps** (→ the cadence lane → VAR) and
**days-since-last** (→ recency_gap, churn) in working days — so a Fri→Mon gap is ~1, not 3. The VAR
**formula/weights are unchanged**; only its cadence/recency *inputs* are de-corrupted (so steady
weekday buyers earn the VAR they deserve). `forecasting.forecast_customer` counts "days silent vs
cadence" (the `slowing` damp/flag) in working days too. Each customer uses its home/dominant terminal's
learned Saturday weight (network fallback).

## Operational demand-hedging (Phase 2) — stage product against demand surprise

`backend/app/hedging.py` (engine) + inline `HedgingConfig` tell the operator each morning, **per
terminal**, how much product to stage = expected demand + a **behavior-aware buffer**, and who drives
the risk. This is **physical/operational** hedging (staging against demand surprise), NOT financial
price hedging. It is built entirely on the Phase-1 working-day calendar and reuses
`scoring.compute_scores` (the today-anchored per-customer **forecast** + the daily **behavioral**
profile + **VAR** + working-day cadence/recency). All accuracy is out-of-sample; bursty customers
contribute wide, never fake-precise. Live-computed over the shared connection; `api/hedging.py` caches
the heavy scoring per `(data-sig, window, date)` so the **service-level slider** and **terminal**
selector re-derive only the cheap aggregation.

Per terminal, over horizons in **working days** (default 3 & 5, config), anchored to today:

- **Expected demand band.** Each customer's near-term per-**working-day** rate (from its 7-day
  forecast, attributed to the terminal by its volume mix; behavioral fallback for thin/bursty accounts)
  is summed to the terminal **P50**; the **P10/P50/P90** band combines per-customer out-of-sample error
  **with correlation** (same-product and weather-linked accounts co-move — cold snaps lift distillate
  together — so the band is honestly wider than independence). The reliable **FLOOR** (steady-customer
  volume) is split from the volatile **UPSIDE**.
- **Behavior-aware dynamic buffer (the heart).** `band_buffer` = z·σ at the service level, plus a
  `coil_buffer`: for each **bursty/intermittent** customer, the share of its typical load that its
  **overdue-ness** (working days silent ÷ its working-day cadence, measured against the data date so a
  uniformly stale book doesn't make everyone look overdue) says is "coiled" and due now — a burst buyer
  past its cadence **raises** the buffer; a recently-lifted one adds ~nothing. `recommended_staging =
  P50 + band_buffer + coil_buffer`.
- **Risk concentration.** Customers ranked by contribution to demand **variability** (variance share),
  not volume — "who makes the buffer necessary"; flags any single customer whose one load could exceed
  the buffer.
- **Morning readout + operational customer view** (behavioral type · working-day cadence · working-days
  since last vs normal + overdue flag · typical load · terminal share · risk contribution). **Honesty:**
  if inventory/tank capacity isn't loaded, it advises **target** staging (not days-of-cover) and says so
  — inventory is never fabricated (it only *reads* `demand._latest_inventory`).

---

## Position & days-of-cover (Phase 7) — supply vs lifts · gauge-vs-proxy · nominate a barge

A per-terminal × per-product (family) **running net position** + **days-of-cover** engine that
reconciles **inbound barge supply** against **outbound lifts**, plus a "**nominate a barge**" cure when
cover runs short. Engine is `backend/app/hedging.py` (`compute_position` + `PositionConfig`); the
inbound **Trips report** ingestion is `backend/app/barges.py`; the API is `backend/app/api/position.py`
(`/api/position/*`). Backend/API only — no frontend page; the endpoint is shaped as a **facet-ready
summary** (each cell carries a plain-English `facet` tile) for the converged terminal view to pull.

- **INBOUND — `barges.py` (NEW format-aware Trips parser).** The Trips report is a messy `.xls` of barge
  discharges. **Trips volumes are in BARRELS** → the barrels→gallons **`×42`** conversion happens
  **exactly once** here (`nominal_gallons = volume_bbl × 42`, asserted, reported in the load audit); the
  engine reads `delivered_gallons` (already gallons) and never re-multiplies — this is the #1
  silent-error source. Per discharge: terminal · product (→ family) · discharge/ETA date · barrels
  (→ gal, reusing pricegrid's "mb" thousand-barrel heuristic) · **VEF** (vessel experience factor) +
  derived **transit gain/loss** · landed cost ¢/gal (**metadata only**). `delivered_gallons` = nominal ×
  VEF when a plausible VEF is present (`volume_basis="vef_adjusted"`), else nominal (`"nominal"`). Lands
  in the `barge_discharges` store — **idempotent** upsert on a stable `discharge_key`, **survives
  reset/demo** (created by `barges.ensure_tables`, NOT in `schema.ALL_TABLES`), **re-uploadable** via
  `POST /api/position/upload` like the deal/price sources.
- **OUTBOUND — reuse the BOL `lifts`.** Outbound = `lifts.net_gallons` (compartments already grouped by
  BOL → one lift, control rows dropped, Ship Date, master names via the crosswalk). Position grains on
  **product family** (`dealbook.product_family`) so inbound and outbound join on one product key.
- **TWO MODES, both honestly labeled.** **GAUGE-ANCHORED ("verified")** — a terminal-verified
  `physical_inventory` snapshot exists, so `position(t) = gauge_level + inbound_since − outbound_since`
  (a TRUE tank level). **NET-FLOW PROXY** — no gauge, so `position = cumulative inbound − outbound since
  start of data` — a **flow delta, NOT a tank level** (opening stock isn't in the flow), labeled as such
  everywhere; never presented as a gauge reading.
- **Inbound source priority** (so it runs on real AND synthetic data): `barge_discharges` (real Trips) →
  canonical `receipts` (net gallons) → `inventory_snapshots.receipts`. The source is reported.
- **Days-of-cover in WORKING days** (reuses the Phase-1 calendar): `position ÷ avg outbound per working
  day` over a trailing window (default 45 calendar days; the window's working-day denominator + outbound
  are exposed). A Fri→Mon gap is ~1 working day; a lift on a Sun/holiday is an exception (volume kept,
  not a working day). **Trend** = net flow per working day (building / drawing / balanced) + a
  `trending_short` projection.
- **CURE — nominate a barge.** When cover < the short floor (or trends below within the planning
  horizon), surface `gallons_short` to restore target cover and the **implied barge size in BARRELS**
  (gallons ÷ 42) + a `nominate_by` working-day date. Advisory only. The facet sentence reads e.g.
  *"≈ 3.8 working days of ULSD cover at Linden, gauge-verified — nominate ~1,647 bbl by Sat Jun 27 to
  hold 10 working days."*
- **Validation.** Proven on **SYNTHETIC** data (the real Trips `.xls` is local-only/gitignored): on the
  demo `full` book inbound = canonical `receipts`, gauge = `inventory_snapshots.physical_inventory`
  (there is no Trips file in the cloud DB). Real-book accuracy is a separate local run. Tests
  (`test_position.py`) assert `×42` runs exactly once, the net flow ties out on a hand-checked
  terminal×product (proxy = in−out; gauge = anchor + roll-forward), cover is in working days, and the
  barrel cure ties out. CLI: **`rackiq-position`** (print the readout) and **`rackiq-load-barges`** (load
  the Trips supply locally).

---

## Frontend

Vite + React 19 + TypeScript + **Tailwind v4 (CSS-first)** + Recharts. A **left-nav dashboard
shell** (`App.tsx`) switches between modules via a tiny dependency-free hash router
(`lib/useHashRoute.ts`). The nav leads with a single prominent **VAR Home** (the app's default
landing / spine); **every other module sits one click away under a de-emphasized “More / Advanced”
area** grouped into **Operate** / **Analyze** / **Data**. Nothing is removed — the focus is
presentation: the home screen serves understanding demand & VAR; secondary metrics move off it.

**Home (the spine)**
- **VAR Home — Demand Predictability** (`pages/VarHome.tsx`, route `""`, default landing) — a clean,
  scannable screen centered on the VAR score *as a forecast*. Top: a plain-language explainer (VAR =
  "how predictable each customer's buying is — high = steady & forecastable, low = erratic"), then
  the **bottom-up book demand forecast** (`BookForecastPanel`, `GET /api/scores/book-forecast`) — the
  prominent *"Across all customers, expect ~X gal in the next 30 days (range Y–Z)"* headline with
  **7/30/90-day** cards and **terminal/product filter** dropdowns — and the **forecastability
  summary** (the A/B-steady vs C/D-erratic volume split as a two-segment bar + one headline % with
  its quarter trend). A **data-recency banner** appears when the book is behind today (forecasts are
  anchored to today and projected across the gap). Next, the **behavioral map**
  (`components/scores/BehaviorMap.tsx`) — the FREQUENCY × SIZE-CONSISTENCY 2-axis grid (dots ∝ volume,
  a rose ring marks an avg-misleading burst buyer) so you instantly see who's **baseload** (↖) vs. a
  **buffer-risk burst buyer** (↘). Below sits the **VAR movers** worklist (`MoversPanel`: who tightened
  / widened most this quarter). Main: the customer list **ranked by VAR** — coded name · VAR+grade ·
  **daily-pattern label** (sortable/filterable column with a ⚠ avg-misleading flag) · **next-30d
  forecast** · **trend badge** — with a frequency filter, a misleading-avg-only toggle, and a
  **"show more columns"** toggle (cadence, archetype). Click a customer → the **plain-English read**,
  the **daily presence-aware behavioral profile** (`components/scores/BehaviorProfile.tsx`: the label
  headline + misleading-average flag + the **daily bar view** `components/scores/DailyBars.tsx`
  [presence + size — Taylor reads as a row of similar bars, Super Quality as mostly-empty with
  occasional towers] + the presence/size split + the full descriptive-stats panel with a **7/30/90/all
  window toggle**), the **forward forecast**
  card (`components/scores/ForwardProjection.tsx`, now showing the **chosen model + backtested accuracy**
  — *"seasonal model · ±12% typ. error"* — plus `low predictability` / `slowing` / `rough` badges and
  the per-customer gap note), the **base-range chart** as the hero now **continued forward as the real
  (possibly non-flat, seasonal) model curve** past a "forecast →" boundary with a **"Today"** marker
  over the recency gap (base line, ±1σ lane, ±2σ band, actual lifts) and a **legend/key**, the
  **lane-break list** (`components/scores/LaneBreaks.tsx` —
  excursions tagged spike/shortfall/no-show + the weather pattern note), a **VAR-trend badge**
  (`components/scores/VarTrendBadge.tsx`), the **score-component breakdown**
  (`components/scores/VarBreakdown.tsx`), the **cadence lane**, the **steadiness-trend** result, and a
  foldaway **Advanced statistics** panel. The **Customer Name Map** + unmapped-names panel
  (`components/studio/NameMapPanel.tsx`) sits at the bottom.

**Operate**
- **Daily Operating Dashboard** (`pages/DailyOps.tsx`, route `daily`) — Blueprint C. One view per
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
- **Pricing Sandbox** (`pages/Pricing.tsx`, route `pricing`) — Blueprint I. The rack-vs-street
  **spread slider** with a **total-margin-vs-spread curve** marking the margin-maximizing post
  (`components/pricing/MarginCurveChart.tsx`); stat cards (max-margin post, uplift, margin at the
  selected spread, elasticity mix); the **acceptance-model** panel (source, spread coefficient,
  per-segment fits); a **customer-sensitivity table** with in/out toggles and price-driven/captive
  badges that re-solves the optimum client-side; and the **engine** below — a regime selector + the
  GP summary + **today's underpriced accounts** (current → recommended price, accept-prob, +GP/yr).
  Clear lock + "Feed me unit_price / rack_benchmark" (with the rack/quote collecting pills) when gated.
- **Scorecards** (`pages/Scorecards.tsx`, routes `scorecards` / `scorecard/{id}`) — Blueprint E.
  One-page per-customer cards: sub-scores, Base Value, today's Regime-Adjusted Score (+ per-axis
  multiplier breakdown), archetype(s), why-now, recommended action, posture, expected impact, the
  **flip-side** panel, and the inline **Pricing engine** block (recommended price · accept-prob ·
  expected GP, from `/api/pricing/recommendations`). An exemplar gallery covers every archetype present.
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
uv run rackiq-load-realbook               # load the real book: Account Reference Chart → raw BOLs → deal book
                                          #   (reads backend/sample_data/deals/: account_reference_chart.xlsx,
                                          #    *bols*.csv [multi-year ok], deals_summary/forward_fixed/spot workbooks)
uv run rackiq-load-prices                 # load the Phase-2 price/cost book: wholesale sell grid + barge
                                          #   Trips landed cost (reads backend/sample_data/deals/:
                                          #   1__Wholesale_Prices___Costs_V1.xlsx + the Trips report)
uv run rackiq-margin                      # print the margin readout (coverage, plausibility, deal-type
                                          #   margins, forward mark-to-market) — ranks the book by VALUE
uv run rackiq-variability                 # print the two-axis variability + spot/rack validation readout (the real-book gate)
uv run rackiq-opportunity                 # print the Phase-6 modeled missing-volume / opportunity readout (peak ≈ wallet; gut-checks the exemplars)
uv run rackiq-load-hdd [file]             # load the HDD book (the "HDD'S" sheet) into the re-uploadable weather store
uv run rackiq-weather                     # print the Stage-1 weather readout (station coverage, HDD→demand β/OOS, anchor, raw-vs-adjusted size axis)
uv run rackiq-load-barges                 # load the barge Trips report (inbound supply) → barge_discharges (barrels→gal ×42 once, idempotent)
uv run rackiq-position                    # print the Phase-7 position / days-of-cover readout (supply vs lifts, gauge-vs-proxy, barge-cure)
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
                            #   (+ reapply_crosswalk: re-resolve the whole store to master ids; unmapped_customers)
                            #   (scoring caches customer_scores/customer_lane are managed in scoring.ensure_tables)
    capabilities.py         # ★ FEATURES registry + runtime matrix (incl. "feed" collecting state)
    scoring.py              # ★ scoring engine: VAR lane (+ transparency/statistics layer; + VAR-as-forecast:
                            #   forward forecast (→ forecasting.py), lane-break excursions+weather, book bottom-up forecast, VAR trend), sub-scores, base value, archetypes, backtest, forecast_backtest (new-vs-old-vs-naive proof)
    forecasting.py          # ★ per-customer demand forecasting engine: multi-model (seasonal/HW/trend/cadence/recency/flat) selected by walk-forward backtest, must-beat-naive + low-pred flag, reliability shrinkage, honest per-customer band, TODAY-anchored horizons (proration over the data-recency gap)
    behavioral.py           # ★ daily presence-aware behavioral profile (enriches VAR; never changes the score): split PRESENCE (all days, zeros incl.) from SIZE-WHEN-PRESENT (active days), full descriptive stats, median-0/mean>0 misleading-average flag, FREQUENCY×SIZE classifier (Steady Daily…Sporadic/Bursty) + plain read + daily bars, per master customer
    scoring_config.py       # ★ ScoringConfig — every weight/threshold/window/forecast param (model selection, backtest, shrinkage, today-anchoring) as a parameter
    calendar_days.py        # ★ working-day calendar (Phase 1): 3 day-types (Sun+US-holiday excluded · Saturday data-driven partial weight per terminal · Mon–Fri full), holidays lib, learns the per-terminal day-of-week rhythm, O(1) working-day counting (CalendarConfig)
    weather.py              # ★ HDD/CDD feed: live NOAA/ERA5 auto-fetch (no key) → weather_daily cache → seasonal proxy fallback (explains lane breaks)
    weather_hdd.py          # ★ HDD ingestion (Stage 0): the "HDD'S" sheet → weather_hdd (station×day → HDD + Normal/5yr/10yr) + hdd_demand_anchor (BX HO SOLD); empirical header/axis detection (tidy + year-matrix), idempotent upsert, HDD=max(0,65−tmean) verify
    weather_model.py        # ★ Weather model (Stage 1): station→terminal map (modeled vs proxy, never cross-applies LGA), HDD→demand β per terminal×heating-product (β/baseload/R²/OOS, sign+overfit guard), BX HO SOLD anchor, forward-HDD seam, and the SIZE-AXIS rewrite (per-lift residual after β·HDD; kept only if it lowers CV — no over-smoothing). Heating fuels only
    reconciliation.py       # ★ P8 loss-control engine: book vs physical, BOL-grouped disbursements,
                            #   net-recon cross-check, mechanism split, meter-drift control charts, $loss
    reconciliation_config.py# ★ ReconConfig — control limits / thresholds / period grain as parameters
    regime.py               # ★ daily operating engine: regime re-rank + the nine ranked panels + scorecards
    regime_config.py        # ★ the V1 regime-multiplier matrix (axes/states/multipliers — every value a param)
    playbook.py             # ★ Sales Playbook source (archetype plays + regime cheat-sheets + routine) + md render
    demand.py               # ★ Demand Cockpit: per-customer HW/seasonal-naive forecast → terminal P10/P50/P90 band, days-of-cover/burn-down, order-up-to action, persisted distributions (DemandConfig)
    pricing.py              # ★ Pricing Sandbox + Engine (Blueprint I): spread what-if (per-customer vol/margin curves via β, margin-maximizing post), acceptance model (per-segment logistic from quotes + elasticity proxy), GP-maximizing quote price with shadow-price floor
    pricing_config.py       # ★ PricingConfig — spread/price grids, shadow-price schedule, acceptance priors, elasticity-class thresholds as parameters
    hedging.py              # ★ Operational demand-hedging (Phase 2, on the working-day calendar): per-terminal expected band (P10/P50/P90 w/ customer correlation), FLOOR vs UPSIDE, behavior-aware dynamic buffer (statistical safety + overdue-burst coil), risk concentration, morning readout (HedgingConfig). ALSO Phase-7 POSITION engine (compute_position + PositionConfig): per terminal×product net position (gauge-anchored vs net-flow proxy), days-of-cover in WORKING days, drawdown trend, nominate-a-barge cure, facet-ready summary
    barges.py               # ★ Phase-7 inbound supply ingestion: format-aware Trips parser (barrels→gallons ×42 EXACTLY ONCE + asserted, mb heuristic, VEF/transit gain-loss → delivered gallons w/ labeled basis, landed cost ¢/gal metadata) → idempotent barge_discharges store (survives reset like deals/landed_costs); product→family via dealbook
    variability.py          # ★ TWO-AXIS variability + the SPOT/RACK channel rec (Stage 2): cadence consistency (working-day gap regularity) × size consistency (active-day per-lift CV; weather-adjusted for heating fuels via weather_model). The 2×2 is read off the two SCORES with regularity cutoffs (FIXES the all-spot bug: timing was frequency, not regularity) → metronome/predictable_timing/predictable_size/unpredictable → channel (rack/term vs spot, set by quadrant + confidence ONLY). Confidence tier (lift count+span; low=provisional, never suppressed); current-vs-recommended mismatch (from the deal book); margin = ranking note only (audited, never flips a channel). validation_readout = the real-book gate
    dealbook.py             # ★ Deal-book parsers + canonical deals table: product_family() normalization (blends are product not identity, GEC 10/20→GEC), three format-aware parsers (term pivot / forward-fixed Active-Deals / spot monthly), stable deal_key, crosswalk bridge_candidates + confirm_bridge (propose, never auto-merge)
    bookload.py             # ★ Repeatable real-book loaders: Account Reference Chart → crosswalk, raw BOLs → lifts (group-by-BOL, drop 0/0/0, Ship Date, product→family, consignee→master), deal book → deals (idempotent); multi-year BOL concat; load_real_book one-shot
    pricegrid.py            # ★ Phase-2 price/cost ingestion: format-aware parsers (Matrix concat keys, per-terminal multi-row headers, Benchmarks diffs, Trips barge landed cost — barrels→gal, mb heuristic, cargo-flat sanity gate) → idempotent price_grid / landed_costs / price_differentials stores (survive reset like deals); crosswalk + product-family resolution
    margin.py               # ★ Phase-2 margin engine (reads deals+lifts+pricegrid; never imports hedging): per-lift BOOK & REPLACEMENT margin w/ sell/cost provenance, deal-type margins (term flat-cancel / forward locked−landed / spot realized−landed), value-vs-volume roll-ups, forward mark-to-market, margin_for_gap (committed vs spot — the P3 contract), coverage + plausibility gate
    margin_config.py        # ★ MarginConfig — cost-basis window, units/mb + cargo-flat heuristics, plausibility gate (¢/gal), term basis assumption, Matrix product prefixes as parameters
    opportunity.py          # ★ Phase-6 modeled missing-volume engine (OpportunityConfig; reuses variability quadrant + weather_model β·HDD residual + margin, never re-derives): per customer×family true-demand proxy (top-decile ACTIVE days, weather-adjusted; peak ≈ wallet, MODELED) → gap (noise-floored, annualized) → winnability (shrunk=down+stale-peak YoY vs under-served; low-conf flagged not suppressed) → three rankings (gallons / gap×margin / gap×winnability) + spot-rack tag + drop-in facet (facets_by_master); validation_readout gut-checks the exemplars
    generator.py            # parameterized Soundview synthetic data + profiles (+ BOL/seeded losses)
    ingest.py               # Data Studio: parse, fuzzy mapping (BOL/EDI aliases, 2-tier threshold), inspect (+profiling), validate, coerce (mixed + Excel-serial dates)
    profiling.py            # data-quality scorecard (type/null/distinct/min-max/outliers/flags + score)
    crosswalk.py            # ★ Customer Master crosswalk (fuzzy merge groups, confirm/reject, apply, load_name_map raw→coded) + Product Reference chart (load_product_map / apply_products_to_frame, raw→standardized)
    validation.py           # rule engine: required-only gating (+ EDI-control-row junk), negatives-as-corrections, drill-down + quarantine index
    hygiene.py              # configurable cleaning pipeline (HygieneOptions, apply_fixes, group_by_bol, ASTM D1250 vcf)
    data_health.py          # standing quality score + drift alerts + quarantine/crosswalk/audit summary
    cli.py                  # rackiq-generate / -serve / -info / -export-samples (+dirty) / -export-playbook / -load-realbook / -load-prices / -margin / -variability / -opportunity / -load-barges / -position
    config.py               # settings (db path, CORS, host/port)
    main.py                 # FastAPI app factory (routes + studio + scores + reconciliation + daily + demand + pricing + calendar + hedging + deals + variability + margin routers)
    api/{routes,queries}.py # read endpoints + SQL
    api/studio.py           # /api/studio/* inspect / crosswalk / validate / commit / quarantine / data-health / feeds
    api/scores.py           # /api/scores/* ranked / customer drill-down / quadrant / backtest / forecast-backtest (new-vs-old-vs-naive) / config / recompute (+ data-recency block)
    api/reconciliation.py   # /api/reconciliation/* loss-control payload / config / recompute (cached)
    api/daily.py            # /api/daily, /api/regime/config, /api/scorecards, /api/playbook (Blueprints C/E/G)
    api/demand.py           # /api/demand/cockpit / persist / forecasts / config (the Demand Cockpit)
    api/pricing.py          # /api/pricing (sandbox + recommendations) / recommendations / config / recompute (cached base)
    api/calendar.py         # /api/calendar (measured day-of-week rhythm + Saturday weights + exclusions) / config / recompute
    api/hedging.py          # /api/hedging (per-terminal staging readout) / overview / config / recompute (heavy scoring cached per data/window/day)
    api/variability.py      # /api/variability (two-axis score + spot/rack channel rec) / validation (the real-book gate) / customer / config (cached per data+deal signature)
    api/weather.py          # /api/weather/hdd/* (re-uploadable HDD source: upload/summary/load-samples) + /api/weather (Stage-1 model readout: coverage/β/OOS/anchor/axis adjustment, cached)
    api/deals.py            # /api/deals/* deal-book Data Studio source (upload/idempotent) + crosswalk bridge (summary / bridge / confirm / load-samples)
    api/margin.py           # /api/margin/* Phase-2 margin layer (value ranking, deal-type margins, forward MTM, gap helper, coverage) + the re-uploadable price/cost source (cached per data-sig/window/terminal)
    api/opportunity.py      # /api/opportunity/* Phase-6 modeled missing-volume layer (per customer×family gap, winnability, spot/rack tag, three rankings, drop-in facet) / rankings / validation / customer / config (cached per lifts+deals+prices+weather+day signature)
    api/position.py         # /api/position/* Phase-7 net position & days-of-cover (gauge-vs-proxy, working-day cover, nominate-a-barge cure, facet-ready) + summary/config/recompute + the re-uploadable Trips supply source (cached per data-sig/terminal/product)
  tests/                    # pytest: test_hygiene_studio + test_studio_api + test_data_studio_robustness + test_bol_ingest + test_early_feeds + test_scoring + test_forecasting + test_behavioral + test_calendar + test_hedging + test_name_map + test_product_map + test_regime + test_reconciliation + test_demand + test_pricing + test_dealbook + test_variability + test_pricegrid + test_margin + test_weather_hdd + test_weather_model + test_position + test_opportunity
  sample_data/deals/        # (gitignored) the operator's real book: account_reference_chart.xlsx, *bols*.csv, deal workbooks, 1__Wholesale_Prices___Costs_V1.xlsx, Trips report — read by the repeatable loaders
  data/rackiq.duckdb        # runtime store, gitignored (regenerable / re-feedable)
samples/                    # sample CSV/XLSX incl. lifts_dirty.csv / lifts_barrels.csv (rackiq-export-samples)
docs/hygiene-studio/        # worked screenshots of the merge + fix flow and Data Health page
docs/margin/                # MODELING_DECISION.md — the written Phase-2 margin discovery + model
docs/playbook.md            # generated Sales Playbook (rackiq-export-playbook)
frontend/
  vite.config.ts            # react + tailwindcss plugins; /api dev proxy
  src/
    App.tsx, main.tsx, index.css       # App.tsx = the left-nav dashboard shell (Operate/Analyze/Data)
    lib/{useHashRoute,format}.ts, lib/scoreui.tsx
    api/{client,types}.ts
    pages/{VarHome,DailyOps,DemandCockpit,Hedging,Calendar,Pricing,Scorecards,Playbook,BookOverview,Variability,Radar,Scores,Reconciliation,Dashboard,DataStudio,DataHealth}.tsx   # VarHome = the default landing (route ""); Variability = the two-axis score + deal-book bridge staging (Analyze); Hedging = Demand Hedging (Operate); Calendar = Working-Day Calendar (Data)
    components/{ConnectionBanner,ProfileBadge,CapabilityGrid,VolumeChart,MarketPriceChart,Panel,DataCapabilityPanel,RegimeSelector}.tsx
    components/scores/{BaseRangeChart,QuadrantScatter,VarBreakdown,ForwardProjection,LaneBreaks,VarTrendBadge,BehaviorMap,BehaviorProfile,DailyBars}.tsx   # BaseRangeChart draws the dotted forward projection; ForwardProjection/LaneBreaks/VarTrendBadge = VAR-as-forecast UI; BehaviorMap (2-axis freq×size map) / BehaviorProfile (presence/size split + stats panel + window toggle) / DailyBars (daily presence+size bars) = the presence-aware behavioral profile UI
    components/demand/{DemandForecastChart,BurnDownChart}.tsx
    components/pricing/{MarginCurveChart}.tsx
    components/reconciliation/{MechanismBar,ControlChart,LossTrendChart}.tsx
    components/studio/{Stepper,UploadStep,MappingStep,CleanStep,ProfilingScorecard,CustomerMasterPanel,FixOptions,ValidateStep,DoneStep,QuickFeeds,NameMapPanel}.tsx   # NameMapPanel = raw→coded upload + unmapped list
CLAUDE.md
```

## Notes & gotchas
- **numpy < 2.5** on Python 3.11 (2.5 requires 3.12); pinned in `pyproject.toml`. The scoring
  engine adds **statsmodels** (STL) + **scipy** (rank percentiles) + the **holidays** library (the
  working-day calendar — offline/algorithmic, no network) — all installed by `uv sync`.
- **The working-day calendar (`calendar_days.py`) corrects the cadence/recency INPUTS to VAR — the
  VAR formula stays frozen.** Daily presence / cadence / gaps / intermittency (behavioral) and the
  inter-lift gaps → cadence lane + days-since → recency_gap/churn (scoring) are now counted in
  **weighted working days** (Sun/holiday weight 0, Saturday a data-driven per-terminal partial weight,
  Mon–Fri full). This shifts published VAR numbers (steady Mon–Fri buyers rise) but the formula/weights
  are unchanged. The Saturday weight is **measured per terminal from the data** (min-obs fallback to the
  default); a real lift on a Sun/holiday is an **exception** (volume kept, presence/gaps unaffected).
  `working_days_between`/`cumulative_at` are O(1) via a per-terminal cumulative-weight cache, so the gap
  math stays vectorized (no per-pair Python loop). Demonstrable best on real Mon–Fri operating data;
  on the uniform synthetic demo book the measured Saturday weight is ~1.0 (honest).
- **Operational hedging (`hedging.py`) is physical, not financial.** It stages product against demand
  surprise (expected demand + a behavior-aware buffer), reusing the scoring forecast/behavior/VAR over
  the working-day calendar. Overdue-ness is measured against the **data date** (a uniformly stale book
  doesn't make everyone look overdue); the **coil buffer** only fires for **bursty/intermittent**
  accounts past their working-day cadence. It never fabricates inventory — absent supply data ⇒ TARGET
  staging + a note. The heavy scoring is cached per `(data-sig, window, day)` so the service-level
  slider/terminal selector stay snappy.
- **Position / days-of-cover (Phase 7, also `hedging.py`) — UNITS ARE THE WHOLE GAME.** Gallons are
  canonical everywhere; the barrels→gallons **×42** lives in **exactly one place** (`barges.parse_trips_supply`,
  asserted + reported), and `compute_position` reads `delivered_gallons` (already gallons) — it never
  re-multiplies. The barge **nomination** is the only place gallons go back to barrels (÷42). Two modes
  are **honestly labeled and never conflated**: **gauge-anchored** (a verified `physical_inventory`
  snapshot → a true tank level) vs **net-flow proxy** (cumulative inbound − outbound since start = a flow
  delta, NOT a tank level; can be negative because opening stock isn't in the flow). Days-of-cover is in
  **WORKING days** (the Phase-1 calendar), never calendar days. Inbound source priority is `barge_discharges`
  → `receipts` → `inventory_snapshots.receipts` (so it runs on the real Trips book AND the synthetic
  demo). VALIDATED ON SYNTHETIC DATA ONLY (no Trips `.xls` in the cloud DB) — real-book accuracy is a
  separate local run. `barge_discharges` survives reset/demo (outside `schema.ALL_TABLES`) like
  `deals`/`landed_costs`. This is **physical position/cover**, distinct from Phase-2 financial margin and
  from the Phase-2 operational *staging* buffer above — it reconciles a tank ledger, it doesn't score.
- **Customer identity vs. display.** The Consignee **Number** stays the internal stable key /
  crosswalk variant key; the UI **always shows the coded (master) Name**, falling back to the raw
  name only when unmapped (never a bare number). The hand-built **name map** (`raw → coded`) is the
  source of truth — `status='confirmed', source='name_map'`, **overriding fuzzy merges** — and is
  **re-uploadable** to keep extending. Uploading it runs `db.reapply_crosswalk`, which rewrites
  `customer_id` across lifts/invoices/quotes/BOLs to the master id and rebuilds the customers dim, so
  **all raw spellings of one customer roll up into one master** and VAR/forecasts/charts recompute on
  it. The upload bumps `last_import_at`, busting the scores/demand/pricing/reconciliation caches.
  "Unmapped" = a customer whose `name == customer_id` and is not a confirmed master.
- **The Phase-2 margin layer is value accounting, NOT the elasticity Pricing Sandbox — keep them
  distinct.** `pricing.py` (Blueprint I) is the forward-looking what-if/acceptance-model engine in
  *contemporaneous spread space* against the rack benchmark; `margin.py` is the *realized & committed
  margin valuation* against actual **landed cost** (Trips) and **sell** (the wholesale grid / deal
  prices). They never share state. Margin ranks the book by VALUE — it **never alters the VAR score**
  (a customer can be stable AND thin-margin, or variable AND fat-margin; both are shown). **Units
  discipline is the whole game:** per-gallon cost legs are already `$/gal`, so the running cost basis is
  unit-robust; the `Estimated Trip Value` all-in cargo flat is trusted ONLY inside a $/gal sanity band
  (else logistics-only + a flagged cargo gap, because the index flat is NOT loaded); Trips `Product Vol`
  barrels→gal ×42 with an "mb" magnitude heuristic. The **plausibility gate** flags any margin near
  $1/gal as a units/basis error instead of shipping it (rack diesel reads single-to-low-double-digit
  ¢/gal). **TERM margin is recoverable with NO market level** — when sell and cargo reference the same
  index the flat cancels (`sell_diff − cargo_diff − logistics`); `basis` defaults 0 (same-index) and is
  surfaced as an assumption, never silently absorbed. `margin_for_gap` is the one-way **hedge → margin**
  contract (margin never imports hedging), so Phase-3 can price a demand gap (committed/must-serve vs
  spot upside) without a circular import. The price/cost stores survive reset/demo like `deals`.
- **Phase-6 opportunity is a MODELED PROXY (peak ≈ wallet), surfaced as a premise, never as fact —
  and it REUSES, never re-derives.** `opportunity.py` is a one-way SCORING-side consumer of the Phase-1
  quadrant/channel (`variability` → the spot/rack tag), the heating-fuel β·HDD residual (`weather_model`
  → the peak adjustment) and the Phase-2 margin ¢/gal (`margin` → the dollar rank); none of them import
  it (acyclic). The true-demand proxy is the **top decile of ACTIVE days, weather-adjusted** — never
  zero-diluted (zero-dilution was the all-spot bug), floored to 2 for thin lifters, capped for frequent
  ones. The **noise floor** (`min_gap_frac` 0.30, 0.38 for consistent-size accounts where the peak-vs-mean
  spread is mostly sampling noise) is what keeps a steady metronome showing **no phantom upside** — the
  FuelExpress gut-check. **Winnability** splits *shrunk* (declining **year-over-year** — seasonally fair —
  AND a stale peak → down-weighted, never silently zeroed) from *under-served*; **low confidence FLAGS
  (provisional), never suppresses** — the Narragansett gut-check. **Margin is RANKING-ONLY and never flips
  the channel** (the tag is the reused quadrant's). Trend/staleness anchor to the **data date**, not
  `today`, so a uniformly-stale book doesn't make everyone look shrunk. Gallons are canonical (no barrels).
  The per-customer `facet` is a **drop-in superset of `api/profile._opportunity`** so the interim
  channel-mismatch tile swaps to this engine as a data-source change, not a redesign. **Validated on
  SYNTHETIC data only** — real-book confirmation is a separate local run.
- **The VAR statistics layer is diagnostics only — the headline score is frozen.** Components,
  base/variability ranges, cadence lane, steadiness-trend test, plain-English read, and the advanced
  diagnostics (forecastability, predictability skill, Mann–Kendall, residual white-noise, bootstrap
  base-volume CI, STL strengths) *explain* the VAR number without changing its math
  (`var_w_in_band·in_band + var_w_tightness·tightness + var_w_excursion·(1−excursion)`, blended 70/30
  with cadence). Every stat is wrapped in `_safe(...)` so a degenerate/short series returns `None`
  rather than breaking scoring; thresholds live in `ScoringConfig`.
- **VAR-as-a-forecast is a layer ON TOP of VAR — it never changes the score.** The forward forecast,
  book bottom-up forecast, excursion weather, and VAR trend all read the frozen lane; the score math
  is untouched. The **forward forecast is a real per-customer engine** (`forecasting.py`), not a flat
  run-rate: it backtests several candidate models walk-forward and **selects the lowest-error one per
  customer** (`select_model`), reliability-shrinks it toward the recent level, and bands it from that
  customer's **own** backtest error. The book band sums per-customer forecasts assuming independent
  customers (`z·√Σσ²`); terminal/product filtering uses each customer's `tp_share` volume mix. The
  **VAR trend** re-fits the lane at `as_of − {30,90}d` over a trailing `var_trend_lookback_days` window
  using the cheap diagnostics-free light path (`_customer_core(..., light=True)`, STL skipped) — it is
  **window-independent** (always from `as_of`), so it reads the same on every display window.
- **The daily behavioral profile splits PRESENCE from SIZE — and it ENRICHES VAR, never changes the
  score** (`behavioral.py`). The trap it fixes: a daily ~39k buyer and a silent-then-spiky 0/0/60k/0/50k
  buyer share a weekly total but a naive daily average (~22k) is meaningless for the second — they
  never lift that. So presence (active-day rate / gaps / silence) is computed over **all** calendar
  days (zeros are data, never skipped) while size stats are computed over **active days only**; the
  **misleading-average flag** is the literal `all-days median == 0 and mean > 0` (with a
  `misleading_severity` so a once-a-month marine parcel reads louder than an every-3-days ratable). The
  classifier's headline axes are FREQUENCY × SIZE-CONSISTENCY; **timing regularity (gap CV) only
  disambiguates the intermittent quadrant** (Steady Intermittent = predictable bursts vs Sporadic/Bursty
  = irregular). It is **window-independent of the scoring window** (it computes its OWN 7/30/90/all
  calendar windows from the full history, anchored to `as_of`, clipped at first-active so a new account
  isn't charged for pre-existence). The lane "goes presence-aware" by restating it as *active-day size ×
  frequency* (`presence_lane`), not by touching the VAR math. Every threshold is a `behavior_*`
  `ScoringConfig` param (`behavior_freq_frequent=0.30` means "frequent" = ~3+ days/week, so a twice-a-
  week burst buyer reads "occasional"). Slim copy on `/api/scores` (`behavioral.slim_behavior`), full
  block (all windows + bars) on `/api/scores/customer/{id}`.
- **Forecasts anchor to TODAY, not the last data date** (the bug this fixed). `compute_scores(...,
  today=)` defaults to `datetime.now()` (injectable for tests) and is **never before `as_of`**; the
  rolling windows still measure from `as_of` (history depth) but the 7/30/90-day horizons, the forward
  curve, and the period labels are measured from `today`. Each future period's expected volume is
  **prorated by its day-overlap with `[today, today+H]`**, so periods that fall in the data-recency gap
  (before today) are correctly excluded — a forecast made in June covers late-June→late-July, never a
  past month. The gap is surfaced (`data_through`/`forecast_anchor`/`data_lag_days`/`recency_note`, per-
  customer `gap_note`) and a customer **silent past their own cadence** is damped + flagged `slowing`
  (slowdown/churn risk), so recency is reflected, not ignored. `date.today()` is in the `/api/scores`
  cache signature so the anchor re-rolls at the day boundary. Model-class **selection** uses the full
  series (what the engine displays); the `forecast_backtest` comparison evaluates that model strictly
  **out-of-sample** (each prediction fit only on prior periods) — the honest proof it beats old+naive.
  It selects **once per customer** (not per walk-forward step) to stay fast (~5s for the demo book).
- **Plain-English reads are honest across every account shape.** `_plain_read` (the VAR read) and the
  forecast engine (`forecasting.forecast_customer`) read naturally for the awkward cases: a **one-time
  buyer** ("…has bought just once so far — too new to read a buying pattern or forecast yet"; the
  forecast is `available:false`), a **sparse/few-week** account, **erratic C/D** accounts (the forecast
  names a `low_predictability` "rough guess"), and a **silent** account ("…been quiet N days… treat
  this as a rough range"). The forecast sentence always names the **chosen model + its backtested
  accuracy** and every `rough` forecast says so in the same words ("treat this as a rough range").
  `_plural(n, word)` kills the robotic "order(s)" placeholders (1 → "order", N → "orders").
  `_var_explanation` is None-guarded so a degenerate "ok" lane formats rather than raising.
- **VAR Home is the polished, non-technical spine.** Presentation only — the math is untouched. The
  **base-range chart owns its own always-visible plain-language legend** (`BaseRangeChart`: Normal
  volume · Usual range ±1σ · Wider range ±2σ · Actual lifts · Forecast), shades the forecast region,
  and shows a friendly **empty-lane state** (not a blank chart) for thin accounts. Shared score UI
  (`lib/scoreui.tsx`) carries the plain-language glossary (`gradeWord`, `varMeaning`) and a
  dependency-free `Tip` hover (so "VAR 71 B" expands to *"Variability score 71 of 100 — steady and
  fairly predictable"*); grade/trend colours mean the same everywhere (emerald = steady/good, amber =
  caution, rose = problem). `fmtGal`/`fmtGalFull` + `format.ts` (`fmtDate`/`fmtMonthYear`) keep units
  ("gal"), dates, and rounding consistent (no false precision). Thin/insufficient accounts show a
  **"limited history"** badge, "— no rating", an honest "Not enough history yet to map their normal
  lane" instead of zero-width ranges, and the forward card badges **"rough — wide lane"** when the
  band is wide.
- **Weather (HDD/CDD) is best-effort with a deterministic fallback.** `weather.py` auto-fetches real
  degree-days from the **key-less Open-Meteo archive (ERA5 — the reanalysis behind NOAA's products)**
  per terminal lat/lon, caches them in `weather_daily`, and falls back to a **seasonal climatology
  proxy** (matching the generator's ambient curve) when offline or past the ~5-day archive horizon —
  so excursion patterns work with no network at all. A process-wide circuit breaker stops retrying
  after the first failure; the archive's recent-edge lag is excluded from the fetch trigger so a call
  never re-fetches chasing un-fillable days. The **bulk** scores list computes lane breaks on the free
  proxy (`live=False`); only the **opened customer detail** re-runs with the live fetch
  (`scoring.customer_excursions`, `live=True`) for that one terminal. Under pytest the fetch is
  disabled (proxy only) for determinism. `weather_daily` survives reset/demo (not a canonical table).
- **Pricing works in contemporaneous spread space.** The sandbox/engine measure every lift against
  the rack benchmark *on its own date* (`cost_rel`, `current_spread`), so margin at a posted spread
  `s` is `s − cost_rel` — the absolute street level and its seasonal trend cancel, and multi-product
  customers don't blend a wrong-product reference into the margin. Acceptance is `logistic(a +
  b·spread + c·size + d·regime)` fit per archetype (pooled / elasticity-proxy fallbacks); the
  **shadow price** floor (`shadow_price(regime)`, positive when supply/capacity binds) means the
  engine **never recommends a discount below the street** under a binding constraint. `pricing_engine`
  is the 22nd capability (analysis), gated on `unit_price` + `rack_benchmark`; the pricing base +
  acceptance fit are a per-scope live-compute cache (busted when demand re-persists, via
  `demand_computed_at` in the data signature).
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
  `product_crosswalk`, `hygiene_audit`, `quarantine`) **survive reset/demo** by design; init runs
  idempotent `ALTER TABLE … ADD COLUMN IF NOT EXISTS` migrations (`import_profiles.hygiene`;
  `lifts.bol_number`) for pre-existing stores.
- **Product standardization mirrors the customer name map.** A two-column **raw product code →
  standardized code** chart loads as confirmed `product_crosswalk` entries; `hygiene._resolve_products`
  rewrites the `product` column at commit (option `resolve_products`, default on) and
  `db.reapply_product_crosswalk` restates it across every product-bearing table for already-loaded
  data. Customers key on the consignee **name** (so the name map resolves) and products standardize
  via this chart — the consignee **number** is internal-only and never a label.
- Uploads are cached in-process by `upload_id`; a server restart between map/commit means
  re-uploading the file (the UI surfaces this as "upload expired").
- **Tests:** `uv run pytest` (dev group adds `pytest` + `httpx`); covers VCF, profiling, crosswalk,
  validation, the hygiene pipeline, scoring (incl. **VAR-as-forecast**: forward band,
  tighter-band-for-higher-VAR, excursion weather pattern, VAR trend, book bottom-up forecast +
  terminal filter, the `/api/scores/book-forecast` endpoint; **plus the plain-English edge cases** —
  one-time/sparse/few-week reads, `_plural` pluralization, the `rough`-forecast flag, and
  `_var_explanation` None-safety), the **forecasting engine** (`test_forecasting`: per-customer model
  selection across the book, the seasonal model NOT collapsing to zero on sparse history, the new-vs-
  old-vs-naive `forecast_backtest` measurably beating both baselines, the low-predictability + erratic
  + silent/slowing flags, and the **critical today-anchoring** — a fixed future `today` starts the
  forecast from today not the last data date and raises the recency note), the reconciliation engine
  (BOL grouping, mechanism split, net-recon, meter drift, dollarize), and the full API flow against a
  throwaway DuckDB. Weather fetch is disabled under pytest (deterministic seasonal proxy).
