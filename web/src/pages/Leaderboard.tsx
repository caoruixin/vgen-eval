import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n, type TKey } from "../i18n";
import type { LeaderRow, Task } from "../types";
import { cls, pct, secs, usd, vendorColor } from "../util";
import { ErrorBox, Meter, VendorDot } from "../components/common";
import Scatter from "../components/Scatter";

const TASKS: Task[] = ["t2v", "i2v", "flf2v", "r2v", "v2v_edit", "extend", "a2v"];

function CI({ ci }: { ci: [number, number] | null }) {
  if (!ci) return null;
  return (
    <span className="tnum text-[10.5px] text-muted">
      {" "}
      [{pct(ci[0])}–{pct(ci[1])}]
    </span>
  );
}

export default function LeaderboardPage() {
  const { t, tr, lang } = useI18n();
  const { data: scen } = useQuery({ queryKey: ["scenarios"], queryFn: api.scenarios });
  const [f, setF] = useState({ scenario_id: "", task: "", ratio: "", duration: "", need_audio: false, max_cost: "", max_latency: "", rank_by: "quality" });
  const { data, error, isFetching } = useQuery({
    queryKey: ["leaderboard", f],
    queryFn: () =>
      api.leaderboard({
        scenario_id: f.scenario_id,
        task: f.task,
        ratio: f.ratio,
        duration: f.duration ? Number(f.duration) : undefined,
        need_audio: f.need_audio,
        max_cost: f.max_cost ? Number(f.max_cost) : undefined,
        max_latency: f.max_latency ? Number(f.max_latency) : undefined,
        rank_by: f.rank_by,
      }),
    placeholderData: (prev) => prev,
    refetchInterval: 15000,
  });
  const set = (patch: Partial<typeof f>) => setF({ ...f, ...patch });
  const rows = data?.models ?? [];
  const eligible = rows.filter((r) => r.eligible);
  const rated = eligible.filter((r) => r.quality !== null);
  const avgQ = rated.length ? rated.reduce((s, r) => s + (r.quality ?? 0), 0) / rated.length : null;
  const reasonText = (badge: string, r: LeaderRow | undefined): string => {
    if (!r) return "";
    if (badge === "best_quality")
      return t("lb.reason.best_quality", {
        source: r.quality_source ? t(`lb.src_long.${r.quality_source}` as TKey) : "",
        q: pct(r.quality),
        avg: pct(avgQ),
        n: r.quality_source === "human" ? r.rated_n : r.judge_n,
      });
    if (badge === "best_value") return t("lb.reason.best_value", { q: pct(r.quality), cost: usd(r.cost_median) });
    return t("lb.reason.fastest", { lat: secs(r.latency_median), n: r.latency_n });
  };

  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{t("lb.title")}</h1>
        <p className="max-w-4xl text-[13.5px] text-ink-2">{t("lb.intro")}</p>
      </div>

      <div className="card flex flex-wrap items-end gap-3 p-3">
        <label className="space-y-1">
          <span className="label">{t("lb.scenario")}</span>
          <select className="input !w-56" value={f.scenario_id} onChange={(e) => set({ scenario_id: e.target.value })}>
            <option value="">{t("lb.any")}</option>
            {scen?.packs.map((p) => (
              <option key={p.id} value={p.id}>
                {tr(p.title)}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="label">{t("lb.task")}</span>
          <select className="input !w-40" value={f.task} onChange={(e) => set({ task: e.target.value })}>
            <option value="">{t("lb.any")}</option>
            {TASKS.map((tk) => (
              <option key={tk} value={tk}>
                {t(`task.${tk}` as TKey)}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="label">{t("lb.ratio")}</span>
          <select className="input !w-24" value={f.ratio} onChange={(e) => set({ ratio: e.target.value })}>
            <option value="">{t("lb.any")}</option>
            {["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"].map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="label">{t("lb.duration")}</span>
          <input className="input !w-20" type="number" min={1} max={15} value={f.duration} onChange={(e) => set({ duration: e.target.value })} />
        </label>
        <label className="flex items-center gap-1.5 pb-2 text-[13px]">
          <input type="checkbox" checked={f.need_audio} onChange={(e) => set({ need_audio: e.target.checked })} /> {t("lb.audio")}
        </label>
        <label className="space-y-1">
          <span className="label">{t("lb.max_cost")}</span>
          <input className="input !w-20" type="number" step="0.05" min={0} value={f.max_cost} onChange={(e) => set({ max_cost: e.target.value })} />
        </label>
        <label className="space-y-1">
          <span className="label">{t("lb.max_latency")}</span>
          <input className="input !w-20" type="number" min={0} value={f.max_latency} onChange={(e) => set({ max_latency: e.target.value })} />
        </label>
        <div className="ml-auto space-y-1">
          <span className="label">{t("lb.rank_by")}</span>
          <div className="seg">
            {(["quality", "value", "speed"] as const).map((k) => (
              <button key={k} aria-pressed={f.rank_by === k} onClick={() => set({ rank_by: k })}>
                {t(`lb.${k}` as TKey)}
              </button>
            ))}
          </div>
        </div>
      </div>

      <ErrorBox error={error} />
      {data && !rows.length && <div className="card p-6 text-sm text-ink-2">{t("lb.empty")}</div>}

      {rows.length > 0 && (
        <div className={cls("space-y-5 transition-opacity", isFetching && "opacity-70")}>
          <div className="grid gap-3 md:grid-cols-3">
            {data!.reasons.map((r) => (
              <div key={r.badge} className="card p-3">
                <div className="label">{t(`lb.badge.${r.badge}` as TKey)}</div>
                <div className="mt-1 flex items-center gap-2 text-[15px] font-semibold">
                  <VendorDot vendor={rows.find((x) => x.model === r.model)?.vendor ?? ""} />
                  {rows.find((x) => x.model === r.model)?.label ?? r.model}
                </div>
                <div className="mt-0.5 text-xs text-ink-2">{reasonText(r.badge, rows.find((x) => x.model === r.model))}</div>
              </div>
            ))}
          </div>

          <div className="card space-y-2 p-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-[15px] font-semibold">{t("lb.scatter")}</h2>
              <span className="text-xs text-muted">{t("lb.summary", { clips: t("plan.clips", { n: data!.clips }), runs: t("lb.runs", { n: data!.runs }) })}</span>
            </div>
            <p className="text-xs text-muted">{t("lb.scatter_hint")}</p>
            <Scatter rows={eligible} />
          </div>

          <div className="card overflow-x-auto">
            <table className="w-full min-w-[1100px] text-left text-[13px]">
              <thead className="border-b border-line text-xs text-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">#</th>
                  <th className="px-3 py-2 font-medium">{t("lb.model")}</th>
                  <th className="px-3 py-2 font-medium">{t("lb.quality")}</th>
                  <th className="px-3 py-2 font-medium">{t("lb.usable")}</th>
                  <th className="px-3 py-2 font-medium">{t("lb.wins")}</th>
                  <th className="px-3 py-2 font-medium">{t("lb.judge")}</th>
                  <th className="px-3 py-2 font-medium">{t("lb.success")}</th>
                  <th className="px-3 py-2 font-medium">{t("lb.compliance")}</th>
                  <th className="px-3 py-2 text-right font-medium">{t("lb.cost")}</th>
                  <th className="px-3 py-2 text-right font-medium">{t("lb.cost_s")}</th>
                  <th className="px-3 py-2 text-right font-medium">{t("lb.latency")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--line)]">
                {rows.map((r: LeaderRow, i) => (
                  <tr key={r.model} className={cls(!r.eligible && "opacity-45")}>
                    <td className="tnum px-3 py-2.5 text-muted">{r.eligible ? i + 1 : "–"}</td>
                    <td className="px-3 py-2.5">
                      <div className="flex items-center gap-2 font-medium">
                        <VendorDot vendor={r.vendor} />
                        {r.label}
                        {r.badges.map((b) => (
                          <span key={b} className="chip !text-[10.5px]">
                            {t(`lb.badge.${b}` as TKey)}
                          </span>
                        ))}
                        {r.pareto && <span className="chip !text-[10.5px]">{t("lb.frontier_chip")}</span>}
                      </div>
                      {!r.eligible && <div className="text-xs text-muted">{t("lb.not_eligible", { why: r.why_not.join(", ") })}</div>}
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="flex items-center gap-2">
                        <Meter value={r.quality} color={vendorColor(r.vendor)} />
                        <span className="tnum font-medium">{pct(r.quality)}</span>
                      </div>
                      <div className="text-[10.5px] text-muted">{r.quality_source ? t(`lb.src.${r.quality_source}` as TKey) : "—"}</div>
                    </td>
                    <td className="px-3 py-2.5">
                      <span className="tnum">{pct(r.usable_rate)}</span>
                      <CI ci={r.usable_ci} />
                      <div className="text-[10.5px] text-muted">n={r.rated_n}</div>
                    </td>
                    <td className="tnum px-3 py-2.5">
                      {r.appearances ? `${r.wins}/${r.appearances}` : "—"}
                      <CI ci={r.win_ci} />
                    </td>
                    <td className="px-3 py-2.5">
                      <span className="tnum">{pct(r.judge_adherence)}</span>
                      <div className="text-[10.5px] text-muted">
                        n={r.judge_n}
                        {r.defect_mean !== null ? ` · ${t("lb.defects")} ${r.defect_mean}` : ""}
                      </div>
                    </td>
                    <td className="px-3 py-2.5">
                      <span className="tnum">
                        {r.n_success}/{r.n_attempts}
                      </span>
                      <CI ci={r.success_ci} />
                    </td>
                    <td className="tnum px-3 py-2.5">{pct(r.compliance_rate)}</td>
                    <td className="tnum px-3 py-2.5 text-right">
                      {usd(r.cost_median)}
                      <div className="text-[10.5px] text-muted">{t(`lb.cost_src.${r.cost_source}` as TKey)}</div>
                    </td>
                    <td className="tnum px-3 py-2.5 text-right">{usd(r.cost_per_s, 3)}</td>
                    <td className="tnum px-3 py-2.5 text-right">
                      {secs(r.latency_median)}
                      <div className="text-[10.5px] text-muted">n={r.latency_n}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data!.agreement.n > 0 && (
            <p className="text-xs text-ink-2">
              {t("lb.agreement", { tau: data!.agreement.tau ?? "—", n: data!.agreement.n })} {!data!.agreement.reliable && t("lb.agreement_low")}
            </p>
          )}
        </div>
      )}
      <span className="hidden">{lang}</span>
    </div>
  );
}
