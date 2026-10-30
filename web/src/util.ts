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
