import type { ExcursionsBlock, Excursion } from "../../api/types";
import { fmtGal } from "../../lib/scoreui";

/** A customer's LANE BREAKS — every lift that landed outside their variability range, tagged
 *  spike / shortfall / no-show with the weather that period, plus a plain-English pattern note
 *  that separates a weather-driven account from a genuinely random one. */

const KIND: Record<string, { label: string; tone: string }> = {
  spike: { label: "▲ Spike", tone: "bg-amber-100 text-amber-700" },
  shortfall: { label: "▼ Shortfall", tone: "bg-orange-100 text-orange-700" },
  no_show: { label: "∅ No-show", tone: "bg-slate-200 text-slate-600" },
};

const PATTERN_TONE: Record<string, string> = {
  cold_snap: "border-sky-200 bg-sky-50 text-sky-800",
  hot_spell: "border-amber-200 bg-amber-50 text-amber-800",
  random: "border-slate-200 bg-slate-50 text-slate-600",
  too_few: "border-slate-200 bg-slate-50 text-slate-500",
  none: "border-emerald-200 bg-emerald-50 text-emerald-700",
};

function WeatherCell({ b }: { b: Excursion }) {
  if (b.cold_snap) return <span className="text-sky-600" title={`HDD ${b.hdd}`}>❄ cold snap</span>;
  if (b.hot_spell) return <span className="text-amber-600" title={`CDD ${b.cdd}`}>☀ hot spell</span>;
  if (b.hdd != null && b.hdd >= (b.cdd ?? 0)) return <span className="text-slate-400">HDD {b.hdd}</span>;
  if (b.cdd != null) return <span className="text-slate-400">CDD {b.cdd}</span>;
  return <span className="text-slate-300">—</span>;
}

export default function LaneBreaks({ excursions }: { excursions?: ExcursionsBlock }) {
  if (!excursions || !excursions.available) {
    return <div className="text-[11px] text-slate-400">Lane breaks need a fitted lane (enough history) to detect.</div>;
  }
  const { breaks, pattern, weather_source } = excursions;
  return (
    <div className="space-y-2">
      {pattern && (
        <div className={`rounded-lg border px-3 py-2 text-[12px] leading-snug ${PATTERN_TONE[pattern.type] ?? PATTERN_TONE.random}`}>
          <span className="font-semibold">
            {pattern.type === "cold_snap" ? "❄ Weather pattern: " : pattern.type === "hot_spell" ? "☀ Weather pattern: " : ""}
          </span>
          {pattern.note}
        </div>
      )}
      {breaks.length > 0 ? (
        <div className="max-h-56 overflow-auto rounded-lg border border-slate-200">
          <table className="w-full text-[11px]">
            <thead className="sticky top-0 bg-slate-50 text-left text-[10px] uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-2 py-1.5">Date</th>
                <th className="px-2 py-1.5">What happened</th>
                <th className="px-2 py-1.5 text-right">Actual</th>
                <th className="px-2 py-1.5 text-right">vs base</th>
                <th className="px-2 py-1.5">Weather</th>
              </tr>
            </thead>
            <tbody>
              {breaks.map((b) => (
                <tr key={b.period_start} className="border-t border-slate-100">
                  <td className="px-2 py-1.5 text-slate-600">{b.period_start}</td>
                  <td className="px-2 py-1.5">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${KIND[b.kind]?.tone ?? "bg-slate-100"}`}>
                      {KIND[b.kind]?.label ?? b.kind}
                    </span>
                  </td>
                  <td className="px-2 py-1.5 text-right text-slate-700">{fmtGal(b.actual)}</td>
                  <td className={`px-2 py-1.5 text-right font-medium ${b.delta_pct != null && b.delta_pct >= 0 ? "text-emerald-600" : "text-rose-600"}`}>
                    {b.delta_pct != null ? `${b.delta_pct > 0 ? "+" : ""}${b.delta_pct}%` : "—"}
                  </td>
                  <td className="px-2 py-1.5"><WeatherCell b={b} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-[11px] text-slate-400">No lane breaks — every lift stayed inside their variability range.</p>
      )}
      {weather_source && (
        <p className="text-[10px] text-slate-400">
          Weather: {weather_source === "open-meteo" ? "NOAA/ERA5 archive (auto-fetched)" : "seasonal climatology (offline)"} · degree-days base 65°F.
        </p>
      )}
    </div>
  );
}
