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

**26 canonical fields = 3 required + 23 optional**, organized into four canonical data
tables plus a `customers` dimension. Defined in `backend/app/schema.py`.

| Table | Grain | Fields |
|---|---|---|
| **lifts** | one lift/load event | **`customer_id`\***, **`lift_datetime`\***, **`net_gallons`\***, `terminal`, `product`, `gross_gallons`, `observed_temp`, `api_gravity`, `unit_price`, `unit_cost` |
| **inventory_snapshots** | terminal × product × tank × time | `tank_id`, `tank_capacity`, `min_heel`, `inventory_snapshot`, `physical_inventory`, `receipts` (+ keys `snapshot_datetime`, `terminal`, `product`) |
| **invoices** | one invoice (AR) | `invoice_date`, `due_date`, `paid_date` (NULL = open), `invoice_amount`, `credit_limit` (+ key `customer_id`) |
| **market_prices** | price_date × product × terminal | `market_price`, `nyh_basis`, `street_rack`, `committed_buys`, `committed_sells` |
| **customers** *(dimension)* | one customer | `customer_id`, `name`, `archetype`, `home_terminal` |

\* = required core field. `terminal`/`product` are detected for presence on **lifts** (their
primary home); their copies on inventory/market are dimensional keys.

**Derived concepts** (computed from stored columns; nothing is discarded):
net-vs-gross / VCF shrinkage ← `gross_gallons`,`net_gallons`(+`observed_temp`,`api_gravity`);
DSO & aging buckets ← invoice dates + amount; days-of-supply ← inventory + capacity + heel;
gain/loss ← `physical_inventory` vs `inventory_snapshot`; net position ← `committed_buys` − `committed_sells`.

---

## Capability matrix

`backend/app/capabilities.py` declares **17 features**. Each feature lists the canonical
fields it `requires` (and optional fields that `enhance` it). At runtime:

- A field is **present** if it has ≥1 non-null value in its primary table.
- `coverage` = non-null ÷ that table's own row count (an empty sibling table never dilutes
  another table's coverage).
- A feature is **enabled** iff all its required fields are present.

Served at **`GET /api/capabilities`**:

```jsonc
{
  "profile": "full",
  "categories": ["Demand","Margin","Receivables","Inventory","Market"],
  "fields":   { "unit_cost": {"present":true,"nonnull":6541,"applicable":6541,"coverage":1.0}, ... },
  "features": [ { "key":"margin_analysis","enabled":true,"missing_fields":[],
                  "enhanced_by":["product","terminal"],"coverage":1.0, ... } ],
  "summary":  { "enabled": 17, "total": 17 }
}
```

| Category | Features (required fields) |
|---|---|
| **Demand** | demand_ranking (customer_id, net_gallons) · lift_cadence (customer_id, lift_datetime) · archetype_detection (core 3) · demand_forecast (core 3) · product_mix (net_gallons, product) · terminal_breakdown (net_gallons, terminal) |
| **Margin** | net_vs_gross (net_gallons, gross_gallons) · margin_analysis (unit_price, unit_cost, net_gallons) · revenue (net_gallons, unit_price) |
| **Receivables** | ar_aging (invoice_date, due_date, invoice_amount) · dso (invoice_date, paid_date, invoice_amount) · credit_risk_late_payers (due_date, paid_date) |
| **Inventory** | inventory_days_of_supply (inventory_snapshot, tank_capacity, min_heel) · gain_loss_reconciliation (physical_inventory, inventory_snapshot) · tank_utilization (inventory_snapshot, tank_capacity) |
| **Market** | basis_tracking (market_price, nyh_basis) · position_committed (committed_buys, committed_sells) |

### Data profiles make the matrix flex

The generator can omit optional field groups, so you can watch capabilities turn on/off
from the **same code** on different data:

| Profile | Populated | Enabled features |
|---|---|---|
| `core` | only the 3 required fields (no inventory/invoices/market) | **4** |
| `lite` | core + `terminal` + `product` on lifts | **6** |
| `full` | every canonical field | **17** |

```
rackiq-generate --profile core   #  capabilities enabled: 4/17
rackiq-generate --profile lite   #  capabilities enabled: 6/17
rackiq-generate --profile full   #  capabilities enabled: 17/17
```

---

## Data Studio — the front door for feeding RackIQ

Data Studio is how a real book gets in: upload a CSV/Excel file, map its columns to canonical
fields, preview validation, and commit. Capabilities then flex from the fields actually present.

**Backend modules**
- `app/ingest.py` — parse (CSV/TSV/Excel), **fuzzy header matching** (curated synonyms +
  string-similarity + token overlap), per-table mapping suggestions, target-table inference,
  column inspection, type **coercion** (now with **mixed-format date** salvage — a day-first
  retry pass), and mapping **validation**. Parsed uploads are cached in-process (bounded) keyed
  by an `upload_id` so the wizard's steps don't re-transmit the file.
- `app/profiling.py` — the **data-quality scorecard**: per column type, null %, distinct count,
  min/max, sample values, outlier counts (IQR fences), and quality flags (mixed-type, high-null,
  negatives, unparsed-dates, whitespace, constant) + an overall 0–100 score.
- `app/crosswalk.py` — the **Customer Master crosswalk** (entity resolution / de-duplication):
  fuzzy-clusters customer key variants into proposed merge groups with a confidence score,
  persists confirm/reject decisions, and rewrites variant ids → master id on every commit.
- `app/validation.py` — the **rule engine**: required-present, dates-parseable, dates-in-range,
  volume-non-negative, value-bounds, duplicate-lifts, price≥cost — each with a severity, a count,
  and **drill-down rows**; rules with `action="quarantine"` feed the quarantine index.
- `app/hygiene.py` — the **configurable cleaning pipeline** (`HygieneOptions` → `apply_fixes`):
  trim, drop-empty, **unit standardization** (bbl→gal ×42), **default fill**, **ASTM D1250
  net(60°F) correction** (`vcf(api, temp, product)`), and **crosswalk resolution**. Each step
  emits a human report line and a structured audit entry. `run_pipeline(df, table)` is kept as
  the conservative lossless default.
- `app/data_health.py` — the **standing health report**: composite quality score
  (completeness · validity · consistency · resolution) + drift alerts (un-mapped/variant customer
  codes, volume out of historical pattern) + quarantine/crosswalk/audit summaries.
- `app/api/studio.py` — the `/api/studio/*` endpoints, orchestrating profile → map → fix →
  validate → **quarantine split** → write → audit → recompute capabilities on every write.

**Import targets.** A file targets exactly one canonical table; its columns map to that table's
*import targets* = structural keys (grain/foreign keys) + that table's canonical fields. Required
mappings per table (must be set to commit): lifts → `customer_id, lift_datetime, net_gallons`;
invoices → `customer_id`; inventory → `snapshot_datetime, terminal, product`; market →
`price_date, product`. Derived in `schema.import_targets(table)` from the single source of truth.

**Wizard flow** (`POST` unless noted):

| Step | Endpoint | What it does |
|---|---|---|
| Inspect | `/api/studio/inspect` (multipart) | parse + stash; return the **profiling scorecard** (columns + samples + null rates + distinct + min/max + outliers + flags + score), suggested table, per-table fuzzy suggestions, mappable targets, matched profile, crosswalk size |
| Validate | `/api/studio/validate` | apply the chosen **hygiene fixes**, then run the **rule engine**: returns `rules` (with drill-down rows), `fixes_preview`, `quarantine_count`, `clean_rows`, plus the mapping-level `can_commit` |
| Commit | `/api/studio/commit` | coerce → `apply_fixes` → run rules → **split quarantine** → write clean rows (replace/append) → derive `customers` (names from crosswalk) → log audit → recompute capabilities; returns rows written, quarantined count, hygiene report |
| Targets | `GET /api/studio/targets` | static registry powering the mapping dropdowns (+ `customer_key_column`, `defaultable_fields`) |
| Crosswalk | `POST …/crosswalk/propose`, `…/crosswalk/confirm`, `GET …/crosswalk`, `DELETE …/crosswalk/{key}`, `POST …/crosswalk/clear` | propose merge groups, persist confirm/reject, browse/edit the master crosswalk |
| Quarantine | `GET …/quarantine`, `POST …/quarantine/reimport`, `…/quarantine/discard` | review held rows, fix-and-re-import (with edits), or discard |
| Data health | `GET …/data-health` | the standing quality-score + drift report |
| Audit | `GET …/audit` | recent hygiene transformations |
| Profiles | `GET/POST /api/studio/profiles`, `DELETE …/{name}` | save/list/delete named **cleaning profiles** (mapping **+ hygiene options**); a re-uploaded file whose columns satisfy a profile auto-applies its mapping *and* its fix settings |
| History | `GET /api/studio/history` | recent imports (table, filename, rows, mode) |
| Demo / Reset | `/api/studio/load-demo`, `/api/studio/reset` | load the synthetic book (`core`/`lite`/`full`) or clear the store |

Saved profiles, the import log, the **customer crosswalk**, the **hygiene audit log**, and the
**quarantine queue** live in dedicated tables (`import_profiles`, `import_log`,
`customer_crosswalk`, `hygiene_audit`, `quarantine`) that **survive** demo regeneration / reset on
purpose — merge decisions and held rows are never lost when the book is reloaded.

**Frontend** (`pages/DataStudio.tsx` + `components/studio/*`): a **5-step** wizard — **Upload**,
**Map Columns**, **Clean** (`CleanStep` = `ProfilingScorecard` + `CustomerMasterPanel` +
`FixOptions`), **Validate** (stat cards + fixes preview + rule cards with row-level drill-down),
and **Commit** (rows written + hygiene report + quarantine link). A live **Data Capability** panel
sits alongside, unlocking features the instant data lands.

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
3. **Validation rules** (`validation.py`) — required-present, dates-parseable, dates-in-range,
   volume-non-negative, value-bounds, duplicate-lifts (customer·datetime·net), price≥cost. Each
   failure carries a **drill-down** to the offending rows.
4. **Auto-fix with approval + audit** (`hygiene.apply_fixes`) — trim whitespace, **standardize units**
   (barrels→gallons), parse mixed date formats, **fill terminal/product defaults**, resolve customers.
   Toggled per import; every transformation is written to **`hygiene_audit`**.
5. **Net (60°F) correction** — when `gross_gallons` is mapped, compute net via an **ASTM D1250-style
   VCF** (`hygiene.vcf(api, temp, product)`); modes: `auto` (D1250 where temp+API exist), `factor`
   (flat user factor), `gross` (net = gross), `off`. Gated on field availability.
6. **Quarantine + re-import** — rows failing a hard rule (missing required, negative volume, opted-in
   duplicate lifts) are diverted to the **`quarantine`** table instead of being dropped. The Data
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
inventory_snapshots}_sample.csv` (+ `lifts_sample.xlsx`) with *friendly* headers (e.g. "Customer",
"Lift Date", "Sell Price", "Posted Rack") so the fuzzy matcher has something to chew on. Importing
all four walks capabilities 9 → 12 → 14 → 17. It also writes **deliberately-dirty** Hygiene Studio
demo files: `samples/lifts_dirty.csv` (customer NAMES with spelling/ID variants of several
customers, mixed/bad dates, negative volumes, exact-duplicate rows, stray whitespace) and
`samples/lifts_barrels.csv` (volumes in barrels, for the unit-standardization toggle). `--no-dirty`
skips them. Worked screenshots of the merge + fix flow live in `docs/hygiene-studio/`.

---

## Synthetic data generator

`backend/app/generator.py` builds a realistic, deterministic-per-seed "Soundview" book:
~40 customers across 3 terminals (Linden / Providence / Albany), products RBOB / ULSD /
ULSHO, ~21 months, plus matching AR, inventory snapshots, and daily market prices.

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
recent invoices left **open** (`paid_date` NULL); inventory book rolls forward
`book − lifts + receipts`, with `physical_inventory` = book ± small gain/loss.

**Parameters:** `--seed --n-customers --months --terminals --products --profile {core,lite,full}
--end-date --db`. Regeneration drops and recreates all tables, deterministic per seed.

---

## API endpoints

All return JSON over the shared connection (`db.lock()` serializes access). Read endpoints
live in `api/routes.py`; the `/api/studio/*` write/upload endpoints are in `api/studio.py`
(see **Data Studio** above).

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | liveness + active profile |
| `GET /api/summary` | counts, terminals, products, date range, total net gallons (drives the banner) |
| `GET /api/schema` | canonical field registry joined with live coverage |
| `GET /api/capabilities` | the capability matrix (above) |
| `GET /api/customers` | per-customer rollups; `avg_margin_per_gal`/`dso_days` are `null` when those capabilities are off — the API itself honors the matrix |
| `GET /api/market-prices?product=ULSD` | market vs street-rack time series (`available:false` when absent) |
| `GET /api/monthly-volume` | monthly net gallons (needs only required fields; survives `core`) |

Interactive docs at `http://localhost:8000/docs`.

---

## Frontend

Vite + React 19 + TypeScript + **Tailwind v4 (CSS-first)** + Recharts. A top nav switches
between three pages via a tiny dependency-free hash router (`lib/useHashRoute.ts`):

- **Dashboard** (`pages/Dashboard.tsx`) — the "Connected — N customers loaded" banner, the live
  **capability-matrix grid** (enabled = green with coverage bar; disabled = grey with the missing
  fields), a monthly-volume bar chart, a market-price line chart, and a top-customers table
  (margin/DSO columns appear only when enabled). With no data it shows an empty state that points
  to Data Studio.
- **Data Studio** (`pages/DataStudio.tsx`) — the upload → map → **clean** → validate → commit
  wizard with its live "Feed me &lt;field&gt;" capability panel (see **Data Studio** above).
- **Data Health** (`pages/DataHealth.tsx`) — the standing quality score + drift alerts + quarantine
  review + crosswalk browser + audit log. The nav shows a quarantine-count badge when rows are held.

`App.tsx` owns `summary` + `capabilities`; Data Studio returns fresh copies on every write so the
header badge and panels update without a reload.

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
    capabilities.py         # ★ FEATURES registry + runtime matrix
    generator.py            # parameterized Soundview synthetic data + profiles
    ingest.py               # Data Studio: parse, fuzzy mapping, inspect (+profiling), validate, coerce
    profiling.py            # data-quality scorecard (type/null/distinct/min-max/outliers/flags + score)
    crosswalk.py            # ★ Customer Master crosswalk — fuzzy merge groups, confirm/reject, apply
    validation.py           # validation rule engine with row-level drill-down + quarantine index
    hygiene.py              # configurable cleaning pipeline (HygieneOptions, apply_fixes, ASTM D1250 vcf)
    data_health.py          # standing quality score + drift alerts + quarantine/crosswalk/audit summary
    cli.py                  # rackiq-generate / rackiq-serve / rackiq-info / rackiq-export-samples (+dirty)
    config.py               # settings (db path, CORS, host/port)
    main.py                 # FastAPI app factory (routes + studio routers)
    api/{routes,queries}.py # read endpoints + SQL
    api/studio.py           # /api/studio/* inspect / crosswalk / validate / commit / quarantine / data-health
  tests/                    # pytest: test_hygiene_studio.py (units) + test_studio_api.py (e2e flow)
  data/rackiq.duckdb        # runtime store, gitignored (regenerable / re-feedable)
samples/                    # sample CSV/XLSX incl. lifts_dirty.csv / lifts_barrels.csv (rackiq-export-samples)
docs/hygiene-studio/        # worked screenshots of the merge + fix flow and Data Health page
frontend/
  vite.config.ts            # react + tailwindcss plugins; /api dev proxy
  src/
    App.tsx, main.tsx, index.css
    lib/{useHashRoute,format}.ts
    api/{client,types}.ts
    pages/{Dashboard,DataStudio,DataHealth}.tsx
    components/{ConnectionBanner,ProfileBadge,CapabilityGrid,VolumeChart,MarketPriceChart,Panel,DataCapabilityPanel}.tsx
    components/studio/{Stepper,UploadStep,MappingStep,CleanStep,ProfilingScorecard,CustomerMasterPanel,FixOptions,ValidateStep,DoneStep}.tsx
CLAUDE.md
```

## Notes & gotchas
- **numpy < 2.5** on Python 3.11 (2.5 requires 3.12); pinned in `pyproject.toml`.
- DuckDB bulk insert casts each column to its declared schema type, so pandas
  datetime → DATE/TIMESTAMP and `NaT` → NULL are handled in `db.insert_df`.
- Coverage is measured against each field's **own** table row count.
- The live server holds the DuckDB file **read/write** (one shared connection). Don't run the
  CLI `rackiq-generate`/`rackiq-info` against the served file while it's up — use the UI's
  **Load demo / Reset**, or stop the server (or target a separate `--db` path).
- **Hygiene fixes are opt-in and ordered** (trim → drop-empty → units → defaults → net-60 →
  resolve-customers); exact-duplicate removal is lossless, grain-aware duplicate *lifts* are
  quarantined (not dropped) when that toggle is on.
- **`at` is a reserved word in DuckDB** — the audit/quarantine tables use `ts` for the timestamp
  column (the JSON still exposes `at`).
- **Crosswalk resolution happens at commit** (variant ids are rewritten to master ids before the
  write), so downstream queries need no crosswalk awareness; re-importing more data auto-resolves.
- **Net-60 `auto` recomputes net from gross** wherever temp+API exist (it overwrites a provided
  net with the corrected value); quarantine **re-import uses `net_correction="off"`** so hand-fixed
  values are respected.
- The studio persistence tables (`import_profiles`, `import_log`, `customer_crosswalk`,
  `hygiene_audit`, `quarantine`) **survive reset/demo** by design; init runs an idempotent
  `ALTER TABLE … ADD COLUMN IF NOT EXISTS hygiene` migration for pre-existing profile stores.
- Uploads are cached in-process by `upload_id`; a server restart between map/commit means
  re-uploading the file (the UI surfaces this as "upload expired").
- **Tests:** `uv run pytest` (dev group adds `pytest` + `httpx`); covers VCF, profiling, crosswalk,
  validation, the hygiene pipeline, and the full API flow against a throwaway DuckDB.
