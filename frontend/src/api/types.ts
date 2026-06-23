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

// ---- Customer scoring -----------------------------------------------------------
export interface Availability {
  available: boolean;
  reason: string;
}

export interface VarBlock {
  score: number | null;
  grade: string | null;
  volume_var: number | null;
  cadence_var: number | null;
  status: string;
  base_level: number;
  base_cadence_days: number | null;
  in_band_rate: number | null;
  tightness: number | null;
  excursion_penalty: number | null;
  method: string;
  explanation: string;
}

export interface BaseValueBlock {
  score: number;
  grade: string | null;
  egp: number;
  friction_cost: number;
  credit_cost: number;
  rfap: number;
  profit_per_gallon: number | null;
  profit_per_rackhour: number | null;
  profit_per_credit_dollar: number | null;
  profit_per_order: number | null;
  strategic_uplift: number;
  annual_gallons: number;
  available: boolean;
}

export interface SubScore {
  value: number | null;
  available: boolean;
  reason?: string;
  note?: string;
  beta?: number | null;
  ratio?: number | null;
  accept_rate?: number | null;
  collecting?: boolean;
  profile?: { level: number; momentum: number; volatility: number } | null;
}

export interface QuadrantBlock {
  explainability: number | null;
  profitability: number | null;
  quadrant: string | null;
}

export interface ArchetypeBlock {
  primary: string;
  secondary: string;
  confidence: number;
  ambiguous: boolean;
  posture: Record<string, string>;
  scores: Record<string, number>;
}

export interface LanePoint {
  period_start: string;
  base: number;
  base_lo: number;
  base_hi: number;
  var_lo: number;
  var_hi: number;
  actual: number;
}

export interface ScoreCustomer {
  customer_id: string;
  name: string;
  archetype_true: string | null;
  home_terminal: string | null;
  window: string;
  grain: string;
  data_sufficient: boolean;
  n_lifts: number;
  total_net_gallons: number;
  monthly_volume: number;
  trend_pct: number;
  recency_gap: number;
  var: VarBlock;
  base_value: BaseValueBlock;
  account_value: number | null;
  quadrant: QuadrantBlock;
  archetype: ArchetypeBlock;
  subscores: Record<string, SubScore>;
  lane_series?: LanePoint[];
  facts?: Record<string, number | string | null | Record<string, number>>;
}

export interface ScoresResponse {
  window: string;
  as_of: string | null;
  availability: Record<string, Availability>;
  windows: string[];
  n_customers: number;
  customers: ScoreCustomer[];
}

export interface QuadrantPoint {
  customer_id: string;
  name: string;
  explainability: number;
  profitability: number;
  quadrant: string;
  primary_archetype: string;
  var_score: number | null;
  base_value: number;
  total_net_gallons: number;
  data_sufficient: boolean;
}

export interface QuadrantResponse {
  window: string;
  as_of: string | null;
  points: QuadrantPoint[];
  axes: { x: string; y: string };
}

export interface CustomerScoreResponse {
  window: string;
  as_of: string | null;
  availability: Record<string, Availability>;
  customer: ScoreCustomer;
}

export interface BacktestRow {
  customer_id: string;
  name: string;
  grain: string;
  mae: Record<string, number>;
  best: string;
}

export interface BacktestResponse {
  customers: BacktestRow[];
  methods: string[];
  summary: Record<string, number>;
}

// ---- Regime / Daily operating dashboard (Blueprint C) ---------------------------
export interface RegimeState {
  label: string;
  hint: string;
}
export interface RegimeAxis {
  label: string;
  states: Record<string, RegimeState>;
  default: string;
}
export type Regime = Record<string, string>;

export interface RegimeConfig {
  axes: Record<string, RegimeAxis>;
  default: Regime;
  multiplier: Record<string, Record<string, Record<string, number>>>;
  archetypes: string[];
  posture: Record<string, Record<string, string>>;
}

export interface DailyRow {
  customer_id: string;
  name: string;
  archetype: string;
  secondary_archetype: string;
  home_terminal: string | null;
  action: string;
  why_now: string;
  expected_impact: string;
  impact_value: number;
  base_value: number;
  regime_score: number | null;
  regime_delta: number | null;
  source?: string;
}

export interface DailyPanel {
  key: string;
  label: string;
  description: string;
  rows: DailyRow[];
  total: number;
}

export interface DailyResponse {
  as_of: string | null;
  window: string;
  regime: Regime;
  regime_label: string;
  terminal: string | null;
  terminals: string[];
  n_customers: number;
  availability: Record<string, Availability>;
  panels: DailyPanel[];
}

// ---- Scorecards (Blueprint E) ---------------------------------------------------
export interface ScorecardFlip {
  regime: Regime;
  regime_label: string;
  regime_score: number | null;
  delta: number | null;
  action: string;
  line: string;
}

export interface Scorecard {
  customer_id: string;
  name: string;
  home_terminal: string | null;
  archetype: ArchetypeBlock;
  base_value: BaseValueBlock;
  var: VarBlock;
  subscores: Record<string, SubScore>;
  quadrant: QuadrantBlock;
  monthly_volume: number;
  trend_pct: number;
  recency_gap: number;
  facts?: Record<string, number | string | null | Record<string, number>>;
  regime_score: number | null;
  regime_multiplier: number;
  regime_breakdown: Record<string, number>;
  why_now: string;
  recommended_action: string;
  expected_impact: string;
  flip: ScorecardFlip;
}

export interface ScorecardsResponse {
  as_of: string | null;
  window: string;
  regime: Regime;
  regime_label: string;
  flip_regime_label: string;
  terminal: string | null;
  terminals: string[];
  availability: Record<string, Availability>;
  n: number;
  archetypes_present: string[];
  exemplars: Scorecard[];
  cards: Scorecard[];
}

// ---- Playbook (Blueprint G) -----------------------------------------------------
export interface ArchetypePlay {
  archetype: string;
  present: boolean;
  posture: Record<string, string>;
  play: {
    say?: string;
    call_when?: string;
    quote?: string;
    terms?: string;
    avoid?: string;
  };
}
export interface RegimeCheatState {
  state: string;
  label: string;
  hint: string;
  do?: string;
  dont?: string;
}
export interface RegimeCheat {
  axis: string;
  label: string;
  states: RegimeCheatState[];
}
export interface MorningStep {
  step: string;
  detail: string;
}
export interface PlaybookResponse {
  archetypes: ArchetypePlay[];
  present_archetypes: string[];
  regime_cheatsheet: RegimeCheat[];
  morning_routine: MorningStep[];
}

// ---- Demand Cockpit -------------------------------------------------------------
export interface DemandHistoryPoint {
  period_start: string;
  actual: number;
}
export interface DemandForecastPoint {
  period_start: string;
  p10: number;
  p50: number;
  p90: number;
  sigma?: number;
}
export interface DemandCustomerForecast {
  customer_id: string;
  name: string;
  method: string;
  n_periods: number;
  var_score: number | null;
  mape: number | null;
  bias: number | null;
  next_p50: number;
  horizon_p50: number;
}
export interface DemandAccuracy {
  mape: number | null;
  bias: number | null;
  n: number;
  method?: string;
  by_method: Record<string, number>;
}
export interface DemandInventory {
  inventory: number;
  capacity: number;
  min_heel: number;
  as_of: string | null;
}
export interface BurndownPoint {
  day: number;
  date: string;
  p50: number;
  fast: number;
  slow: number;
  heel: number;
  capacity: number;
}
export interface DemandBurndown {
  horizon_days: number;
  breach_day: number | null;
  series: BurndownPoint[];
}
export interface DemandRecommendation {
  mode: string; // buy | target_only | no_demand
  supply_gap: boolean;
  service_level: number;
  lead_time_days: number;
  review_period_days: number;
  lot_size: number | null;
  daily_demand_p50: number;
  daily_demand_sigma: number;
  safety_stock: number;
  reorder_point_above_heel: number;
  order_up_to_above_heel: number;
  target_cover_days: number;
  headline: string;
  // buy mode
  inventory?: number;
  capacity?: number;
  min_heel?: number;
  available_above_heel?: number;
  days_of_cover?: number | null;
  days_to_reorder?: number;
  buy_by_date?: string | null;
  buy_quantity?: number | null;
  quantity_capped?: boolean;
  ullage?: number;
  // target_only mode
  target_inventory?: number | null;
  gap_note?: string;
}
export interface DemandCockpit {
  terminal: string | null;
  terminals: string[];
  product: string;
  products: string[];
  window: string;
  windows: string[];
  grain: string;
  as_of: string | null;
  n_customers: number;
  availability: Record<string, Availability>;
  history: DemandHistoryPoint[];
  forecast: DemandForecastPoint[];
  customer_forecasts: DemandCustomerForecast[];
  accuracy: DemandAccuracy;
  inventory: DemandInventory | null;
  days_of_cover: number | null;
  burndown: DemandBurndown | null;
  recommendation: DemandRecommendation | null;
  inputs?: { service_level: number; lead_time_days: number; lot_size: number | null };
  config?: Record<string, number | string | boolean>;
}
export interface DemandForecastRow {
  terminal: string;
  product: string;
  score_window: string;
  computed_at: string;
  grain: string;
  h_index: number;
  period_start: string;
  p10: number;
  p50: number;
  p90: number;
  daily_p50?: number;
  [k: string]: number | string | undefined;
}
export interface DemandForecastsResponse {
  level: string;
  computed_at: string | null;
  count: number;
  rows: DemandForecastRow[];
}
