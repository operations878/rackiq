import Panel from "../components/Panel";
import { DEFS, type Def } from "../lib/varGlossary";

function DefCard({ d }: { d: Def }) {
  return (
    <div className="rounded-lg border border-slate-200 p-3">
      <div className="text-sm font-semibold text-slate-800">{d.term}</div>
      <dl className="mt-1.5 space-y-1 text-xs text-slate-600">
        <div>
          <dt className="inline font-medium text-slate-500">What it measures: </dt>
          <dd className="inline">{d.what}</dd>
        </div>
        <div>
          <dt className="inline font-medium text-slate-500">How it's computed: </dt>
          <dd className="inline">{d.how}</dd>
        </div>
        {d.threshold && (
          <div>
            <dt className="inline font-medium text-slate-500">Threshold: </dt>
            <dd className="inline font-mono text-[11px] text-slate-700">{d.threshold}</dd>
          </div>
        )}
        <div>
          <dt className="inline font-medium text-slate-500">What it means for the desk: </dt>
          <dd className="inline text-slate-700">{d.meaning}</dd>
        </div>
      </dl>
    </div>
  );
}

function Section({ title, keys, sub }: { title: string; keys: string[]; sub?: string }) {
  return (
    <Panel title={title}>
      {sub && <p className="mb-3 max-w-3xl text-sm text-slate-500">{sub}</p>}
      <div className="grid gap-3 md:grid-cols-2">
        {keys.map((k) => DEFS[k] && <DefCard key={k} d={DEFS[k]} />)}
      </div>
    </Panel>
  );
}

const QUAD_GRID: Array<{ k: string; row: string; col: string }> = [
  { k: "metronome", row: "Regular timing", col: "Consistent size" },
  { k: "predictable_timing", row: "Regular timing", col: "Variable size" },
  { k: "predictable_size", row: "Irregular timing", col: "Consistent size" },
  { k: "unpredictable", row: "Irregular timing", col: "Variable size" },
];

export default function Glossary() {
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-slate-800">Spot / Rack — definitions & glossary</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-500">
          Every metric, axis, cutoff, quadrant, confidence tier, β, weather adjustment, and channel —
          in plain English. The same definitions appear as hover tooltips throughout the app. Anyone
          should be able to read <i>why</i> a customer got their recommendation without seeing code.
        </p>
      </div>

      <Section
        title="The two axes"
        sub="A customer's predictability is two independent things, scored separately. The size axis is never diluted by silent days; the timing axis is about regularity, not frequency."
        keys={["cadence", "size"]}
      />

      <Panel title="The 2×2 quadrant — worked examples">
        <p className="mb-3 max-w-3xl text-sm text-slate-500">
          Cross the two axes (regular timing? × consistent size?) to name exactly what you can plan —
          and the channel it implies.
        </p>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {QUAD_GRID.map(({ k, row, col }) => {
            const d = DEFS[k];
            return (
              <div key={k} className="rounded-lg border border-slate-200 p-3">
                <div className="flex items-center justify-between">
                  <div className="text-sm font-semibold text-slate-800">{d.term}</div>
                  <div className="text-[10px] uppercase tracking-wide text-slate-400">
                    {row} · {col}
                  </div>
                </div>
                <div className="mt-1 text-xs text-slate-600">{d.meaning}</div>
              </div>
            );
          })}
        </div>
      </Panel>

      <Section
        title="Channel, confidence & mismatch"
        sub="The channel is set by the quadrant and confidence ONLY. Margin ranks the book but never moves a channel between rack and spot."
        keys={["channel", "confidence", "current_channel", "mismatch", "margin_note"]}
      />

      <Section
        title="Weather (heating fuels only)"
        sub="Heating fuels (ULSHO / #2 / HO4) swing with the cold. The size axis is measured on the HDD residual so cold-snap sizing isn't misread as inconsistency — without flattening genuine non-weather lumpiness. Gasoline is never touched."
        keys={["weather_adjust", "beta"]}
      />

      <Panel title="Confidence tiers — worked examples">
        <ul className="space-y-1.5 text-sm text-slate-600">
          <li>
            <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-xs font-semibold text-emerald-700">High</span>{" "}
            A ~5,800-lift account over years of history — trust the rec.
          </li>
          <li>
            <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-amber-700">Medium</span>{" "}
            ~120 lifts over a year — a usable read, but watch it.
          </li>
          <li>
            <span className="rounded bg-rose-100 px-1.5 py-0.5 text-xs font-semibold text-rose-700">Low</span>{" "}
            An ~88-lift account — still gets a rec, explicitly flagged <i>"provisional — based on only 88 lifts"</i>. Never suppressed.
          </li>
        </ul>
      </Panel>
    </div>
  );
}
