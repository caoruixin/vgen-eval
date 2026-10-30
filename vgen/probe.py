"""Live capability probe: one cheapest-settings call per model / request shape.

Results update vgen/catalog/probe_status.json (task states, input delivery, observed output, quota unit).
"""

from __future__ import annotations

import asyncio
import io
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

from PIL import Image
from sqlmodel import select

from vgen import db
from vgen.api import AppState, EngineLock, build_runtime, refresh_live
from vgen.assets import get_asset, store_bytes
from vgen.catalog.registry import save_probe_status
from vgen.config import Settings
from vgen.costs import estimate_job
from vgen.db import TERMINAL, Job, ProbeResult, Run, session
from vgen.runs import PromptIn, RunRequest, create, plan, spent_usd

T2V_PROMPT = "A paper boat floats down a rain-filled street gutter, close-up, soft overcast light, gentle current."


@dataclass
class Case:
    id: str
    model: str
    task: str
    params: dict
    prompt: str = T2V_PROMPT
    assets: dict = field(default_factory=dict)
    checks: list[str] = field(default_factory=lambda: ["resolution", "duration"])
    updates_task: bool = True
    delivery: tuple[str, str] | None = None      # (kind, method) verified by this case
    note: str = ""


def cases() -> list[Case]:
    hh = {"resolution": "720p", "ratio": "16:9", "duration": 3}
    sd = {"resolution": "480p", "ratio": "16:9", "duration": 4}
    i2v = "The cat turns its head toward the camera as waves roll past."
    bird = "CG animation: the small blue bird takes off from the ground and flies up into the blue sky."
    r2v = "The cartoon girl from {ref1} hugs the cartoon snake from {ref2} in a sunny garden."
    return [
        Case("hh10-t2v", "happyhorse-1.0-t2v", "t2v", hh, checks=["resolution", "aspect_ratio", "duration"]),
        Case("hh10-i2v-url", "happyhorse-1.0-i2v", "i2v", hh, i2v, {"first_frame": "cat"}, delivery=("image", "url")),
        Case("hh10-i2v-datauri", "happyhorse-1.0-i2v", "i2v", hh, i2v, {"first_frame": "cat_local"},
             updates_task=False, delivery=("image", "data_uri")),
        Case("hh10-r2v", "happyhorse-1.0-r2v", "r2v", hh, r2v, {"references": ["girl", "snake"]},
             checks=["resolution", "aspect_ratio", "duration"]),
        Case("wan27-t2v", "wan2.7-t2v", "t2v", hh, checks=["resolution", "aspect_ratio", "duration"]),
        Case("wan27-i2v-datauri", "wan2.7-i2v", "i2v", hh, i2v, {"first_frame": "cat_local"},
             delivery=("image", "data_uri")),
        Case("wan27-flf2v", "wan2.7-i2v", "flf2v", hh, bird, {"first_frame": "bird_first", "last_frame": "bird_last"}),
        Case("wan27-r2v", "wan2.7-r2v", "r2v", hh, r2v, {"references": ["girl", "snake"]}),
        Case("wan27-edit", "wan2.7-videoedit", "v2v_edit", hh,
             "Restyle the video as a hand-drawn watercolour illustration; keep the motion unchanged.",
             {"source_video": "market_clip"}, checks=["resolution"]),
        Case("wan30-t2v", "wan3.0", "t2v", hh, checks=["resolution", "aspect_ratio", "duration"]),
        Case("wan30-i2v", "wan3.0", "i2v", {**hh, "ratio": "auto"}, i2v, {"first_frame": "cat"}),
        Case("veo-t2v", "veo-3.1-generate-preview", "t2v", {"resolution": "720p", "ratio": "16:9", "duration": 4},
             checks=["resolution", "aspect_ratio", "duration"]),
        Case("veo-fast-audio", "veo-3.1-fast-generate-preview", "t2v",
             {"resolution": "720p", "ratio": "16:9", "duration": 4, "audio": True},
             "A street musician plays a short melody on an acoustic guitar and says 'thank you' to the camera.",
             checks=["resolution", "duration", "audio"], updates_task=False),
        Case("veo-lite-flf2v-datauri", "veo-3.1-lite-generate-preview", "flf2v",
             {"resolution": "720p", "ratio": "16:9", "duration": 4}, bird,
             {"first_frame": "bird_first_local", "last_frame": "bird_last_local"}, delivery=("image", "data_uri")),
        Case("sd20-t2v", "seedance-2.0", "t2v", sd, checks=["resolution", "aspect_ratio", "duration"]),
        Case("sd20fast-t2v", "seedance-2.0-fast", "t2v", sd, checks=["resolution", "aspect_ratio", "duration"]),
        Case("sd25-t2v", "seedance-2.5", "t2v", sd, checks=["resolution", "aspect_ratio", "duration"]),
        Case("sdmini-t2v-audio", "seedance-2.0-mini", "t2v", {**sd, "audio": True},
             "A small dog barks twice at a doorbell in a cosy hallway.", checks=["resolution", "duration", "audio"]),
        Case("sdmini-i2v-datauri", "seedance-2.0-mini", "i2v", {**sd, "ratio": "auto"}, i2v, {"first_frame": "cat_local"},
             delivery=("image", "data_uri")),
        Case("sdmini-flf2v", "seedance-2.0-mini", "flf2v", {**sd, "ratio": "auto"}, bird,
             {"first_frame": "bird_first", "last_frame": "bird_last"}),
        Case("sdmini-r2v", "seedance-2.0-mini", "r2v", sd, r2v, {"references": ["girl", "snake"]}),
        Case("sdmini-extend", "seedance-2.0-mini", "extend", sd,
             "Continue the video: the boy looks up and walks toward the camera.", {"source_video": "market_clip"},
             checks=["resolution"]),
        Case("sdmini-a2v", "seedance-2.0-mini", "a2v", {**sd, "ratio": "auto", "audio": True},
             "The cat talks to the camera, its mouth moving with the voice.", {"first_frame": "cat", "audio_input": "talk"},
             checks=["resolution"]),
    ]


def _local_copy(st: AppState, name: str) -> str | None:
    """Same picture, different bytes (re-encoded) and no public URL -> forces data-URI delivery."""
    src_id = st.asset_ids.get(name)
    asset = get_asset(src_id) if src_id else None
    if asset is None:
        return None
    with Image.open(asset.local_path) as im:
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=93)
    a = store_bytes(st.settings.uploads_dir, buf.getvalue(), f"{name}_local.jpg", label=f"{name} (local copy, no URL)",
                    source="probe", public_urls=[])
    return a.id


def _asset_ids(st: AppState, assets: dict) -> dict:
    out: dict[str, Any] = {}
    for key, value in assets.items():
        names = value if isinstance(value, list) else [value]
        ids = []
        for n in names:
            if n.endswith("_local"):
                ids.append(_local_copy(st, n[: -len("_local")]))
            else:
                ids.append(st.asset_ids.get(n))
        if any(i is None for i in ids):
            raise RuntimeError(f"missing example asset for {value}")
        out[key] = ids if isinstance(value, list) else ids[0]
    return out


def request_for(case: Case, st: AppState) -> RunRequest:
    return RunRequest(title=f"Probe · {case.id}", kind="probe", task=case.task, endpoint="com",
                      prompts=[PromptIn(key=case.id, title=case.id, text=case.prompt)], models=[case.model],
                      params=case.params, assets=_asset_ids(st, case.assets), judge=False,
                      allow_experimental=True, allow_unknown_price=True, confirm=True)


def plan_cases(st: AppState, selected: list[str] | None, budget_usd: float,
               log=print) -> tuple[list[tuple[Case, RunRequest, float]], float]:
    todo = [c for c in cases() if not selected or c.id in selected]
    planned_cases: list[tuple[Case, RunRequest, float]] = []
    total = 0.0
    log(f"{'case':26s} {'model':32s} {'task':8s} {'est $':>7s}  note")
    for c in todo:
        req = request_for(c, st)
        p = plan(st.registry, req)
        item = p["items"][0]
        if not item["ok"]:
            log(f"{c.id:26s} {c.model:32s} {c.task:8s} {'-':>7s}  skipped: {item['reason']}")
            continue
        est = item["estimate"]["usd"] or 0.0
        if st.registry.available(c.model, "com") is False:
            log(f"{c.id:26s} {c.model:32s} {c.task:8s} {'-':>7s}  skipped: not in /v1/models")
            continue
        if total + est > budget_usd:
            log(f"{c.id:26s} {c.model:32s} {c.task:8s} {est:7.3f}  skipped: budget ${budget_usd:g} reached")
            continue
        total += est
        planned_cases.append((c, req, est))
        adj = "; ".join(f"{a['field']}: {a['requested']}→{a['used']}" for a in item["adjustments"]) or ""
        log(f"{c.id:26s} {c.model:32s} {c.task:8s} {est:7.3f}  {adj}")
    log(f"\n{len(planned_cases)} cases, estimated ${total:.2f} (worst case); ledger so far ${spent_usd():.2f}")
    return planned_cases, total


async def execute_cases(st: AppState, planned_cases: list[tuple[Case, RunRequest, float]], judge_case: bool = True,
                        log=print, timeout_s: float = 25 * 60) -> dict[str, Any]:
    """Submit the planned cases through st.engine (already running), wait, judge one clip, update the overlay."""
    job_by_case: dict[str, str] = {}
    for c, req, _ in planned_cases:
        run = create(st.registry, req, plan(st.registry, req))
        with session() as s:
            job = s.exec(select(Job).where(Job.run_id == run.id)).first()
        job_by_case[c.id] = job.id
    st.engine.wake()
    deadline = time.time() + timeout_s
    last_print = 0.0
    while time.time() < deadline:
        jobs = {cid: db.get_job(jid) for cid, jid in job_by_case.items()}
        pending = [cid for cid, j in jobs.items() if j.status not in TERMINAL]
        if time.time() - last_print > 20 or not pending:
            last_print = time.time()
            summary = ", ".join(f"{cid}:{j.status}{'(' + j.progress + ')' if j.progress else ''}"
                                for cid, j in jobs.items() if j.status not in TERMINAL)
            log(f"[{time.strftime('%H:%M:%S')}] {len(pending)} running {summary}")
        if not pending:
            break
        await asyncio.sleep(3)
    judged = None
    if judge_case:
        ok_job = next((db.get_job(jid) for cid, jid in job_by_case.items()
                       if db.get_job(jid).status == db.SUCCEEDED and cid.endswith("t2v")), None)
        if ok_job:
            with session() as s:
                run = s.get(Run, ok_job.run_id)
                run.prompts = [{**run.prompts[0], "checklist": [
                    "A paper boat is visible", "The boat floats along flowing water", "Rainy street setting"]}]
                s.add(run)
                s.commit()
            await st.engine.judge(ok_job.id)
            judged = db.get_job(ok_job.id)
    status = summarize(st, planned_cases, job_by_case, judged, log=log)
    save_probe_status(status)
    st.registry.probe = status
    for m in st.registry.models.values():
        m.probe = (status.get("models") or {}).get(m.id, {})
    log("wrote vgen/catalog/probe_status.json")
    log(f"ledger total now ${spent_usd():.2f}")
    return status


async def run_probe(settings: Settings, selected: list[str] | None, budget_usd: float, assume_yes: bool,
                    judge_case: bool = True, dry_run: bool = False) -> int:
    lock = EngineLock(settings.data_dir / "engine.lock")
    lock.acquire()
    st = build_runtime(settings)
    try:
        await refresh_live(st)
        planned_cases, total = plan_cases(st, selected, budget_usd)
        if not planned_cases or dry_run:
            if dry_run:
                for c, req, _ in planned_cases:
                    item = plan(st.registry, req)["items"][0]
                    print(f"\n# {c.id}\n{item['payload']}")
            return 0 if dry_run else 1
        if not assume_yes:
            answer = input("Submit these paid probe calls? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("aborted")
                return 1

        await st.engine.start()
        await execute_cases(st, planned_cases, judge_case)
        return 0
    finally:
        await st.engine.stop()
        for gw in st.gateways.values():
            await gw.aclose()
        lock.release()


def resummarize(st: AppState, log=print, reanalyze: bool = True) -> dict[str, Any]:
    """Rebuild probe_status.json from the latest probe job per case (no spending)."""
    from pathlib import Path as _P

    from vgen.analysis import analyze_video

    with session() as s:
        runs = list(s.exec(select(Run).where(Run.kind == "probe").order_by(Run.created_at)))
        latest: dict[str, Job] = {}
        for r in runs:
            case_id = r.title.split("· ", 1)[-1]
            job = s.exec(select(Job).where(Job.run_id == r.id)).first()
            if job is None:
                continue
            prev = latest.get(case_id)
            # a newer attempt wins unless it is still running and the older one finished
            if prev is None or job.status in TERMINAL or prev.status not in TERMINAL:
                latest[case_id] = job
    by_id = {c.id: c for c in cases()}
    planned = [(by_id[cid], request_for(by_id[cid], st), job.est_cost_usd) for cid, job in latest.items() if cid in by_id]
    if reanalyze:
        for c, _, _ in planned:
            job = latest[c.id]
            if job.status == db.SUCCEEDED and job.video_path and _P(job.video_path).exists():
                spec = job.spec or {}
                first = get_asset((spec.get("first_frame") or {}).get("asset_id", "")) if spec.get("first_frame") else None
                last = get_asset((spec.get("last_frame") or {}).get("asset_id", "")) if spec.get("last_frame") else None
                model = st.registry.models.get(job.model)
                res = analyze_video(_P(job.video_path), st.settings.frames_dir / job.id, effective=job.effective or {},
                                    task=spec.get("task", "t2v"), model_audio=model.audio if model else "unknown",
                                    first_ref=_P(first.local_path) if first else None,
                                    last_ref=_P(last.local_path) if last else None)
                db.update_job(job.id, compliance=res["compliance"], signals=res["signals"], fidelity=res["fidelity"],
                              media=res["media"])
    judged = None
    with session() as s:
        for r in reversed(runs):
            j = s.exec(select(Job).where(Job.run_id == r.id, Job.judge_status.in_(["done", "error"]))).first()
            if j is not None:
                judged = j
                break
    base = dict(st.registry.probe or {})
    base["models"] = {k: {kk: vv for kk, vv in v.items() if kk in ("unavailable", "notes")}
                      for k, v in (base.get("models") or {}).items()}
    st.registry.probe = base
    status = summarize(st, planned, {cid: j.id for cid, j in latest.items()}, judged, log=log)
    save_probe_status(status)
    st.registry.probe = status
    for m in st.registry.models.values():
        m.probe = (status.get("models") or {}).get(m.id, {})
    return status


def _outcome(job: Job) -> str:
    if job.status == db.SUCCEEDED:
        return "ok"
    if job.status in (db.FAILED, db.BLOCKED):
        return "fail"
    return "unknown"


def summarize(st: AppState, planned: list[tuple[Case, RunRequest, float]], job_by_case: dict[str, str],
              judged: Job | None, log=print) -> dict[str, Any]:
    status: dict[str, Any] = dict(st.registry.probe or {})
    models = status.setdefault("models", {})
    ratios: list[float] = []
    log(f"\n{'case':26s} {'result':8s} {'checks':30s} detail")
    for c, req, est in planned:
        job = db.get_job(job_by_case[c.id])
        outcome = _outcome(job)
        comp = {i["key"]: i for i in job.compliance or []}
        failed_checks = [k for k in c.checks if comp.get(k, {}).get("ok") is False]
        if outcome == "ok" and failed_checks:
            outcome = "partial"
        m = models.setdefault(c.model, {})
        if outcome in ("ok", "partial"):
            m.pop("unavailable", None)            # it works again
        elif job.status == db.SUBMIT_UNKNOWN and job.error_code == "server":
            m["unavailable"] = (f"tokenbay.com returned '{job.fail_reason.split(' — ')[0]}' on submit "
                                f"({time.strftime('%Y-%m-%d')}); re-check from the Models page")
        if c.updates_task and outcome in ("ok", "partial", "fail"):
            m.setdefault("tasks", {})[c.task] = "fail" if outcome == "fail" else "ok"
        if c.delivery and outcome in ("ok", "partial", "fail"):
            kind, method = c.delivery
            m.setdefault("delivery", {}).setdefault(kind, {})[method] = "no" if outcome == "fail" else "yes"
        media = job.media or {}
        v = media.get("video") or {}
        observed = {"case": c.id, "outcome": outcome, "status": job.status, "fail_reason": job.fail_reason,
                    "requested": job.effective, "duration": media.get("duration"),
                    "size": f"{v.get('width')}x{v.get('height')}" if v else None, "fps": v.get("fps"),
                    "codec": v.get("codec"), "audio": bool(media.get("audio")), "quota": job.quota,
                    "est_usd": job.est_cost_usd, "latency_s": (job.gw_finish_time - job.gw_submit_time)
                    if job.gw_finish_time and job.gw_submit_time else None,
                    "failed_checks": failed_checks, "ts": time.strftime("%Y-%m-%d %H:%M")}
        cases_map = m.setdefault("cases", {})
        cases_map[c.id] = observed
        if outcome in ("ok", "partial") and job.quota and job.est_cost_usd and not req.params.get("audio"):
            info = st.registry.get(c.model)
            if "per_second" in (st.registry.price(c.model) or {}) and info:
                # bill on the observed duration where available
                dur = media.get("duration") or job.effective.get("duration")
                rate_est = estimate_job(st.registry, c.model, {**job.effective, "duration": round(dur or 0)}).usd
                if rate_est:
                    ratios.append(job.quota / rate_est)
        with session() as s:
            s.add(ProbeResult(case_id=c.id, model=c.model, ok=outcome == "ok", job_id=job.id, detail=observed))
            s.commit()
        detail = job.fail_reason or f"{observed['size']} {observed['duration']}s audio={observed['audio']} quota={job.quota}"
        log(f"{c.id:26s} {outcome:8s} {','.join(failed_checks) or '-':30s} {detail[:120]}")
    if ratios:
        med = statistics.median(ratios)
        spread = (max(ratios) - min(ratios)) / med if med else 1
        status["quota_per_usd"] = round(med)
        status["quota_calibrated"] = len(ratios) >= 2 and spread < 0.2
        status["quota_samples"] = [round(r) for r in ratios]
        log(f"quota per USD ≈ {med:,.0f} from {len(ratios)} samples (spread {spread:.0%}) → "
            f"{'calibrated' if status['quota_calibrated'] else 'NOT calibrated'}")
    if judged is not None:
        j = judged.judge or {}
        status["judge"] = {"model": st.settings.judge_model, "ok": judged.judge_status == "done" and bool(j.get("checklist")),
                           "mode": j.get("mode"), "error": j.get("error"), "usage": j.get("usage"),
                           "ts": time.strftime("%Y-%m-%d %H:%M")}
        log(f"judge ({st.settings.judge_model}): {judged.judge_status} {j.get('error') or j.get('verdict', '')}")
    return status
