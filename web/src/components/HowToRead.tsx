import { useState } from "react";
import { useI18n } from "../i18n";
import type { EvaluationDetail, EvaluationGroup, EvaluationSample } from "../types";
import { TERMINAL } from "../util";
import { SourceTag } from "./common";

/** What one source on screen was asked for and produced. Pure: every line of the panel is derived from these. */
export interface SourceFacts {
  id: string; label: string; model: string; provider: string; ok: boolean;
  resolution: string | null; duration: number | null; audio: boolean | null;
  /** output facts: succeeded clips only */
  size: string | null; fps: number | null; length: number | null; latencyBasis: string | null; costBasis: string | null;
  seed: string | null; steps: number | null; turboUnverified: boolean;
  clipsOk: number; clipsFailed: number; rated: boolean; judged: boolean;
}

const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown) => (v == null || v === "" ? null : String(v));

export function sourceFacts(group: EvaluationGroup, shown: EvaluationSample): SourceFacts {
  const ok = shown.status === "succeeded";
  const spec = shown.spec ?? {};
  const prov = shown.provenance ?? {};
  const video = ok ? shown.media?.video : undefined;
  const length = ok ? num(shown.media?.duration) : null;
  const steps = num(prov.requested_num_inference_steps);
  const cost = shown.cost;
  return {
    id: group.id, label: group.label, model: group.model, provider: group.provider, ok,
    resolution: str(spec.resolution ?? shown.effective?.resolution),
    duration: num(spec.duration),
    audio: typeof spec.audio === "boolean" ? spec.audio : null,
    size: video?.width && video?.height ? `${video.width}×${video.height}` : null,
    fps: num(video?.fps),
    length: length == null ? null : Math.round(length * 10) / 10,
    latencyBasis: ok ? str(shown.latency_basis) : null,
    costBasis: ok ? (cost?.basis || (cost?.actual_usd != null ? "billed_usage" : "catalog_estimate")) : null,
    seed: str(prov.seed) ?? (typeof spec.seed === "number" ? String(spec.seed) : null),
    steps,
    turboUnverified: steps === 8 && prov.turbo_activation_verified !== true,
    clipsOk: group.samples.filter((s) => s.status === "succeeded").length,
    clipsFailed: group.samples.filter((s) => s.status !== "succeeded" && TERMINAL.has(s.status)).length,
    rated: shown.rating != null && (shown.rating.overall != null || shown.rating.usable != null),
    judged: shown.judge_status === "done",
  };
}

/** Not a Tokenbay source and not a model in the Tokenbay registry for this case. */
export const offCatalog = (group: EvaluationGroup, detail: EvaluationDetail) =>
  group.provider !== "tokenbay" && !detail.coverage.some((c) => c.model === group.model);

/** Tokenbay models with no clip on this page: failed (their cards stay in the grid), not run, not supported, unavailable. */
export const GAP_STATUSES = ["failed", "missing", "unsupported", "experimental", "probe_failed"];

function Block({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="space-y-1.5">
      <div className="label">{title}</div>
      {items.length ? (
        <ul className="space-y-1 text-xs leading-relaxed text-ink-2">
          {items.map((s) => <li key={s}>{s}</li>)}
        </ul>
      ) : (
        <p className="text-xs text-muted">—</p>
      )}
    </div>
  );
}

export default function HowToRead({ detail, shown, gaps: tokenbayGaps, blind, onBlind }: {
  detail: EvaluationDetail;
  shown: { group: EvaluationGroup; sample: EvaluationSample }[];
  /** Tokenbay models with no clip here, after the page's filters */
  gaps: EvaluationDetail["coverage"];
  blind: boolean;
  onBlind: () => void;
}) {
  const { t, tx, lang } = useI18n();
  const [open, setOpen] = useState(() => {
    try {
      const v = localStorage.getItem("vgen.howto");
      if (v === "1" || v === "0") return v === "1";
    } catch {
      /* storage unavailable */
    }
    return shown.length > 1;
  });
  const toggle = () => {
    setOpen(!open);
    try {
      localStorage.setItem("vgen.howto", open ? "0" : "1");
    } catch {
      /* ignore */
    }
  };

  const facts = shown.map(({ group, sample }) => sourceFacts(group, sample));
  const n = facts.length;
  const done = facts.filter((f) => f.ok);
  const sep = lang === "zh" ? "，" : ", ";
  const unrecorded = t("how.unrecorded");
  const secs = (v: number) => t("common.seconds", { n: v });
  const secs1 = (v: number) => t("common.seconds", { n: v.toFixed(1) });
  const basis = (b: string) => tx(`eval.${b}`, b);
  const tally = (vals: string[]) => {
    const m = new Map<string, number>();
    for (const v of vals) m.set(v, (m.get(v) ?? 0) + 1);
    return m;
  };
  const counted = (vals: string[]) => [...tally(vals)].map(([label, c]) => t("how.count", { label, n: c })).join(sep);

  const same = [t("how.same_prompt")];
  if (Object.keys(detail.case.assets ?? {}).length) same.push(t("how.same_image"));
  const diff: string[] = [];
  const attr = (what: string, vals: string[], mixed?: (k: number, list: string) => string) => {
    if (!vals.length) return;
    const k = new Set(vals).size;
    if (k === 1) same.push(t("how.same_value", { what, value: vals[0], n: vals.length }));
    else diff.push(mixed ? mixed(k, counted(vals)) : t("how.diff_value", { what, list: counted(vals) }));
  };
  attr(t("how.attr.resolution"), facts.map((f) => f.resolution ?? unrecorded));
  attr(t("how.attr.duration"), facts.map((f) => (f.duration == null ? unrecorded : secs(f.duration))));
  attr(t("how.attr.audio"), facts.map((f) => (f.audio == null ? unrecorded : t(f.audio ? "common.yes" : "common.no"))));
  attr(t("how.attr.size"), done.flatMap((f) => (f.size ? [f.size] : [])));
  attr(t("how.attr.fps"), done.flatMap((f) => (f.fps == null ? [] : [`${Math.round(f.fps * 100) / 100} fps`])));
  attr(t("how.attr.length"), done.flatMap((f) => (f.length == null ? [] : [secs1(f.length)])));
  const seeded = facts.filter((f) => f.seed != null).length;
  if (seeded > 0 && seeded < n) diff.push(t("how.seed_some", { k: seeded, n, m: n - seeded }));
  else attr(t("how.attr.seed"), facts.map((f) => f.seed ?? unrecorded));
  attr(t("how.attr.steps"), facts.map((f) => (f.steps == null ? unrecorded : String(f.steps))));
  attr(t("how.attr.latency"), done.flatMap((f) => (f.latencyBasis ? [basis(f.latencyBasis)] : [])), (k, list) => t("how.latency_mixed", { k, list }));
  attr(t("how.attr.cost"), done.flatMap((f) => (f.costBasis ? [basis(f.costBasis)] : [])), (k, list) => t("how.cost_mixed", { k, list }));

  const gaps: string[] = [];
  const rated = done.filter((f) => f.rated).length;
  if (done.length && rated < done.length) gaps.push(rated ? t("how.some_human", { k: rated, n: done.length }) : t("how.no_human"));
  const judged = done.filter((f) => f.judged).length;
  if (done.length && judged < done.length) gaps.push(judged ? t("how.some_judge", { k: judged, n: done.length }) : t("how.no_judge"));
  const withClips = facts.filter((f) => f.clipsOk > 0);
  const single = withClips.filter((f) => f.clipsOk === 1).length;
  if (single && single === withClips.length) gaps.push(t("how.samples_single"));
  else if (single) gaps.push(t("how.samples_some_single", { k: single, n: withClips.length }));
  const noClip = facts.filter((f) => f.clipsOk === 0).length;
  if (noClip) gaps.push(t("how.failed", { k: noClip, n }));
  if (facts.some((f) => f.turboUnverified)) gaps.push(t("eval.turbo_unverified"));
  // failed models keep a card in the grid and are counted above; these have none
  const absent = tokenbayGaps.filter((c) => c.status !== "failed");
  if (absent.length) gaps.push(t("how.absent", { k: absent.length, n: absent.length, list: counted(absent.map((c) => tx(`eval.pub.${c.status}`, c.status))) }));

  const refs = shown.filter(({ group }) => offCatalog(group, detail));
  const reference = !refs.length ? null
    : blind ? t("how.reference_blind", { k: refs.length })
    : t("how.reference", { models: [...new Set(refs.map(({ group }) => group.model))].join(sep) });

  return (
    <section className="card space-y-3 p-4" aria-label={t("how.title")}>
      <button className="flex w-full items-center justify-between gap-3 text-left" aria-expanded={open} onClick={toggle}>
        <span className="text-sm font-semibold">{t("how.title")}</span>
        <span className="text-xs text-muted">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <>
          <p className="text-xs text-muted">{t("how.lead")}</p>
          <div className="grid gap-4 md:grid-cols-3">
            <Block title={t("how.same")} items={same} />
            <Block title={t("how.differs")} items={diff} />
            <Block title={t("how.unmeasured")} items={gaps} />
          </div>
          {reference && <p className="eval-notice">{reference}</p>}
          <p className="text-xs font-medium text-ink-2">{t("how.no_ranking")}</p>
          {!blind && (
            <p className="text-xs text-muted">
              {t("how.blind_hint")}{" "}
              <button className="underline hover:text-ink" onClick={onBlind}>{t("board.blind")} →</button>
            </p>
          )}
          {!blind && n > 0 && (
            <details>
              <summary className="cursor-pointer text-xs text-muted">{t("how.per_source")}</summary>
              <div className="mt-2 overflow-x-auto">
                <table className="eval-table">
                  <thead>
                    <tr>
                      <th>{t("eval.source")}</th><th>{t("eval.requested")}</th><th>{t("eval.output")}</th><th>{t("how.attr.seed")}</th>
                      <th>{t("how.attr.steps")}</th><th>{t("how.attr.latency")}</th><th>{t("how.attr.cost")}</th><th>{t("how.clips")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map(({ group, sample }, i) => {
                      const f = facts[i];
                      const requested = [f.resolution, f.duration == null ? null : secs(f.duration), f.audio == null ? null : f.audio ? "♪" : t("board.no_audio")];
                      const output = [f.size, f.fps == null ? null : `${Math.round(f.fps * 100) / 100} fps`, f.length == null ? null : secs1(f.length)];
                      return (
                        <tr key={group.id}>
                          <td>
                            <div className="font-medium text-ink">{group.label}</div>
                            <SourceTag provider={group.provider} vendor={sample.vendor} hardware={group.hardware} offCatalog={offCatalog(group, detail)} compact />
                          </td>
                          <td>{requested.filter(Boolean).join(" · ") || "—"}</td>
                          <td>{output.filter(Boolean).join(" · ") || "—"}</td>
                          <td>{f.seed ?? unrecorded}</td>
                          <td>{f.steps ?? unrecorded}</td>
                          <td>{f.latencyBasis ? basis(f.latencyBasis) : "—"}</td>
                          <td>{f.costBasis ? basis(f.costBasis) : "—"}</td>
                          <td>{t("how.clips_value", { ok: f.clipsOk, failed: f.clipsFailed })}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </details>
          )}
        </>
      )}
    </section>
  );
}
