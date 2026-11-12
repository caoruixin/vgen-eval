import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, IS_PUBLIC } from "../api";
import { storedKey } from "../apiKey";
import { useI18n, type TKey } from "../i18n";
import type { Job, Rating } from "../types";
import { cls, pct, secs, TERMINAL, usd } from "../util";
import { Check, ErrorBox, StatusBadge, VendorDot } from "./common";

export const DEFECT_TAGS = ["morphing", "anatomy", "physics", "text_garbled", "identity_drift", "lip_sync", "audio_artifact", "ignores_prompt", "flicker", "letterbox_watermark"];

const DEFECT_LABEL: Record<string, { en: string; zh: string }> = {
  morphing: { en: "morphing", zh: "变形" },
  anatomy: { en: "anatomy", zh: "肢体结构" },
  physics: { en: "physics", zh: "物理" },
  text_garbled: { en: "garbled text", zh: "文字乱码" },
  identity_drift: { en: "identity drift", zh: "身份漂移" },
  lip_sync: { en: "lip-sync", zh: "口型" },
  audio_artifact: { en: "audio artifacts", zh: "音频瑕疵" },
  ignores_prompt: { en: "ignores prompt", zh: "未遵循提示" },
  flicker: { en: "flicker", zh: "闪烁" },
  letterbox_watermark: { en: "letterbox / watermark", zh: "黑边 / 水印" },
  other: { en: "other", zh: "其他" },
};

const COMPLIANCE_LABEL: Record<string, { en: string; zh: string }> = {
  resolution: { en: "resolution", zh: "分辨率" },
  aspect_ratio: { en: "ratio", zh: "比例" },
  duration: { en: "duration", zh: "时长" },
  fps: { en: "fps", zh: "帧率" },
  codec: { en: "codec", zh: "编码" },
  audio: { en: "audio", zh: "音频" },
  black_bars: { en: "no black bars", zh: "无黑边" },
  blank_frames: { en: "no blank frames", zh: "无空白帧" },
  video_stream: { en: "video stream", zh: "视频流" },
};

const GRADE_LABEL: Record<string, { en: string; zh: string }> = {
  high: { en: "high", zh: "高" },
  medium: { en: "medium", zh: "中" },
  low: { en: "low", zh: "低" },
};

const SEVERITY = { minor: { icon: "·", color: "var(--warn)" }, major: { icon: "!", color: "var(--serious)" }, critical: { icon: "‼", color: "var(--critical)" } };

function TwoStep({ label, confirmLabel, onConfirm, disabled }: { label: string; confirmLabel: string; onConfirm: () => void; disabled?: boolean }) {
  const [armed, setArmed] = useState(false);
  return armed ? (
    <span className="inline-flex items-center gap-1">
      <button className="btn" style={{ borderColor: "var(--critical)" }} onClick={() => { setArmed(false); onConfirm(); }}>
        {confirmLabel}
      </button>
      <button className="btn btn-ghost" onClick={() => setArmed(false)}>✕</button>
    </span>
  ) : (
    <button className="btn" disabled={disabled} onClick={() => setArmed(true)}>
      {label}
    </button>
  );
}

export default function JobCard({
  job,
  rating,
  blind,
  picked,
  onPick,
  registerVideo,
  onSeek,
  muted,
  judgeEnabled,
  editable = true,
  pickEnabled = true,
  onSound,
}: {
  job: Job;
  rating: Rating | undefined;
  blind: boolean;
  picked: boolean;
  onPick: () => void;
  registerVideo: (id: string, el: HTMLVideoElement | null) => void;
  onSeek: (t: number) => void;
  muted: boolean;
  judgeEnabled: boolean;
  /** false on the hosted demo's example runs: watch only */
  editable?: boolean;
  pickEnabled?: boolean;
  onSound?: (id: string) => void;
}) {
  const { t, lang } = useI18n();
  const qc = useQueryClient();
  const [details, setDetails] = useState(false);
  const [showChecklist, setShowChecklist] = useState(false);
  const [actionErr, setActionErr] = useState<unknown>(null);
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["evaluation"] }); return qc.invalidateQueries({ queryKey: ["run", job.run_id] }); };
  // hosted demo: paid follow-ups (check again, re-submit, re-judge) are billed to the visitor's own key
  const key = () => (IS_PUBLIC ? storedKey() : undefined);
  const act = (call: () => Promise<unknown>) => {
    setActionErr(null);
    call().then(refresh, setActionErr);
  };
  const rate = (patch: Partial<Rating>) => act(() => api.rate(job.id, patch));
  const name = blind ? `${lang === "zh" ? "视频" : "Clip"} ${job.label}` : job.model_label;
  const requestedSteps = job.provenance.requested_num_inference_steps;
  const actualSteps = job.provenance.actual_denoise_updates;
  const done = job.status === "succeeded";
  const active = !TERMINAL.has(job.status);
  const v = job.media?.video;
  const judge = job.judge ?? {};
  const fid = job.fidelity ?? {};
  const sig = job.signals ?? {};
  const failedComp = (job.compliance ?? []).filter((c) => c.ok === false);
  const defects = rating?.defects ?? [];
  const L = (m: Record<string, { en: string; zh: string }>, k: string) => m[k]?.[lang] ?? k;

  return (
    <div className={cls("card flex flex-col overflow-hidden", picked && "ring-2 ring-[var(--good)]")}>
      <div className="relative aspect-video bg-black">
        {done && job.video_url ? (
          <video
            ref={(el) => registerVideo(job.id, el)}
            src={job.video_url}
            poster={job.poster_url ?? undefined}
            className="h-full w-full object-contain"
            controls
            playsInline
            muted={muted}
            onVolumeChange={(e) => { if (!e.currentTarget.muted && onSound) onSound(job.id); }}
            loop={false}
            preload="metadata"
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center text-[13px] text-white/80">
            <StatusBadge status={job.status} progress={job.progress} />
            {active && job.status === "pending" && <span className="text-xs text-white/60">{t("board.waiting")}</span>}
            {job.fail_reason && <span className="max-w-[90%] text-xs text-white/70">{job.fail_reason}</span>}
            {active && job.progress && (
              <span className="h-1 w-40 overflow-hidden rounded-full bg-white/20">
                <span className="block h-full bg-white/80" style={{ width: job.progress }} />
              </span>
            )}
          </div>
        )}
      </div>

      <div className="flex flex-1 flex-col gap-3 p-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-[14px] font-semibold">
              {!blind && <VendorDot vendor={job.vendor} />}
              <span className="truncate">{name}</span>
            </div>
            {!blind && (
              <div className="text-xs text-muted">
                {job.vendor}
                {done && v ? ` · ${v.width}×${v.height} · ${(job.media.duration ?? 0).toFixed(1)}s${job.media.audio ? " · ♪" : ""}` : ""}
              </div>
            )}
          </div>
          <div className="flex flex-col items-end gap-1">
            {done && <StatusBadge status={job.status} />}
            {editable && pickEnabled ? (
              <button className={cls("chip", picked && "!border-[var(--good)]")} onClick={onPick} disabled={!done} title={t("board.pick")}>
                {picked ? "★" : "☆"} {picked ? t("board.picked") : t("board.pick")}
              </button>
            ) : (
              picked && <span className="chip !border-[var(--good)]">★ {t("board.picked")}</span>
            )}
          </div>
        </div>

        {!blind && job.origin === "imported" && <div className="space-y-1 text-xs">
          <div className="flex flex-wrap gap-1.5">
            {job.provenance.phase != null && <span className="chip">{t(`phase.${job.provenance.phase}` as TKey)}</span>}
            <span className="chip">{typeof requestedSteps === "number" ? `${t("eval.requested_steps")}: ${requestedSteps}` : t(job.provenance.source === "official_api" ? "eval.steps_undisclosed" : "eval.steps_unrecorded")}</span>
            {typeof actualSteps === "number" && <span className="chip">{t("eval.observed_steps")}: {actualSteps}</span>}
            {job.provenance.seed != null && <span className="chip">{t("eval.seed")}: {String(job.provenance.seed)}</span>}
          </div>
          {requestedSteps === 8 && job.provenance.turbo_activation_verified !== true && <p className="text-[var(--warn)]">{t("eval.turbo_unverified")}</p>}
        </div>}

        {!blind && (done || job.task_id) && (
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="rounded-md bg-surface-2 px-2 py-1.5">
              <div className="text-muted">{t("board.cost")}</div>
              <div className="tnum font-medium text-ink">
                {job.cost.actual_usd !== null ? usd(job.cost.actual_usd) : usd(job.cost.estimate_usd)}
                <span className="ml-1 font-normal text-muted">{job.cost.actual_usd !== null ? t("board.actual") : job.cost.estimate_usd != null ? t("board.estimate") : t("eval.unknown")}</span>
              </div>
              {job.cost.per_second ? <div className="tnum text-muted">{usd(job.cost.per_second, 3)}/s</div> : null}
            </div>
            <div className="rounded-md bg-surface-2 px-2 py-1.5">
              <div className="text-muted">{t("board.latency")}</div>
              <div className="tnum font-medium text-ink">{secs(job.latency_s ?? (!done && job.submitted_at ? Date.now() / 1000 - job.submitted_at : null))}</div>
              {job.gw_start_time && job.gw_submit_time ? (
                <div className="tnum text-muted">
                  {t("board.queue")} {secs(job.gw_start_time - job.gw_submit_time)}
                  {job.gw_finish_time ? ` · ${t("board.render")} ${secs(job.gw_finish_time - job.gw_start_time)}` : ""}
                </div>
              ) : null}
            </div>
          </div>
        )}

        {!blind && done && (
          <div className="space-y-1 text-xs text-muted">
            <p>{t("eval.requested")}: {String(job.spec.resolution ?? job.effective.resolution ?? "—")} · {String(job.spec.duration ?? "—")}s</p>
            {job.latency_basis && <p>{t(`eval.${job.latency_basis}` as TKey)}</p>}
            {job.cost.basis && <p>{t(`eval.${job.cost.basis}` as TKey)}</p>}
            {job.origin === "imported" && !rating?.overall && rating?.usable == null && <p>{t("eval.unrated")}</p>}
          </div>
        )}

        {done && (
          <div className="space-y-1.5">
            <div className="label">{t("board.compliance")}</div>
            <div className="flex flex-wrap gap-1">
              {(job.compliance ?? []).map((c) => (
                <Check
                  key={c.key}
                  ok={c.ok}
                  label={L(COMPLIANCE_LABEL, c.key)}
                  title={`${t("board.compliance")}: expected ${String(c.expected)} · actual ${String(c.actual)}${c.note ? ` · ${c.note}` : ""}`}
                />
              ))}
            </div>
            {failedComp.length > 0 && (
              <div className="text-xs text-ink-2">
                {failedComp.map((c) => (
                  <div key={c.key}>
                    ✕ {L(COMPLIANCE_LABEL, c.key)}: {String(c.expected)} → {String(c.actual)}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {done && (Object.keys(fid).length > 0 || Object.keys(sig).length > 0) && (
          <div className="space-y-1.5">
            <div className="label">{t("board.signals")}</div>
            <div className="flex flex-wrap gap-1 text-xs">
              {fid.first && (
                <span className="chip" title={`score ${fid.first.score}`}>
                  {t("board.first_frame")}: <b className="text-ink">{L(GRADE_LABEL, fid.first.grade)}</b>
                </span>
              )}
              {fid.last && (
                <span className="chip" title={`score ${fid.last.score}`}>
                  {t("board.last_frame")}: <b className="text-ink">{L(GRADE_LABEL, fid.last.grade)}</b>
                </span>
              )}
              {sig.frozen_flag && <Check ok={false} label={`${t("board.frozen")} ${pct(sig.frozen_ratio)}`} />}
              {(sig.scene_cuts?.length ?? 0) > 0 && (
                <span className="chip" title={sig.scene_cuts?.join("s, ") + "s"}>
                  {t("board.cuts")}: {sig.scene_cuts?.length}
                </span>
              )}
              {sig.motion_pct_per_s !== undefined && (
                <span className="chip" title="mean optical-flow magnitude, % of frame diagonal per second (information only)">
                  {t("board.motion")}: <span className="tnum">{sig.motion_pct_per_s?.toFixed(1)}</span>
                </span>
              )}
              {sig.audio && !blind && (
                <span className="chip">
                  {t("board.audio_level")}:{" "}
                  {sig.audio.has_audio
                    ? (sig.audio.silence_ratio ?? 1) > 0.9
                      ? t("board.silent")
                      : `${sig.audio.rms_dbfs} dBFS`
                    : t("board.no_audio")}
                </span>
              )}
            </div>
            {sig.error && <div className="text-xs text-muted">{sig.error}</div>}
          </div>
        )}

        {done && judgeEnabled && (
          <div className="space-y-1.5 rounded-md border border-line p-2">
            <div className="flex items-center justify-between">
              <span className="label">{t("board.judge")}</span>
              {job.judge_status === "done" && judge.total ? (
                <span className="tnum text-xs font-medium">
                  {judge.passed}/{judge.total} ✓ · {pct(judge.adherence)}
                </span>
              ) : null}
            </div>
            {(job.judge_status === "pending" || job.judge_status === "running") && <div className="text-xs text-muted">{t("board.judge_pending")}</div>}
            {job.judge_status === "error" && (
              <div className="text-xs" style={{ color: "var(--critical)" }}>
                ✕ {judge.error}
              </div>
            )}
            {job.judge_status === "done" && (
              <>
                {judge.verdict && <p className="text-xs leading-relaxed text-ink-2">{judge.verdict}</p>}
                <div className="flex flex-wrap gap-1">
                  {(judge.defects ?? []).map((d, i) => (
                    <button key={i} className="chip" title={d.description} onClick={() => d.t != null && onSeek(d.t)}>
                      <span style={{ color: SEVERITY[d.severity]?.color }}>{SEVERITY[d.severity]?.icon}</span>
                      {L(DEFECT_LABEL, d.type)}
                      {d.t != null ? <span className="tnum text-muted">@{d.t.toFixed(1)}s</span> : null}
                    </button>
                  ))}
                </div>
                <button className="text-xs text-muted hover:text-ink" onClick={() => setShowChecklist(!showChecklist)}>
                  {showChecklist ? "▾" : "▸"} {t("board.checklist")}
                </button>
                {showChecklist && (
                  <ul className="space-y-1 text-xs">
                    {(judge.checklist ?? []).map((c, i) => (
                      <li key={i} className="flex gap-1.5">
                        <span style={{ color: c.pass ? "var(--good)" : "var(--critical)" }}>{c.pass ? "✓" : "✕"}</span>
                        <span className="text-ink-2">
                          <span className="text-ink">{c.item}</span> — {c.evidence}
                          {c.t != null && (
                            <button className="ml-1 text-muted underline" onClick={() => onSeek(c.t as number)}>
                              @{(c.t as number).toFixed(1)}s
                            </button>
                          )}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>
        )}

        {done && editable && (
          <div className="space-y-2 border-t border-line pt-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-1.5 text-xs">
                <span className="label">{t("board.usable")}</span>
                <button className={cls("chip", rating?.usable === true && "!border-[var(--good)] !text-ink")} onClick={() => rate({ usable: rating?.usable === true ? null : true })}>
                  👍 {t("common.yes")}
                </button>
                <button className={cls("chip", rating?.usable === false && "!border-[var(--critical)] !text-ink")} onClick={() => rate({ usable: rating?.usable === false ? null : false })}>
                  👎 {t("common.no")}
                </button>
              </div>
              <div className="flex items-center gap-0.5" aria-label={t("board.overall")}>
                {[1, 2, 3, 4, 5].map((n) => (
                  <button key={n} className="text-base leading-none" style={{ color: (rating?.overall ?? 0) >= n ? "var(--warn)" : "var(--axis)" }} onClick={() => rate({ overall: rating?.overall === n ? null : n })} aria-label={`${n}`}>
                    ★
                  </button>
                ))}
              </div>
            </div>
            <div className="flex flex-wrap gap-1">
              {DEFECT_TAGS.map((d) => {
                const on = defects.includes(d);
                return (
                  <button key={d} className={cls("chip !text-[11px]", on && "!border-[var(--serious)] !text-ink")} onClick={() => rate({ defects: on ? defects.filter((x) => x !== d) : [...defects, d] })}>
                    {on ? "✕ " : ""}
                    {L(DEFECT_LABEL, d)}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        <ErrorBox error={actionErr} />
        <div className="mt-auto flex flex-wrap items-center gap-1.5 pt-1">
          {editable && ["pending", "queued", "in_progress"].includes(job.status) && (
            <TwoStep label={t("board.stop")} confirmLabel={`${t("board.stop")}?`} onConfirm={() => act(() => api.stopJob(job.id))} />
          )}
          {editable && ["timeout", "stopped"].includes(job.status) && job.task_id && (
            <button className="btn" onClick={() => act(() => api.repollJob(job.id, key()))}>
              {t("board.repoll")}
            </button>
          )}
          {editable && ["failed", "submit_unknown", "timeout", "stopped", "blocked"].includes(job.status) && (
            <TwoStep label={t("board.resubmit")} confirmLabel={job.task_id ? t("board.resubmit_confirm") : `${t("board.resubmit")}?`} onConfirm={() => act(() => api.resubmitJob(job.id, true, key()))} />
          )}
          {editable && done && job.origin !== "imported" && (
            <button className="btn" onClick={() => act(() => api.judgeJob(job.id, key()))}>
              {t("board.rejudge")}
            </button>
          )}
          <button className="btn btn-ghost ml-auto text-xs" onClick={() => setDetails(!details)}>
            {t("board.details")} {details ? "▴" : "▾"}
          </button>
        </div>
        {details && (
          <div className="space-y-2 text-xs">
            {!blind && <div className="text-muted">model: {job.model} · task: {job.task_id ?? "—"} · {job.endpoint}</div>}
            {!blind && job.adjustments.length > 0 && (
              <div>
                <div className="label">{t("board.adjustments")}</div>
                {job.adjustments.map((a, i) => (
                  <div key={i} className="text-ink-2">
                    ↳ {a.field}: {String(a.requested ?? "")} → {String(a.used ?? "")} ({a.reason})
                  </div>
                ))}
              </div>
            )}
            {!blind && <pre className="json">{JSON.stringify(job.payload, null, 2)}</pre>}
            {!blind && job.origin === "imported" && <pre className="json">{JSON.stringify(job.provenance, null, 2)}</pre>}
            {!blind && Object.keys(job.last_detail ?? {}).length > 0 && <pre className="json">{JSON.stringify(job.last_detail, null, 2)}</pre>}
            {job.strip_url && <img src={job.strip_url} alt="frame strip" className="w-full rounded" />}
            {["pending", "queued", "in_progress"].includes(job.status) && <p className="text-muted">{t("board.no_cancel")}</p>}
          </div>
        )}
        <span className="hidden">{t(`status.${job.status}` as TKey)}</span>
      </div>
    </div>
  );
}
