import { Fragment, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, IS_ADMIN, IS_PUBLIC } from "../api";
import { useI18n, type TKey } from "../i18n";
import type { ModelRow, Task } from "../types";
import { cls, isUnavailable, rawUnavailable, secs, usd } from "../util";
import { AdminOnly, ErrorBox, Spinner, VendorDot } from "../components/common";
import { useModels } from "../components/RunSetup";

const TASKS: Task[] = ["t2v", "i2v", "flf2v", "r2v", "v2v_edit", "extend", "a2v"];

const CELL: Record<string, { icon: string; color: string }> = {
  enabled: { icon: "✓", color: "var(--good)" },
  experimental: { icon: "?", color: "var(--warn)" },
  probe_failed: { icon: "✕", color: "var(--critical)" },
  unsupported: { icon: "", color: "var(--muted)" },
};

const VALUE_LABEL: Record<string, { en: string; zh: string }> = {
  always: { en: "always", zh: "总是生成" },
  optional: { en: "optional", zh: "可选" },
  none: { en: "none", zh: "无" },
  unknown: { en: "unknown", zh: "未知" },
  yes: { en: "yes", zh: "支持" },
  no: { en: "no", zh: "不支持" },
};

function durations(m: ModelRow): string {
  const d = m.durations;
  if (Array.isArray(d)) return `${d.join("/")}s`;
  if (d && typeof d === "object") return `${d.min}–${d.max}s`;
  return "—";
}

export default function ModelsPage() {
  const { t, tr, lang } = useI18n();
  const V = (v: string | undefined) => (v ? (VALUE_LABEL[v]?.[lang] ?? v) : "—");
  const qc = useQueryClient();
  const { data, error, isLoading } = useModels();
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [offer, setOffer] = useState<{ model: string; cases: string[]; total: number } | null>(null);
  const { data: probe } = useQuery({
    queryKey: ["probe"],
    queryFn: api.probe,
    enabled: IS_ADMIN,
    refetchInterval: (q) => (q.state.data?.running ? 4000 : false),
  });
  const recheck = async (m: ModelRow) => {
    const ids = (probe?.cases ?? []).filter((c) => c.model === m.id).map((c) => c.id);
    if (!ids.length) return;
    const r = await api.startProbe(ids, false);
    setOffer({ model: m.id, cases: r.cases, total: r.total });
  };
  const confirmRecheck = async () => {
    if (!offer) return;
    await api.startProbe(offer.cases, true);
    setOffer(null);
    await qc.invalidateQueries({ queryKey: ["probe"] });
  };
  if (isLoading) return <Spinner />;
  if (error || !data) return <ErrorBox error={error} />;
  const probed = (m: ModelRow, task: Task) => m.probe?.tasks?.[task];
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">{t("models.title")}</h1>
          <p className="max-w-4xl text-[13.5px] text-ink-2">{t(IS_ADMIN ? "models.intro" : "models.intro_public")}</p>
        </div>
        {!IS_PUBLIC && (
          <button
            className="btn"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              await api.refreshModels().catch(() => undefined);
              await qc.invalidateQueries({ queryKey: ["models"] });
              setBusy(false);
            }}
          >
            {busy ? <Spinner /> : "↻"} {t("models.refresh")}
          </button>
        )}
      </div>
      {(probe?.running || offer) && (
        <div className="card space-y-2 p-3 text-xs">
          {offer && !probe?.running && (
            <div className="flex flex-wrap items-center gap-2">
              <span>
                {t("models.recheck")} <b>{offer.model}</b>: {offer.cases.join(", ") || "—"} · ≈ ${offer.total.toFixed(2)}
              </span>
              <button className="btn btn-primary" disabled={!offer.cases.length} onClick={confirmRecheck}>
                {t("plan.confirm")}
              </button>
              <button className="btn btn-ghost" onClick={() => setOffer(null)}>
                {t("common.cancel")}
              </button>
            </div>
          )}
          {probe?.running && (
            <>
              <div className="flex items-center gap-2 font-medium">
                <Spinner /> {t("models.probe_running")}
              </div>
              <pre className="json !max-h-40">{probe.log.slice(-12).join("\n")}</pre>
            </>
          )}
        </div>
      )}
      <div className="flex flex-wrap gap-3 text-xs text-ink-2">
        {Object.entries(CELL)
          .filter(([k]) => k !== "unsupported")
          .map(([k, c]) => (
            <span key={k} className="flex items-center gap-1">
              <span style={{ color: c.color }}>{c.icon}</span> {t(`models.state.${k}` as TKey)}
            </span>
          ))}
        <span>· {t("models.verified")}</span>
      </div>
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[1200px] text-left text-[13px]">
          <thead className="border-b border-line text-xs text-muted">
            <tr>
              <th className="px-3 py-2 font-medium">{t("lb.model")}</th>
              <th className="px-2 py-2 font-medium">{t("models.available")}</th>
              {TASKS.map((tk) => (
                <th key={tk} className="px-2 py-2 text-center font-medium" title={t(`task.${tk}` as TKey)}>
                  {t(`task.short.${tk}` as TKey)}
                </th>
              ))}
              <th className="px-3 py-2 font-medium">{t("models.durations")}</th>
              <th className="px-3 py-2 font-medium">{t("models.resolutions")}</th>
              <th className="px-3 py-2 font-medium">{t("models.ratios")}</th>
              <th className="px-3 py-2 font-medium">{t("models.audio")}</th>
              <th className="px-3 py-2 font-medium">{t("models.delivery")}</th>
              <th className="px-3 py-2 text-right font-medium">{t("models.price")}</th>
              <th className="px-3 py-2 text-right font-medium">{t("models.latency")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--line)]">
            {data.models.map((m) => {
              const cases = Object.values(m.probe?.cases ?? {});
              return (
                <Fragment key={m.id}>
                  <tr className="cursor-pointer hover:bg-surface-2" onClick={() => setOpen(open === m.id ? null : m.id)}>
                    <td className="px-3 py-2.5">
                      <div className="flex items-center gap-2 font-medium">
                        <VendorDot vendor={m.vendor} /> {m.label}
                        {IS_ADMIN && <span className={cls("chip !text-[10px]", m.doc === "inferred" && "!border-[var(--warn)]")}>{m.doc === "inferred" || m.doc === "documented" ? t(`models.doc.${m.doc}` as TKey) : m.doc}</span>}
                      </div>
                      <div className="font-mono text-[11px] text-muted">{m.id}</div>
                      {isUnavailable(m) && (
                        <div className="mt-0.5 max-w-xs text-[11px]" style={{ color: "var(--critical)" }}>
                          ✕ {t("models.unavailable")}
                        </div>
                      )}
                    </td>
                    <td className="px-2 py-2.5 text-center">
                      {m.available.com === true ? <span style={{ color: "var(--good)" }}>✓</span> : m.available.com === false ? <span style={{ color: "var(--critical)" }}>✕</span> : "·"}
                    </td>
                    {TASKS.map((tk) => {
                      const s = m.task_states[tk];
                      const c = CELL[s];
                      const verified = probed(m, tk) === "ok";
                      return (
                        <td key={tk} className="px-2 py-2.5 text-center" title={t(`models.state.${s}` as TKey)}>
                          <span style={{ color: c.color }}>{c.icon}{verified ? "✓" : ""}</span>
                        </td>
                      );
                    })}
                    <td className="tnum px-3 py-2.5">
                      {durations(m)}
                      {Object.keys(m.by_resolution ?? {}).length ? <div className="text-[10.5px] text-muted">{Object.entries(m.by_resolution).map(([r, d]) => `${r}: ${d.join("/")}s`).join(" · ")}</div> : null}
                    </td>
                    <td className="px-3 py-2.5">{m.resolutions.join(" · ")}</td>
                    <td className="px-3 py-2.5 text-xs">{m.ratios.join(" ")}</td>
                    <td className="px-3 py-2.5">{V(m.audio)}</td>
                    <td className="px-3 py-2.5 text-xs">
                      URL {V(m.delivery_effective.image?.url)} · data {V(m.delivery_effective.image?.data_uri)}
                    </td>
                    <td className="tnum px-3 py-2.5 text-right">
                      {usd(m.price_ref.usd)}
                      <div className="text-[10.5px] text-muted">{m.price_ref.settings}</div>
                    </td>
                    <td className="tnum px-3 py-2.5 text-right">{secs(m.latency_s)}</td>
                  </tr>
                  {open === m.id && (
                    <tr className="bg-surface-2">
                      <td colSpan={TASKS.length + 9} className="px-4 py-3 text-xs">
                        <p className="mb-2 text-ink-2">{tr(m.notes)}</p>
                        <p className="mb-2 text-muted">{m.price_ref.basis}</p>
                        <AdminOnly>
                          {rawUnavailable(m) && <p className="mb-2" style={{ color: "var(--critical)" }}>{rawUnavailable(m)}</p>}
                          {(m.probe?.notes ?? []).map((n) => (
                            <p key={n} className="mb-1 text-muted">• {n}</p>
                          ))}
                          {!IS_PUBLIC && (probe?.cases ?? []).some((c) => c.model === m.id) && (
                            <button className="btn mb-2" disabled={probe?.running} onClick={(e) => { e.stopPropagation(); void recheck(m); }}>
                              ↻ {t("models.recheck")}
                            </button>
                          )}
                          {cases.length === 0 ? (
                            <p className="text-muted">{t("models.probe")}: —</p>
                          ) : (
                            <table className="w-full">
                              <tbody>
                                {cases.map((c) => (
                                  <tr key={c.case}>
                                    <td className="py-0.5 pr-3 font-mono">{c.case}</td>
                                    <td className="pr-3" style={{ color: c.outcome === "ok" ? "var(--good-text)" : c.outcome === "fail" ? "var(--critical)" : "var(--ink-2)" }}>
                                      {c.outcome}
                                    </td>
                                    <td className="tnum pr-3">{c.size ?? "—"}</td>
                                    <td className="tnum pr-3">{c.duration ? `${c.duration.toFixed(1)}s` : "—"}</td>
                                    <td className="pr-3">{c.audio ? "♪" : ""}</td>
                                    <td className="tnum pr-3">{c.latency_s ? secs(c.latency_s) : ""}</td>
                                    <td className="text-muted">{c.fail_reason || (c.failed_checks?.length ? `checks: ${c.failed_checks.join(", ")}` : "")}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          )}
                        </AdminOnly>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {Object.entries(data.unregistered ?? {}).some(([, v]) => v.length) && (
        <AdminOnly>
          <div className="card p-3 text-xs text-ink-2">
            {t("models.unregistered")} {Object.values(data.unregistered).flat().join(", ")}
          </div>
        </AdminOnly>
      )}
    </div>
  );
}
