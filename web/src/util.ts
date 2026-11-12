import { IS_ADMIN } from "./api";
import type { FailureCategory, Hardware, Job, ModelRow } from "./types";

export function usd(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (v > 0 && v < 0.01) return `$${v.toFixed(3)}`;
  return `$${v.toFixed(digits)}`;
}

export function cny(v: number | null | undefined, fx: number): string {
  if (v === null || v === undefined) return "—";
  return `¥${(v * fx).toFixed(2)}`;
}

export function pct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export function secs(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (v >= 120) return `${Math.floor(v / 60)}m${String(Math.round(v % 60)).padStart(2, "0")}s`;
  return `${Math.round(v)}s`;
}

export function ago(ts: number, lang: "en" | "zh"): string {
  const d = Date.now() / 1000 - ts;
  const fmt = (n: number, u: string, uz: string) => (lang === "zh" ? `${n} ${uz}前` : `${n}${u} ago`);
  if (d < 60) return lang === "zh" ? "刚刚" : "just now";
  if (d < 3600) return fmt(Math.floor(d / 60), "m", "分钟");
  if (d < 86400) return fmt(Math.floor(d / 3600), "h", "小时");
  return new Date(ts * 1000).toLocaleString(lang === "zh" ? "zh-CN" : "en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export const VENDOR_COLOR: Record<string, string> = {
  Google: "var(--v-google)",
  ByteDance: "var(--v-bytedance)",
  Alibaba: "var(--v-alibaba)",
};

export function vendorColor(vendor: string): string {
  return VENDOR_COLOR[vendor] ?? "var(--muted)";
}

export const TERMINAL = new Set(["succeeded", "failed", "submit_unknown", "timeout", "stopped", "blocked"]);

export function cls(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export const isUnavailable = (m: ModelRow | undefined): boolean => Boolean(m?.probe?.unavailable);

/** The raw probe text behind "unavailable": admins only, and only when the server sent the text. */
export function rawUnavailable(m: ModelRow | undefined): string | null {
  const u = m?.probe?.unavailable;
  return IS_ADMIN && typeof u === "string" && u ? u : null;
}

const FAILURE_CATEGORIES = new Set<FailureCategory>([
  "content_policy", "insufficient_balance", "auth", "rate_limited", "provider_error", "timeout", "invalid_request",
  "unsupported", "download_failed", "stopped", "network", "internal", "unknown",
]);
const STATUS_FAILURE: Record<string, FailureCategory> = { failed: "unknown", timeout: "timeout", stopped: "stopped", blocked: "unsupported", submit_unknown: "unknown" };

/** Why a finished job has no clip; null while it is running or after success. Unknown server categories read as "unknown". */
export function failureCategory(job: Pick<Job, "status" | "failure">): FailureCategory | null {
  if (job.status === "succeeded" || !TERMINAL.has(job.status)) return null;
  const c = job.failure?.category;
  if (c) return FAILURE_CATEGORIES.has(c as FailureCategory) ? (c as FailureCategory) : "unknown";
  return STATUS_FAILURE[job.status] ?? "unknown";
}

/** A deployment's free-form hardware record, reduced to the fields the UI labels. */
export function asHardware(h: Record<string, unknown> | null | undefined): Hardware | null {
  if (!h) return null;
  const s = (v: unknown) => (typeof v === "string" && v ? v : null);
  const n = (v: unknown) => (typeof v === "number" ? v : null);
  return { gpu: s(h.gpu), gpu_count: n(h.gpu_count), memory_gb_per_gpu: n(h.memory_gb_per_gpu) };
}
