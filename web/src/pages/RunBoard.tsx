import SyncBar from "../components/SyncBar";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, IS_PUBLIC } from "../api";
import { useApiKey } from "../apiKey";
import { useI18n } from "../i18n";
import type { Job, RunRequest } from "../types";
import { ago, cls, usd } from "../util";
import { ErrorBox, Spinner, TaskBadge } from "../components/common";
import JobCard from "../components/JobCard";
import { KeyField } from "../components/KeyField";
import { PlanDialog } from "../components/RunSetup";

/** Hosted demo, own run: hand the visitor's key back after a server restart, or take one for paid clip actions. */
function KeyPanel({ runId, needsKey }: { runId: string; needsKey: boolean }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const stored = useApiKey();
  const [draft, setDraft] = useState(stored.key);
  const [remember, setRemember] = useState(stored.remember);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const tried = useRef(false);

  const attach = useCallback(
    async (key: string) => {
      setBusy(true);
      setErr(null);
      try {
        await api.attachKey(runId, key);
        await qc.invalidateQueries({ queryKey: ["run", runId] });
      } catch (e) {
        setErr(e);
      } finally {
        setBusy(false);
      }
    },
    [runId, qc],
  );

  // a key saved in this browser resumes the run without asking
  useEffect(() => {
    if (needsKey && stored.key && !tried.current) {
      tried.current = true;
      void attach(stored.key);
    }
  }, [needsKey, stored.key, attach]);

  if (!needsKey && stored.key) return null;
  return (
    <div className="card space-y-3 p-3" style={{ borderLeft: "4px solid var(--warn)" }}>
      <p className="text-[13px] text-ink-2">{needsKey ? t("key.resume_note") : t("key.actions_note")}</p>
      <KeyField value={draft} onChange={setDraft} remember={remember} onRemember={setRemember} />
      <div className="flex items-center gap-2">
        <button
          className="btn btn-primary"
          disabled={!draft || busy}
          onClick={() => {
            stored.save(draft, remember);
            if (needsKey) void attach(draft);
          }}
        >
          {busy ? <Spinner className="text-surface" /> : null}
          {needsKey ? t("key.resume") : t("key.use")}
        </button>
      </div>
      <ErrorBox error={err} />
    </div>
  );
}


export default function RunBoardPage() {
  const { runId = "" } = useParams();
  const { t, lang } = useI18n();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { data, error, isLoading } = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.run(runId),
    refetchInterval: (q) => {
      const d = q.state.data;
      if (!d) return 3000;
      const judging = d.jobs.some((j) => j.judge_status === "pending" || j.judge_status === "running");
      return d.active > 0 || judging ? 2500 : false;
    },
  });
  const [blind, setBlind] = useState(false);
  const [audioId, setAudioId] = useState("");
  const [loop, setLoop] = useState(true);
  const [promptKey, setPromptKey] = useState<string>("");
  const [rerunOpen, setRerunOpen] = useState(false);
  const [deleteArmed, setDeleteArmed] = useState(false);
  const [actionErr, setActionErr] = useState<unknown>(null);
  const videoRefs = useRef(new Map<string, HTMLVideoElement>());
  const run = data?.run;
  // stable while polling (react-query keeps `run` identical when it hasn't changed), so the plan isn't re-fetched
  const rerunRequest = useMemo<RunRequest | null>(
    () =>
      run
        ? {
            title: `${run.title} (re-run)`,
            kind: run.kind === "probe" ? "compare" : run.kind,
            scenario_id: run.scenario_id,
            category: run.category,
            task: run.task,
            lang: run.lang,
            prompts: run.prompts,
            models: run.models,
            params: Object.fromEntries(Object.entries(run.params).filter(([k]) => ["duration", "resolution", "ratio", "audio", "seed", "negative_prompt"].includes(k))),
            model_overrides: (run.params.model_overrides as Record<string, Record<string, unknown>>) ?? {},
            assets: (run.params.assets as Record<string, string | string[]>) ?? {},
            judge: run.judge,
            allow_experimental: Boolean(run.params.allow_experimental),
          }
        : null,
    [run],
  );

  const prompts = data?.run.prompts ?? [];
  const current = promptKey || prompts[0]?.key || "";
  useEffect(() => {
    setAudioId("");
  }, [blind, current]);

  const jobs = useMemo(() => (data?.jobs ?? []).filter((j) => j.prompt_key === current).sort((a, b) => (blind ? a.label.localeCompare(b.label) : a.model_label.localeCompare(b.model_label))), [data, current, blind]);
  const register = useCallback((id: string, el: HTMLVideoElement | null) => {
    if (el) videoRefs.current.set(id, el);
    else videoRefs.current.delete(id);
  }, []);
  const ids = jobs.map((j) => j.id).join(",");
  const videos = useCallback(() => ids.split(",").map((id) => videoRefs.current.get(id)).filter(Boolean) as HTMLVideoElement[], [ids]);
  const maxDur = Math.max(1, ...jobs.map((j) => j.media?.duration ?? 0));
  const seekAll = (s: number) => videos().forEach((v) => { v.pause(); v.currentTime = Math.min(s, (v.duration || s) - 0.05); });

  if (isLoading) return <Spinner />;
  if (error || !data || !run) return <ErrorBox error={error} />;
  const prompt = prompts.find((p) => p.key === current);
  const pickFor = (key: string) => data.picks[key];
  const pick = async (job: Job) => {
    setActionErr(null);
    try {
      await api.pick(run.id, job.prompt_key, pickFor(job.prompt_key) === job.id ? "" : job.id);
      qc.invalidateQueries({ queryKey: ["run", run.id] });
    } catch (e) {
      setActionErr(e);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <button className="text-xs text-muted hover:text-ink" onClick={() => nav("/runs")}>
            ← {t("nav.runs")}
          </button>
          <div className="flex flex-wrap items-center gap-2">
            <TaskBadge task={run.task} />
            <h1 className="text-xl font-semibold">{run.title}</h1>
            {!data.mine && <span className="chip">{t("board.example")}</span>}
          </div>
          <div className="text-xs text-muted">
            {ago(run.created_at, lang)} · {t("plan.clips", { n: data.jobs.length })} · {usd(data.spent_usd)}
            {data.active ? ` · ${t("runs.active", { n: data.active })}` : ""}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className={cls("btn", blind && "btn-primary")}>
            <input type="checkbox" className="sr-only" checked={blind} onChange={(e) => setBlind(e.target.checked)} />
            {blind ? `👁 ${t("board.reveal")}` : `🙈 ${t("board.blind")}`}
          </label>
          <button className="btn" disabled={run.kind === "experiment"} onClick={() => setRerunOpen(true)}>
            ↻ {t("board.rerun")}
          </button>
          <a className="btn" href={`/api/runs/${run.id}/export?format=csv`}>
            ⤓ CSV
          </a>
          <a className="btn" href={`/api/runs/${run.id}/export?format=json`}>
            ⤓ JSON
          </a>
          {data.mine &&
            (deleteArmed ? (
              <button
                className="btn"
                style={{ borderColor: "var(--critical)" }}
                onClick={async () => {
                  setActionErr(null);
                  try {
                    await api.deleteRun(run.id);
                    qc.invalidateQueries({ queryKey: ["runs"] });
                    nav("/runs");
                  } catch (e) {
                    setDeleteArmed(false);
                    setActionErr(e);
                  }
                }}
              >
                {t("board.delete_confirm")}
              </button>
            ) : (
              <button className="btn" disabled={data.active > 0 || run.kind === "experiment"} onClick={() => setDeleteArmed(true)}>
                {t("board.delete")}
              </button>
            ))}
        </div>
      </div>
      <ErrorBox error={actionErr} />
      {IS_PUBLIC && data.mine && <KeyPanel runId={run.id} needsKey={data.needs_key} />}

      {prompts.length > 1 && (
        <div className="flex flex-wrap gap-1.5">
          {prompts.map((p) => (
            <button key={p.key} className={cls("btn", p.key === current && "btn-primary")} onClick={() => setPromptKey(p.key)}>
              {p.title || p.key}
              {pickFor(p.key) ? " ★" : ""}
            </button>
          ))}
        </div>
      )}
      {prompt && (
        <div className="card p-3 text-[13px]">
          <div className="label mb-1">{t("board.prompt")}</div>
          <p className="leading-relaxed text-ink-2">{prompt.text}</p>
          {prompt.checklist?.length > 0 && (
            <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted">
              {prompt.checklist.map((c) => (
                <li key={c}>☐ {c}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <SyncBar key={ids} videos={videos} duration={maxDur} audioId={audioId} setAudioId={setAudioId} audioOptions={jobs.filter(j => j.status === "succeeded").map(j => ({ id: j.id, label: blind ? j.label : j.model_label }))} loop={loop} setLoop={setLoop} />

      <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
        {jobs.map((j) => (
          <JobCard
            key={j.id}
            job={j}
            rating={data.ratings[j.id]}
            blind={blind}
            picked={pickFor(j.prompt_key) === j.id}
            onPick={() => pick(j)}
            registerVideo={register}
            onSeek={seekAll}
            muted={j.id !== audioId}
            onSound={setAudioId}
            judgeEnabled={run.judge || Boolean(j.judge_status)}
            editable={data.mine}
          />
        ))}
      </div>
      <PlanDialog request={rerunOpen ? rerunRequest : null} open={rerunOpen} onClose={() => setRerunOpen(false)} />
    </div>
  );
}
