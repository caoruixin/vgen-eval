import type { EvaluationSet, EvaluationDetail, BackfillPlan, BenchmarkSummary, BenchmarkDetail, Deployment } from "./types";
import type { AdminJobFields, AppConfig, Asset, Job, Leaderboard, Ledger, ModelRow, Plan, Rating, RunDetail, RunRequest, RunSummary, Scenarios } from "./types";

declare global {
  interface Window {
    __VGEN__?: { token: string; mock: boolean; public?: boolean; admin?: boolean };
  }
}

const TOKEN = () => window.__VGEN__?.token ?? "";

/** Hosted demo: no server key; visitors generate with their own Tokenbay key. */
export const IS_PUBLIC = Boolean(window.__VGEN__?.public);

/** May see diagnostics: everyone on the local instance; on the hosted site only after admin sign-in. Fails closed:
 *  a built page without the boot payload is not admin. Only the Vite dev server, which never injects the payload and
 *  proxies to the local operator instance, falls back to admin. */
export const IS_ADMIN = window.__VGEN__ ? (window.__VGEN__.admin ?? !IS_PUBLIC) : import.meta.env.DEV;

/** The job's raw diagnostics, or null when this viewer is not an admin or the server left them out (e.g. an expired session). */
export function adminFields<J extends Job>(job: J): (J & AdminJobFields) | null {
  return IS_ADMIN && "task_id" in job ? (job as J & AdminJobFields) : null;
}

/** There is no error boundary: give the objects the UI reads unguarded a safe default. */
export function normalizeJob<J extends Job>(job: J): J {
  return {
    ...job,
    provenance: job.provenance ?? {},
    spec: job.spec ?? {},
    effective: job.effective ?? {},
    adjustments: job.adjustments ?? [],
    media: job.media ?? {},
    cost: job.cost ?? { estimate_usd: null, actual_usd: null, per_second: null },
  };
}

const normalizeRun = (d: RunDetail): RunDetail => ({ ...d, jobs: (d.jobs ?? []).map(normalizeJob), ratings: d.ratings ?? {}, picks: d.picks ?? {} });
const normalizeEvaluation = (d: EvaluationDetail): EvaluationDetail => ({
  ...d, groups: (d.groups ?? []).map((g) => ({ ...g, samples: (g.samples ?? []).map(normalizeJob) })), coverage: d.coverage ?? [],
});
/** Visitors get a reduced benchmark; keep every collection the page maps over defined. */
function normalizeBenchmark(d: BenchmarkDetail): BenchmarkDetail {
  const s: Partial<BenchmarkDetail["summary"]> = d.summary ?? {};
  return {
    ...d, config: d.config ?? {}, deployments: d.deployments ?? [], artifacts: d.artifacts ?? {},
    attempts: (d.attempts ?? []).map((a) => ({ ...a, metrics: a.metrics ?? {} })),
    summary: { groups: s.groups ?? [], phases: s.phases ?? [], scenes: s.scenes ?? [], telemetry: s.telemetry ?? {}, costs: s.costs ?? {}, utilization: s.utilization ?? [] },
  };
}

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
  evaluations: () => request<EvaluationSet[]>("GET", "/api/evaluations"),
  evaluation: (dataset: string, key: string) =>
    request<EvaluationDetail>("GET", `/api/evaluations/${encodeURIComponent(dataset)}/cases/${encodeURIComponent(key)}`).then(normalizeEvaluation),
  backfill: (dataset: string) => request<BackfillPlan>("GET", `/api/evaluations/${encodeURIComponent(dataset)}/backfill-plan`),
  benchmarks: () => request<{ experiments: BenchmarkSummary[]; deployments: Deployment[] }>("GET", "/api/benchmarks"),
  benchmark: (id: string) => request<BenchmarkDetail>("GET", `/api/benchmarks/${encodeURIComponent(id)}`).then(normalizeBenchmark),
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
  createRun: (req: RunRequest, key?: string) => request<RunDetail>("POST", "/api/runs", req, undefined, key).then(normalizeRun),
  checkKey: (key: string) => request<{ ok: boolean }>("POST", "/api/key/check", {}, undefined, key),
  attachKey: (runId: string, key: string) => request<RunDetail>("POST", `/api/runs/${runId}/key`, {}, undefined, key).then(normalizeRun),
  runs: (kind?: string) => request<RunSummary[]>("GET", `/api/runs${kind ? `?kind=${kind}` : ""}`),
  run: (id: string) => request<RunDetail>("GET", `/api/runs/${id}`).then(normalizeRun),
  rerun: (id: string, confirm: boolean, key?: string) => request<RunDetail>("POST", `/api/runs/${id}/rerun`, { confirm }, undefined, key).then(normalizeRun),
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

/** 200 sets an HttpOnly session cookie; 401 wrong secret, 429 throttled, 404 sign-in not enabled. Reload afterwards. */
export const adminLogin = (secret: string) => request<{ ok: boolean }>("POST", "/api/admin/login", { secret });
export const adminLogout = () => request<{ ok: boolean }>("POST", "/api/admin/logout", {});
