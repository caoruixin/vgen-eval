"""Cross-run comparisons and archived benchmarks, scoped to the current viewer."""
from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response
from sqlmodel import select

from vgen import public_view, runs
from vgen.db import BenchmarkAttempt, Deployment, EvaluationCase, Experiment, Job, Rating, Run, session
from vgen.experiments import case_fingerprint, spec_fingerprint


def visible_results(public: bool, visitor: str | None) -> tuple[list[Run], list[Job]]:
    with session() as s:
        q = select(Run).where(Run.kind != "probe")
        if public:
            q = q.where((Run.owner == None) | (Run.owner == visitor))  # noqa: E711
        visible_runs = list(s.exec(q))
        jobs = list(s.exec(select(Job).where(Job.run_id.in_([r.id for r in visible_runs])))) if visible_runs else []
    return visible_runs, jobs


def datasets(public: bool, visitor: str | None) -> tuple[dict, list[Job], dict[str, Run]]:
    visible_runs, jobs = visible_results(public, visitor)
    with session() as s:
        cases = list(s.exec(select(EvaluationCase).order_by(EvaluationCase.key)))
    result = {}
    common = {c.fingerprint for c in cases}
    for c in cases:
        d = result.setdefault(c.dataset_id, {"id": c.dataset_id, "title": c.dataset_title, "cases": []})
        d["cases"].append(c.model_dump())
    for dataset in result.values():
        dataset["cases"].sort(key=lambda c: (c["task"] != "t2v", c["key"]))
    legacy = {}
    for run in visible_runs:
        if run.kind == "experiment":
            continue
        for prompt in run.prompts:
            refs = {**run.params.get("assets", {}), **prompt.get("assets", {})}
            fp = case_fingerprint(run.task, prompt["text"], refs)
            if fp in common:
                continue
            legacy.setdefault(fp, {"id": f"tokenbay:{fp}", "key": f"{prompt['key']}-{fp[:8]}", "title": prompt.get("title") or run.title,
                "dataset_id": "tokenbay", "task": run.task, "prompt": prompt["text"], "checklist": prompt.get("checklist", []),
                "assets": refs, "fingerprint": fp})
    if legacy:
        result["tokenbay"] = {"id": "tokenbay", "title": {"en": "Tokenbay · original scenarios", "zh": "Tokenbay · 原有场景"},
                              "cases": list(legacy.values())}
    for dataset in result.values():
        for c in dataset["cases"]:
            c["n_results"] = sum(j.status == "succeeded" and (j.case_fingerprint or spec_fingerprint(j.spec)) == c["fingerprint"] for j in jobs)
    return result, jobs, {r.id: r for r in visible_runs}


def imported_packs(registry) -> list[dict]:
    with session() as s:
        cases = list(s.exec(select(EvaluationCase).order_by(EvaluationCase.key)))
    groups = defaultdict(list)
    for c in cases:
        groups[(c.dataset_id, c.task)].append(c)
    packs = []
    for (dataset, task), group in groups.items():
        packs.append({"id": f"{dataset}-{task}", "category": "h3", "task": task,
            "title": {"en": f"H3 shared inputs · {task}", "zh": f"H3 共同输入 · {'文生视频' if task == 't2v' else '首帧图生视频'}"},
            "description": {"en": "Frozen Chinese prompts and original images. Review a Tokenbay generation plan; judging is off by default.",
                            "zh": "使用冻结的中文提示词及原始首帧。可查看 Tokenbay 补跑计划，默认关闭 AI 评分。"},
            "params": {"duration": 5, "resolution": "720p", "ratio": "auto" if task == "i2v" else "16:9", "audio": True},
            "models": [m.id for m in registry.models.values() if m.task_state(task) == "enabled"],
            "judge": False, "assets": {}, "missing_assets": [],
            "look_for": {"en": ["Same input, different output specifications", "Audio and visual consistency"],
                         "zh": ["相同输入、输出规格差异", "画面质量与音画一致性"]},
            "prompts": [{"key": c.key, "title": {"en": c.title, "zh": c.title}, "text": {"en": c.prompt, "zh": c.prompt},
                "checklist": {"en": c.checklist, "zh": c.checklist}, "prefer_lang": "zh", "assets": c.assets,
                "missing_assets": []} for c in group]})
    return packs


def register_routes(app, state, viewer, job_out, budget_view, is_admin, admin_only):
    """`is_admin` decides between the full objects and their public projection (see vgen.public_view)."""

    def snapshot(request):
        return datasets(state(request).settings.public, viewer(request))

    def find_case(request, dataset_id, key):
        all_sets, jobs, run_map = snapshot(request)
        dataset = all_sets.get(dataset_id)
        case = next((c for c in (dataset or {}).get("cases", []) if c["key"] == key), None)
        if not case:
            raise HTTPException(404, "evaluation case not found")
        return dataset, case, jobs, run_map

    @app.get("/api/evaluations")
    def list_evaluations(request: Request):
        all_sets, _, _ = snapshot(request)
        return list(all_sets.values())

    @app.get("/api/evaluations/{dataset_id}/cases/{case_key}")
    def evaluation_case(request: Request, dataset_id: str, case_key: str):
        st = state(request)
        admin = is_admin(request)
        dataset, case, jobs, run_map = find_case(request, dataset_id, case_key)
        matching = [j for j in jobs if (j.case_fingerprint or spec_fingerprint(j.spec)) == case["fingerprint"]]
        with session() as s:
            deployments = {d.id: d for d in s.exec(select(Deployment))}
            ratings = {r.job_id: r.model_dump() for r in s.exec(select(Rating).where(Rating.job_id.in_([j.id for j in matching])))}
        groups = {}
        for j in matching:
            deploy_id = j.deployment_id or f"tokenbay-{j.endpoint}-{j.model}"
            dep = deployments.get(deploy_id)
            label = dep.label if dep else (st.registry.models[j.model].label if j.model in st.registry.models else j.model)
            group = groups.setdefault(deploy_id, {"id": deploy_id, "model": j.model, "label": label,
                "provider": dep.provider if dep else "tokenbay",
                # what the clips ran on (Tokenbay-served models have no entry): same reduced view for every viewer
                "hardware": public_view.public_hardware(dep.hardware) if dep else {}, "samples": []})
            run = run_map[j.run_id]
            editable = not st.settings.public or (run.owner is not None and run.owner == viewer(request))
            rating = ratings.get(j.id)
            if rating is not None and not (admin or editable):    # someone else's rating: scores without the comment
                rating = public_view.public_rating(rating)
            group["samples"].append({**job_out(st, j, admin), "editable": editable, "rating": rating})
        for group in groups.values():
            group["samples"].sort(key=lambda j: (j["status"] != "succeeded", not j["provenance"].get("preferred"), -j["created_at"], j["id"]))
            group["default_job_id"] = group["samples"][0]["id"]
        coverage = []
        for m in st.registry.models.values():
            state_name = m.task_state(case["task"])
            mine = [j for j in matching if j.origin == "generated" and j.model == m.id]
            status = "available" if any(j.status == "succeeded" for j in mine) else (
                "failed" if mine else "missing" if state_name == "enabled" else state_name)
            coverage.append({"model": m.id, "label": m.label, "status": status, "task_state": state_name,
                             # the stored reason quotes the gateway's error text
                             "reason": (m.probe.get("unavailable") or "") if admin else ""})
        return {"dataset": {"id": dataset["id"], "title": dataset["title"]}, "case": case,
                "groups": list(groups.values()), "coverage": coverage}

    @app.get("/api/evaluations/{dataset_id}/backfill-plan")
    def backfill_plan(request: Request, dataset_id: str):
        admin_only(request)                # an operator planning tool: request payloads and probe-derived reasons
        st = state(request)
        all_sets, jobs, _ = snapshot(request)
        dataset = all_sets.get(dataset_id)
        if not dataset or dataset_id == "tokenbay":
            raise HTTPException(404, "shared dataset not found")
        items = []
        for c in dataset["cases"]:
            models = [m.id for m in st.registry.models.values() if c["task"] in (*m.tasks, *m.experimental_tasks)]
            req = runs.RunRequest(task=c["task"], lang="zh", prompts=[runs.PromptIn(key=c["key"], title=c["title"],
                text=c["prompt"], checklist=c["checklist"], assets=c["assets"])], models=models,
                params={"duration": 5, "resolution": "720p", "ratio": "auto" if c["task"] == "i2v" else "16:9", "audio": True}, judge=False)
            planned = runs.plan(st.registry, req)
            for item in planned["items"]:
                same = [j for j in jobs if j.origin == "generated" and j.model == item["model"] and
                        (j.case_fingerprint or spec_fingerprint(j.spec)) == c["fingerprint"]]
                complete = any(j.status == "succeeded" for j in same)
                attempted = bool(same)
                items.append({**item, "case_key": c["key"], "case_title": c["title"], "task": c["task"],
                    "existing_result": complete, "previous_attempt": attempted,
                    "missing": not complete, "ready": item["ok"] and not attempted})
        total = round(sum((i.get("estimate") or {}).get("usd") or 0 for i in items if i["ready"]), 4)
        return {"dataset_id": dataset_id, "items": items, "runnable": sum(i["ready"] for i in items), "total_usd": total,
                "unknown_prices": sum(i["ready"] and (i.get("estimate") or {}).get("usd") is None for i in items),
                "judge": False, "submitted": False, "price_basis": "repository_catalog", "budget": budget_view(st, total)}

    @app.get("/api/benchmarks")
    def benchmarks(request: Request):
        with session() as s:
            experiments = list(s.exec(select(Experiment).order_by(Experiment.date.desc())))
            deployments = list(s.exec(select(Deployment)))
        rows = [{**e.model_dump(exclude={"summary", "artifacts", "config"}),
                 "n_attempts": sum(p["n_submitted_attempts"] for p in e.summary["phases"]),
                 "n_videos": sum(p["n_technical_valid"] for p in e.summary["phases"])} for e in experiments]
        shown = [d.model_dump() for d in deployments]
        if not is_admin(request):
            rows = [public_view.public_experiment_row(r) for r in rows]
            shown = [public_view.public_deployment(d) for d in shown]
        return {"experiments": rows, "deployments": shown}

    def experiment_or_404(experiment_id):
        with session() as s:
            e = s.get(Experiment, experiment_id)
        if not e:
            raise HTTPException(404, "experiment not found")
        return e

    def experiment_detail(e):
        with session() as s:
            attempts = list(s.exec(select(BenchmarkAttempt).where(BenchmarkAttempt.experiment_id == e.id)))
            deployments = list(s.exec(select(Deployment).where(Deployment.id.in_(e.deployment_ids))))
        return {**e.model_dump(exclude={"artifacts"}), "deployments": [d.model_dump() for d in deployments],
                "artifacts": {name: f"/api/benchmarks/{e.id}/artifacts/{name}" for name in e.artifacts},
                "attempts": [a.model_dump() for a in attempts]}

    @app.get("/api/benchmarks/{experiment_id}")
    def benchmark(request: Request, experiment_id: str):
        detail = experiment_detail(experiment_or_404(experiment_id))
        return detail if is_admin(request) else public_view.public_experiment(detail)

    @app.get("/api/benchmarks/{experiment_id}/export")
    def export_benchmark(request: Request, experiment_id: str, format: str = "json"):
        admin_only(request)                # the raw attempt records, account figures included
        d = experiment_detail(experiment_or_404(experiment_id))
        if format == "json":
            body = json.dumps(d, ensure_ascii=False, indent=2)
            media = "application/json"
        elif format == "csv":
            buf = io.StringIO()
            rows = [{"experiment_id": experiment_id, "deployment_id": a["deployment_id"], "job_id": a["job_id"], **a["metrics"]} for a in d["attempts"]]
            columns = list(dict.fromkeys(k for row in rows for k in row))
            w = csv.DictWriter(buf, fieldnames=columns)
            w.writeheader()
            for row in rows:
                w.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})
            body, media = "\ufeff" + buf.getvalue(), "text/csv"
        else:
            raise HTTPException(400, "format must be json or csv")
        return Response(body, media_type=media, headers={"content-disposition": f'attachment; filename="{experiment_id}.{format}"'})

    @app.get("/api/benchmarks/{experiment_id}/artifacts/{name:path}")
    def artifact(request: Request, experiment_id: str, name: str):
        e = experiment_or_404(experiment_id)
        path = e.artifacts.get(name)
        # the report and its figures are for everyone; raw records (attempts.csv, provenance, figure data) are not
        open_to_all = public_view.is_public_artifact(name)
        if not path or not Path(path).is_file() or not (open_to_all or is_admin(request)):
            raise HTTPException(404, "artifact not found")
        # on the hosted site an admin-only file must never be kept by a browser or proxy
        cache = "private, no-store" if state(request).settings.public and not open_to_all else "no-cache"
        return FileResponse(path, filename=Path(name).name,
                            content_disposition_type="inline" if name == "report.html" else "attachment",
                            headers={"Content-Security-Policy": "default-src 'none'; script-src 'none'; img-src data:; style-src 'unsafe-inline'; "
                                     "base-uri 'none'; form-action 'none'; object-src 'none'",
                                     "X-Content-Type-Options": "nosniff", "Cache-Control": cache})
