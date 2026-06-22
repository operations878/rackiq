export interface Summary {
  connected: boolean;
  customers: number;
  lifts: number;
  terminals: string[];
  products: string[];
  date_range: { start: string | null; end: string | null };
  total_net_gallons: number;
  profile: string;
  generated_at: string | null;
}

export interface FieldPresence {
  present: boolean;
  nonnull: number;
  applicable: number;
  coverage: number;
}

export interface Feature {
  key: string;
  label: string;
  description: string;
  category: string;
  required_fields: string[];
  optional_fields: string[];
  enabled: boolean;
  missing_fields: string[];
  enhanced_by: string[];
  coverage: number;
}

export interface Capabilities {
  profile: string;
  categories: string[];
  fields: Record<string, FieldPresence>;
  features: Feature[];
  summary: { enabled: number; total: number };
}

export interface Customer {
  customer_id: string;
  name: string;
  archetype: string;
  home_terminal: string;
  lift_count: number;
  total_net_gallons: number;
  avg_gallons_per_lift: number;
  last_lift: string | null;
  avg_margin_per_gal: number | null;
  dso_days: number | null;
}

export interface CustomersResponse {
  customers: Customer[];
  count: number;
  margin_enabled: boolean;
  dso_enabled: boolean;
}

export interface MonthlyVolumePoint {
  month: string;
  net_gallons: number;
}

export interface MonthlyVolume {
  points: MonthlyVolumePoint[];
}

export interface MarketPoint {
  date: string;
  market_price: number;
  nyh_basis: number;
  street_rack: number;
}

export interface MarketPrices {
  product: string | null;
  available: boolean;
  products: string[];
  points: MarketPoint[];
}
