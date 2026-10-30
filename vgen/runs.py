"""Planning (payloads, adjustments, cost) and creation of comparison runs."""

from __future__ import annotations

import random
import string
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import select

from vgen.adapters import build_request
from vgen.adapters.base import AssetRef, GenerationSpec, Unsupported, redact_payload
from vgen.assets import resolver
from vgen.catalog.registry import Registry
from vgen.costs import estimate_job, estimate_judge, quota_to_usd
from vgen.db import BLOCKED, PENDING, Job, LedgerEntry, Run, session


class PromptIn(BaseModel):
    key: str
    title: str = ""
    text: str
    checklist: list[str] = Field(default_factory=list)
    assets: dict[str, Any] = Field(default_factory=dict)     # overrides run-level assets


class RunRequest(BaseModel):
    title: str = ""
    kind: str = "compare"
    scenario_id: str | None = None
    category: str | None = None
    task: str = "t2v"
    lang: str = "en"
    endpoint: str = "com"
    prompts: list[PromptIn]
    models: list[str]
    params: dict[str, Any] = Field(default_factory=dict)
    model_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    assets: dict[str, Any] = Field(default_factory=dict)
    judge: bool = False
    allow_experimental: bool = False
    allow_unknown_price: bool = False
    confirm: bool = False


PARAM_KEYS = ("duration", "resolution", "ratio", "audio", "seed", "negative_prompt")


def _asset_refs(assets: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("first_frame", "last_frame", "source_video", "audio_input"):
        if assets.get(key):
            out[key] = AssetRef(asset_id=assets[key], role=key)
    refs = assets.get("references") or []
    if refs:
        out["references"] = [AssetRef(asset_id=a, role="reference") for a in refs]
    return out


def make_spec(req: RunRequest, prompt: PromptIn, model_id: str) -> GenerationSpec:
    params = {k: v for k, v in req.params.items() if k in PARAM_KEYS and v not in (None, "")}
    params.update({k: v for k, v in req.model_overrides.get(model_id, {}).items() if k in PARAM_KEYS})
    assets = {**req.assets, **prompt.assets}
    return GenerationSpec(task=req.task, prompt=prompt.text, **params, **_asset_refs(assets))


def plan(registry: Registry, req: RunRequest, judge_model: str | None = None) -> dict[str, Any]:
    res = resolver()
    items: list[dict[str, Any]] = []
    total = 0.0
    unknown = 0
    for prompt in req.prompts:
        for model_id in req.models:
            item: dict[str, Any] = {"prompt_key": prompt.key, "model": model_id}
            try:
                model = registry.get(model_id)
                if req.kind == "probe":          # probes re-check models regardless of earlier probe results
                    model = model.model_copy(update={"probe": {}})
                if req.endpoint not in model.endpoints:
                    raise Unsupported(f"{model.label} is not offered on {req.endpoint}")
                spec = make_spec(req, prompt, model_id)
                built = build_request(model, spec, res, allow_experimental=req.allow_experimental)
                est = estimate_job(registry, model_id, built.effective)
                item.update({
                    "ok": True,
                    "spec": spec.model_dump(mode="json"),
                    "payload": redact_payload(built.payload),
                    "adjustments": [a.as_dict() for a in built.adjustments],
                    "warnings": built.warnings,
                    "effective": built.effective,
                    "estimate": est.as_dict(),
                    "task_state": model.task_state(req.task),
                })
                if est.usd is None:
                    unknown += 1
                else:
                    total += est.usd
                if req.judge and judge_model:
                    j = estimate_judge(registry, judge_model, float(built.effective.get("duration") or 6),
                                       images=len(spec.references) + (1 if spec.first_frame else 0) + (1 if spec.last_frame else 0),
                                       with_audio=bool(built.effective.get("audio")))
                    item["judge_estimate_usd"] = round(j, 4) if j else None
                    total += j or 0.0
            except (Unsupported, KeyError) as exc:
                item.update({"ok": False, "reason": str(exc).strip("'\"")})
            items.append(item)
    return {"items": items, "total_usd": round(total, 4), "unknown_prices": unknown,
            "runnable": sum(1 for i in items if i["ok"])}


def spent_usd() -> float:
    with session() as s:
        rows = list(s.exec(select(LedgerEntry)))
    return round(sum((r.actual_usd if r.actual_usd is not None else r.est_usd) for r in rows), 4)


def spent_by_run(run_ids: list[str]) -> dict[str, float]:
    """Ledger spend per run (video + judge): the actual cost where known, else the estimate."""
    if not run_ids:
        return {}
    with session() as s:
        rows = list(s.exec(select(LedgerEntry).where(LedgerEntry.run_id.in_(run_ids))))
    out: dict[str, float] = {}
    for r in rows:
        out[r.run_id] = out.get(r.run_id, 0.0) + (r.actual_usd if r.actual_usd is not None else r.est_usd)
    return {k: round(v, 4) for k, v in out.items()}


def create(registry: Registry, req: RunRequest, planned: dict[str, Any]) -> Run:
    rng = random.Random()
    run = Run(title=req.title or _default_title(req), kind=req.kind, scenario_id=req.scenario_id,
              category=req.category, task=req.task, lang=req.lang, endpoint=req.endpoint,
              params={**req.params, "model_overrides": req.model_overrides, "assets": req.assets,
                      "allow_experimental": req.allow_experimental},
              prompts=[p.model_dump() for p in req.prompts], models=list(req.models), judge=req.judge,
              est_cost_usd=planned["total_usd"])
    jobs: list[Job] = []
    by_prompt: dict[str, list[dict]] = {}
    for item in planned["items"]:
        by_prompt.setdefault(item["prompt_key"], []).append(item)
    for key, group in by_prompt.items():
        labels = list(string.ascii_uppercase[: len(group)])
        rng.shuffle(labels)
        for label, item in zip(labels, group):
            spec = dict(item.get("spec") or {})
            spec["allow_experimental"] = req.allow_experimental
            if req.kind == "probe":
                spec["probe_override"] = True
            jobs.append(Job(
                run_id=run.id, prompt_key=key, model=item["model"], endpoint=req.endpoint, label=label,
                status=PENDING if item["ok"] else BLOCKED, spec=spec, payload=item.get("payload") or {},
                adjustments=(item.get("adjustments") or []) + [{"field": "warning", "requested": None, "used": None,
                                                                 "reason": w} for w in item.get("warnings", [])],
                effective=item.get("effective") or {},
                est_cost_usd=(item.get("estimate") or {}).get("usd") or 0.0,
                fail_reason="" if item["ok"] else item.get("reason", ""),
                judge_status="",
            ))
    with session() as s:
        s.add(run)
        for j in jobs:
            s.add(j)
        s.commit()
        s.refresh(run)
    return run


def _default_title(req: RunRequest) -> str:
    first = req.prompts[0].title or req.prompts[0].text[:40] if req.prompts else req.task
    return f"{req.task.upper()} · {first}"


def record_video_spend(job: Job) -> None:
    with session() as s:
        s.add(LedgerEntry(kind="video", job_id=job.id, run_id=job.run_id, model=job.model, endpoint=job.endpoint,
                          est_usd=job.est_cost_usd or 0.0, note=job.task_id or ""))
        s.commit()


def settle_quota(registry: Registry, job_id: str, quota: int | None, failed: bool) -> None:
    if quota is None:
        return
    with session() as s:
        entry = s.exec(select(LedgerEntry).where(LedgerEntry.job_id == job_id, LedgerEntry.kind == "video")).first()
        if entry is None:
            return
        entry.quota = int(quota)
        if registry.quota_calibrated:
            entry.actual_usd = round(quota_to_usd(registry, quota) or 0.0, 5)
        elif failed and int(quota) == 0:
            entry.actual_usd = 0.0
        s.add(entry)
        s.commit()


def record_unknown_spend(job: Job) -> None:
    with session() as s:
        if s.exec(select(LedgerEntry).where(LedgerEntry.job_id == job.id, LedgerEntry.kind == "video")).first():
            return
        s.add(LedgerEntry(kind="video", job_id=job.id, run_id=job.run_id, model=job.model, endpoint=job.endpoint,
                          est_usd=job.est_cost_usd or 0.0, note="submit outcome unknown — possibly billed"))
        s.commit()


def settle_actual(registry: Registry, job: Job | None) -> None:
    """Replace the worst-case estimate with the cost computed from what the gateway reports."""
    if job is None:
        return
    usd, basis = actual_cost(registry, job)
    if usd is None:
        return
    with session() as s:
        entry = s.exec(select(LedgerEntry).where(LedgerEntry.job_id == job.id, LedgerEntry.kind == "video")).first()
        if entry is None:
            return
        entry.actual_usd = round(usd, 5)
        entry.note = basis
        s.add(entry)
        s.commit()


def actual_cost(registry: Registry, job: Job) -> tuple[float | None, str]:
    """Seedance: reported usage tokens × price. Per-second models: billed seconds × price (audio multiplier stays)."""
    price = registry.price(job.model) or {}
    eff = job.effective or {}
    detail = job.last_detail or {}
    usage = detail.get("usage") or {}
    if "per_mtok" in price and usage.get("totalTokens"):
        table = price["per_mtok"]["with_video" if eff.get("has_video_input") else "without_video"]
        res = str(usage.get("resolution") or eff.get("resolution") or "720p").lower()
        rate = table.get(res) or max(table.values())
        tokens = int(usage["totalTokens"])
        return tokens * rate / 1e6, f"{tokens:,} tokens reported × ${rate:g}/M"
    if "per_second" in price:
        est = estimate_job(registry, job.model, eff)
        return est.usd, f"billed seconds × rate ({est.basis})"
    return None, ""


def record_judge_spend(job: Job, usd: float | None, model: str) -> None:
    if not usd:
        return
    with session() as s:
        s.add(LedgerEntry(kind="judge", job_id=job.id, run_id=job.run_id, model=model, endpoint="com",
                          est_usd=usd, actual_usd=usd))
        s.commit()
