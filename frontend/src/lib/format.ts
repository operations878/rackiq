/** Turn a canonical field/table name into a human label: "net_gallons" -> "Net Gallons". */
export function humanize(name: string): string {
  return name
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function pct(x: number): string {
  return `${Math.round(x * 100)}%`;
}
