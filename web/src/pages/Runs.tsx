import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, IS_PUBLIC } from "../api";
import { useI18n, type TKey } from "../i18n";
import { ago, cls, usd } from "../util";
import { ErrorBox, Spinner, TaskBadge } from "../components/common";

const SEG: [string, string][] = [
  ["succeeded", "var(--good)"],
  ["failed", "var(--critical)"],
  ["submit_unknown", "var(--serious)"],
  ["timeout", "var(--serious)"],
  ["blocked", "var(--muted)"],
  ["stopped", "var(--muted)"],
];

export default function RunsPage() {
  const { t, lang } = useI18n();
  const [kind, setKind] = useState<string>("");
  const { data, error, isLoading } = useQuery({
    queryKey: ["runs", kind],
    queryFn: () => api.runs(kind || undefined),
    refetchInterval: (q) => ((q.state.data ?? []).some((r) => r.active > 0) ? 3000 : 15000),
  });
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{t("runs.title")}</h1>
        <div className="seg">
          {(IS_PUBLIC ? (["", "pack", "compare"] as const) : (["", "pack", "compare", "probe"] as const)).map((k) => (
            <button key={k} aria-pressed={kind === k} onClick={() => setKind(k)}>
              {t(`runs.kind.${k || "all"}` as TKey)}
            </button>
          ))}
        </div>
      </div>
      {isLoading && <Spinner />}
      <ErrorBox error={error} />
      {data && data.length === 0 && (
        <div className="card p-6 text-sm text-ink-2">
          {t("runs.empty")}{" "}
          <Link className="underline" to="/scenarios/quick-tour">
            {t("scen.open")} →
          </Link>
        </div>
      )}
      <div className="space-y-2">
        {data?.map((r) => {
          const done = SEG.reduce((s, [k]) => s + (r.counts[k] ?? 0), 0);
          return (
            <Link key={r.id} to={`/runs/${r.id}`} className="card flex flex-wrap items-center gap-4 p-3 hover:border-line-strong">
              <div className="flex gap-1">
                {r.posters.length ? (
                  r.posters.map((p) => <img key={p} src={p} alt="" className="h-12 w-20 rounded object-cover" />)
                ) : (
                  <span className="grid h-12 w-20 place-items-center rounded bg-surface-3 text-muted">▶</span>
                )}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <TaskBadge task={r.task} />
                  <span className="truncate text-[14px] font-medium">{r.title}</span>
                  {r.kind === "probe" && <span className="chip">{t("runs.probe_chip")}</span>}
                  {IS_PUBLIC && <span className="chip">{r.mine ? t("runs.yours") : t("runs.example")}</span>}
                </div>
                <div className="mt-1 text-xs text-muted">
                  {ago(r.created_at, lang)} · {r.n_prompts} × {r.models.length} · {t("plan.clips", { n: r.n_jobs })}
                  {r.judge ? ` · ${t("board.judge")}` : ""}
                </div>
              </div>
              <div className="w-48 space-y-1">
                <div className="flex h-1.5 overflow-hidden rounded-full bg-surface-3" title={JSON.stringify(r.counts)}>
                  {SEG.map(([k, c]) =>
                    r.counts[k] ? <span key={k} style={{ width: `${(r.counts[k] / r.n_jobs) * 100}%`, background: c }} className="border-r-2 border-[var(--surface)] last:border-r-0" /> : null,
                  )}
                </div>
                <div className="flex justify-between text-xs">
                  <span className={cls(r.active ? "text-ink" : "text-muted")}>{r.active ? t("runs.active", { n: r.active }) : `${done}/${r.n_jobs}`}</span>
                  <span className="tnum text-ink-2">{usd(r.spent_usd)}</span>
                </div>
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
