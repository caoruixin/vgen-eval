import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import type { Job } from "../types";
import { ago, cls, usd } from "../util";
import { ErrorBox, Spinner, TaskBadge } from "../components/common";
import JobCard from "../components/JobCard";
import { PlanDialog } from "../components/RunSetup";

function SyncBar({ videos, duration, muted, setMuted, loop, setLoop }: { videos: () => HTMLVideoElement[]; duration: number; muted: boolean; setMuted: (m: boolean) => void; loop: boolean; setLoop: (l: boolean) => void }) {
  const { t } = useI18n();
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const loopRef = useRef(loop);
  loopRef.current = loop;
  const groupPlaying = useRef(false);
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const vs = videos();
      // the longest clip is the clock; shorter clips hold their last frame until the group restarts
      const master = vs.reduce<HTMLVideoElement | undefined>((a, v) => (!a || (v.duration || 0) > (a.duration || 0) ? v : a), undefined);
      if (master) setTime(master.currentTime);
      const anyPlaying = vs.some((v) => !v.paused && !v.ended);
      if (master && groupPlaying.current && loopRef.current && master.ended) {
        vs.forEach((v) => {
          v.currentTime = 0;
          void v.play().catch(() => undefined);
        });
      } else if (groupPlaying.current && !anyPlaying && !loopRef.current) {
        groupPlaying.current = false;
      }
      setPlaying(anyPlaying || (groupPlaying.current && loopRef.current));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [videos]);
  const seek = (s: number) => videos().forEach((v) => (v.currentTime = Math.min(s, Math.max(0, (v.duration || s) - 0.05))));
  const playAll = () => {
    const vs = videos();
    const master = vs.reduce<HTMLVideoElement | undefined>((a, v) => (!a || (v.duration || 0) > (a.duration || 0) ? v : a), undefined);
    const at = master && !master.ended ? master.currentTime : 0;
    groupPlaying.current = true;
    vs.forEach((v) => {
      v.currentTime = Math.min(at, Math.max(0, (v.duration || at) - 0.05));
      void v.play().catch(() => undefined);
    });
  };
  const pauseAll = () => {
    groupPlaying.current = false;
    videos().forEach((v) => v.pause());
  };
  return (
    <div className="card sticky top-[52px] z-20 flex flex-wrap items-center gap-3 px-3 py-2">
      <button className="btn btn-primary" onClick={() => (playing ? pauseAll() : playAll())}>
        {playing ? `❚❚ ${t("board.pause")}` : `▶ ${t("board.play")}`}
      </button>
      <button className="btn" onClick={() => { seek(0); }}>⏮</button>
      <input type="range" min={0} max={Math.max(1, duration)} step={0.04} value={Math.min(time, duration)} onChange={(e) => seek(Number(e.target.value))} className="min-w-[200px] flex-1" aria-label="scrub" />
      <span className="tnum w-24 text-xs text-ink-2">
        {time.toFixed(1)}s / {duration.toFixed(1)}s
      </span>
      <label className="flex items-center gap-1.5 text-xs">
        <input type="checkbox" checked={loop} onChange={(e) => setLoop(e.target.checked)} /> {t("board.loop")}
      </label>
      <button className="btn" onClick={() => setMuted(!muted)}>
        {muted ? `🔇 ${t("board.mute")}` : `🔊 ${t("board.sound")}`}
      </button>
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
  const [muted, setMuted] = useState(true);
  const [loop, setLoop] = useState(true);
  const [promptKey, setPromptKey] = useState<string>("");
  const [rerunOpen, setRerunOpen] = useState(false);
  const [deleteArmed, setDeleteArmed] = useState(false);
  const videoRefs = useRef(new Map<string, HTMLVideoElement>());

  const prompts = data?.run.prompts ?? [];
  const current = promptKey || prompts[0]?.key || "";
  useEffect(() => {
    if (blind) setMuted(true);
  }, [blind]);

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
  if (error || !data) return <ErrorBox error={error} />;
  const { run } = data;
  const prompt = prompts.find((p) => p.key === current);
  const pickFor = (key: string) => data.picks[key];
  const pick = async (job: Job) => {
    await api.pick(run.id, job.prompt_key, pickFor(job.prompt_key) === job.id ? "" : job.id);
    qc.invalidateQueries({ queryKey: ["run", run.id] });
  };
  const rerunRequest = {
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
          <button className="btn" onClick={() => setRerunOpen(true)}>
            ↻ {t("board.rerun")}
          </button>
          <a className="btn" href={`/api/runs/${run.id}/export?format=csv`}>
            ⤓ CSV
          </a>
          <a className="btn" href={`/api/runs/${run.id}/export?format=json`}>
            ⤓ JSON
          </a>
          {deleteArmed ? (
            <button
              className="btn"
              style={{ borderColor: "var(--critical)" }}
              onClick={async () => {
                await api.deleteRun(run.id);
                qc.invalidateQueries({ queryKey: ["runs"] });
                nav("/runs");
              }}
            >
              {t("board.delete_confirm")}
            </button>
          ) : (
            <button className="btn" disabled={data.active > 0} onClick={() => setDeleteArmed(true)}>
              {t("board.delete")}
            </button>
          )}
        </div>
      </div>

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

      <SyncBar videos={videos} duration={maxDur} muted={muted} setMuted={setMuted} loop={loop} setLoop={setLoop} />

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
            muted={muted}
            judgeEnabled={run.judge || Boolean(j.judge_status)}
          />
        ))}
      </div>
      <PlanDialog request={rerunOpen ? rerunRequest : null} open={rerunOpen} onClose={() => setRerunOpen(false)} />
    </div>
  );
}
