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
  quarantine_total?: number;
  crosswalk_total?: number;
}

export interface FieldPresence {
  present: boolean;
  nonnull: number;
  applicable: number;
  coverage: number;
}

export interface Collecting {
  count: number;
  target: number;
  unit: string;
  label: string;
  rejections?: number;
}

export interface Feature {
  key: string;
  label: string;
  description: string;
  category: string;
  kind?: "analysis" | "feed";
  status?: "enabled" | "collecting" | "locked";
  required_fields: string[];
  optional_fields: string[];
  enabled: boolean;
  missing_fields: string[];
  enhanced_by: string[];
  coverage: number;
  collecting?: Collecting | null;
}

export interface Capabilities {
  profile: string;
  categories: string[];
  fields: Record<string, FieldPresence>;
  features: Feature[];
  feeds?: Record<string, number>;
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
export interface ColumnFlag {
  level: "warn" | "info";
  code: string;
  message: string;
}

export interface SourceColumn {
  name: string;
  samples: string[];
  null_rate: number;
  dtype_guess: string;
  // Profiling scorecard (added by the Data Hygiene Studio):
  distinct?: number;
  n_total?: number;
  n_nonblank?: number;
  min?: number | string | null;
  max?: number | string | null;
  outliers?: number;
  flags?: ColumnFlag[];
  quality?: number;
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
  hygiene?: HygieneOptions | null;
}

export interface InspectResponse {
  upload_id: string;
  filename: string;
  n_rows: number;
  n_columns: number;
  columns: SourceColumn[];
  profile: { score: number; n_flagged_columns: number; n_warnings: number };
  suggested_table: string;
  suggestions_by_table: Record<string, Record<string, Suggestion>>;
  targets_by_table: Record<string, ImportTarget[]>;
  table_labels: Record<string, string>;
  required_keys: Record<string, string[]>;
  matched_profile: MatchedProfile | null;
  crosswalk_size: number;
}

// ---- Hygiene options (the approved auto-fixes) ----------------------------------
export interface HygieneOptions {
  trim_whitespace: boolean;
  drop_empty_rows: boolean;
  standardize_units: boolean;
  source_unit: string;            // "gallons" | "barrels"
  fill_defaults: boolean;
  default_terminal: string | null;
  default_product: string | null;
  net_correction: string;         // "auto" | "factor" | "gross" | "off"
  net_factor: number | null;
  resolve_customers: boolean;
  dedupe_exact: boolean;
  dedupe_lifts_grain: boolean;
  quarantine_failures: boolean;
}

export const DEFAULT_HYGIENE: HygieneOptions = {
  trim_whitespace: true,
  drop_empty_rows: true,
  standardize_units: false,
  source_unit: "gallons",
  fill_defaults: false,
  default_terminal: null,
  default_product: null,
  net_correction: "auto",
  net_factor: null,
  resolve_customers: true,
  dedupe_exact: true,
  dedupe_lifts_grain: false,
  quarantine_failures: true,
};

// ---- Customer Master crosswalk --------------------------------------------------
export interface MergeMember {
  key: string;
  name: string;
  count: number;
  in_file: boolean;
  already_confirmed: boolean;
  similarity: number;
}

export interface MergeGroup {
  group_id: string;
  master_id: string;
  master_name: string;
  confidence: number;
  from_existing: boolean;
  members: MergeMember[];
}

export interface ProposeResponse {
  groups: MergeGroup[];
  n_distinct_keys: number;
  n_groups: number;
  n_resolved: number;
  n_new_singletons: number;
  threshold: number;
  crosswalk_size: number;
  key_column: string;
}

export interface CrosswalkEntry {
  variant_key: string;
  master_id: string;
  master_name: string;
  confidence: number | null;
  status: string;
  source: string;
  updated_at: string;
}

// ---- Validation rule engine -----------------------------------------------------
export interface RuleRow {
  row: number;
  values: Record<string, string | number | null>;
}

export interface RuleResult {
  key: string;
  label: string;
  severity: "error" | "warning" | "info";
  action: "quarantine" | "fix" | "none";
  passed: boolean;
  count: number;
  message: string;
  rows: RuleRow[];
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
  // Data Hygiene Studio additions:
  rules: RuleResult[];
  fixes_preview: HygieneStep[];
  rule_errors: number;
  rule_warnings: number;
  quarantine_count: number;
  clean_rows: number;
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
  quarantined: number;
  hygiene: HygieneStep[];
  rules: RuleResult[];
  saved_profile: string | null;
  summary: Summary;
  capabilities: Capabilities;
}

export interface SavedProfile {
  name: string;
  target_table: string;
  mapping: Record<string, string>;
  source_columns: string[];
  hygiene: HygieneOptions | null;
  created_at: string;
}

// ---- Standing Data Health -------------------------------------------------------
export interface HealthComponent {
  key: string;
  score: number;
  weight: number;
  detail: Record<string, number | string>;
}

export interface CustomerDriftAlert {
  code: string;
  kind: "possible_variant" | "new_code";
  near?: string;
  similarity?: number;
}

export interface VolumeDrift {
  month: string;
  value: number;
  mean: number;
  z: number;
  alert: boolean;
  direction?: string;
}

export interface AuditEntry {
  at: string;
  target_table: string;
  filename: string;
  step: string;
  detail: string;
  rows_affected: number;
}

export interface FeedCounts {
  rack_benchmark_days: number;
  quotes: { total: number; rejected: number; by_outcome: Record<string, number> };
  receipts: { rows: number; by_source: Record<string, number> };
}

export interface DataHealth {
  score: number;
  grade: string;
  components: HealthComponent[];
  drift: {
    customers: CustomerDriftAlert[];
    n_possible_variants: number;
    n_new_codes: number;
    volume: VolumeDrift | null;
  };
  quarantine: { total: number; by_table: Record<string, number> };
  feeds?: FeedCounts;
  crosswalk: { size: number; masters: number };
  recent_audit: AuditEntry[];
  profile: string;
}

// ---- Early data feeds (quick-entry forms) ---------------------------------------
export interface RackBenchmarkEntry {
  price_date: string;
  terminal: string;
  product: string;
  rack_benchmark: number;
}

export interface QuoteEntry {
  customer_id: string;
  quote_time: string;
  product: string;
  quoted_price: number;
  outcome: string;
  market_price_at_quote?: number | null;
  inventory_state?: string | null;
  capacity_state?: string | null;
  competitor_context?: string | null;
  time_to_decision?: number | null;
  final_gallons?: number | null;
}

export interface FeedWriteResponse extends StudioState {
  ok: boolean;
  rows_written: number;
  quarantined: number;
}

export interface QuarantineRow {
  id: string;
  at: string;
  target_table: string;
  filename: string;
  reasons: string[];
  payload: Record<string, string | number | null>;
}

export interface QuarantineResponse {
  rows: QuarantineRow[];
  counts: Record<string, number>;
  total: number;
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
