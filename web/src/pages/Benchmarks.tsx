import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQueries, useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n, type TKey } from "../i18n";
import type { BenchmarkDetail, MetricRow } from "../types";
import { usd, cls } from "../util";
import { ErrorBox, Spinner } from "../components/common";

function num(row: MetricRow | undefined, key: string): number | null { const v = row?.[key]; return typeof v === "number" ? v : null; }
function value(n: number | null | undefined, digits = 2): string { return n == null ? "—" : n.toLocaleString(undefined, { maximumFractionDigits: digits }); }
function Metric({ title, children, note }: { title: string; children: React.ReactNode; note?: string }) { return <div className="rounded-xl border border-line bg-surface p-4"><p className="text-xs text-muted">{title}</p><div className="mt-2 text-2xl font-semibold tnum">{children}</div>{note && <p className="mt-2 text-xs text-muted">{note}</p>}</div>; }

function Platforms({ experiments }: { experiments: BenchmarkDetail[] }) {
  const { t } = useI18n();
  const seen = new Set<string>();
  return <section className="space-y-3"><h2 className="font-semibold">{t("bench.platforms")}</h2><div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
    {experiments.flatMap(e => e.deployments.map(d => {
      seen.add(d.id);
      const phase = d.provider === "official_api" ? "comparison" : "baseline";
      const window = e.summary.phases.find(p => p.provider === d.provider && p.phase === phase);
      const groups = e.summary.groups.filter(g => g.provider === d.provider && g.phase === phase);
      const billed = e.attempts.filter(a => a.deployment_id === d.id && a.metrics.technical_valid);
      const apiCost = billed.length && billed.every(a => num(a.metrics, "api_cost_estimate_usd") != null)
        ? billed.reduce((sum, a) => sum + num(a.metrics, "api_cost_estimate_usd")!, 0) / billed.length : null;
      return <article key={`${e.id}-${d.id}`} className="card p-4 space-y-3">
        <div><p className="text-xs text-muted">{e.date}</p><h3 className="font-semibold mt-1">{d.label}</h3><p className="text-xs text-muted mt-1">{String(d.hardware.gpu ?? d.model)}{d.hardware.gpu_count ? ` · ×${d.hardware.gpu_count}` : ""}</p></div>
        <div className="space-y-1.5">{groups.map(g => <div key={String(g.task)} className="flex justify-between text-sm"><span className="text-muted">{String(g.task).toUpperCase()} · P50</span><span className="tnum font-semibold">{value(num(g, "e2e_p50_s"))}s <small className="font-normal text-muted">n={value(num(g, "n_technical_valid"), 0)}</small></span></div>)}</div>
        <div className="border-t border-line pt-3 space-y-2 text-xs"><div className="flex justify-between gap-2"><span className="text-muted">{t("bench.throughput")}</span><span className="tnum">{value(num(window, "throughput_success_per_hour"))}</span></div><div className="flex justify-between gap-2"><span className="text-muted">{t(d.provider === "official_api" ? "bench.api_per_clip" : "bench.cost")}</span><span className="tnum">{usd(d.provider === "official_api" ? apiCost : num(window, "cost_per_technical_success_usd"), 3)}</span></div></div>
        <p className="text-xs text-muted">{t(`eval.${d.timing_scope}` as TKey)}</p>
        <details><summary className="cursor-pointer text-xs text-muted">{t("bench.configuration")}</summary><p className="mt-2 break-all text-[11px] text-muted">{e.id} · {d.provider === "official_api" ? t("eval.unknown") : `${String(e.config.model_revision ?? "—")} · ${String(e.config.num_inference_steps ?? "—")} steps`}</p></details>
      </article>;
    }))}
    {![...seen].some(id => id.toLowerCase().includes("pro5500")) && <article className="rounded-xl border border-dashed border-line-strong p-5 flex flex-col justify-center min-h-40"><span className="text-xs text-muted">NEXT PLATFORM</span><h3 className="mt-2 font-semibold">{t("bench.pro5500")}</h3><p className="mt-2 text-sm text-muted">{t("bench.no_data")}</p></article>}
  </div></section>;
}

function Experiment({ data }: { data: BenchmarkDetail }) {
  const { t } = useI18n();
  const [phase, setPhase] = useState("");
  const [provider, setProvider] = useState("");
  const phaseName = (v: unknown) => t(`phase.${v}` as TKey);
  const providerName = (v: unknown) => data.deployments.find(d => d.provider === v)?.label ?? String(v);
  const phases = data.summary.phases.filter(p => (!phase || p.phase === phase) && (!provider || p.provider === provider));
  const groups = data.summary.groups.filter(p => (!phase || p.phase === phase) && (!provider || p.provider === provider));
  const attempts = data.attempts.filter(a => (!phase || a.metrics.phase === phase) && (!provider || a.metrics.provider === provider));
  const costs = data.summary.costs;
  const maxLatency = Math.max(1, ...data.summary.scenes.flatMap(s => [num(s, "baseline_p50_s") ?? 0, num(s, "official_e2e_s") ?? 0]));
  return <div className="space-y-7">
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Metric title={t("bench.attempts")}>{data.attempts.length}</Metric>
      <Metric title={t("bench.valid")} note={t("bench.sample_note")}>{data.attempts.filter(a => a.metrics.technical_valid).length}</Metric>
      <Metric title={t("bench.observed")} note={t("bench.observed")}>{usd(num(costs, "vast_observed_credit_decrease_usd"))}</Metric>
      <Metric title={t("bench.api_estimate")} note={t("eval.official_list_price")}>{usd(num(costs, "api_usage_price_estimate_usd"))}</Metric>
    </div>
    <section className="card space-y-3 p-4"><h2 className="font-semibold">{t("bench.scenes")}</h2>
      <div className="flex gap-4 text-xs"><span className="text-[var(--v-alibaba)]">● {t("bench.baseline")} P50</span><span className="text-[var(--v-bytedance)]">● {t("bench.official")} E2E · n=1</span></div>
      <div className="grid gap-4 lg:grid-cols-2">{data.summary.scenes.map(s => <div key={String(s.case_id)} className="rounded-lg border border-line p-3">
        <Link className="flex justify-between gap-3 text-xs mb-2" to={`/results?dataset=${data.dataset_id}&case=${s.case_id}`}><span className="font-medium">{String(s.case_id)} · {String(s.title)}</span><span className="text-muted">{t("bench.view")} →</span></Link>
        {[{ v: num(s, "baseline_p50_s"), color: "var(--v-alibaba)", n: num(s, "baseline_n") }, { v: num(s, "official_e2e_s"), color: "var(--v-bytedance)", n: 1 }].map((bar, i) => <div key={i} className="flex gap-2 items-center h-6"><div className="flex-1 h-2.5 rounded bg-surface-2 overflow-hidden"><div style={{ width: `${100 * (bar.v ?? 0) / maxLatency}%`, background: bar.color }} className="h-full rounded" /></div><span className="w-28 text-right text-[11px] tnum">{value(bar.v)}s · n={bar.n}</span></div>)}
      </div>)}</div>
    </section>
    <div className="flex flex-wrap items-center gap-2"><select className="input !w-auto" aria-label={t("eval.phase")} value={phase} onChange={e => setPhase(e.target.value)}><option value="">{t("bench.all")}</option>{[...new Set(data.summary.phases.map(p => String(p.phase)))].map(p => <option key={p} value={p}>{phaseName(p)}</option>)}</select><select className="input !w-auto" aria-label={t("bench.platform")} value={provider} onChange={e => setProvider(e.target.value)}><option value="">{t("bench.all_platforms")}</option>{data.deployments.map(d => <option key={d.id} value={d.provider}>{d.label}</option>)}</select></div>
    <section className="space-y-3"><h2 className="font-semibold">{t("bench.groups")}</h2><div className="card overflow-x-auto"><table className="eval-table"><thead><tr><th>{t("bench.platform")}</th><th>{t("eval.phase")}</th><th>{t("bench.task")}</th><th>{t("bench.n")}</th><th>{t("bench.p50")}</th><th>{t("bench.p95")}</th><th>{t("bench.range")}</th></tr></thead><tbody>{groups.map((g, i) => <tr key={i}><td>{providerName(g.provider)}</td><td>{phaseName(g.phase)}</td><td>{String(g.task).toUpperCase()}</td><td>{value(num(g, "n_technical_valid"), 0)} / {value(num(g, "n_submitted_attempts"), 0)}</td><td>{value(num(g, "e2e_p50_s"))}s</td><td>{value(num(g, "e2e_p95_s"))}s</td><td>{value(num(g, "e2e_min_s"))}–{value(num(g, "e2e_max_s"))}</td></tr>)}</tbody></table></div><p className="text-xs text-muted">{t("bench.small_sample")}</p></section>
    <section className="space-y-3"><h2 className="font-semibold">{t("bench.phases")}</h2><div className="card overflow-x-auto"><table className="eval-table"><thead><tr><th>{t("eval.phase")}</th><th>{t("bench.n")}</th><th>{t("bench.window")}</th><th>{t("bench.throughput")}</th><th>{t("bench.cost")}</th><th>{t("bench.inference")}</th><th>{t("bench.memory")}</th><th>{t("bench.utilization")}</th><th>{t("bench.power")}</th></tr></thead><tbody>{phases.map((p, i) => {
      const telemetry = p.provider === "self" ? data.summary.telemetry.phase_metrics.find(m => m.phase === p.phase) : undefined;
      return <tr key={i}><td>{phaseName(p.phase)}</td><td>{value(num(p, "n_technical_valid"), 0)} / {value(num(p, "n_submitted_attempts"), 0)}</td><td>{value(num(p, "window_seconds"))}</td><td>{value(num(p, "throughput_success_per_hour"))}</td><td>{usd(num(p, "cost_per_technical_success_usd"), 3)}</td><td>{value(num(telemetry, "server_inference_p50_s"))}</td><td>{value(num(telemetry, "max_single_gpu_memory_mib"), 0)} MiB</td><td>{value(num(telemetry, "mean_8gpu_utilization_pct") ?? num(telemetry, "mean_gpu_utilization_pct"))}%</td><td>{value(num(telemetry, "max_sampled_8gpu_power_w") ?? num(telemetry, "max_sampled_total_power_w"), 0)} W</td></tr>;
    })}</tbody></table></div><p className="text-xs text-muted">{t("bench.telemetry_note")}</p><p className="eval-notice">{t("bench.turbo_note")}</p></section>
    <section className="card space-y-4 p-4"><h2 className="font-semibold">{t("bench.costs")}</h2><div className="grid gap-3 sm:grid-cols-3"><Metric title={t("bench.total")}>{usd(num(costs, "whole_poc_observation_plus_api_estimate_usd"))}</Metric><Metric title={t("bench.invoice")}>{t("eval.unknown")}</Metric><Metric title={t("bench.quality")}>{t("eval.unknown")}</Metric></div>
      <div className="overflow-x-auto"><table className="eval-table"><thead><tr><th>{t("bench.demand_utilization")}</th><th>{t("bench.cost")}</th></tr></thead><tbody>{data.summary.utilization.map((u, i) => <tr key={i}><td>{value((num(u, "utilization") ?? 0) * 100)}%</td><td>{usd(num(u, "gpu_disk_cost_per_success_usd"), 3)}</td></tr>)}</tbody></table></div><details><summary className="text-xs text-muted cursor-pointer">{t("bench.raw")}</summary><pre className="json mt-2 max-h-96">{JSON.stringify(costs, null, 2)}</pre></details>
    </section>
    <section className="card p-4"><details><summary className="font-semibold cursor-pointer">{t("bench.resources")}</summary><pre className="json mt-3 max-h-96">{JSON.stringify({ deployments: data.deployments, self_hosted_config: data.config }, null, 2)}</pre></details></section>
    <section className="space-y-3"><h2 className="font-semibold">{t("bench.attempts")} · {attempts.length}</h2><div className="card overflow-auto max-h-[480px]"><table className="eval-table"><thead><tr><th>{t("bench.request")}</th><th>{t("eval.phase")}</th><th>{t("bench.outcome")}</th><th>E2E (s)</th><th>{t("bench.raw")}</th></tr></thead><tbody>{attempts.map(a => <tr key={a.id}><td><span className="text-[11px] break-all">{a.request_id} · #{a.attempt}</span>{a.job_id && <Link className="block mt-1 underline text-xs" to={`/results?dataset=${data.dataset_id}&case=${a.metrics.case_id}&job=${a.job_id}`}>{t("bench.view")} →</Link>}</td><td>{phaseName(a.metrics.phase)}</td><td>{String(a.metrics.outcome ?? a.metrics.status)}</td><td>{value(num(a.metrics, "e2e_seconds"))}</td><td><details><summary className="cursor-pointer">JSON</summary><pre className="json max-w-md max-h-72">{JSON.stringify(a.metrics, null, 2)}</pre></details></td></tr>)}</tbody></table></div></section>
  </div>;
}

export default function BenchmarksPage() {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const { data: catalog, error, isLoading } = useQuery({ queryKey: ["benchmarks"], queryFn: api.benchmarks });
  const chosen = catalog?.experiments.find(e => e.id === params.get("experiment")) ?? catalog?.experiments[0];
  const queries = useQueries({ queries: (catalog?.experiments ?? []).filter(e => e.dataset_id === chosen?.dataset_id).map(e => ({ queryKey: ["benchmark", e.id], queryFn: () => api.benchmark(e.id) })) });
  const experiments = queries.flatMap(q => q.data ? [q.data] : []);
  const selected = experiments.find(e => e.id === chosen?.id);
  return <div className="space-y-6">
    <div className="eval-hero"><div><p className="eval-eyebrow">H3 / INFRASTRUCTURE</p><h1 className="mt-2 text-2xl sm:text-3xl font-semibold tracking-tight">{t("bench.title")}</h1><p className="mt-2 text-sm text-ink-2 max-w-2xl">{t("bench.intro")}</p></div><div className="flex flex-wrap gap-2">{selected?.artifacts["report.html"] && <a className="btn btn-primary" href={selected.artifacts["report.html"]}>{t("bench.read_report")} ↗</a>}<Link className="btn" to="/results">{t("nav.results")} ↗</Link></div></div>
    <ErrorBox error={error ?? queries.find(q => q.error)?.error} />
    {(isLoading || queries.some(q => q.isLoading)) && <Spinner />}
    {catalog && !catalog.experiments.length && <div className="card p-8 text-muted">{t("bench.no_data")}</div>}
    {!!experiments.length && <><p className="eval-notice">{t("bench.scope_note")}</p><Platforms experiments={experiments} /></>}
    {selected && <>
      <div className="flex flex-wrap gap-3 items-center"><label className="label min-w-0 max-w-full">{t("bench.experiment")}<select value={selected.id} onChange={e => setParams({ experiment: e.target.value })} className="input !w-auto ml-2 !max-w-full">{catalog?.experiments.map(e => <option value={e.id} key={e.id}>{e.title}</option>)}</select></label><div className="flex flex-wrap gap-2 ml-auto"><a className="btn" href={`/api/benchmarks/${selected.id}/export?format=csv`}>CSV ↓</a><a className="btn" href={`/api/benchmarks/${selected.id}/export`}>JSON ↓</a>{Object.entries(selected.artifacts).filter(([name]) => ["report.html", "report.docx", "report.md", "licenses.md"].includes(name)).map(([name, url]) => <a key={name} href={url} className={cls("btn", (name === "report.html" || (!selected.artifacts["report.html"] && name === "report.docx")) && "btn-primary")}>{name === "licenses.md" ? t("bench.licenses") : name === "report.html" ? t("bench.read_report") : `${t("bench.report")} · ${name.split(".").pop()?.toUpperCase()}`} {name === "report.html" ? "↗" : "↓"}</a>)}</div></div>
      <Experiment key={selected.id} data={selected} />
    </>}
  </div>;
}
