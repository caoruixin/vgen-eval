import { useEffect, type ReactNode } from "react";
import { useI18n, type TKey } from "../i18n";
import type { Task } from "../types";
import { cls, vendorColor } from "../util";

export function VendorDot({ vendor, size = 8 }: { vendor: string; size?: number }) {
  return <span className="vendor-dot" style={{ background: vendorColor(vendor), width: size, height: size }} aria-hidden />;
}

export function TaskBadge({ task }: { task: Task }) {
  const { t } = useI18n();
  return (
    <span className="chip font-semibold tracking-wide" title={t(`task.${task}` as TKey)}>
      {t(`task.short.${task}` as TKey)}
    </span>
  );
}

const STATUS_STYLE: Record<string, { color: string; icon: string }> = {
  pending: { color: "var(--muted)", icon: "○" },
  submitting: { color: "var(--focus)", icon: "↑" },
  queued: { color: "var(--muted)", icon: "◔" },
  in_progress: { color: "var(--focus)", icon: "◑" },
  downloading: { color: "var(--focus)", icon: "↓" },
  analyzing: { color: "var(--focus)", icon: "◕" },
  succeeded: { color: "var(--good)", icon: "✓" },
  failed: { color: "var(--critical)", icon: "✕" },
  submit_unknown: { color: "var(--serious)", icon: "?" },
  timeout: { color: "var(--serious)", icon: "⏱" },
  stopped: { color: "var(--muted)", icon: "■" },
  blocked: { color: "var(--muted)", icon: "–" },
};

export function StatusBadge({ status, progress }: { status: string; progress?: string }) {
  const { t } = useI18n();
  const s = STATUS_STYLE[status] ?? STATUS_STYLE.pending;
  return (
    <span className="chip" style={{ borderColor: s.color }}>
      <span style={{ color: s.color }} aria-hidden>
        {s.icon}
      </span>
      <span className="text-ink">{t(`status.${status}` as TKey)}</span>
      {progress && status === "in_progress" ? <span className="tnum text-muted">{progress}</span> : null}
    </span>
  );
}

/** Status chip for pass / fail / info — always icon + label, never colour alone. */
export function Check({ ok, label, title }: { ok: boolean | null | undefined; label: string; title?: string }) {
  const color = ok === true ? "var(--good)" : ok === false ? "var(--critical)" : "var(--muted)";
  const icon = ok === true ? "✓" : ok === false ? "✕" : "·";
  return (
    <span className="chip" title={title} style={{ borderColor: ok === false ? "var(--critical)" : undefined }}>
      <span style={{ color }} aria-hidden>
        {icon}
      </span>
      <span className="text-ink-2">{label}</span>
    </span>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg className={cls("h-4 w-4 animate-spin text-muted", className)} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  const { t } = useI18n();
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="card border-l-4 p-3 text-sm" style={{ borderLeftColor: "var(--critical)" }}>
      <strong>{t("common.error")}:</strong> {msg}
    </div>
  );
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  const { t } = useI18n();
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 sm:p-10" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cls("card w-full shadow-2xl", wide ? "max-w-5xl" : "max-w-2xl")}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <h2 className="text-[15px] font-semibold">{title}</h2>
          <button className="btn btn-ghost" onClick={onClose} aria-label={t("common.close")}>
            ✕
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

export function Meter({ value, color = "var(--ink-2)", width = 64 }: { value: number | null | undefined; color?: string; width?: number }) {
  if (value === null || value === undefined) return <span className="text-muted">—</span>;
  return (
    <span className="relative inline-block h-1.5 overflow-hidden rounded-full bg-surface-3 align-middle" style={{ width }}>
      <span className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${Math.max(2, Math.min(1, value) * 100)}%`, background: color }} />
    </span>
  );
}

export function Section({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="label uppercase tracking-wide">{title}</h3>
        {right}
      </div>
      {children}
    </section>
  );
}
