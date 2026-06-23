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
    validate: (body: { upload_id: string; table: string; mapping: Record<string, string> }) =>
      postJSON<ValidateResponse>("/studio/validate", body),
    commit: (body: {
      upload_id: string;
      table: string;
      mapping: Record<string, string>;
      mode: string;
      save_profile?: string | null;
    }) => postJSON<CommitResponse>("/studio/commit", body),
    profiles: () => getJSON<{ profiles: SavedProfile[] }>("/studio/profiles"),
    saveProfile: (body: { name: string; table: string; mapping: Record<string, string>; source_columns: string[] }) =>
      postJSON<{ ok: boolean; name: string }>("/studio/profiles", body),
    async deleteProfile(name: string): Promise<void> {
      const res = await fetch(`${BASE}/studio/profiles/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (!res.ok) throw new Error(await readError(res, "/studio/profiles"));
    },
    history: () => getJSON<{ imports: ImportLogEntry[] }>("/studio/history"),
    loadDemo: (profile: string) => postJSON<StudioState & { ok: boolean }>("/studio/load-demo", { profile }),
    reset: () => postJSON<StudioState & { ok: boolean }>("/studio/reset", {}),
  },
};
