import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, IS_ADMIN, IS_PUBLIC } from "../api";
import { useApiKey } from "../apiKey";
import { useI18n, type TKey } from "../i18n";
import type { ModelRow, Plan, RunRequest, Task } from "../types";
import { cls, isUnavailable, rawUnavailable, secs, usd } from "../util";
import { AdminOnly, ErrorBox, Modal, Spinner, VendorDot } from "./common";
import { KeyField } from "./KeyField";

export function useModels() {
  return useQuery({ queryKey: ["models"], queryFn: api.models, staleTime: 60_000 });
}

export function useConfig() {
  return useQuery({ queryKey: ["config"], queryFn: api.config, staleTime: 10_000 });
}

const VENDOR_ORDER = ["Google", "ByteDance", "Alibaba"];

export function ModelPicker({
  task,
  selected,
  onChange,
  allowExperimental,
}: {
  task: Task;
  selected: string[];
  onChange: (ids: string[]) => void;
  allowExperimental: boolean;
}) {
  const { t, tr } = useI18n();
  const { data } = useModels();
  const models = data?.models ?? [];
  const groups = VENDOR_ORDER.map((v) => [v, models.filter((m) => m.vendor === v)] as const).filter(([, ms]) => ms.length);

  const usable = (m: ModelRow) => {
    const s = m.task_states[task];
    return s === "enabled" || (s === "experimental" && allowExperimental);
  };

  // drop selections that became unusable (task change / experimental toggle)
  useEffect(() => {
    if (!models.length) return;
    const keep = selected.filter((id) => {
      const m = models.find((x) => x.id === id);
      return m && usable(m);
    });
    if (keep.length !== selected.length) onChange(keep);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task, allowExperimental, models.length]);

  return (
    <div className="space-y-3">
      {groups.map(([vendor, ms]) => (
        <div key={vendor}>
          <div className="mb-1.5 flex items-center gap-2 text-xs font-semibold text-ink-2">
            <VendorDot vendor={vendor} /> {vendor}
          </div>
          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 xl:grid-cols-3">
            {ms.map((m) => {
              const state = m.task_states[task];
              const ok = usable(m);
              const on = selected.includes(m.id);
              const live = m.available.com;
              return (
                <label
                  key={m.id}
                  title={rawUnavailable(m) ?? (isUnavailable(m) ? t("models.unavailable") : ok ? tr(m.notes) : t(`models.state.${state}` as TKey))}
                  className={cls(
                    "flex cursor-pointer items-start gap-2 rounded-lg border px-2.5 py-2 text-[13px]",
                    on ? "border-[var(--focus)] bg-surface-2" : "border-line",
                    !ok && "cursor-not-allowed opacity-45",
                  )}
                >
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    disabled={!ok}
                    checked={on}
                    onChange={() => onChange(on ? selected.filter((x) => x !== m.id) : [...selected, m.id])}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5 font-medium">
                      {m.label}
                      {IS_ADMIN && m.doc === "inferred" && <span className="chip !px-1.5 !text-[10px]">{t("models.doc.inferred")}</span>}
                      {state === "experimental" && <span className="chip !px-1.5 !text-[10px]" style={{ borderColor: "var(--warn)" }}>{t("setup.exp")}</span>}
                      {live === false && <span className="chip !px-1.5 !text-[10px]" style={{ borderColor: "var(--critical)" }}>{t("setup.offline")}</span>}
                    </span>
                    <span className="block text-xs text-muted">
                      {ok ? (
                        <>
                          {usd(m.price_ref.usd)} / {m.price_ref.settings} · ~{secs(m.latency_s)}
                          {m.audio === "optional" ? " · ♪" : ""}
                        </>
                      ) : isUnavailable(m) ? (
                        t("models.unavailable")
                      ) : (
                        t(`models.state.${state}` as TKey)
                      )}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

export interface Params {
  duration: number;
  resolution: string;
  ratio: string;
  audio: boolean;
  seed?: number | null;
  negative_prompt?: string;
}

const RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "auto"];
const RESOLUTIONS = ["480p", "720p", "1080p", "4k"];

export function ParamsForm({ value, onChange, task, compact }: { value: Params; onChange: (p: Params) => void; task: Task; compact?: boolean }) {
  const { t } = useI18n();
  const set = (patch: Partial<Params>) => onChange({ ...value, ...patch });
  const followsInput = ["i2v", "flf2v", "a2v", "v2v_edit"].includes(task);
  return (
    <div className={cls("grid gap-3", compact ? "grid-cols-2 sm:grid-cols-4" : "grid-cols-2 sm:grid-cols-3 lg:grid-cols-6")}>
      <label className="space-y-1">
        <span className="label">
          {t("setup.duration")} · <span className="tnum">{value.duration}s</span>
        </span>
        <input type="range" min={2} max={15} value={value.duration} onChange={(e) => set({ duration: Number(e.target.value) })} className="w-full" />
      </label>
      <label className="space-y-1">
        <span className="label">{t("setup.resolution")}</span>
        <select className="input" value={value.resolution} onChange={(e) => set({ resolution: e.target.value })}>
          {RESOLUTIONS.map((r) => (
            <option key={r}>{r}</option>
          ))}
        </select>
      </label>
      <label className="space-y-1">
        <span className="label">{t("setup.ratio")}</span>
        <select className="input" value={value.ratio} onChange={(e) => set({ ratio: e.target.value })}>
          {RATIOS.filter((r) => r !== "auto" || followsInput).map((r) => (
            <option key={r} value={r}>
              {r === "auto" ? t("setup.auto") : r}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-end gap-2 pb-1.5">
        <input type="checkbox" checked={value.audio} onChange={(e) => set({ audio: e.target.checked })} />
        <span className="text-[13px]">{t("setup.audio")}</span>
      </label>
      {!compact && (
        <>
          <label className="space-y-1">
            <span className="label">{t("setup.seed")}</span>
            <input
              className="input"
              type="number"
              value={value.seed ?? ""}
              placeholder="random"
              onChange={(e) => set({ seed: e.target.value === "" ? null : Number(e.target.value) })}
            />
          </label>
          <label className="space-y-1">
            <span className="label">{t("setup.negative")}</span>
            <input className="input" value={value.negative_prompt ?? ""} onChange={(e) => set({ negative_prompt: e.target.value })} />
          </label>
        </>
      )}
    </div>
  );
}

export function PlanDialog({ request, open, onClose }: { request: RunRequest | null; open: boolean; onClose: () => void }) {
  const { t } = useI18n();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { data: cfg } = useConfig();
  const { data: models } = useModels();
  const [plan, setPlan] = useState<Plan | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const stored = useApiKey();
  const [key, setKey] = useState(stored.key);
  const [remember, setRemember] = useState(stored.remember);
  const needsKey = IS_PUBLIC && !key;

  useEffect(() => {
    if (!open || !request) return;
    setPlan(null);
    setErr(null);
    api.plan(request).then(setPlan, setErr);
  }, [open, request]);

  const label = (id: string) => models?.models.find((m) => m.id === id)?.label ?? id;
  const vendor = (id: string) => models?.models.find((m) => m.id === id)?.vendor ?? "";
  const byModel = useMemo(() => {
    const out = new Map<string, Plan["items"]>();
    for (const it of plan?.items ?? []) out.set(it.model, [...(out.get(it.model) ?? []), it]);
    return [...out.entries()];
  }, [plan]);
  const runnable = plan?.runnable ?? 0;

  const submit = async () => {
    if (!request) return;
    setBusy(true);
    setErr(null);
    try {
      if (IS_PUBLIC) stored.save(key, remember);
      const detail = await api.createRun({ ...request, confirm: true, allow_unknown_price: true }, IS_PUBLIC ? key : undefined);
      await qc.invalidateQueries({ queryKey: ["runs"] });
      await qc.invalidateQueries({ queryKey: ["config"] });
      onClose();
      nav(`/runs/${detail.run.id}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setErr(new Error(((e.body as { reasons?: string[] })?.reasons ?? []).join("; ")));
      else setErr(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title={t("plan.title")} wide>
      {!plan && !err && (
        <div className="flex items-center gap-2 text-sm text-muted">
          <Spinner /> {t("common.loading")}
        </div>
      )}
      <ErrorBox error={err} />
      {plan && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <div className="label">{t("plan.total")}</div>
              <div className="text-3xl font-semibold">
                {usd(plan.total_usd)}
                {cfg && <span className="ml-2 text-base font-normal text-muted">≈ ¥{(plan.total_usd * cfg.usd_cny).toFixed(2)}</span>}
              </div>
              <div className="text-xs text-muted">
                {t("plan.clips", { n: runnable })} · {t("plan.worst")}
              </div>
            </div>
            <div className="text-right text-xs text-ink-2">
              {IS_PUBLIC ? (
                <div>{t("plan.billed_to_key")}</div>
              ) : (
                <div>
                  {usd(plan.budget.spent_usd)} / {usd(plan.budget.budget_usd, 0)}
                </div>
              )}
              {plan.budget.confirm_reasons.map((r) => (
                <div key={r} style={{ color: "var(--serious)" }}>
                  ⚠ {r}
                </div>
              ))}
              {plan.unknown_prices > 0 && <div style={{ color: "var(--serious)" }}>⚠ {t("plan.unknown_price")}</div>}
              {plan.budget.hard_cap_blocked && <div style={{ color: "var(--critical)" }}>✕ {t("plan.hard_cap")}</div>}
            </div>
          </div>
          <div className="divide-y divide-[var(--line)] rounded-lg border border-line">
            {byModel.map(([model, items]) => {
              const ok = items.filter((i) => i.ok);
              const sum = ok.reduce((s, i) => s + (i.estimate?.usd ?? 0) + (i.judge_estimate_usd ?? 0), 0);
              const adj = new Map<string, string>();
              for (const i of ok) for (const a of i.adjustments ?? []) adj.set(`${a.field}`, `${a.field}: ${a.requested ?? ""} → ${a.used ?? ""} (${a.reason})`);
              const warns = new Set(ok.flatMap((i) => i.warnings ?? []));
              const blocked = items.filter((i) => !i.ok);
              return (
                <div key={model} className={cls("grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 px-3 py-2.5 text-[13px]", !ok.length && "opacity-60")}>
                  <div className="flex items-center gap-2 font-medium">
                    <VendorDot vendor={vendor(model)} /> {label(model)}
                    <span className="text-xs font-normal text-muted">× {ok.length}</span>
                  </div>
                  <div className="tnum text-right font-medium">{ok.length ? usd(sum) : "—"}</div>
                  <div className="col-span-2 space-y-0.5 text-xs text-ink-2">
                    {ok[0]?.estimate && <div className="text-muted">{ok[0].estimate.basis}</div>}
                    {[...adj.values()].map((a) => (
                      <div key={a}>↳ {t("plan.adjust")}: {a}</div>
                    ))}
                    {[...warns].map((w) => (
                      <div key={w} style={{ color: "var(--serious)" }}>
                        ⚠ {w}
                      </div>
                    ))}
                    {blocked.length > 0 && (
                      <div style={{ color: "var(--critical)" }}>
                        ✕ {t("plan.blocked")}: {[...new Set(blocked.map((b) => b.reason))].join("; ")}
                      </div>
                    )}
                    {ok[0]?.payload && (
                      <AdminOnly>
                        <details>
                          <summary className="cursor-pointer text-muted">{t("plan.payload")}</summary>
                          <pre className="json mt-1">{JSON.stringify(ok[0].payload, null, 2)}</pre>
                        </details>
                      </AdminOnly>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          {IS_PUBLIC && (
            <div className="rounded-lg border border-line p-3">
              <KeyField
                value={key}
                onChange={setKey}
                remember={remember}
                onRemember={setRemember}
                onForget={() => {
                  stored.forget();
                  setKey("");
                  setRemember(false);
                }}
              />
            </div>
          )}
          <div className="flex justify-end gap-2">
            <button className="btn" onClick={onClose}>
              {t("common.cancel")}
            </button>
            <button className="btn btn-primary" disabled={busy || runnable === 0 || plan.budget.hard_cap_blocked || needsKey} onClick={submit}>
              {busy ? <Spinner className="text-surface" /> : null}
              {t("plan.confirm")} · {usd(plan.total_usd)}
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
