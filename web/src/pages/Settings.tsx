import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n, type TKey } from "../i18n";
import { ago, usd } from "../util";
import { ErrorBox, Spinner } from "../components/common";
import { useConfig } from "../components/RunSetup";

export default function SettingsPage() {
  const { t, lang } = useI18n();
  const kindLabel = (k: string) => (["video", "judge", "image"].includes(k) ? t(`settings.kind.${k}` as TKey) : k);
  const qc = useQueryClient();
  const { data: cfg, error } = useConfig();
  const { data: ledger } = useQuery({ queryKey: ["ledger"], queryFn: api.ledger, refetchInterval: 15000 });
  const [form, setForm] = useState({ budget_usd: "", confirm_above_usd: "", usd_cny: "", judge_model: "", rpm: "", max_inflight: "" });
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    if (cfg)
      setForm({
        budget_usd: String(cfg.budget_usd),
        confirm_above_usd: String(cfg.confirm_above_usd),
        usd_cny: String(cfg.usd_cny),
        judge_model: cfg.judge_model,
        rpm: String(cfg.endpoints[0]?.rpm ?? 15),
        max_inflight: String(cfg.max_inflight ?? 6),
      });
  }, [cfg]);
  if (error) return <ErrorBox error={error} />;
  if (!cfg) return <Spinner />;
  const save = async () => {
    setErr(null);
    try {
      await api.updateSettings({
        budget_usd: Number(form.budget_usd),
        confirm_above_usd: Number(form.confirm_above_usd),
        usd_cny: Number(form.usd_cny),
        judge_model: form.judge_model,
        rpm: Number(form.rpm),
        max_inflight: Number(form.max_inflight),
      });
      await qc.invalidateQueries({ queryKey: ["config"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } catch (e) {
      setErr(e);
    }
  };
  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-semibold tracking-tight">{t("settings.title")}</h1>
      <div className="grid gap-5 lg:grid-cols-2">
        <div className="card space-y-4 p-4">
          <div className="grid grid-cols-2 items-end gap-3">
            <label className="space-y-1">
              <span className="label">{t("settings.budget")}</span>
              <input className="input" type="number" value={form.budget_usd} onChange={(e) => setForm({ ...form, budget_usd: e.target.value })} />
            </label>
            <label className="space-y-1">
              <span className="label">{t("settings.confirm")}</span>
              <input className="input" type="number" step="0.5" value={form.confirm_above_usd} onChange={(e) => setForm({ ...form, confirm_above_usd: e.target.value })} />
            </label>
            <label className="space-y-1">
              <span className="label">{t("settings.fx")}</span>
              <input className="input" type="number" step="0.01" value={form.usd_cny} onChange={(e) => setForm({ ...form, usd_cny: e.target.value })} />
            </label>
            <label className="space-y-1">
              <span className="label">{t("settings.judge_model")}</span>
              <select className="input" value={form.judge_model} onChange={(e) => setForm({ ...form, judge_model: e.target.value })}>
                {cfg.judge_models.map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </select>
            </label>
            <label className="space-y-1">
              <span className="label">{t("settings.rpm")}</span>
              <input className="input" type="number" min={1} max={60} value={form.rpm} onChange={(e) => setForm({ ...form, rpm: e.target.value })} />
            </label>
            <label className="space-y-1">
              <span className="label">{t("settings.inflight")}</span>
              <input className="input" type="number" min={1} max={24} value={form.max_inflight} onChange={(e) => setForm({ ...form, max_inflight: e.target.value })} />
            </label>
          </div>
          <div className="flex items-center gap-3">
            <button className="btn btn-primary" onClick={save}>
              {t("settings.save")}
            </button>
            {saved && <span className="text-xs" style={{ color: "var(--good-text)" }}>✓ {t("settings.saved")}</span>}
          </div>
          <ErrorBox error={err} />
          <div className="space-y-1 border-t border-line pt-3 text-xs text-ink-2">
            {cfg.endpoints.map((e) => (
              <div key={e.key}>
                {t("settings.endpoint")}: <b>{e.name}</b> · {e.rpm} rpm · {t("settings.used", { n: e.used_last_minute })}
                {e.disabled_reason ? <span style={{ color: "var(--critical)" }}> · disabled: {e.disabled_reason}</span> : null}
              </div>
            ))}
            <div>
              {t("settings.hard_cap")}: {cfg.hard_cap_usd !== null ? usd(cfg.hard_cap_usd) : "—"}
            </div>
            {cfg.quota_note && !cfg.quota_calibrated ? (
              <div>
                {t("settings.billing")}: {t("settings.billing_note")}
              </div>
            ) : (
              <div>
                {t("settings.quota")}: {cfg.quota_per_usd.toLocaleString()} / USD · {cfg.quota_calibrated ? t("settings.calibrated") : t("settings.uncalibrated")}
              </div>
            )}
            <div className="text-muted">{t("settings.env_note")}</div>
          </div>
        </div>
        <div className="card space-y-3 p-4">
          <div className="flex items-baseline justify-between">
            <h2 className="text-[15px] font-semibold">{t("settings.ledger")}</h2>
            <span className="text-2xl font-semibold">{usd(ledger?.spent_usd ?? cfg.spent_usd)}</span>
          </div>
          {ledger && (
            <>
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div>
                  <div className="label mb-1">{t("settings.by_kind")}</div>
                  {Object.entries(ledger.by_kind).map(([k, v]) => (
                    <div key={k} className="flex justify-between">
                      <span>{kindLabel(k)}</span>
                      <span className="tnum">{usd(v)}</span>
                    </div>
                  ))}
                </div>
                <div>
                  <div className="label mb-1">{t("settings.by_model")}</div>
                  {Object.entries(ledger.by_model)
                    .sort((a, b) => b[1] - a[1])
                    .map(([k, v]) => (
                      <div key={k} className="flex justify-between gap-2">
                        <span className="truncate">{k}</span>
                        <span className="tnum">{usd(v)}</span>
                      </div>
                    ))}
                </div>
              </div>
              <div className="max-h-72 overflow-y-auto border-t border-line pt-2 text-xs">
                {ledger.entries.slice(0, 100).map((e) => (
                  <div key={e.id} className="flex justify-between gap-2 py-0.5">
                    <span className="text-muted">{ago(e.ts, lang)}</span>
                    <span className="flex-1 truncate">
                      {kindLabel(e.kind)} · {e.model}
                    </span>
                    <span className="tnum">
                      {usd(e.actual_usd ?? e.est_usd, 3)}
                      {e.quota !== null ? <span className="text-muted"> · q {e.quota}</span> : null}
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
