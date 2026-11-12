import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, IS_ADMIN } from "../api";
import { useI18n } from "../i18n";
import type { EvaluationDetail, EvaluationGroup, EvaluationSample } from "../types";
import { cls, TERMINAL, usd } from "../util";
import JobCard from "../components/JobCard";
import SyncBar from "../components/SyncBar";
import HowToRead, { GAP_STATUSES, offCatalog } from "../components/HowToRead";
import { AdminOnly, AdminTag, ErrorBox, SourceTag, Spinner, TaskBadge } from "../components/common";

// Blind order: a hash of a per-page-load seed and the group id, so it says nothing about names or list order.
const SEED = Math.random().toString(36).slice(2);
function blindRank(id: string): number {
  let h = 2166136261;
  for (const c of `${SEED}:${id}`) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return h >>> 0;
}

function Backfill({ dataset }: { dataset: string }) {
  const { t } = useI18n();
  const { data, error, isLoading } = useQuery({ queryKey: ["backfill", dataset], queryFn: () => api.backfill(dataset) });
  const download = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const a = document.createElement("a"); a.href = url; a.download = `${dataset}-backfill-plan.json`; a.click(); URL.revokeObjectURL(url);
  };
  return <section className="card space-y-4 p-4" aria-label={t("eval.plan_title")}>
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h2 className="flex items-center gap-2 font-semibold">{t("eval.plan_title")} <AdminTag /></h2><p className="mt-1 max-w-3xl text-xs text-muted">{t("eval.plan_note")}</p></div>
      {data && <button className="btn" onClick={download}>{t("eval.export")}</button>}
    </div>
    {isLoading && <Spinner />}<ErrorBox error={error} />
    {data && <>
      <div className="flex flex-wrap items-center gap-3"><span className="text-2xl font-semibold tnum">{data.runnable}</span><span className="text-sm text-muted">{t("eval.planned")}</span><span className="chip">{usd(data.total_usd)} · {t("eval.estimate")}</span></div>
      {data.budget.hard_cap_blocked && <p className="text-sm text-[var(--serious)]">{t("plan.hard_cap")}</p>}
      <div className="flex flex-wrap gap-2">{["t2v", "i2v"].map(task => <Link key={task} className="btn" to={`/scenarios/${dataset}-${task}`}>{t("eval.open_pack")} · {task.toUpperCase()} →</Link>)}</div>
      <div className="max-h-[420px] overflow-auto"><table className="eval-table"><thead><tr><th>{t("eval.case")}</th><th>{t("eval.model")}</th><th>{t("eval.status")}</th><th>{t("eval.settings")}</th><th>{t("eval.estimate")}</th></tr></thead>
        <tbody>{data.items.map(i => <tr key={`${i.case_key}-${i.model}`}><td>{i.case_key}</td><td>{i.model}</td><td>{i.existing_result ? t("eval.available") : i.previous_attempt ? t("eval.failed") : i.ok ? t("eval.missing") : i.reason}</td><td>{i.effective && `${i.effective.resolution} · ${i.effective.duration}s · ${i.effective.ratio}`}{i.adjustments?.map((a, n) => <p key={n} className="text-xs text-muted">{a.field}: {String(a.requested)} → {String(a.used)}</p>)}{i.warnings?.map(w => <p key={w} className="text-xs text-muted">{w}</p>)}</td><td>{usd(i.estimate?.usd)}</td></tr>)}</tbody></table></div>
    </>}
  </section>;
}

function Comparison({ detail, requestedJob, blind, setBlind }: { detail: EvaluationDetail; requestedJob: string | null; blind: boolean; setBlind: (on: boolean) => void }) {
  const { t, tx } = useI18n();
  // A deployment can contain several inference configurations. Keep model and
  // hardware identity intact while giving each step count its own comparison slot.
  const comparisonGroups = useMemo(() => detail.groups.flatMap(group => {
    if (group.provider !== "self") return [group];
    const buckets = new Map<number | null, EvaluationGroup["samples"]>();
    for (const sample of group.samples) {
      const value = sample.provenance.requested_num_inference_steps;
      const steps = typeof value === "number" && value > 0 ? value : null;
      buckets.set(steps, [...(buckets.get(steps) ?? []), sample]);
    }
    return [...buckets].map(([steps, samples]) => ({
      ...group, id: `${group.id}:steps:${steps ?? "unknown"}`,
      label: `${group.label} · ${steps == null ? t("eval.steps_unrecorded") : `${steps} ${t("eval.steps")}`}`,
      samples, default_job_id: samples.find(j => j.id === group.default_job_id)?.id
        ?? samples.find(j => j.provenance.preferred)?.id ?? samples[0].id,
    }));
  }), [detail.groups, t]);
  // Once shuffled for blind viewing, keep that order after reveal so each clip stays where it was judged.
  const [shuffled, setShuffled] = useState(blind);
  const [audioId, setAudioId] = useState("");
  const [loop, setLoop] = useState(true);
  const [model, setModel] = useState("");
  const [provider, setProvider] = useState("");
  const [selection, setSelection] = useState<Record<string, string>>(() => Object.fromEntries(
    comparisonGroups.filter(g => g.samples.some(j => j.id === requestedJob)).map(g => [g.id, requestedJob!]),
  ));
  const refs = useRef(new Map<string, HTMLVideoElement>());
  const filtered = comparisonGroups.filter(g => (!model || g.model === model) && (!provider || g.provider === provider));
  const groups = shuffled ? [...filtered].sort((a, b) => blindRank(a.id) - blindRank(b.id)) : filtered;
  const selfSamples = detail.groups.filter(g => g.provider === "self").flatMap(g => g.samples);
  const hasEightSteps = selfSamples.some(j => j.provenance.requested_num_inference_steps === 8);
  const samples = groups.map(g => g.samples.find(j => j.id === selection[g.id]) ?? g.samples.find(j => j.id === g.default_job_id) ?? g.samples[0]);
  const gaps = detail.coverage.filter(c => GAP_STATUSES.includes(c.status) && (!model || c.model === model) && (!provider || provider === "tokenbay"));
  const ids = samples.map(j => j.id).join(",");
  const register = useCallback((id: string, video: HTMLVideoElement | null) => { if (video) refs.current.set(id, video); else refs.current.delete(id); }, []);
  const videos = useCallback(() => ids.split(",").map(id => refs.current.get(id)).filter(Boolean) as HTMLVideoElement[], [ids]);
  useEffect(() => { setAudioId(""); }, [ids, blind]);
  const seek = (time: number) => videos().forEach(v => { v.pause(); v.currentTime = Math.max(0, Math.min(time, (v.duration || time) - .05)); });
  const name = (i: number) => blind ? t("board.clip", { x: String.fromCharCode(65 + i) }) : groups[i].label;
  const toggleBlind = () => { setModel(""); setProvider(""); if (!blind) setShuffled(true); setBlind(!blind); };
  const setSound = (id: string) => {
    // Also enforce exclusivity for native video controls, not only the shared selector.
    for (const [key, v] of refs.current) v.muted = key !== id;
    setAudioId(id);
  };
  const sampleText = (s: EvaluationSample, n: number) => [
    s.provenance.phase ? tx(`phase.${s.provenance.phase}`, String(s.provenance.phase)) : `${t("eval.sample_n", { n })} · ${tx(`status.${s.status}`, s.status)}`,
    s.provenance.seed != null ? `${t("eval.seed")} ${s.provenance.seed}` : null,
    IS_ADMIN ? (s.experiment_id ?? s.id) : null,
  ].filter(Boolean).join(" · ");
  return <div className="space-y-4">
    <div className="card p-4 space-y-3">
      <div className="flex flex-wrap items-center gap-2"><TaskBadge task={detail.case.task} /><span className="text-xs text-muted">{detail.case.key}</span><h2 className="font-semibold">{detail.case.title}</h2><span className="ml-auto chip">{detail.case.n_results} {t("eval.results")}</span></div>
      <div className="flex flex-col gap-4 sm:flex-row">
        {Object.entries(detail.case.assets).flatMap(([role, value]) => (Array.isArray(value) ? value : [value]).map(id => <a key={`${role}-${id}`} href={`/api/assets/${id}/file`} target="_blank" rel="noreferrer"><img src={`/api/assets/${id}/file`} alt={`${t("eval.input")} · ${role}`} className="max-h-40 w-56 rounded-lg bg-surface-2 object-contain" /></a>))}
        <div className="min-w-0 flex-1"><div className="label mb-1">{t("eval.prompt")}</div><p className="text-[13px] leading-7 text-ink-2 whitespace-pre-wrap">{detail.case.prompt}</p></div>
      </div>
      <details><summary className="text-xs text-muted cursor-pointer">{t("eval.requirements")}</summary><ul className="grid gap-1 pt-2 text-xs text-ink-2 sm:grid-cols-2">{detail.case.checklist.map(c => <li key={c}>☐ {c}</li>)}</ul></details>
      {!blind && selfSamples.length > 0 && <p className="border-t border-line pt-3 text-xs text-ink-2">{t("eval.steps_note")} {t(hasEightSteps ? "eval.eight_steps_available" : "eval.eight_steps_missing")}</p>}
    </div>
    <HowToRead detail={detail} shown={groups.map((group, i) => ({ group, sample: samples[i] }))} gaps={gaps} blind={blind} onBlind={toggleBlind} />
    <div className="flex flex-wrap items-center gap-2">
      {!blind && <>
        <select aria-label={t("eval.model")} className="input !w-auto" value={model} onChange={e => setModel(e.target.value)}><option value="">{t("eval.all_models")}</option>{[...new Set(detail.groups.map(g => g.model))].map(m => <option key={m}>{m}</option>)}</select>
        <select aria-label={t("bench.platform")} className="input !w-auto" value={provider} onChange={e => setProvider(e.target.value)}><option value="">{t("eval.all_deployments")}</option>{[...new Set(detail.groups.map(g => g.provider))].map(p => <option key={p} value={p}>{p === "official_api" ? t("bench.official") : p === "self" ? t("bench.baseline") : t("src.tokenbay")}</option>)}</select>
      </>}
      <button className={cls("btn ml-auto", blind && "btn-primary")} onClick={toggleBlind}>{blind ? t("board.reveal") : t("board.blind")}</button>
    </div>
    {!!samples.length && <SyncBar key={ids} videos={videos} duration={Math.max(1, ...samples.map(j => j.media.duration ?? 0))} audioId={audioId} setAudioId={setSound} audioOptions={samples.filter(j => j.status === "succeeded").map(j => ({ id: j.id, label: name(samples.indexOf(j)) }))} loop={loop} setLoop={setLoop} />}
    {!samples.length && <p className="card p-8 text-center text-muted">{t("eval.no_results")}</p>}
    <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2 2xl:grid-cols-3">
      {samples.map((j, i) => {
        const g = groups[i];
        const failedEarlier = g.samples.filter(s => s.id !== j.id && s.status !== "succeeded" && TERMINAL.has(s.status)).length;
        return <section key={g.id} className="min-w-0 space-y-2">
          {!blind && <div className="flex flex-wrap items-center gap-2 px-1 text-xs">
            <SourceTag provider={g.provider} vendor={j.vendor} hardware={g.hardware} offCatalog={offCatalog(g, detail)} />
            {j.status === "succeeded" && failedEarlier > 0 && <span className="chip">{t("src.failed_attempts", { n: failedEarlier })}</span>}
            {g.samples.length > 1 && <select className="input min-w-0 !w-auto !max-w-full !py-1 flex-1" aria-label={`${t("eval.samples")} ${g.label}`} value={j.id} onChange={e => setSelection({ ...selection, [g.id]: e.target.value })}>
              {g.samples.map((sample, n) => <option key={sample.id} value={sample.id}>{sample.provenance.preferred ? "● " : ""}{sampleText(sample, n + 1)}</option>)}
            </select>}
          </div>}
          <JobCard job={{ ...j, model_label: g.label, label: String.fromCharCode(65 + i) }} rating={j.rating} blind={blind} picked={false} pickEnabled={false} onPick={() => undefined} registerVideo={register} onSeek={seek} muted={audioId !== j.id} onSound={setSound} judgeEnabled={j.judge_status === "done"} editable={j.editable} />
        </section>;
      })}
    </div>
    {!blind && gaps.length > 0 && <div className="card flex flex-wrap items-center gap-2 p-3 text-xs"><span className="label mr-1">{t("eval.not_in_comparison")}</span>{gaps.map(c => <span key={c.model} className="chip !whitespace-normal" title={IS_ADMIN && c.reason ? c.reason : undefined}>{c.label} · {tx(`eval.pub.${c.status}`, c.status)}</span>)}</div>}
    {!blind && <details className="card p-4"><summary className="font-medium cursor-pointer text-sm">{t("eval.coverage")}</summary><div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{detail.coverage.map(c => <div key={c.model} title={IS_ADMIN && c.reason ? c.reason : undefined} className="rounded-lg border border-line px-3 py-2 text-xs flex justify-between gap-3"><span>{c.label}</span><span className={c.status === "available" ? "text-[var(--good)]" : "text-muted"}>{tx(`eval.pub.${c.status}`, c.status)}</span></div>)}</div></details>}
  </div>;
}

export default function ResultsPage() {
  const { t, tr } = useI18n();
  const [params, setParams] = useSearchParams();
  const [showPlan, setShowPlan] = useState(false);
  // Blind mode lives in the URL: it survives switching scenarios and can be shared as ?blind=1.
  const blind = params.get("blind") === "1";
  const go = (next: Record<string, string>) => setParams(blind ? { ...next, blind: "1" } : next);
  const setBlind = (on: boolean) => setParams(prev => { const q = new URLSearchParams(prev); if (on) q.set("blind", "1"); else q.delete("blind"); return q; }, { replace: true });
  const { data: sets, error, isLoading } = useQuery({ queryKey: ["evaluations"], queryFn: api.evaluations });
  const dataset = sets?.find(d => d.id === params.get("dataset")) ?? sets?.find(d => d.id === "h3-v1") ?? sets?.[0];
  const chosen = dataset?.cases.find(c => c.key === params.get("case")) ?? dataset?.cases[0];
  const { data, error: detailError, isLoading: loadingDetail } = useQuery({ queryKey: ["evaluation", dataset?.id, chosen?.key], queryFn: () => api.evaluation(dataset!.id, chosen!.key), enabled: Boolean(chosen), refetchInterval: q => q.state.data?.groups.some(g => g.samples.some(j => !["succeeded", "failed", "blocked", "timeout", "stopped", "submit_unknown"].includes(j.status))) ? 3000 : false });
  const count = useMemo(() => dataset?.cases.reduce((sum, c) => sum + c.n_results, 0) ?? 0, [dataset]);
  return <div className="space-y-6">
    <div className="eval-hero"><div><p className="eval-eyebrow">VGEN / EVALUATION</p><h1 className="text-2xl sm:text-3xl font-semibold tracking-tight mt-2">{t("eval.title")}</h1><p className="mt-2 max-w-2xl text-sm text-ink-2">{t("eval.intro")}</p></div><Link className="btn" to="/benchmarks">{t("eval.view_benchmark")}</Link></div>
    {isLoading && <Spinner />}<ErrorBox error={error} />
    {sets && !sets.length && <div className="card p-8 text-muted">{t("eval.empty")}</div>}
    {dataset && <>
      <div className="flex flex-wrap items-center gap-3"><label className="label">{t("eval.dataset")}<select className="input !w-auto ml-2" value={dataset.id} onChange={e => { setShowPlan(false); go({ dataset: e.target.value }); }}>{sets?.map(d => <option value={d.id} key={d.id}>{tr(d.title)}</option>)}</select></label><span className="text-xs text-muted">{dataset.cases.length} {t("eval.cases")} · {count} {t("eval.results")}</span>{IS_ADMIN && dataset.id !== "tokenbay" && <span className="ml-auto"><AdminOnly inline><button className="btn" onClick={() => setShowPlan(!showPlan)}>{showPlan ? "▾" : "▸"} {t("eval.backfill")}</button></AdminOnly></span>}</div>
      {IS_ADMIN && showPlan && <Backfill dataset={dataset.id} />}
      <div className="grid gap-5 lg:grid-cols-[190px_minmax(0,1fr)]">
        <aside className="flex gap-1 overflow-x-auto lg:flex-col lg:overflow-visible" aria-label={t("eval.cases")}>{dataset.cases.map(c => <button key={c.id} onClick={() => go({ dataset: dataset.id, case: c.key })} className={cls("eval-case", chosen?.key === c.key && "eval-case-active")}><span className="text-[10px] uppercase tracking-wider opacity-65">{c.key} · {c.task}</span><span className="text-[13px] font-medium">{c.title}</span><span className="text-[11px] opacity-65">{c.n_results} {t("eval.results")}</span></button>)}</aside>
        <div className="min-w-0">{loadingDetail && <Spinner />}<ErrorBox error={detailError} />{data && <Comparison key={`${data.case.id}:${params.get("job") ?? ""}`} detail={data} requestedJob={params.get("job")} blind={blind} setBlind={setBlind} />}</div>
      </div>
    </>}
  </div>;
}
