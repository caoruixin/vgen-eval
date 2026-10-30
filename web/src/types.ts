export type Lang = "en" | "zh";
export type L10n = { en?: string; zh?: string };
export type Task = "t2v" | "i2v" | "flf2v" | "r2v" | "v2v_edit" | "extend" | "a2v";

export interface AppConfig {
  mock: boolean;
  endpoints: { key: string; name: string; rpm: number; used_last_minute: number; disabled_reason: string | null; last_error: string | null }[];
  budget_usd: number;
  hard_cap_usd: number | null;
  confirm_above_usd: number;
  spent_usd: number;
  usd_cny: number;
  judge_model: string;
  max_inflight: number;
  judge_models: string[];
  tasks: Task[];
  probe_updated_at: string | null;
  quota_per_usd: number;
  quota_calibrated: boolean;
  quota_note: string | null;
  live_checked_at: number | null;
  live_error: string | null;
}

export interface ModelRow {
  id: string;
  family: string;
  vendor: string;
  label: string;
  endpoints: string[];
  doc: string;
  tasks: Task[];
  experimental_tasks: Task[];
  durations: { min: number; max: number } | number[] | null;
  by_resolution: Record<string, number[]>;
  resolutions: string[];
  ratios: string[];
  audio: string;
  max_refs: number;
  latency_s: number;
  notes: L10n;
  probe: { tasks?: Record<string, string>; delivery?: Record<string, Record<string, string>>; cases?: Record<string, ProbeCase>; unavailable?: string; notes?: string[] };
  task_states: Record<Task, "enabled" | "experimental" | "probe_failed" | "unsupported">;
  delivery_effective: Record<string, Record<string, string>>;
  available: Record<string, boolean | null>;
  price: Record<string, unknown> | null;
  price_ref: { settings: string; usd: number | null; basis: string; per_second: number | null };
}

export interface ProbeCase {
  case: string;
  outcome: string;
  status: string;
  fail_reason: string;
  duration: number | null;
  size: string | null;
  fps: number | null;
  codec: string | null;
  audio: boolean;
  quota: number | null;
  latency_s: number | null;
  failed_checks: string[];
  ts: string;
}

export interface PackPrompt {
  key: string;
  title: L10n;
  text: L10n;
  checklist: { en: string[]; zh: string[] };
  assets: Record<string, string | string[]>;
  missing_assets: string[];
  prefer_lang?: Lang;
}

export interface Pack {
  id: string;
  category: string;
  task: Task;
  title: L10n;
  description: L10n;
  params: Record<string, unknown>;
  models: string[];
  model_overrides?: Record<string, Record<string, unknown>>;
  look_for: { en: string[]; zh: string[] };
  prompts: PackPrompt[];
  assets: Record<string, string | string[]>;
  missing_assets: string[];
}

export interface ScenarioAsset {
  name: string;
  id: string | null;
  label: L10n;
  generated: boolean;
  kind: string | null;
  public_urls: string[];
  width: number | null;
  height: number | null;
  duration: number | null;
}

export interface Scenarios {
  categories: Record<string, L10n>;
  packs: Pack[];
  assets: Record<string, ScenarioAsset>;
}

export interface Asset {
  id: string;
  kind: "image" | "video" | "audio";
  label: string;
  filename: string;
  mime: string;
  size: number;
  width: number | null;
  height: number | null;
  duration: number | null;
  public_urls: string[];
  source: string;
  url: string;
}

export interface PromptIn {
  key: string;
  title?: string;
  text: string;
  checklist: string[];
  assets?: Record<string, string | string[]>;
}

export interface RunRequest {
  title?: string;
  kind?: string;
  scenario_id?: string | null;
  category?: string | null;
  task: Task;
  lang: Lang;
  endpoint?: string;
  prompts: PromptIn[];
  models: string[];
  params: Record<string, unknown>;
  model_overrides?: Record<string, Record<string, unknown>>;
  assets?: Record<string, string | string[]>;
  judge: boolean;
  allow_experimental?: boolean;
  allow_unknown_price?: boolean;
  confirm?: boolean;
}

export interface Adjustment {
  field: string;
  requested: unknown;
  used: unknown;
  reason: string;
}

export interface PlanItem {
  prompt_key: string;
  model: string;
  ok: boolean;
  reason?: string;
  payload?: Record<string, unknown>;
  adjustments?: Adjustment[];
  warnings?: string[];
  effective?: Record<string, unknown>;
  estimate?: { usd: number | null; basis: string; per_second: number | null };
  judge_estimate_usd?: number | null;
  task_state?: string;
}

export interface Plan {
  items: PlanItem[];
  total_usd: number;
  unknown_prices: number;
  runnable: number;
  budget: { spent_usd: number; budget_usd: number; confirm_reasons: string[]; hard_cap_usd: number | null; hard_cap_blocked: boolean };
}

export interface ComplianceItem {
  key: string;
  ok: boolean | null;
  expected: unknown;
  actual: unknown;
  note: string;
}

export interface JudgeResult {
  checklist?: { item: string; pass: boolean; evidence: string; t?: number | null }[];
  defects?: { type: string; severity: "minor" | "major" | "critical"; t?: number | null; description: string }[];
  verdict?: string;
  adherence?: number | null;
  passed?: number;
  total?: number;
  defect_score?: number;
  usage?: { input_tokens: number; output_tokens: number; usd: number | null };
  model?: string;
  error?: string;
  cached?: boolean;
}

export interface Job {
  id: string;
  run_id: string;
  prompt_key: string;
  model: string;
  model_label: string;
  family: string;
  vendor: string;
  endpoint: string;
  label: string;
  status: string;
  spec: Record<string, unknown>;
  payload: Record<string, unknown>;
  adjustments: Adjustment[];
  effective: Record<string, unknown>;
  est_cost_usd: number;
  task_id: string | null;
  progress: string;
  remote_status: string;
  submit_response: Record<string, unknown>;
  last_detail: Record<string, unknown>;
  fail_reason: string;
  error_code: string;
  quota: number | null;
  created_at: number;
  submitted_at: number | null;
  gw_submit_time: number | null;
  gw_start_time: number | null;
  gw_finish_time: number | null;
  done_at: number | null;
  media: {
    duration?: number | null;
    size_bytes?: number;
    video?: { codec: string; width: number; height: number; fps: number | null };
    audio?: { codec: string; sample_rate: number };
  };
  compliance: ComplianceItem[];
  signals: {
    motion_pct_per_s?: number;
    motion_timeline?: (number | null)[];
    frozen_ratio?: number;
    frozen_flag?: boolean;
    scene_cuts?: number[];
    audio?: { has_audio: boolean; rms_dbfs?: number | null; silence_ratio?: number };
    error?: string;
  };
  fidelity: { first?: { score: number | null; grade: string; t: number | null }; last?: { score: number | null; grade: string; t: number | null } };
  judge: JudgeResult;
  judge_status: string;
  video_url: string | null;
  poster_url: string | null;
  strip_url: string | null;
  latency_s: number | null;
  cost: { estimate_usd: number; quota: number | null; actual_usd: number | null; per_second: number | null };
}

export interface Rating {
  job_id: string;
  usable: boolean | null;
  overall: number | null;
  defects: string[];
  comment: string;
}

export interface RunDetail {
  run: {
    id: string;
    created_at: number;
    title: string;
    kind: string;
    scenario_id: string | null;
    category: string | null;
    task: Task;
    lang: Lang;
    params: Record<string, unknown>;
    prompts: PromptIn[];
    models: string[];
    judge: boolean;
    est_cost_usd: number;
  };
  jobs: Job[];
  ratings: Record<string, Rating>;
  picks: Record<string, string>;
  active: number;
  spent_usd: number;
}

export interface RunSummary {
  id: string;
  created_at: number;
  title: string;
  kind: string;
  scenario_id: string | null;
  category: string | null;
  task: Task;
  lang: Lang;
  judge: boolean;
  est_cost_usd: number;
  models: string[];
  n_prompts: number;
  n_jobs: number;
  counts: Record<string, number>;
  active: number;
  posters: string[];
  spent_usd: number;
}

export interface LeaderRow {
  model: string;
  label: string;
  family: string;
  vendor: string;
  eligible: boolean;
  why_not: string[];
  n_attempts: number;
  n_success: number;
  success_rate: number | null;
  success_ci: [number, number] | null;
  latency_median: number | null;
  latency_n: number;
  cost_median: number | null;
  cost_per_s: number | null;
  cost_source: string;
  compliance_rate: number | null;
  judge_adherence: number | null;
  judge_n: number;
  defect_mean: number | null;
  usable_rate: number | null;
  usable_ci: [number, number] | null;
  rated_n: number;
  overall_mean: number | null;
  wins: number;
  appearances: number;
  win_rate: number | null;
  win_ci: [number, number] | null;
  quality: number | null;
  quality_source: "human" | "judge" | null;
  value: number | null;
  badges: string[];
  pareto: boolean;
}

export interface Leaderboard {
  models: LeaderRow[];
  reasons: { badge: string; model: string; text: string }[];
  agreement: { n: number; tau: number | null; reliable: boolean };
  rank_by: string;
  runs: number;
  clips: number;
}

export interface Ledger {
  spent_usd: number;
  by_kind: Record<string, number>;
  by_model: Record<string, number>;
  entries: { id: string; ts: number; kind: string; model: string; est_usd: number; actual_usd: number | null; quota: number | null; run_id: string | null; job_id: string | null }[];
}
