# RackIQ

**Customer demand & margin intelligence for wholesale fuel terminals.**

RackIQ ingests a terminal company's lift/sales book — and, when available, AR, physical
inventory, and market prices — and surfaces demand, margin, receivables, inventory, and
market analytics. It targets a multi-terminal wholesale fuel marketer selling refined
products (RBOB / ULSD / ULSHO); **no blending operations**.

> **Core principle — capabilities flex with the data you provide.**
> One canonical schema; only `customer_id`, `lift_datetime`, and `net_gallons` are required.
> A **capability matrix** inspects which canonical fields are actually populated and
> enables/disables 21 features accordingly — exposed over the API so the UI reflects it live.

| Profile | Populated | Features enabled |
|---|---|---|
| `core` | the 3 required fields only | **4 / 21** |
| `lite` | core + `terminal` + `product` | **6 / 21** |
| `full` | every canonical field | **21 / 21** |

## Stack

- **Backend:** Python · FastAPI · DuckDB (single-file store) · pandas / numpy
- **Frontend:** Vite · React 19 · TypeScript · Tailwind v4 · Recharts

## Quickstart

```bash
# 1. Backend  (http://localhost:8000)
cd backend
uv sync
uv run rackiq-serve

# 2. Frontend (http://localhost:5173)  — in a second terminal
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. The store starts **empty** — open **Data Studio** and either:

- click **Load demo data** (`core` / `lite` / `full`) to load the synthetic Soundview book, or
- upload a file and map its columns (generate ready-made samples with
  `cd backend && uv run rackiq-export-samples`, then import `samples/*.csv`).

Watch the capability grid light up as data lands.

## Data Studio (the front door)

Upload **CSV / Excel → map columns → validate → commit**:

1. **Inspect** the file: columns, sample values, null rates, and a fuzzy-matched mapping
   suggestion (it even infers whether the file is lifts, AR, inventory, or market prices).
2. **Map** each column to a canonical field; required fields are enforced before commit,
   optional ones are skippable. Save the mapping as a **named profile** for one-click re-upload.
3. **Validate**: row count, date range, duplicates, per-field null rates, and parse errors.
4. **Commit**: data is run through the **Hygiene Studio** pipeline, written to canonical tables,
   and the capability matrix is recomputed from the fields actually present.

A live **Data Capability** panel shows every feature as unlocked (green) or locked with a
**"Feed me: &lt;field&gt;"** hint.

## CLI

```bash
uv run rackiq-generate --profile {core,lite,full}   # (re)build the synthetic book
uv run rackiq-export-samples                         # write samples/*.csv (+ .xlsx)
uv run rackiq-info                                   # row counts + enabled capability count
uv run rackiq-serve                                  # FastAPI + interactive docs at /docs
```

> The live server holds the DuckDB file read/write. Use the UI's **Load demo / Reset** instead
> of running the CLI generator against the served file, or stop the server first.

## More

Architecture, the canonical schema, the capability matrix, and the full module map are
documented in **[CLAUDE.md](./CLAUDE.md)**.
