import type {
  Summary,
  Capabilities,
  CustomersResponse,
  MonthlyVolume,
  MarketPrices,
  InspectResponse,
  ValidateResponse,
  CommitResponse,
  SavedProfile,
  StudioState,
  ImportLogEntry,
  HygieneOptions,
  ProposeResponse,
  CrosswalkEntry,
  DataHealth,
  QuarantineResponse,
  AuditEntry,
  RackBenchmarkEntry,
  QuoteEntry,
  FeedWriteResponse,
  ScoresResponse,
  QuadrantResponse,
  CustomerScoreResponse,
  BacktestResponse,
  Reconciliation,
  CreditResponse,
  CreditRow,
  RegimeConfig,
  Regime,
  DailyResponse,
  ScorecardsResponse,
  PlaybookResponse,
  DemandCockpit,
  DemandForecastsResponse,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "/api";

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return (await res.json()) as T;
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readError(res, path));
  return (await res.json()) as T;
}

async function readError(res: Response, path: string): Promise<string> {
  try {
    const data = await res.json();
    const detail = data?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message + (detail.errors ? `: ${detail.errors.join("; ")}` : "");
    if (Array.isArray(detail) && detail[0]?.msg) return detail.map((d: { msg: string }) => d.msg).join("; ");
  } catch {
    /* fall through */
  }
  return `${path} -> HTTP ${res.status}`;
}

export const api = {
  summary: () => getJSON<Summary>("/summary"),
  capabilities: () => getJSON<Capabilities>("/capabilities"),
  customers: () => getJSON<CustomersResponse>("/customers"),
  monthlyVolume: () => getJSON<MonthlyVolume>("/monthly-volume"),
  marketPrices: (product?: string) =>
    getJSON<MarketPrices>(`/market-prices${product ? `?product=${encodeURIComponent(product)}` : ""}`),

  // ---- Data Studio ----
  studio: {
    async inspect(file: File): Promise<InspectResponse> {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`${BASE}/studio/inspect`, { method: "POST", body: fd });
      if (!res.ok) throw new Error(await readError(res, "/studio/inspect"));
      return (await res.json()) as InspectResponse;
    },
    validate: (body: {
      upload_id: string;
      table: string;
      mapping: Record<string, string>;
      options?: HygieneOptions;
    }) => postJSON<ValidateResponse>("/studio/validate", body),
    commit: (body: {
      upload_id: string;
      table: string;
      mapping: Record<string, string>;
      mode: string;
      save_profile?: string | null;
      options?: HygieneOptions;
    }) => postJSON<CommitResponse>("/studio/commit", body),
    profiles: () => getJSON<{ profiles: SavedProfile[] }>("/studio/profiles"),
    saveProfile: (body: {
      name: string;
      table: string;
      mapping: Record<string, string>;
      source_columns: string[];
      hygiene?: HygieneOptions | null;
    }) => postJSON<{ ok: boolean; name: string }>("/studio/profiles", body),
    async deleteProfile(name: string): Promise<void> {
      const res = await fetch(`${BASE}/studio/profiles/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (!res.ok) throw new Error(await readError(res, "/studio/profiles"));
    },
    history: () => getJSON<{ imports: ImportLogEntry[] }>("/studio/history"),
    loadDemo: (profile: string) => postJSON<StudioState & { ok: boolean }>("/studio/load-demo", { profile }),
    reset: () => postJSON<StudioState & { ok: boolean }>("/studio/reset", {}),

    // ---- Early data feeds (quick entry) ----
    rackBenchmark: (entries: RackBenchmarkEntry[]) =>
      postJSON<FeedWriteResponse>("/studio/rack-benchmark", { entries }),
    quote: (entries: QuoteEntry[]) => postJSON<FeedWriteResponse>("/studio/quote", { entries }),

    // ---- Customer Master crosswalk ----
    crosswalkPropose: (body: {
      upload_id: string;
      table: string;
      mapping: Record<string, string>;
      name_source?: string | null;
      threshold?: number;
    }) => postJSON<ProposeResponse>("/studio/crosswalk/propose", body),
    crosswalkConfirm: (body: { groups: unknown[]; rejected_keys: string[] }) =>
      postJSON<{ written: number; crosswalk_size: number; crosswalk: CrosswalkEntry[] }>(
        "/studio/crosswalk/confirm", body),
    crosswalkList: () => getJSON<{ crosswalk: CrosswalkEntry[] }>("/studio/crosswalk"),
    async crosswalkDelete(key: string): Promise<void> {
      const res = await fetch(`${BASE}/studio/crosswalk/${encodeURIComponent(key)}`, { method: "DELETE" });
      if (!res.ok) throw new Error(await readError(res, "/studio/crosswalk"));
    },
    crosswalkClear: () => postJSON<{ ok: boolean }>("/studio/crosswalk/clear", {}),

    // ---- Data health, quarantine, audit ----
    dataHealth: () => getJSON<DataHealth>("/studio/data-health"),
    quarantine: (table?: string) =>
      getJSON<QuarantineResponse>(`/studio/quarantine${table ? `?table=${encodeURIComponent(table)}` : ""}`),
    quarantineReimport: (body: { ids?: string[]; edits?: Record<string, Record<string, unknown>> }) =>
      postJSON<StudioState & { ok: boolean; reimported: number; still_quarantined: number }>(
        "/studio/quarantine/reimport", body),
    quarantineDiscard: (body: { ids?: string[] }) =>
      postJSON<{ ok: boolean; discarded: number }>("/studio/quarantine/discard", body),
    audit: (limit = 100) => getJSON<{ audit: AuditEntry[] }>(`/studio/audit?limit=${limit}`),
  },

  // ---- Customer scoring ----
  scores: {
    list: (window = "all") => getJSON<ScoresResponse>(`/scores?window=${window}`),
    customer: (id: string, window = "all") =>
      getJSON<CustomerScoreResponse>(`/scores/customer/${encodeURIComponent(id)}?window=${window}`),
    quadrant: (window = "all") => getJSON<QuadrantResponse>(`/scores/quadrant?window=${window}`),
    backtest: () => getJSON<BacktestResponse>("/scores/backtest"),
    config: () => getJSON<{ config: Record<string, number | string>; windows: string[]; archetypes: string[] }>("/scores/config"),
    recompute: (overrides?: Record<string, number | string>) =>
      postJSON<{ ok: boolean; computed_at: string; windows: Record<string, number> }>(
        "/scores/recompute", { overrides: overrides ?? null }),
  },

  // ---- Credit & account risk (P9) ----
  credit: {
    get: (window = "all") => getJSON<CreditResponse>(`/credit?window=${window}`),
    customer: (id: string, window = "all") =>
      getJSON<{ window: string; as_of: string | null; customer: CreditRow & { credit: Record<string, unknown> } }>(
        `/credit/customer/${encodeURIComponent(id)}?window=${window}`),
    config: () =>
      getJSON<{ config: Record<string, number | string>; windows: string[]; quadrant_order: string[] }>(
        "/credit/config"),
    recompute: (overrides?: Record<string, number | string>) =>
      postJSON<{ ok: boolean; computed_at: string; windows: Record<string, number> }>(
        "/credit/recompute", { overrides: overrides ?? null }),
  },

  // ---- Reconciliation & loss control ----
  reconciliation: {
    get: (period = "month") => getJSON<Reconciliation>(`/reconciliation?period=${period}`),
    config: () =>
      getJSON<{ config: Record<string, number | string>; period_grains: string[] }>(
        "/reconciliation/config"),
  },

  // ---- Daily operating dashboard / regime / scorecards / playbook ----
  regimeConfig: () => getJSON<RegimeConfig>("/regime/config"),
  daily: (regime: Regime, terminal?: string | null, window = "all") => {
    const qs = new URLSearchParams({ window });
    if (terminal) qs.set("terminal", terminal);
    for (const [k, v] of Object.entries(regime)) qs.set(k, v);
    return getJSON<DailyResponse>(`/daily?${qs.toString()}`);
  },
  dailyPersist: (regime: Regime, window = "all") =>
    postJSON<{ ok: boolean; run_date: string; computed_at: string; terminals: string[]; rows_written: number }>(
      "/daily/persist", { regime, window }),
  scorecards: (regime: Regime, terminal?: string | null, window = "all") => {
    const qs = new URLSearchParams({ window });
    if (terminal) qs.set("terminal", terminal);
    for (const [k, v] of Object.entries(regime)) qs.set(k, v);
    return getJSON<ScorecardsResponse>(`/scorecards?${qs.toString()}`);
  },
  playbook: (terminal?: string | null, window = "all") => {
    const qs = new URLSearchParams({ window });
    if (terminal) qs.set("terminal", terminal);
    return getJSON<PlaybookResponse>(`/playbook?${qs.toString()}`);
  },

  // ---- Demand Cockpit (per-terminal operating forecast) ----
  demand: {
    cockpit: (opts: {
      terminal?: string | null;
      product?: string | null;
      window?: string;
      serviceLevel?: number;
      leadTimeDays?: number;
      lotSize?: number | null;
    }) => {
      const qs = new URLSearchParams({ window: opts.window ?? "all" });
      if (opts.terminal) qs.set("terminal", opts.terminal);
      if (opts.product) qs.set("product", opts.product);
      if (opts.serviceLevel != null) qs.set("service_level", String(opts.serviceLevel));
      if (opts.leadTimeDays != null) qs.set("lead_time_days", String(opts.leadTimeDays));
      if (opts.lotSize != null && opts.lotSize > 0) qs.set("lot_size", String(opts.lotSize));
      return getJSON<DemandCockpit>(`/demand/cockpit?${qs.toString()}`);
    },
    persist: (window = "all") =>
      postJSON<{ ok: boolean; computed_at: string; window: string; terminals: string[];
        products: string[]; customer_rows: number; terminal_rows: number }>(
        "/demand/persist", { window }),
    forecasts: (level: "terminal" | "customer" = "terminal", terminal?: string | null,
                product?: string | null) => {
      const qs = new URLSearchParams({ level });
      if (terminal) qs.set("terminal", terminal);
      if (product) qs.set("product", product);
      return getJSON<DemandForecastsResponse>(`/demand/forecasts?${qs.toString()}`);
    },
  },
};
