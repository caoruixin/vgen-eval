import type { AppConfig, Asset, Leaderboard, Ledger, ModelRow, Plan, Rating, RunDetail, RunRequest, RunSummary, Scenarios } from "./types";

declare global {
  interface Window {
    __VGEN__?: { token: string; mock: boolean; public?: boolean };
  }
}

const TOKEN = () => window.__VGEN__?.token ?? "";

/** Hosted demo: no server key; visitors generate with their own Tokenbay key. */
export const IS_PUBLIC = Boolean(window.__VGEN__?.public);

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

/** The launch token changes when the server restarts (e.g. a deploy); read the new one from the page. */
async function refreshToken(): Promise<boolean> {
  try {
    const html = await (await fetch("/", { cache: "no-store" })).text();
    const m = html.match(/window\.__VGEN__=(\{.*?\})<\/script>/);
    if (!m) return false;
    window.__VGEN__ = JSON.parse(m[1]);
    return true;
  } catch {
    return false;
  }
}

async function request<T>(method: string, path: string, body?: unknown, form?: FormData, key?: string, retry = true): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["x-vgen-token"] = TOKEN();
  if (body !== undefined) headers["content-type"] = "application/json";
  if (key) headers["x-tokenbay-key"] = key;
  const res = await fetch(path, { method, headers, body: form ?? (body !== undefined ? JSON.stringify(body) : undefined) });
  const text = await res.text();
  if (res.status === 403 && retry && method !== "GET" && text.includes("launch token") && (await refreshToken())) {
    return request<T>(method, path, body, form, key, false);
  }
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
  createRun: (req: RunRequest, key?: string) => request<RunDetail>("POST", "/api/runs", req, undefined, key),
  checkKey: (key: string) => request<{ ok: boolean }>("POST", "/api/key/check", {}, undefined, key),
  attachKey: (runId: string, key: string) => request<RunDetail>("POST", `/api/runs/${runId}/key`, {}, undefined, key),
  runs: (kind?: string) => request<RunSummary[]>("GET", `/api/runs${kind ? `?kind=${kind}` : ""}`),
  run: (id: string) => request<RunDetail>("GET", `/api/runs/${id}`),
  rerun: (id: string, confirm: boolean, key?: string) => request<RunDetail>("POST", `/api/runs/${id}/rerun`, { confirm }, undefined, key),
  deleteRun: (id: string) => request<{ ok: boolean }>("DELETE", `/api/runs/${id}`),
  stopJob: (id: string) => request("POST", `/api/jobs/${id}/stop`, {}),
  repollJob: (id: string, key?: string) => request("POST", `/api/jobs/${id}/repoll`, {}, undefined, key),
  resubmitJob: (id: string, confirm: boolean, key?: string) => request("POST", `/api/jobs/${id}/resubmit`, { confirm }, undefined, key),
  judgeJob: (id: string, key?: string) => request("POST", `/api/jobs/${id}/judge`, {}, undefined, key),
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
