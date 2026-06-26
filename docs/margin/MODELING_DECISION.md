# Phase 2 — Margin & Pricing: Modeling Decision (read this before the code)

**Scope.** Add a *margin layer* on top of the Phase-1 book so the desk is ranked by **value, not
volume**, the **forward-fixed book is marked to market**, and any **demand gap can be priced in
dollars** (the helper Phase 3's hedge will call). This phase **does not touch** the Phase-1
variability (VAR) score, BOL/deal ingestion, position/inventory (Phase 3), or `hedging.py`. It
**reads** the `deals` table Phase 1 produced and treats `term | forward_fixed | spot` as **deal-type
metadata for margin math** — never as a customer-scoring split. A customer can be **stable AND
thin-margin**, or **variable AND fat-margin**; both facts stay visible side by side.

---

## 0. Data-availability finding (the honest starting point)

The two source workbooks named in the brief —
`1__Wholesale_Prices___Costs_V1.xlsx` (the sell grid + benchmarks) and
`6__/7__Trips_Report.xls` (barge landed cost) — are **operator-sensitive real files that are NOT
committed to this repo** (`backend/sample_data/deals/` is gitignored; the operator drops them in
locally, exactly as for the Account Reference Chart, BOLs, and deal workbooks). They are therefore
**not present in this environment.**

Consequently, this phase follows the **same discipline the existing real-book code already uses**
(`dealbook.py` / `bookload.py`): build **format-aware parsers to the documented layout**, prove them
with **synthetic fixtures** that reproduce each quirk (the Matrix concat keys, the multi-row grid
headers, the Trips barrel/`$/gal` columns), and **validate margin plausibility end-to-end on the
synthetic `full` book** (the only real numbers available here — its `lifts` carry `unit_price` +
`unit_cost`, and `market_prices` carries `rack_benchmark`). The layouts described below are taken as
the discovery spec; **ambiguous keys are flagged, never guessed** (per the brief).

The engine is built to **degrade gracefully across both worlds**: on the real book it reads the new
**price-grid** and **landed-cost** stores; on the synthetic/sample book (no grid/Trips file) it falls
back to the lift's own `unit_price`/`unit_cost`. Every margin row records **where its sell and cost
came from** (`sell_source`, `cost_source`) and a **confidence**, so coverage is auditable.

---

## 1. Discovery answers (required in writing)

### (1) Is the all-in landed PRODUCT cost recoverable, or only the LOGISTICS cost?

**Logistics cost is fully recoverable. The all-in cargo *flat* is index-priced and is NOT recoverable
from the loaded files alone** — so margin is **scoped to the legs where both sides are known** and the
flat-price gap is flagged, never faked.

The Trips report gives logistics as **explicit per-gallon columns**:

```
logistics_$/gal = Barge Cost/gal + Inspector Cost/gal + Operational Cost/gal + Gain-Loss Cost/gal
```

The **cargo** is index-priced: `Pricing Type ∈ {Monthly Average, Fixed Diff}` with a
`Fixed Differential`. So the true cargo cost is

```
cargo_$/gal = INDEX(t)  +  cargo_differential  +  logistics_$/gal
              └ Argus/Platts — NOT loaded ┘     └ Fixed Differential ┘   └ Trips legs ┘
```

The **`INDEX(t)` flat is the gap.** What we therefore treat as recoverable:

* **`logistics_$/gal`** — always (explicit Trips columns).
* **`cargo_differential`** — where `Pricing Type = Fixed Diff` (the `Fixed Differential` column).
* **`Estimated Trip Value`** — a *candidate* all-in cargo value. We use it **only if it passes a
  magnitude sanity check** (`Estimated Trip Value ÷ net_gallons` lands in a plausible **flat** band,
  ~\$1.50–\$4.00/gal — i.e. it embeds the index — rather than near a differential ~\$0.00–\$0.20). If
  it passes, it yields an **all-in landed cost proxy**; if it fails or is absent, we fall back to
  **logistics-only** and **flag the cargo-flat gap** for that trip. This is the "scope around it and
  FLAG it" rule made mechanical.

**Net consequence by deal type** (see §2): **TERM margin is fully recoverable with no market data**
(the flat cancels); **FORWARD-FIXED and SPOT book margins need the cargo flat** and so depend on the
Estimated-Trip-Value proxy (used where it passes the sanity check) or are flagged incomplete.

### (2) Confirm: grid = SELL prices; \$/gal; Trips cost cols = \$/gal; Product Vol = barrels.

* **Grid values are customer SELL prices** (`$/gal`), not costs. Confirmed by the brief and
  cross-checked structurally: the per-terminal sheets are titled by *product+terminal* with *customer*
  rows, and the values sit in the **\$2–\$4/gal** band of a refined-product street price. A
  **cross-check** is wired into validation: realized **spot** prices (from `deals`) vs the grid on the
  same customer/date must land in the same neighborhood (else the grid is mis-scaled).
* **Trips per-gallon cost columns are `$/gal`** (Barge/Inspector/Operational/Gain-Loss). Their sum is
  the per-gallon **logistics** leg.
* **Product Vol is in BARRELS**, written as thousand-barrel **"mb"**. Barrels → gallons is **× 42**.
  The "mb" scaling (is `84` → 84 bbl or 84,000 bbl?) is resolved by a **magnitude heuristic** (a barge
  is ~10k–150k bbl; a value < ~1,000 is read as **mb** = `×1000` bbl, ≥ that as raw bbl) and the
  assumption is **recorded on every row**. Because the per-gallon cost legs are already `$/gal`,
  Product Vol only sets **relative weights** in the running cost basis (and the optional
  Estimated-Trip-Value-per-gallon derivation), so a wrong global scale cannot move a `$/gal` margin —
  the **single biggest protection against the "\$1/gal margin" units bug**.

### (3) Map every grid customer/product to the master crosswalk + product family.

Grid customer names resolve through the **same** `customer_crosswalk` the BOL/deal flow uses
(`crosswalk.apply_to_frame`, normalized match), and grid product codes through the **product family**
normalizer (`dealbook.product_family`) + the `product_crosswalk`. **Blend numbers (B5/B10/B20/B99) are
product attributes, not identity** (`GEC 10`/`GEC 20` → master `GEC`, family carries the blend). Grid
names that don't resolve are **surfaced to the unmapped panel** (same machinery as the BOL/deal flow)
— never silently dropped, never guessed.

The Matrix sheet's **concatenated `PRODUCT+CUSTOMER` keys with no delimiter** (`"ULSHO4416 Oil
Corp"`, `"ULSHO24 Hour"`) are split using the **known product-family prefix list** (longest-prefix
match against the family/blend vocabulary). A key that doesn't start with a known product prefix is
**flagged ambiguous and skipped**, not split arbitrarily. The per-terminal sheets are **cleaner**
(explicit `Customer` column) and are **preferred**; the Matrix only **fills gaps** where a
customer/product/date isn't covered by a terminal sheet.

---

## 2. Margin model — BY DEAL TYPE (the core; respect index-on-index physics)

All margins are computed **per gallon** first, then weighted by **BOL net gallons** (the volume spine)
for `$` totals. Two cost views are produced for every leg (§3): **BOOK** (vs the cost actually landed)
and **REPLACEMENT** (vs the most-recent landed cost / nearest spot).

### TERM — index sell vs index cost → **the flat price cancels**

Sell `S(t) = I_sell(t) + d_sell`; cargo `C(t) = I_buy(t) + d_cargo + L`. Margin:

```
M = S − C = [I_sell(t) − I_buy(t)] + (d_sell − d_cargo) − L
          = (d_sell − d_cargo) − L − basis
```

* `d_sell` = the term deal's **sell differential** (`deals.price`, `price_type='basis'`, e.g. "Argus HO
  Barge + \$0.14").
* `d_cargo` = the **cargo differential** (Trips `Fixed Differential` for that terminal×product, vol-
  weighted around the month).
* `L` = **logistics \$/gal** (Trips legs).
* `basis = I_sell − I_buy` = the **index-to-index spread**. If sell and cargo reference the **same**
  index it is **0**; if different (Argus vs Platts, barge vs cargo, location basis) it is non-zero and
  **NOT loaded**. **Assumption (stated explicitly, surfaced on the payload): `basis = 0` (same-index).**
  The number is reported as `term_margin_per_gal = d_sell − d_cargo − L` with a
  `basis_assumption: "same_index_zero"` flag so the desk knows what's baked in.

**→ TERM margin is recoverable from differentials + Trips logistics with NO market level.** This is the
headline defensible number and is surfaced prominently.

### FORWARD-FIXED — flat locked sell vs index cost (where flat-price risk lives)

```
M = locked_sell − landed_cost(cargo_flat + cargo_diff + logistics)
```

* `locked_sell` = `deals.price`, `price_type='fixed'`.
* `landed_cost` = the **Trips landed cost around the delivery month** (§3 running basis). Needs the
  **cargo flat** → uses the Estimated-Trip-Value proxy where it passes the sanity check, else
  **flagged incomplete**. This is the leg that drives **mark-to-market** (§4).

### SPOT — flat realized sell vs cost

```
M = realized_sell − landed_cost(at/around the deal date)
```

* `realized_sell` = `deals.price`, `price_type='realized'`.
* `landed_cost` = Trips landed cost around the **deal date**.

### RACK / untagged lifts — grid sell vs running cost basis

```
M = grid_sell(customer, product, date) − running_landed_cost(terminal, product, date)
```

The default for the **bulk of the BOL book** that isn't covered by a specific deal commitment.

### Cost basis where a running number is needed (no per-lift cost attribution exists)

`running_landed_cost(terminal, product, t)` = the **volume-weighted (WAC) landed cost of recent barges**
discharged into that `terminal × product`, over a **trailing window sized from barge cadence**
(default 45 days / last 3 barges, configurable; widen to the nearest prior barge if the window is
empty). Trips **barrels → gallons (×42)** before weighting. This is the **inventory cost basis at time
t** — the BOOK cost. The **REPLACEMENT** cost is the **most-recent** barge landed cost (or nearest
spot) for that `terminal × product`.

---

## 3. Roll-up to customer · product family · terminal

Per **BOL lift** (the volume spine; sell/cost sourced by the priority chain below), compute
`margin_per_gal` (BOOK and REPLACEMENT) and `margin_$ = margin_per_gal × net_gallons`. Roll up over a
**common window reconciled to BOL lifts** to **customer / product family / terminal**, reporting
**¢/gal** and **\$ total**. Produce a **margin-ranked customer list** and **explicitly contrast it with
the volume ranking** (a `rank_by_margin` vs `rank_by_volume` delta), so a **high-volume/thin-margin**
account and a **low-volume/fat-margin** account are both visible. **This is a value ranking; it does
not alter the VAR score.**

**Sell/cost priority chain (records provenance + confidence on every row):**

* **Sell** ← (1) deal price if the cell is deal-covered (forward locked / spot realized / term
  index+differential) → (2) **grid** sell (`customer×product×date`) → (3) lift `unit_price`
  (synthetic/sample fallback).
* **Cost** ← (1) **Trips** running WAC basis (`terminal×product×date`) → (2) lift `unit_cost`
  (synthetic/sample fallback).

A lift with **no defensible sell or cost source** is **counted as incomplete in the coverage report**,
not given a fabricated margin.

---

## 4. Forward-fixed MARK-TO-MARKET (the high-value piece)

For every **OPEN** forward-fixed deal (locked sell; **remaining committed volume by future month**,
`month ≥ current month`): compare `locked_price` vs **current replacement cost** for that
`terminal×product` →

```
mtm_per_gal = locked_sell − replacement_cost
$ exposure  = mtm_per_gal × remaining_committed_gallons
```

Flag deals **underwater** (`mtm_per_gal < 0`) or **thin** (`0 ≤ mtm_per_gal < threshold`), with the
`$` exposure, ranked. Where `replacement_cost` lacks the cargo flat, the row is flagged
`cost_incomplete` and excluded from the trustworthy MTM total (surfaced separately). This shows the
desk where it is **squeezed on price-locked commitments**.

---

## 5. Margin-priced gap helper (Phase 3's hedge calls this)

`margin.margin_for_gap(con, terminal, product, quantity_gallons, ...)` → given a demand quantity at a
`terminal × product`, return the **\$ margin at stake**, separating **committed / must-serve** margin
from **spot upside**:

* Volume up to the committed (term + forward) book for that cell → **must-serve**, valued at its
  **committed margin** (term differential / forward locked vs landed).
* Volume above committed → **spot upside**, valued at the **spot/replacement margin**.
* Returns `{committed_gallons, committed_margin_$, spot_gallons, spot_margin_$, total_margin_$,
  blended_margin_per_gal, basis_flags, confidence}`.

`margin.py` **never imports `hedging`** (clean one-way dependency: hedge → margin).

---

## 6. Validation (numbers must be PLAUSIBLE, not just present)

* **Sanity gate:** rack diesel margins read in **single-digit to low-double-digit ¢/gal**. If a margin
  comes out near **\$1/gal**, that is a units/basis error → the engine **flags it and the test fails
  loudly** (a `units_warning` on the payload + a hard assertion in `test_margin`).
* **Cross-check:** realized **spot** prices (`deals`) vs the **grid** on the same dates land in the same
  neighborhood.
* **One customer end-to-end:** a worked example (sell, cost, margin) with the arithmetic shown, in the
  test and the payload's `worked_example`.
* **Coverage:** report **% of lifted volume with a defensible margin** vs **flagged incomplete**
  (missing differential / cargo flat / unmapped customer). Be honest about the gaps.

## 7. Conventions

Master **names** via crosswalk (never numbers); product **family** via crosswalk; reuse the working-day
calendar; **gallons canonical** (Trips barrels ×42 when volume-weighting); **repeatable, idempotent**
ingestion exposed as re-uploadable Data Studio source(s) keyed on a stable id, with **format-aware
parsers** for the grid's multi-row headers and the Matrix concat keys. **No frontend redesign.** New,
self-contained modules (`pricegrid.py`, `margin.py`, `margin_config.py`, `api/margin.py`) so this
phase and Phase 3 can run in parallel without fighting over shared files.
