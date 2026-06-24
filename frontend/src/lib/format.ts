/** Turn a canonical field/table name into a human label: "net_gallons" -> "Net Gallons". */
export function humanize(name: string): string {
  return name
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function pct(x: number | null | undefined): string {
  if (x == null || !isFinite(x)) return "—";
  return `${Math.round(x * 100)}%`;
}

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Parse an ISO date string as LOCAL midnight (so "2024-07-01" never shows as Jun 30 in a
 *  west-of-UTC timezone). Returns null on anything unparseable. */
function parseIso(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const s = iso.length <= 10 ? `${iso}T00:00:00` : iso;
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

/** A readable full date for tooltips / headers: "2024-07-01" -> "Jul 1, 2024". */
export function fmtDate(iso: string | null | undefined): string {
  const d = parseIso(iso);
  if (!d) return iso || "—";
  return `${MON[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
}

/** A compact month label for chart axes: "2024-07-01" -> "Jul ’24". */
export function fmtMonthYear(iso: string | null | undefined): string {
  const d = parseIso(iso);
  if (!d) return iso || "";
  return `${MON[d.getMonth()]} ’${String(d.getFullYear()).slice(2)}`;
}
