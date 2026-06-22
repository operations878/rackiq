import type {
  Summary,
  Capabilities,
  CustomersResponse,
  MonthlyVolume,
  MarketPrices,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "/api";

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return (await res.json()) as T;
}

export const api = {
  summary: () => getJSON<Summary>("/summary"),
  capabilities: () => getJSON<Capabilities>("/capabilities"),
  customers: () => getJSON<CustomersResponse>("/customers"),
  monthlyVolume: () => getJSON<MonthlyVolume>("/monthly-volume"),
  marketPrices: (product?: string) =>
    getJSON<MarketPrices>(`/market-prices${product ? `?product=${encodeURIComponent(product)}` : ""}`),
};
