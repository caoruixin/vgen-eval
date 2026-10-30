import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import type { Lang, Pack, RunRequest, Scenarios } from "../types";
import { cls } from "../util";
import { ErrorBox, Spinner, TaskBadge, VendorDot } from "../components/common";
import { ModelPicker, ParamsForm, PlanDialog, useModels, type Params } from "../components/RunSetup";

function assetUrl(id: string) {
  return `/api/assets/${id}/file`;
}

function AssetThumbs({ pack, data, size = 44 }: { pack: Pack; data: Scenarios; size?: number }) {
  const ids = new Set<string>();
  const collect = (a: Record<string, string | string[]>) => Object.values(a).flat().forEach((x) => x && ids.add(x));
  collect(pack.assets);
  pack.prompts.forEach((p) => collect(p.assets));
  const byId = new Map(Object.values(data.assets).filter((a) => a.id).map((a) => [a.id as string, a]));
  const list = [...ids].map((id) => byId.get(id)).filter(Boolean).slice(0, 5);
  if (!list.length) return null;
  return (
    <div className="flex gap-1.5">
      {list.map((a) =>
        a!.kind === "image" ? (
          <img key={a!.id} src={assetUrl(a!.id!)} alt="" className="rounded-md object-cover" style={{ width: size, height: size }} />
        ) : (
          <span key={a!.id} className="grid place-items-center rounded-md bg-surface-3 text-sm" style={{ width: size, height: size }}>
            {a!.kind === "video" ? "▶" : "♪"}
          </span>
        ),
      )}
    </div>
  );
}

function PackCard({ pack, data, onOpen }: { pack: Pack; data: Scenarios; onOpen: () => void }) {
  const { t, tr, lang } = useI18n();
  const { data: models } = useModels();
  const info = (id: string) => models?.models.find((m) => m.id === id);
  const p = pack.params as Record<string, string | number | boolean>;
  return (
    <button onClick={onOpen} className="card group flex h-full flex-col gap-3 p-4 text-left transition hover:border-line-strong">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="mb-1 flex flex-wrap items-center gap-1.5">
            <TaskBadge task={pack.task} />
            <span className="chip tnum">{String(p.ratio === "auto" ? t("setup.auto") : p.ratio)}</span>
            <span className="chip tnum">{String(p.duration)}s</span>
            <span className="chip">{String(p.resolution)}</span>
            {p.audio ? <span className="chip">♪ {t("scen.audio")}</span> : null}
          </div>
          <h3 className="text-[15px] font-semibold leading-snug">{tr(pack.title)}</h3>
        </div>
        <AssetThumbs pack={pack} data={data} />
      </div>
      <p className="text-[13px] leading-relaxed text-ink-2">{tr(pack.description)}</p>
      <div className="flex flex-wrap gap-1">
        {pack.models.map((id) => (
          <span key={id} className={cls("chip", info(id)?.probe?.unavailable && "line-through opacity-50")} title={info(id)?.probe?.unavailable ?? undefined}>
            <VendorDot vendor={info(id)?.vendor ?? ""} size={6} />
            {info(id)?.label ?? id}
          </span>
        ))}
      </div>
      <div className="mt-auto flex items-center justify-between pt-1 text-xs text-muted">
        <span>{pack.prompts.length === 1 ? t("scen.prompt") : t("scen.prompts", { n: pack.prompts.length })}</span>
        <span className="font-medium text-ink group-hover:underline">{t("scen.open")} →</span>
      </div>
      {pack.missing_assets.length > 0 && (
        <div className="text-xs" style={{ color: "var(--serious)" }}>
          ⚠ {t("scen.missing", { names: pack.missing_assets.join(", ") })} — {t("scen.gen_hint")}
        </div>
      )}
      <span className="sr-only">{lang}</span>
    </button>
  );
}

function PackSetup({ pack, data, onClose }: { pack: Pack; data: Scenarios; onClose: () => void }) {
  const { t, tr, lang } = useI18n();
  const [promptLang, setPromptLang] = useState<Lang>(pack.prompts[0]?.prefer_lang ?? lang);
  const [selectedPrompts, setSelectedPrompts] = useState<string[]>(pack.prompts.filter((p) => !p.missing_assets.length).map((p) => p.key));
  const [models, setModels] = useState<string[]>(pack.models);
  const [params, setParams] = useState<Params>({
    duration: Number(pack.params.duration ?? 6),
    resolution: String(pack.params.resolution ?? "720p"),
    ratio: String(pack.params.ratio ?? "16:9"),
    audio: Boolean(pack.params.audio),
  });
  const [judge, setJudge] = useState(true);
  const [experimental, setExperimental] = useState(["v2v_edit", "extend", "a2v"].includes(pack.task));
  const [planOpen, setPlanOpen] = useState(false);
  const { data: registry } = useModels();
  const byId = new Map(Object.values(data.assets).filter((a) => a.id).map((a) => [a.id as string, a]));

  const request: RunRequest = useMemo(
    () => ({
      title: tr(pack.title),
      kind: "pack",
      scenario_id: pack.id,
      category: pack.category,
      task: pack.task,
      lang: promptLang,
      prompts: pack.prompts
        .filter((p) => selectedPrompts.includes(p.key))
        .map((p) => ({
          key: p.key,
          title: p.title[promptLang] ?? p.title.en ?? p.key,
          text: p.text[promptLang] ?? p.text.en ?? "",
          checklist: p.checklist[promptLang] ?? p.checklist.en ?? [],
          assets: p.assets,
        })),
      models,
      params: { ...params },
      model_overrides: pack.model_overrides ?? {},
      assets: pack.assets,
      judge,
      allow_experimental: experimental,
    }),
    [pack, promptLang, selectedPrompts, models, params, judge, experimental, tr],
  );

  const canRun = request.prompts.length > 0 && models.length > 0;
  const overrides = Object.entries(pack.model_overrides ?? {}).filter(([m]) => models.includes(m));

  return (
    <div className="card space-y-6 p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <button className="text-xs text-muted hover:text-ink" onClick={onClose}>
            ← {t("nav.scenarios")}
          </button>
          <div className="flex items-center gap-2">
            <TaskBadge task={pack.task} />
            <h2 className="text-xl font-semibold">{tr(pack.title)}</h2>
          </div>
          <p className="max-w-3xl text-[13px] text-ink-2">{tr(pack.description)}</p>
        </div>
        <div className="max-w-sm text-xs text-ink-2">
          <div className="label mb-1">{t("scen.look_for")}</div>
          <ul className="list-disc space-y-0.5 pl-4">
            {(pack.look_for[lang] ?? pack.look_for.en).map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      </div>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="label uppercase tracking-wide">{t("setup.prompts")}</h3>
          <div className="flex items-center gap-2 text-xs">
            <span className="text-muted">{t("setup.language")}</span>
            <div className="seg">
              <button aria-pressed={promptLang === "en"} onClick={() => setPromptLang("en")}>English</button>
              <button aria-pressed={promptLang === "zh"} onClick={() => setPromptLang("zh")}>中文</button>
            </div>
          </div>
        </div>
        <div className="grid gap-3 lg:grid-cols-3">
          {pack.prompts.map((p) => {
            const on = selectedPrompts.includes(p.key);
            const missing = p.missing_assets.length > 0;
            const assetIds = Object.values({ ...pack.assets, ...p.assets }).flat();
            return (
              <label key={p.key} className={cls("card flex flex-col gap-2 p-3", missing ? "cursor-not-allowed opacity-50" : "cursor-pointer", on ? "border-[var(--focus)]" : "opacity-60")}>
                <div className="flex items-center gap-2">
                  <input type="checkbox" disabled={missing} checked={on} onChange={() => setSelectedPrompts(on ? selectedPrompts.filter((k) => k !== p.key) : [...selectedPrompts, p.key])} />
                  <span className="text-[13px] font-semibold">{p.title[promptLang] ?? p.title.en}</span>
                  {p.prefer_lang && p.prefer_lang !== promptLang && <span className="chip">{t("scen.best_in", { lang: p.prefer_lang === "zh" ? "中文" : "English" })}</span>}
                </div>
                {assetIds.length > 0 && (
                  <div className="flex gap-1.5">
                    {assetIds.map((id) => {
                      const a = byId.get(id);
                      return a?.kind === "image" ? (
                        <img key={id} src={assetUrl(id)} alt="" title={tr(a.label)} className="h-14 w-14 rounded-md object-cover" />
                      ) : (
                        <span key={id} title={a ? tr(a.label) : id} className="grid h-14 w-14 place-items-center rounded-md bg-surface-3">
                          {a?.kind === "video" ? "▶" : "♪"}
                        </span>
                      );
                    })}
                  </div>
                )}
                {missing && (
                  <p className="text-xs" style={{ color: "var(--serious)" }}>
                    ⚠ {t("scen.missing", { names: p.missing_assets.join(", ") })} — {t("scen.gen_hint")}
                  </p>
                )}
                <p className="text-[12.5px] leading-relaxed text-ink-2">{p.text[promptLang] ?? p.text.en}</p>
                <ul className="mt-auto space-y-0.5 border-t border-line pt-2 text-xs text-muted">
                  {(p.checklist[promptLang] ?? p.checklist.en).map((c) => (
                    <li key={c}>☐ {c}</li>
                  ))}
                </ul>
              </label>
            );
          })}
        </div>
      </section>

      <section className="space-y-3">
        <h3 className="label uppercase tracking-wide">{t("setup.params")}</h3>
        <ParamsForm value={params} onChange={setParams} task={pack.task} compact />
        {overrides.length > 0 && (
          <p className="text-xs text-muted">
            {t("setup.overrides")}:{" "}
            {overrides
              .map(([m, o]) => `${registry?.models.find((x) => x.id === m)?.label ?? m} → ${Object.entries(o).map(([k, v]) => `${k} ${v}`).join(", ")}`)
              .join(" · ")}
          </p>
        )}
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
        <ModelPicker task={pack.task} selected={models} onChange={setModels} allowExperimental={experimental} />
      </section>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
        <label className="flex items-center gap-2 text-[13px]">
          <input type="checkbox" checked={judge} onChange={(e) => setJudge(e.target.checked)} />
          {t("setup.judge")}
        </label>
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted">
            {t("plan.clips", { n: request.prompts.length * models.length })}
          </span>
          <button className="btn btn-primary" disabled={!canRun} onClick={() => setPlanOpen(true)}>
            {t("setup.plan")} →
          </button>
        </div>
      </div>
      <PlanDialog request={planOpen ? request : null} open={planOpen} onClose={() => setPlanOpen(false)} />
    </div>
  );
}

export default function ScenariosPage() {
  const { t, tr } = useI18n();
  const { packId } = useParams();
  const nav = useNavigate();
  const { data, error, isLoading } = useQuery({ queryKey: ["scenarios"], queryFn: api.scenarios });
  const [cat, setCat] = useState<string>("all");
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [packId]);
  if (isLoading) return <Spinner />;
  if (error || !data) return <ErrorBox error={error} />;
  const pack = data.packs.find((p) => p.id === packId);
  if (pack) return <PackSetup key={pack.id} pack={pack} data={data} onClose={() => nav("/")} />;
  const cats = Object.keys(data.categories);
  const packs = data.packs.filter((p) => cat === "all" || p.category === cat);
  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{t("scen.title")}</h1>
        <p className="max-w-3xl text-[13.5px] text-ink-2">{t("scen.intro")}</p>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {["all", ...cats].map((c) => (
          <button key={c} className={cls("btn", cat === c && "btn-primary")} onClick={() => setCat(c)}>
            {c === "all" ? t("scen.all") : tr(data.categories[c])}
          </button>
        ))}
      </div>
      {cats
        .filter((c) => cat === "all" || c === cat)
        .map((c) => {
          const group = packs.filter((p) => p.category === c);
          if (!group.length) return null;
          return (
            <section key={c} className="space-y-3">
              <h2 className="label uppercase tracking-wide">{tr(data.categories[c])}</h2>
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {group.map((p) => (
                  <PackCard key={p.id} pack={p} data={data} onOpen={() => nav(`/scenarios/${p.id}`)} />
                ))}
              </div>
            </section>
          );
        })}
    </div>
  );
}
