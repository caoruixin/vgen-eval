import type { AppConfig, Asset, Leaderboard, Ledger, ModelRow, Plan, Rating, RunDetail, RunRequest, RunSummary, Scenarios } from "./types";

declare global {
  interface Window {
    __VGEN__?: { token: string; mock: boolean };
  }
}

const TOKEN = () => window.__VGEN__?.token ?? "";

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(method: string, path: string, body?: unknown, form?: FormData): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["x-vgen-token"] = TOKEN();
  if (body !== undefined) headers["content-type"] = "application/json";
  const res = await fetch(path, { method, headers, body: form ?? (body !== undefined ? JSON.stringify(body) : undefined) });
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown })?.detail;
    const msg = typeof detail === "string" ? detail : res.statusText || `HTTP ${res.status}`;
    throw new ApiError(res.status, msg, data);
  }
  return data as T;
}

export const api = {
  config: () => request<AppConfig>("GET", "/api/config"),
  models: () => request<{ models: ModelRow[]; unregistered: Record<string, string[]>; live_checked_at: number | null }>("GET", "/api/models"),
  refreshModels: () => request<{ ok: boolean }>("POST", "/api/models/refresh", {}),
  scenarios: () => request<Scenarios>("GET", "/api/scenarios"),
  assets: () => request<Asset[]>("GET", "/api/assets"),
  uploadAsset: (file: File, label: string, publicUrl: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("label", label);
    fd.append("public_url", publicUrl);
    return request<Asset>("POST", "/api/assets", undefined, fd);
  },
  assetFromUrl: (url: string, label: string) => request<Asset>("POST", "/api/assets/url", { url, label }),
  plan: (req: RunRequest) => request<Plan>("POST", "/api/plan", req),
  createRun: (req: RunRequest) => request<RunDetail>("POST", "/api/runs", req),
  runs: (kind?: string) => request<RunSummary[]>("GET", `/api/runs${kind ? `?kind=${kind}` : ""}`),
  run: (id: string) => request<RunDetail>("GET", `/api/runs/${id}`),
  rerun: (id: string, confirm: boolean) => request<RunDetail>("POST", `/api/runs/${id}/rerun`, { confirm }),
  deleteRun: (id: string) => request<{ ok: boolean }>("DELETE", `/api/runs/${id}`),
  stopJob: (id: string) => request("POST", `/api/jobs/${id}/stop`, {}),
  repollJob: (id: string) => request("POST", `/api/jobs/${id}/repoll`, {}),
  resubmitJob: (id: string, confirm: boolean) => request("POST", `/api/jobs/${id}/resubmit`, { confirm }),
  judgeJob: (id: string) => request("POST", `/api/jobs/${id}/judge`, {}),
  rate: (id: string, patch: Partial<Rating>) => request<Rating>("PUT", `/api/jobs/${id}/rating`, patch),
  pick: (runId: string, promptKey: string, winner: string) =>
    request("PUT", `/api/runs/${runId}/pick`, { prompt_key: promptKey, winner_job_id: winner }),
  leaderboard: (params: Record<string, string | number | boolean | undefined>) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "" && v !== false) q.set(k, String(v));
    return request<Leaderboard>("GET", `/api/leaderboard?${q.toString()}`);
  },
  ledger: () => request<Ledger>("GET", "/api/ledger"),
  updateSettings: (patch: Record<string, unknown>) => request<AppConfig>("PUT", "/api/settings", patch),
  probe: () => request<{ running: boolean; log: string[]; cases: { id: string; model: string; task: string }[] }>("GET", "/api/probe"),
  startProbe: (cases: string[], confirm: boolean, budget = 3) =>
    request<{ started: boolean; total: number; cases: string[]; log: string[] }>("POST", "/api/probe", { cases, confirm, budget }),
};
