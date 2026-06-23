import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ScoreCustomer, Summary } from "../api/types";
import Panel from "../components/Panel";
import { ArchetypeTag } from "../lib/scoreui";

type Flag = "Overdue" | "Fading" | "Erratic";

interface RadarRow {
  customer_id: string;
  name: string;
  home_terminal: string | null;
  archetype: string;
  secondary: string;
  flags: Flag[];
  why: string[];
  volume_at_risk: number; // annual gallons exposed
  recency_gap: number;
  trend_pct: number;
  var_now: number | null;
  var_prior: number | null;
}

const FLAG_TONE: Record<Flag, string> = {
  Overdue: "bg-amber-100 text-amber-700",
  Fading: "bg-rose-100 text-rose-700",
  Erratic: "bg-violet-100 text-violet-700",
};

function annualGal(c: ScoreCustomer): number {
  return c.base_value.annual_gallons || c.monthly_volume * 12;
}

export default function Radar({ summary }: { summary: Summary }) {
  const [now, setNow] = useState<ScoreCustomer[] | null>(null);
  const [prior, setPrior] = useState<Record<string, ScoreCustomer>>({});
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<Set<Flag>>(new Set(["Overdue", "Fading", "Erratic"]));
  const [asOf, setAsOf] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    // current = 90-day view; prior = all-time baseline (for the VAR-drop comparison)
    Promise.all([api.scores.list("90"), api.scores.list("all")])
      .then(([n, a]) => {
        setNow(n.customers);
        setAsOf(n.as_of);
        const m: Record<string, ScoreCustomer> = {};
        for (const c of a.customers) m[c.customer_id] = c;
        setPrior(m);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const rows = useMemo<RadarRow[]>(() => {
    if (!now) return [];
    const out: RadarRow[] = [];
    for (const c of now) {
      const flags: Flag[] = [];
      const why: string[] = [];
      // Overdue — recency gap > 1.5× base cadence
      if (c.recency_gap > 1.5) {
        flags.push("Overdue");
        why.push(`${c.recency_gap}× past usual cadence (last lift well overdue)`);
      }
      // Fading — sustained negative volume trend
      if (c.trend_pct <= -12) {
        flags.push("Fading");
        why.push(`volume trending ${c.trend_pct}% over recent periods`);
      }
      // Erratic — VAR dropped materially vs the all-time baseline
      const p = prior[c.customer_id];
      const varNow = c.var.score;
      const varPrior = p?.var.score ?? null;
      if (varNow != null && varPrior != null && varPrior - varNow >= 8) {
        flags.push("Erratic");
        why.push(`steadiness slipped: VAR ${varPrior} → ${varNow} vs all-time`);
      }
      if (!flags.length) continue;
      out.push({
        customer_id: c.customer_id, name: c.name, home_terminal: c.home_terminal,
        archetype: c.archetype.primary, secondary: c.archetype.secondary,
        flags, why, volume_at_risk: annualGal(c), recency_gap: c.recency_gap,
        trend_pct: c.trend_pct, var_now: varNow, var_prior: varPrior,
      });
    }
    return out.sort((a, b) => b.volume_at_risk - a.volume_at_risk);
  }, [now, prior]);

  const filtered = rows.filter((r) => r.flags.some((f) => active.has(f)));
  const totalRisk = filtered.reduce((s, r) => s + r.volume_at_risk, 0);

  function toggle(f: Flag) {
    setActive((cur) => {
      const next = new Set(cur);
      if (next.has(f)) next.delete(f); else next.add(f);
      return next.size ? next : new Set<Flag>([f]);
    });
  }

  function exportCsv() {
    const header = ["customer_id", "name", "terminal", "archetype", "flags", "why", "annual_gallons_at_risk", "recency_gap", "trend_pct", "var_now", "var_prior"];
    const lines = [header.join(",")];
    for (const r of filtered) {
      const cells = [r.customer_id, r.name, r.home_terminal ?? "", r.archetype, r.flags.join("|"),
        r.why.join("; "), Math.round(r.volume_at_risk), r.recency_gap, r.trend_pct, r.var_now ?? "", r.var_prior ?? ""];
      lines.push(cells.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(","));
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `early-warning-radar-${asOf ?? "export"}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (!summary.connected) return <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-slate-500">Load a book to run the radar.</div>;
  if (error) return <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700">{error}</div>;
  if (!now) return <div className="text-sm text-slate-500">Scanning the book…</div>;

  const counts: Record<Flag, number> = { Overdue: 0, Fading: 0, Erratic: 0 };
  for (const r of rows) for (const f of r.flags) counts[f]++;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold tracking-tight text-slate-900">Early-Warning Radar</h1>
          <p className="text-xs text-slate-500">
            {filtered.length} accounts flagged · ~{(totalRisk / 1e6).toFixed(2)} MM gal/yr at risk · 90-day view vs all-time, as of {asOf}
          </p>
        </div>
        <button onClick={exportCsv} className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50">
          Export CSV
        </button>
      </div>

      <div className="flex flex-wrap gap-2">
        {(["Overdue", "Fading", "Erratic"] as Flag[]).map((f) => (
          <button key={f} onClick={() => toggle(f)}
            className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition ${active.has(f) ? "border-slate-300 bg-white text-slate-700 shadow-sm" : "border-transparent bg-slate-100 text-slate-400"}`}>
            <span className={`mr-1.5 inline-block rounded px-1.5 py-0.5 text-[10px] ${FLAG_TONE[f]}`}>{counts[f]}</span>
            {f}
            <span className="ml-1.5 text-[10px] text-slate-400">
              {f === "Overdue" ? "recency > 1.5× cadence" : f === "Fading" ? "volume ≤ −12%" : "VAR dropped ≥ 8"}
            </span>
          </button>
        ))}
      </div>

      <Panel title="Ranked worklist — sorted by volume at risk">
        <div className="max-h-[40rem] overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-white text-left text-[10px] uppercase tracking-wide text-slate-400">
              <tr>
                <th className="pb-2">Customer</th>
                <th className="pb-2">Flags</th>
                <th className="pb-2">Why flagged</th>
                <th className="pb-2 text-right">Gal/yr at risk</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 && (
                <tr><td colSpan={4} className="py-8 text-center text-slate-400">No accounts match the active filters — the book is steady.</td></tr>
              )}
              {filtered.map((r) => (
                <tr key={r.customer_id} className="border-t border-slate-100 align-top hover:bg-slate-50">
                  <td className="py-2">
                    <div className="font-medium text-slate-700">{r.name}</div>
                    <div className="mt-0.5"><ArchetypeTag name={r.archetype} /></div>
                    <div className="text-[10px] text-slate-400">{r.home_terminal}</div>
                  </td>
                  <td className="py-2">
                    <div className="flex flex-col gap-1">
                      {r.flags.map((f) => <span key={f} className={`w-fit rounded px-1.5 py-0.5 text-[10px] font-semibold ${FLAG_TONE[f]}`}>{f}</span>)}
                    </div>
                  </td>
                  <td className="py-2 text-[11px] text-slate-600">
                    <ul className="list-inside list-disc space-y-0.5">
                      {r.why.map((w, i) => <li key={i}>{w}</li>)}
                    </ul>
                  </td>
                  <td className="py-2 text-right font-semibold text-slate-800">{(r.volume_at_risk / 1e6).toFixed(2)} MM</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
