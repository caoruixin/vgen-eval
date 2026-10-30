import { useMemo, useState } from "react";
import { useI18n, type TKey } from "../i18n";
import type { Lang, RunRequest, Task } from "../types";
import AssetPicker from "../components/AssetPicker";
import { ModelPicker, ParamsForm, PlanDialog, type Params } from "../components/RunSetup";

const TASKS: Task[] = ["t2v", "i2v", "flf2v", "r2v", "v2v_edit", "extend", "a2v"];

const NEEDS: Record<Task, ("first_frame" | "last_frame" | "references" | "source_video" | "audio_input")[]> = {
  t2v: [],
  i2v: ["first_frame"],
  flf2v: ["first_frame", "last_frame"],
  r2v: ["references"],
  v2v_edit: ["source_video", "references"],
  extend: ["source_video"],
  a2v: ["first_frame", "audio_input"],
};

const REQUIRED: Record<Task, string[]> = {
  t2v: [],
  i2v: ["first_frame"],
  flf2v: ["first_frame", "last_frame"],
  r2v: ["references"],
  v2v_edit: ["source_video"],
  extend: ["source_video"],
  a2v: ["audio_input"],
};

export default function ComparePage() {
  const { t, lang } = useI18n();
  const [task, setTask] = useState<Task>("t2v");
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [checklist, setChecklist] = useState("");
  const [assets, setAssets] = useState<Record<string, string | string[] | undefined>>({});
  const [params, setParams] = useState<Params>({ duration: 6, resolution: "720p", ratio: "16:9", audio: false, seed: null, negative_prompt: "" });
  const [models, setModels] = useState<string[]>([]);
  const [judge, setJudge] = useState(true);
  const [experimental, setExperimental] = useState(false);
  const [planOpen, setPlanOpen] = useState(false);

  const promptLang: Lang = /[一-鿿]/.test(prompt) ? "zh" : "en";
  const cleanAssets = Object.fromEntries(
    Object.entries(assets).filter(([k, v]) => NEEDS[task].includes(k as never) && (Array.isArray(v) ? v.length : v)),
  ) as Record<string, string | string[]>;
  const missing = REQUIRED[task].filter((k) => !cleanAssets[k]);

  const request: RunRequest = useMemo(
    () => ({
      title: title || undefined,
      kind: "compare",
      task,
      lang: promptLang,
      prompts: [{ key: "p1", title: title || prompt.slice(0, 40), text: prompt, checklist: checklist.split("\n").map((s) => s.trim()).filter(Boolean) }],
      models,
      params: { ...params, negative_prompt: params.negative_prompt || undefined, seed: params.seed ?? undefined },
      assets: cleanAssets,
      judge,
      allow_experimental: experimental,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [task, title, prompt, checklist, JSON.stringify(cleanAssets), params, models, judge, experimental, promptLang],
  );

  const changeTask = (tk: Task) => {
    setTask(tk);
    if (["i2v", "flf2v", "a2v", "v2v_edit"].includes(tk)) setParams((p) => ({ ...p, ratio: "auto" }));
    else if (params.ratio === "auto") setParams((p) => ({ ...p, ratio: "16:9" }));
    if (["v2v_edit", "extend", "a2v"].includes(tk)) setExperimental(true);
  };

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{t("nav.compare")}</h1>
      </div>
      <div className="card space-y-6 p-5">
        <div className="flex flex-wrap gap-1.5">
          {TASKS.map((tk) => (
            <button key={tk} className={task === tk ? "btn btn-primary" : "btn"} onClick={() => changeTask(tk)}>
              {t(`task.${tk}` as TKey)}
            </button>
          ))}
        </div>
        <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
          <div className="space-y-3">
            <label className="block space-y-1">
              <span className="label">{t("setup.prompt")}</span>
              <textarea className="input min-h-[110px]" value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder={lang === "zh" ? "描述你想要的视频…（可用 {ref1}、{ref2} 引用参考图）" : "Describe the video… (use {ref1}, {ref2} to point at reference images)"} />
            </label>
            <label className="block space-y-1">
              <span className="label">{t("setup.checklist")}</span>
              <textarea className="input min-h-[80px]" value={checklist} onChange={(e) => setChecklist(e.target.value)} placeholder={lang === "zh" ? "主体是一只狐狸\n镜头跟随主体移动" : "A fox is the main subject\nThe camera tracks the subject"} />
            </label>
            <label className="block space-y-1">
              <span className="label">{t("setup.title")}</span>
              <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
            </label>
          </div>
          <div className="space-y-3">
            {NEEDS[task].includes("first_frame") && (
              <AssetPicker kind="image" label={t("assets.first_frame")} value={assets.first_frame} onChange={(v) => setAssets({ ...assets, first_frame: v })} />
            )}
            {NEEDS[task].includes("last_frame") && (
              <AssetPicker kind="image" label={t("assets.last_frame")} value={assets.last_frame} onChange={(v) => setAssets({ ...assets, last_frame: v })} />
            )}
            {NEEDS[task].includes("references") && (
              <AssetPicker kind="image" multiple label={t("assets.references")} value={assets.references} onChange={(v) => setAssets({ ...assets, references: v })} />
            )}
            {NEEDS[task].includes("source_video") && (
              <AssetPicker kind="video" label={t("assets.source_video")} value={assets.source_video} onChange={(v) => setAssets({ ...assets, source_video: v })} />
            )}
            {NEEDS[task].includes("audio_input") && (
              <AssetPicker kind="audio" label={t("assets.audio_input")} value={assets.audio_input} onChange={(v) => setAssets({ ...assets, audio_input: v })} />
            )}
            {NEEDS[task].length === 0 && <p className="text-xs text-muted">{t("task.t2v")}</p>}
          </div>
        </div>
        <section className="space-y-3">
          <h3 className="label uppercase tracking-wide">{t("setup.params")}</h3>
          <ParamsForm value={params} onChange={setParams} task={task} />
        </section>
        <section className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="label uppercase tracking-wide">
              {t("setup.models")} · <span className="normal-case">{t("setup.selected", { n: models.length })}</span>
            </h3>
            <label className="flex items-center gap-2 text-xs text-ink-2">
              <input type="checkbox" checked={experimental} onChange={(e) => setExperimental(e.target.checked)} />
              {t("setup.experimental")}
            </label>
          </div>
          <p className="text-xs text-muted">{t("setup.models_hint")}</p>
          <ModelPicker task={task} selected={models} onChange={setModels} allowExperimental={experimental} />
        </section>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
          <label className="flex items-center gap-2 text-[13px]">
            <input type="checkbox" checked={judge} onChange={(e) => setJudge(e.target.checked)} />
            {t("setup.judge")}
          </label>
          <div className="flex items-center gap-3">
            {missing.length > 0 && <span className="text-xs" style={{ color: "var(--serious)" }}>⚠ {missing.map((m) => t(`assets.${m}` as TKey)).join(", ")}</span>}
            <button className="btn btn-primary" disabled={!prompt.trim() || !models.length || missing.length > 0} onClick={() => setPlanOpen(true)}>
              {t("setup.plan")} →
            </button>
          </div>
        </div>
      </div>
      <PlanDialog request={planOpen ? request : null} open={planOpen} onClose={() => setPlanOpen(false)} />
    </div>
  );
}
