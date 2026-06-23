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
  last_import?: {
    filename: string | null;
    table: string | null;
    at: string | null;
  };
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

// ---- Data Studio ----------------------------------------------------------------
export interface SourceColumn {
  name: string;
  samples: string[];
  null_rate: number;
  dtype_guess: string;
}

export interface ImportTarget {
  name: string;
  dtype: string;
  canonical: boolean;
  required: boolean;
  description: string;
}

export interface Suggestion {
  target: string;
  confidence: number;
}

export interface MatchedProfile {
  name: string;
  target_table: string;
  mapping: Record<string, string>;
}

export interface InspectResponse {
  upload_id: string;
  filename: string;
  n_rows: number;
  n_columns: number;
  columns: SourceColumn[];
  suggested_table: string;
  suggestions_by_table: Record<string, Record<string, Suggestion>>;
  targets_by_table: Record<string, ImportTarget[]>;
  table_labels: Record<string, string>;
  required_keys: Record<string, string[]>;
  matched_profile: MatchedProfile | null;
}

export interface ValidateFieldReport {
  source: string;
  target: string;
  null_rate: number;
  parse_errors: number;
}

export interface ValidateResponse {
  table: string;
  table_label: string;
  n_rows: number;
  importable_rows: number;
  date_range: { start: string | null; end: string | null; column: string | null };
  duplicate_rows: number;
  droppable_rows: number;
  total_parse_errors: number;
  fields: ValidateFieldReport[];
  missing_required: string[];
  warnings: string[];
  errors: string[];
  can_commit: boolean;
}

export interface HygieneStep {
  step: string;
  detail: string;
  rows_affected: number;
}

export interface CommitResponse {
  ok: boolean;
  table: string;
  mode: string;
  rows_written: number;
  rows_in_file: number;
  hygiene: HygieneStep[];
  saved_profile: string | null;
  summary: Summary;
  capabilities: Capabilities;
}

export interface SavedProfile {
  name: string;
  target_table: string;
  mapping: Record<string, string>;
  source_columns: string[];
  created_at: string;
}

export interface StudioState {
  summary: Summary;
  capabilities: Capabilities;
}

export interface ImportLogEntry {
  imported_at: string;
  target_table: string;
  filename: string;
  rows: number;
  mode: string;
}
