"""Leaderboard aggregation: requirement filters, quality ranking, best-value frontier."""

from __future__ import annotations

import math
import statistics
from typing import Any

from sqlmodel import select

from vgen.catalog.registry import Registry
from vgen.costs import estimate_job, quota_to_usd
from vgen.db import FAILED, SUBMIT_UNKNOWN, SUCCEEDED, TIMEOUT, Job, Pick, Rating, Run, session

ATTEMPT_STATES = {SUCCEEDED, FAILED, TIMEOUT, SUBMIT_UNKNOWN}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)


def kendall_tau(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    conc = disc = tx = ty = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx, dy = xs[i] - xs[j], ys[i] - ys[j]
            if dx == 0 and dy == 0:
                continue
            if dx == 0:
                tx += 1
            elif dy == 0:
                ty += 1
            elif dx * dy > 0:
                conc += 1
            else:
                disc += 1
    denom = math.sqrt((conc + disc + tx) * (conc + disc + ty))
    return round((conc - disc) / denom, 3) if denom else None


def job_cost(registry: Registry, job: Job) -> tuple[float | None, str]:
    if job.quota is not None and registry.quota_calibrated:
        return quota_to_usd(registry, job.quota), "actual"
    from vgen.runs import actual_cost

    usd, basis = actual_cost(registry, job)
    if usd is not None and "reported" in basis:
        return usd, "actual"
    return (job.est_cost_usd or None), "estimate"


def job_latency(job: Job) -> float | None:
    if job.gw_finish_time and job.gw_submit_time and job.gw_finish_time > job.gw_submit_time:
        return float(job.gw_finish_time - job.gw_submit_time)
    if job.done_at and job.submitted_at:
        return float(job.done_at - job.submitted_at)
    return None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def _compliant(job: Job) -> bool | None:
    items = [c for c in (job.compliance or []) if c.get("ok") is not None]
    if not items:
        return None
    return all(c["ok"] for c in items)


def collect(scenario_id: str | None = None, category: str | None = None, task: str | None = None,
            include_probes: bool = False) -> tuple[list[Run], list[Job], dict[str, Rating], list[Pick]]:
    with session() as s:
        q = select(Run)
        if scenario_id:
            q = q.where(Run.scenario_id == scenario_id)
        if category:
            q = q.where(Run.category == category)
        if task:
            q = q.where(Run.task == task)
        runs = [r for r in s.exec(q) if include_probes or r.kind != "probe"]
        run_ids = [r.id for r in runs]
        if not run_ids:
            return runs, [], {}, []
        jobs = list(s.exec(select(Job).where(Job.run_id.in_(run_ids))))
        ratings = {r.job_id: r for r in s.exec(select(Rating).where(Rating.run_id.in_(run_ids)))}
        picks = list(s.exec(select(Pick).where(Pick.run_id.in_(run_ids))))
    return runs, jobs, ratings, picks


def leaderboard(registry: Registry, *, scenario_id: str | None = None, category: str | None = None,
                task: str | None = None, ratio: str | None = None, duration: int | None = None,
                need_audio: bool = False, max_cost: float | None = None, max_latency: float | None = None,
                rank_by: str = "quality", resolution: str = "720p") -> dict[str, Any]:
    runs, jobs, ratings, picks = collect(scenario_id, category, task)
    by_model: dict[str, list[Job]] = {}
    for j in jobs:
        if j.status in ATTEMPT_STATES:
            by_model.setdefault(j.model, []).append(j)

    # pick-best: appearances = groups (run, prompt) where the model had a finished clip
    groups: dict[tuple[str, str], list[Job]] = {}
    for j in jobs:
        if j.status == SUCCEEDED:
            groups.setdefault((j.run_id, j.prompt_key), []).append(j)
    latest_pick: dict[tuple[str, str], Pick] = {}
    for p in sorted(picks, key=lambda p: p.created_at):
        latest_pick[(p.run_id, p.prompt_key)] = p
    job_by_id = {j.id: j for j in jobs}

    rows: list[dict[str, Any]] = []
    pairs_judge: list[float] = []
    pairs_human: list[float] = []
    for model_id, mjobs in by_model.items():
        info = registry.models.get(model_id)
        succ = [j for j in mjobs if j.status == SUCCEEDED]
        costs = [c for c in (job_cost(registry, j)[0] for j in succ) if c is not None]
        sources = {job_cost(registry, j)[1] for j in succ}
        per_s = [c / float((j.media or {}).get("duration") or j.effective.get("duration") or 1)
                 for j in succ for c in [job_cost(registry, j)[0]] if c is not None]
        lat = [x for x in (job_latency(j) for j in succ) if x is not None]
        comp = [x for x in (_compliant(j) for j in succ) if x is not None]
        judged = [j for j in succ if (j.judge or {}).get("adherence") is not None]
        adherence = [float(j.judge["adherence"]) for j in judged]
        defects = [float(j.judge.get("defect_score") or 0) for j in judged]
        rated = [ratings[j.id] for j in succ if j.id in ratings and ratings[j.id].usable is not None]
        usable_k = sum(1 for r in rated if r.usable)
        overall = [r.overall for r in (ratings.get(j.id) for j in succ) if r and r.overall]
        wins = appearances = 0
        for key, g in groups.items():
            if any(j.model == model_id for j in g) and key in latest_pick and len(g) > 1:
                appearances += 1
                winner = job_by_id.get(latest_pick[key].winner_job_id)
                if winner and winner.model == model_id:
                    wins += 1
        for j in judged:
            r = ratings.get(j.id)
            if r and (r.overall or r.usable is not None):
                pairs_judge.append(float(j.judge["adherence"]))
                pairs_human.append(float(r.overall) if r.overall else (5.0 if r.usable else 1.0))

        if len(rated) >= 3:
            quality, q_source = usable_k / len(rated), "human"
        elif adherence:
            penalty = min(0.5, (statistics.mean(defects) if defects else 0) / 12)
            quality, q_source = statistics.mean(adherence) * (1 - penalty), "judge"
        else:
            quality, q_source = None, None
        cost_med = _median(costs)

        # requirement filters (capabilities come from the registry, cost from the requested settings)
        why_not: list[str] = []
        if info:
            if ratio and ratio not in ("auto",) and info.ratios and ratio not in info.ratios and "auto" not in info.ratios:
                why_not.append(f"no {ratio}")
            if duration and max(info.allowed_durations()) < duration:
                why_not.append(f"max {max(info.allowed_durations())} s")
            if need_audio and info.audio not in ("optional", "always"):
                why_not.append("no audio generation")
            if max_cost is not None:
                eff = {"duration": duration or 6, "resolution": resolution, "ratio": ratio or "16:9", "audio": need_audio}
                est = estimate_job(registry, model_id, eff).usd
                if est is not None and est > max_cost:
                    why_not.append(f"≈${est:.2f}/clip > ${max_cost:g}")
        lat_med = _median(lat)
        if max_latency is not None and lat_med is not None and lat_med > max_latency:
            why_not.append(f"median {lat_med:.0f}s > {max_latency:g}s")

        rows.append({
            "model": model_id,
            "label": info.label if info else model_id,
            "family": info.family if info else "",
            "vendor": info.vendor if info else "",
            "eligible": not why_not,
            "why_not": why_not,
            "n_attempts": len(mjobs),
            "n_success": len(succ),
            "success_rate": round(len(succ) / len(mjobs), 3) if mjobs else None,
            "success_ci": wilson(len(succ), len(mjobs)),
            "latency_median": lat_med,
            "latency_n": len(lat),
            "cost_median": cost_med,
            "cost_per_s": _median(per_s),
            "cost_source": "actual" if sources == {"actual"} else ("mixed" if "actual" in sources else "estimate"),
            "compliance_rate": round(sum(comp) / len(comp), 3) if comp else None,
            "judge_adherence": round(statistics.mean(adherence), 3) if adherence else None,
            "judge_n": len(adherence),
            "defect_mean": round(statistics.mean(defects), 2) if defects else None,
            "usable_rate": round(usable_k / len(rated), 3) if rated else None,
            "usable_ci": wilson(usable_k, len(rated)),
            "rated_n": len(rated),
            "overall_mean": round(statistics.mean(overall), 2) if overall else None,
            "wins": wins,
            "appearances": appearances,
            "win_rate": round(wins / appearances, 3) if appearances else None,
            "win_ci": wilson(wins, appearances),
            "quality": round(quality, 3) if quality is not None else None,
            "quality_source": q_source,
            "value": round(quality / cost_med, 3) if quality is not None and cost_med else None,
            "badges": [],
            "pareto": False,
        })

    eligible = [r for r in rows if r["eligible"]]
    _mark_frontier(eligible)
    reasons = _badges(eligible)
    key = {"quality": lambda r: (r["quality"] is None, -(r["quality"] or 0), r["cost_median"] or 9e9),
           "value": lambda r: (r["value"] is None, -(r["value"] or 0)),
           "speed": lambda r: (r["latency_median"] is None, r["latency_median"] or 9e9)}.get(rank_by)
    rows.sort(key=lambda r: (not r["eligible"], *key(r)) if key else (not r["eligible"],))
    agreement = {"n": len(pairs_judge), "tau": kendall_tau(pairs_judge, pairs_human) if len(pairs_judge) >= 3 else None,
                 "reliable": len(pairs_judge) >= 20}
    return {"models": rows, "reasons": reasons, "agreement": agreement, "rank_by": rank_by,
            "runs": len(runs), "clips": sum(r["n_success"] for r in rows)}


def _mark_frontier(rows: list[dict[str, Any]]) -> None:
    pts = [r for r in rows if r["quality"] is not None and r["cost_median"] is not None]
    for r in pts:
        dominated = any(o is not r and o["quality"] >= r["quality"] and o["cost_median"] <= r["cost_median"]
                        and (o["quality"] > r["quality"] or o["cost_median"] < r["cost_median"]) for o in pts)
        r["pareto"] = not dominated


def _badges(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    q = [r for r in rows if r["quality"] is not None]
    if q:
        best = max(q, key=lambda r: (r["quality"], -(r["cost_median"] or 0)))
        best["badges"].append("best_quality")
        avg = statistics.mean(r["quality"] for r in q)
        src = "human ‘usable’ rate" if best["quality_source"] == "human" else "judge checklist score"
        reasons.append({"badge": "best_quality", "model": best["model"],
                        "text": f"{best['label']}: {src} {best['quality']:.0%} vs field average {avg:.0%}"
                                f" (n={best['rated_n'] if best['quality_source'] == 'human' else best['judge_n']})"})
    v = [r for r in rows if r["value"] is not None]
    if v:
        best = max(v, key=lambda r: r["value"])
        best["badges"].append("best_value")
        reasons.append({"badge": "best_value", "model": best["model"],
                        "text": f"{best['label']}: quality {best['quality']:.0%} at ${best['cost_median']:.2f}/clip"})
    s = [r for r in rows if r["latency_median"] is not None]
    if s:
        best = min(s, key=lambda r: r["latency_median"])
        best["badges"].append("fastest")
        reasons.append({"badge": "fastest", "model": best["model"],
                        "text": f"{best['label']}: median {best['latency_median']:.0f}s end-to-end (n={best['latency_n']})"})
    return reasons
