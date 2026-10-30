"""FastAPI application: REST API + built frontend."""

from __future__ import annotations

import asyncio
import csv
import dataclasses
import io
import json
import logging
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlmodel import select
from starlette.middleware.trustedhost import TrustedHostMiddleware

from vgen import db, ranking, runs
from vgen.assets import get_asset, list_assets, store_bytes
from vgen.catalog.registry import TASKS, Registry
from vgen.config import ROOT, Settings, get_settings
from vgen.costs import estimate_job, quota_to_usd
from vgen.db import ACTIVE_REMOTE, TERMINAL, Job, LedgerEntry, Pick, ProbeResult, Rating, Run, session
from vgen.gateway import Gateway, GatewayError
from vgen.jobs import JobEngine
from vgen.mock_gateway import MockGateway
from vgen.scenarios import load_packs, packs_for_api, register_assets
from vgen.security import LAUNCH_TOKEN, TokenGuard

log = logging.getLogger("vgen.api")
WEB_DIST = ROOT / "web" / "dist"


class EngineLock:
    """One engine per data dir (the CLI probe and the server must not poll the same jobs)."""

    def __init__(self, path: Path):
        self.path = path

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                pid = int(self.path.read_text().strip() or 0)
            except ValueError:
                pid = 0
            if pid and pid != os.getpid() and _pid_alive(pid):
                raise RuntimeError(f"another vgen process (pid {pid}) is using {self.path.parent}; stop it first")
        self.path.write_text(str(os.getpid()))

    def release(self) -> None:
        try:
            if self.path.exists() and self.path.read_text().strip() == str(os.getpid()):
                self.path.unlink()
        except OSError:
            pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class AppState:
    settings: Settings
    registry: Registry
    gateways: dict[str, Gateway]
    engine: JobEngine
    packs_raw: dict[str, Any]
    asset_ids: dict[str, str]
    mock: MockGateway | None


def build_runtime(settings: Settings, transport_factory: Callable[[str], httpx.AsyncBaseTransport] | None = None) -> AppState:
    db.init_db(settings.db_path)
    st = AppState()
    st.settings = settings
    st.registry = Registry.load()
    st.mock = MockGateway(st.registry) if settings.mock else None
    st.gateways = {}
    for key, ep in settings.endpoints.items():
        transport = st.mock.transport() if st.mock else (transport_factory(key) if transport_factory else None)
        st.gateways[key] = Gateway(ep, transport=transport, submit_read_timeout=settings.submit_read_timeout_s)
    st.engine = JobEngine(settings, st.registry, st.gateways)
    st.packs_raw = load_packs()
    st.asset_ids = register_assets(settings, st.packs_raw)
    _apply_overrides(st, db.kv_get("settings_overrides") or {})
    return st


SETTING_TYPES = {"budget_usd": float, "confirm_above_usd": float, "usd_cny": float, "judge_model": str, "rpm": int,
                 "max_inflight": int}


def _settings_changes(st: AppState, payload: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for key, kind in SETTING_TYPES.items():
        if key not in payload or payload[key] in (None, ""):
            continue
        try:
            value = kind(payload[key])
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, f"invalid {key}") from exc
        if key == "judge_model" and value not in (st.registry.prices.get("judge") or {}):
            raise HTTPException(400, f"unknown judge model {value}")
        if key == "rpm" and not 1 <= value <= 60:
            raise HTTPException(400, "rpm must be between 1 and 60")
        if key == "max_inflight" and not 1 <= value <= 24:
            raise HTTPException(400, "max_inflight must be between 1 and 24")
        if kind is float and value < 0:
            raise HTTPException(400, f"{key} must be >= 0")
        changes[key] = value
    return changes


def _apply_overrides(st: AppState, overrides: dict[str, Any]) -> None:
    fields = {k: v for k, v in overrides.items()
              if k in ("budget_usd", "confirm_above_usd", "usd_cny", "judge_model", "max_inflight")}
    if fields:
        st.settings = dataclasses.replace(st.settings, **fields)
        st.engine.settings = st.settings
    if "rpm" in overrides and not st.settings.mock:
        for gw in st.gateways.values():
            gw.limiter.rpm = int(overrides["rpm"])


async def refresh_live(st: AppState) -> None:
    for key, gw in st.gateways.items():
        try:
            ids = await gw.list_models()
            st.registry.set_live(key, set(ids))
        except GatewayError as exc:
            st.registry.live_error = str(exc)
            log.warning("model list for %s failed: %s", key, exc)


def create_app(settings: Settings | None = None,
               transport_factory: Callable[[str], httpx.AsyncBaseTransport] | None = None) -> FastAPI:
    settings = settings or get_settings()
    lock = EngineLock(settings.data_dir / "engine.lock")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        lock.acquire()
        st = build_runtime(settings, transport_factory)
        app.state.vgen = st
        await st.engine.start()
        refresher = asyncio.create_task(refresh_live(st))
        try:
            yield
        finally:
            refresher.cancel()
            await st.engine.stop()
            for gw in st.gateways.values():
                await gw.aclose()
            lock.release()

    app = FastAPI(title="vgen-eval", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.add_middleware(TokenGuard)

    def S(request: Request) -> AppState:
        return request.app.state.vgen

    # ------------------------------------------------------------------ config & catalog
    @app.get("/api/config")
    def config(request: Request) -> dict[str, Any]:
        st = S(request)
        return {
            "mock": st.settings.mock,
            "endpoints": [{"key": k, "name": g.endpoint.name, "rpm": g.limiter.rpm, "used_last_minute": g.limiter.used(),
                           "disabled_reason": g.disabled_reason, "last_error": g.last_error} for k, g in st.gateways.items()],
            "budget_usd": st.settings.budget_usd,
            "hard_cap_usd": st.settings.hard_cap_usd,
            "confirm_above_usd": st.settings.confirm_above_usd,
            "spent_usd": runs.spent_usd(),
            "usd_cny": st.settings.usd_cny,
            "judge_model": st.settings.judge_model,
            "max_inflight": st.settings.max_inflight,
            "judge_models": sorted((st.registry.prices.get("judge") or {}).keys()),
            "tasks": TASKS,
            "probe_updated_at": st.registry.probe.get("updated_at"),
            "quota_per_usd": st.registry.quota_per_usd,
            "quota_calibrated": st.registry.quota_calibrated,
            "quota_note": st.registry.probe.get("quota_note"),
            "live_checked_at": st.registry.live_checked_at,
            "live_error": st.registry.live_error,
        }

    @app.get("/api/models")
    def models(request: Request) -> dict[str, Any]:
        st = S(request)
        out = []
        for m in st.registry.models.values():
            res = "720p" if "720p" in m.resolutions else m.resolutions[0]
            ratio = "16:9" if "16:9" in m.ratios else ("adaptive" if "adaptive" in m.ratios else "auto")
            dur5 = min(m.allowed_durations(res), key=lambda d: abs(d - 5))
            est = estimate_job(st.registry, m.id, {"duration": dur5, "resolution": res, "ratio": ratio, "audio": False})
            out.append({
                **m.model_dump(exclude={"probe"}),
                "probe": m.probe,
                "task_states": {t: m.task_state(t) for t in TASKS},
                "delivery_effective": {k: {meth: m.delivery_state(k, meth) for meth in ("url", "data_uri")}
                                       for k in ("image", "video", "audio")},
                "available": {k: st.registry.available(m.id, k) for k in st.gateways},
                "price": st.registry.price(m.id),
                "price_ref": {"settings": f"{dur5}s @ {res}", **est.as_dict()},
            })
        return {"models": out, "unregistered": {k: st.registry.unregistered(k) for k in st.gateways},
                "live_checked_at": st.registry.live_checked_at}

    @app.put("/api/settings")
    def update_settings(request: Request, payload: dict = Body(...)) -> dict[str, Any]:
        st = S(request)
        changes = _settings_changes(st, payload)
        db.kv_set("settings_overrides", {**(db.kv_get("settings_overrides") or {}), **changes})
        _apply_overrides(st, changes)
        return config(request)

    @app.post("/api/models/refresh")
    async def models_refresh(request: Request) -> dict[str, Any]:
        await refresh_live(S(request))
        return {"ok": True, "live_checked_at": S(request).registry.live_checked_at}

    @app.get("/api/scenarios")
    def scenarios(request: Request) -> dict[str, Any]:
        st = S(request)
        return packs_for_api(st.packs_raw, st.asset_ids)

    # ------------------------------------------------------------------ assets
    def asset_out(a) -> dict[str, Any]:
        d = a.model_dump(exclude={"local_path"})
        d["url"] = f"/api/assets/{a.id}/file"
        return d

    @app.get("/api/assets")
    def assets() -> list[dict[str, Any]]:
        return [asset_out(a) for a in list_assets()]

    @app.post("/api/assets")
    async def upload_asset(request: Request, file: UploadFile = File(...), label: str = Form(""),
                           public_url: str = Form("")) -> dict[str, Any]:
        data = await file.read()
        if len(data) > 60 * 1024 * 1024:
            raise HTTPException(413, "file too large (max 60 MB)")
        a = store_bytes(S(request).settings.uploads_dir, data, file.filename or "upload", label=label,
                        public_urls=[public_url] if public_url else [])
        return asset_out(a)

    @app.post("/api/assets/url")
    async def asset_from_url(request: Request, payload: dict = Body(...)) -> dict[str, Any]:
        url = str(payload.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "url must be http(s)")
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
                resp = await c.get(url)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(400, f"could not download: {exc}") from exc
        if len(resp.content) > 60 * 1024 * 1024:
            raise HTTPException(413, "file too large (max 60 MB)")
        name = url.split("?")[0].rstrip("/").split("/")[-1] or "remote"
        a = store_bytes(S(request).settings.uploads_dir, resp.content, name, label=payload.get("label") or name,
                        source="url", public_urls=[url], normalize_images=False)
        return asset_out(a)

    @app.get("/api/assets/{asset_id}/file")
    def asset_file(asset_id: str):
        a = get_asset(asset_id)
        if a is None or not Path(a.local_path).exists():
            raise HTTPException(404, "asset not found")
        return FileResponse(a.local_path, media_type=a.mime)

    # ------------------------------------------------------------------ runs
    def budget_view(st: AppState, total: float) -> dict[str, Any]:
        spent = runs.spent_usd()
        reasons = []
        if total > st.settings.confirm_above_usd:
            reasons.append(f"estimated ${total:.2f} is above the ${st.settings.confirm_above_usd:g} confirmation threshold")
        if spent + total > st.settings.budget_usd:
            reasons.append(f"spent ${spent:.2f} + ${total:.2f} exceeds the ${st.settings.budget_usd:g} budget")
        cap = st.settings.hard_cap_usd
        blocked = cap is not None and spent + total > cap
        return {"spent_usd": spent, "budget_usd": st.settings.budget_usd, "confirm_reasons": reasons,
                "hard_cap_usd": cap, "hard_cap_blocked": blocked}

    @app.post("/api/plan")
    def plan_run(request: Request, req: runs.RunRequest) -> dict[str, Any]:
        st = S(request)
        planned = runs.plan(st.registry, req, st.settings.judge_model)
        return {**planned, "budget": budget_view(st, planned["total_usd"])}

    @app.post("/api/runs")
    def create_run(request: Request, req: runs.RunRequest):
        st = S(request)
        if req.endpoint not in st.gateways:
            raise HTTPException(400, f"endpoint '{req.endpoint}' is not enabled")
        planned = runs.plan(st.registry, req, st.settings.judge_model)
        if planned["runnable"] == 0:
            raise HTTPException(400, "nothing to run: " + "; ".join(sorted({i.get("reason", "") for i in planned["items"]})))
        if planned["unknown_prices"] and not req.allow_unknown_price:
            return JSONResponse(status_code=409, content={"needs_confirmation": True, "plan": planned,
                                                          "reasons": ["some models have no known price"]})
        budget = budget_view(st, planned["total_usd"])
        if budget["hard_cap_blocked"]:
            raise HTTPException(403, f"hard spend cap ${budget['hard_cap_usd']:g} would be exceeded")
        if budget["confirm_reasons"] and not req.confirm:
            return JSONResponse(status_code=409, content={"needs_confirmation": True, "plan": planned,
                                                          "reasons": budget["confirm_reasons"], "budget": budget})
        run = runs.create(st.registry, req, planned)
        st.engine.wake()
        return run_detail(st, run.id)

    @app.get("/api/runs")
    def list_runs(request: Request, limit: int = 100, kind: str | None = None) -> list[dict[str, Any]]:
        with session() as s:
            q = select(Run).order_by(Run.created_at.desc()).limit(limit)
            if kind:
                q = q.where(Run.kind == kind)
            rows = list(s.exec(q))
            jobs = list(s.exec(select(Job).where(Job.run_id.in_([r.id for r in rows])))) if rows else []
        by_run: dict[str, list[Job]] = {}
        for j in jobs:
            by_run.setdefault(j.run_id, []).append(j)
        spend = runs.spent_by_run([r.id for r in rows])
        out = []
        for r in rows:
            js = by_run.get(r.id, [])
            counts: dict[str, int] = {}
            for j in js:
                counts[j.status] = counts.get(j.status, 0) + 1
            posters = [f"/api/jobs/{j.id}/poster" for j in js if j.poster_path][:4]
            out.append({**r.model_dump(include={"id", "created_at", "title", "kind", "scenario_id", "category",
                                                "task", "lang", "judge", "est_cost_usd", "models"}),
                        "n_prompts": len(r.prompts or []), "n_jobs": len(js), "counts": counts,
                        "active": sum(1 for j in js if j.status not in TERMINAL), "posters": posters,
                        "spent_usd": spend.get(r.id, 0.0)})
        return out

    def job_out(st: AppState, j: Job) -> dict[str, Any]:
        d = j.model_dump(exclude={"video_path", "poster_path", "strip_path"})
        info = st.registry.models.get(j.model)
        d["model_label"] = info.label if info else j.model
        d["family"] = info.family if info else ""
        d["vendor"] = info.vendor if info else ""
        d["video_url"] = f"/api/jobs/{j.id}/video" if j.video_path else None
        d["poster_url"] = f"/api/jobs/{j.id}/poster" if j.poster_path else None
        d["strip_url"] = f"/api/jobs/{j.id}/strip" if j.strip_path else None
        d["latency_s"] = ranking.job_latency(j) if j.status == db.SUCCEEDED else None
        actual = quota_to_usd(st.registry, j.quota) if st.registry.quota_calibrated else None
        if actual is None and j.status == db.SUCCEEDED:
            usd, basis = runs.actual_cost(st.registry, j)
            actual = usd if "reported" in basis else None
        d["cost"] = {"estimate_usd": j.est_cost_usd, "quota": j.quota, "actual_usd": actual,
                     "per_second": (actual or j.est_cost_usd or 0) / float((j.media or {}).get("duration") or 1)
                     if j.status == db.SUCCEEDED else None}
        return d

    def run_detail(st: AppState, run_id: str) -> dict[str, Any]:
        with session() as s:
            run = s.get(Run, run_id)
            if run is None:
                raise HTTPException(404, "run not found")
            jobs = list(s.exec(select(Job).where(Job.run_id == run_id).order_by(Job.prompt_key, Job.label)))
            ratings = {r.job_id: r.model_dump() for r in s.exec(select(Rating).where(Rating.run_id == run_id))}
            picks = {}
            for p in sorted(s.exec(select(Pick).where(Pick.run_id == run_id)), key=lambda p: p.created_at):
                picks[p.prompt_key] = p.winner_job_id
        return {"run": run.model_dump(), "jobs": [job_out(st, j) for j in jobs], "ratings": ratings, "picks": picks,
                "active": sum(1 for j in jobs if j.status not in TERMINAL),
                "spent_usd": runs.spent_by_run([run_id]).get(run_id, 0.0)}

    @app.get("/api/runs/{run_id}")
    def get_run(request: Request, run_id: str) -> dict[str, Any]:
        return run_detail(S(request), run_id)

    @app.post("/api/runs/{run_id}/rerun")
    def rerun(request: Request, run_id: str, payload: dict = Body(default={})):
        st = S(request)
        with session() as s:
            run = s.get(Run, run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        params = dict(run.params or {})
        req = runs.RunRequest(
            title=f"{run.title} (re-run)", kind=run.kind if run.kind != "probe" else "compare",
            scenario_id=run.scenario_id, category=run.category, task=run.task, lang=run.lang, endpoint=run.endpoint,
            prompts=[runs.PromptIn(**p) for p in run.prompts], models=payload.get("models") or run.models,
            params={k: v for k, v in params.items() if k in runs.PARAM_KEYS},
            model_overrides=params.get("model_overrides") or {}, assets=params.get("assets") or {}, judge=run.judge,
            allow_experimental=bool(params.get("allow_experimental")), confirm=bool(payload.get("confirm")),
            allow_unknown_price=bool(payload.get("allow_unknown_price")))
        return create_run(request, req)

    @app.delete("/api/runs/{run_id}")
    def delete_run(request: Request, run_id: str) -> dict[str, Any]:
        st = S(request)
        with session() as s:
            run = s.get(Run, run_id)
            if run is None:
                raise HTTPException(404, "run not found")
            jobs = list(s.exec(select(Job).where(Job.run_id == run_id)))
            if any(j.status in ACTIVE_REMOTE or j.status in (db.SUBMITTING, db.DOWNLOADING, db.ANALYZING) for j in jobs):
                raise HTTPException(409, "run still has active jobs; stop them first")
            for model in (Rating, Pick):
                for row in s.exec(select(model).where(model.run_id == run_id)):
                    s.delete(row)
            for j in jobs:
                s.delete(j)
                shutil.rmtree(st.settings.frames_dir / j.id, ignore_errors=True)
            s.delete(run)
            s.commit()
        shutil.rmtree(st.settings.videos_dir / run_id, ignore_errors=True)
        return {"ok": True}

    @app.get("/api/runs/{run_id}/export")
    def export_run(request: Request, run_id: str, format: str = "json"):
        st = S(request)
        detail = run_detail(st, run_id)
        if format == "json":
            return Response(json.dumps(detail, ensure_ascii=False, indent=2, default=str), media_type="application/json",
                            headers={"content-disposition": f'attachment; filename="{run_id}.json"'})
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["run_id", "prompt_key", "label", "model", "status", "fail_reason", "duration_s", "width", "height",
                    "has_audio", "latency_s", "est_usd", "quota", "compliance_ok", "judge_adherence", "judge_defects",
                    "usable", "overall", "picked"])
        for j in detail["jobs"]:
            v = (j.get("media") or {}).get("video") or {}
            comp = [c for c in j.get("compliance") or [] if c.get("ok") is not None]
            r = detail["ratings"].get(j["id"]) or {}
            w.writerow([run_id, j["prompt_key"], j["label"], j["model"], j["status"], j["fail_reason"],
                        (j.get("media") or {}).get("duration"), v.get("width"), v.get("height"),
                        bool((j.get("media") or {}).get("audio")), j.get("latency_s"), j.get("est_cost_usd"),
                        j.get("quota"), all(c["ok"] for c in comp) if comp else "", (j.get("judge") or {}).get("adherence"),
                        (j.get("judge") or {}).get("defect_score"), r.get("usable"), r.get("overall"),
                        detail["picks"].get(j["prompt_key"]) == j["id"]])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"content-disposition": f'attachment; filename="{run_id}.csv"'})

    # ------------------------------------------------------------------ jobs
    def _job_or_404(job_id: str) -> Job:
        j = db.get_job(job_id)
        if j is None:
            raise HTTPException(404, "job not found")
        return j

    @app.post("/api/jobs/{job_id}/stop")
    def stop_job(request: Request, job_id: str) -> dict[str, Any]:
        _job_or_404(job_id)
        st = S(request)
        return job_out(st, st.engine.stop_job(job_id))

    @app.post("/api/jobs/{job_id}/repoll")
    def repoll(request: Request, job_id: str) -> dict[str, Any]:
        _job_or_404(job_id)
        st = S(request)
        return job_out(st, st.engine.repoll(job_id))

    @app.post("/api/jobs/{job_id}/resubmit")
    def resubmit(request: Request, job_id: str, payload: dict = Body(default={})) -> dict[str, Any]:
        j = _job_or_404(job_id)
        if j.status not in (db.FAILED, db.SUBMIT_UNKNOWN, db.STOPPED, db.TIMEOUT, db.BLOCKED):
            raise HTTPException(409, f"job is {j.status}")
        if j.task_id and not payload.get("confirm"):
            raise HTTPException(409, "this job already created a task (possibly billed); resend with confirm=true")
        st = S(request)
        return job_out(st, st.engine.resubmit(job_id))

    @app.post("/api/jobs/{job_id}/judge")
    def rejudge(request: Request, job_id: str) -> dict[str, Any]:
        j = _job_or_404(job_id)
        if j.status != db.SUCCEEDED:
            raise HTTPException(409, "only finished clips can be judged")
        st = S(request)
        return job_out(st, st.engine.rejudge(job_id))

    @app.put("/api/jobs/{job_id}/rating")
    def rate(job_id: str, payload: dict = Body(...)) -> dict[str, Any]:
        j = _job_or_404(job_id)
        with session() as s:
            r = s.get(Rating, job_id) or Rating(job_id=job_id, run_id=j.run_id)
            for k in ("usable", "overall", "defects", "comment"):
                if k in payload:
                    setattr(r, k, payload[k])
            r.updated_at = db.now()
            s.add(r)
            s.commit()
            s.refresh(r)
            return r.model_dump()

    @app.put("/api/runs/{run_id}/pick")
    def pick(run_id: str, payload: dict = Body(...)) -> dict[str, Any]:
        winner = payload.get("winner_job_id") or ""
        key = payload.get("prompt_key") or ""
        if winner:
            j = _job_or_404(winner)
            if j.run_id != run_id:
                raise HTTPException(400, "job does not belong to this run")
        with session() as s:
            s.add(Pick(run_id=run_id, prompt_key=key, winner_job_id=winner))
            s.commit()
        return {"ok": True, "prompt_key": key, "winner_job_id": winner}

    def _job_file(job_id: str, attr: str, media_type: str):
        j = _job_or_404(job_id)
        path = getattr(j, attr)
        if not path or not Path(path).exists():
            raise HTTPException(404, "file not available")
        return FileResponse(path, media_type=media_type)

    @app.get("/api/jobs/{job_id}/video")
    def job_video(job_id: str):
        return _job_file(job_id, "video_path", "video/mp4")

    @app.get("/api/jobs/{job_id}/poster")
    def job_poster(job_id: str):
        return _job_file(job_id, "poster_path", "image/jpeg")

    @app.get("/api/jobs/{job_id}/strip")
    def job_strip(job_id: str):
        return _job_file(job_id, "strip_path", "image/jpeg")

    # ------------------------------------------------------------------ insights
    @app.get("/api/leaderboard")
    def leaderboard(request: Request, scenario_id: str | None = None, category: str | None = None,
                    task: str | None = None, ratio: str | None = None, duration: int | None = None,
                    need_audio: bool = False, max_cost: float | None = None, max_latency: float | None = None,
                    rank_by: str = Query("quality", pattern="^(quality|value|speed)$"),
                    resolution: str = "720p") -> dict[str, Any]:
        return ranking.leaderboard(S(request).registry, scenario_id=scenario_id or None, category=category or None,
                                   task=task or None, ratio=ratio or None, duration=duration, need_audio=need_audio,
                                   max_cost=max_cost, max_latency=max_latency, rank_by=rank_by, resolution=resolution)

    @app.get("/api/ledger")
    def ledger() -> dict[str, Any]:
        with session() as s:
            rows = list(s.exec(select(LedgerEntry).order_by(LedgerEntry.ts.desc())))
        by_kind: dict[str, float] = {}
        by_model: dict[str, float] = {}
        for r in rows:
            usd = r.actual_usd if r.actual_usd is not None else r.est_usd
            by_kind[r.kind] = round(by_kind.get(r.kind, 0) + usd, 4)
            by_model[r.model] = round(by_model.get(r.model, 0) + usd, 4)
        return {"spent_usd": runs.spent_usd(), "by_kind": by_kind, "by_model": by_model,
                "entries": [r.model_dump() for r in rows[:500]]}

    @app.get("/api/probe")
    def probe_status(request: Request) -> dict[str, Any]:
        st = S(request)
        with session() as s:
            rows = list(s.exec(select(ProbeResult).order_by(ProbeResult.ts.desc()).limit(200)))
        task = getattr(st, "probe_task", None)
        from vgen.probe import cases as probe_cases

        return {"status": st.registry.probe, "results": [r.model_dump() for r in rows],
                "running": bool(task and not task.done()), "log": list(getattr(st, "probe_log", []))[-200:],
                "cases": [{"id": c.id, "model": c.model, "task": c.task} for c in probe_cases()]}

    @app.post("/api/probe/resummarize")
    async def probe_resummarize(request: Request) -> dict[str, Any]:
        from vgen.probe import resummarize

        st = S(request)
        lines: list[str] = []
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: resummarize(st, log=lines.append))
        return {"ok": True, "log": lines}

    @app.post("/api/probe")
    async def start_probe(request: Request, payload: dict = Body(...)) -> dict[str, Any]:
        """Plan (and with confirm=true run) live probe cases inside this server's engine."""
        from vgen.probe import execute_cases, plan_cases

        st = S(request)
        task = getattr(st, "probe_task", None)
        if task and not task.done():
            raise HTTPException(409, "a probe is already running")
        selected = payload.get("cases") or None
        budget = float(payload.get("budget") or 3.0)
        lines: list[str] = []
        planned, total = plan_cases(st, selected, budget, log=lines.append)
        if not payload.get("confirm") or not planned:
            return {"started": False, "total": total, "cases": [c.id for c, _, _ in planned], "log": lines}
        st.probe_log = lines
        st.probe_task = asyncio.create_task(execute_cases(st, planned, judge_case=bool(payload.get("judge", True)),
                                                          log=st.probe_log.append))
        return {"started": True, "total": total, "cases": [c.id for c, _, _ in planned], "log": lines}

    # ------------------------------------------------------------------ frontend
    if (WEB_DIST / "assets").exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="web-assets")

    def index_html(request: Request) -> HTMLResponse:
        st = S(request)
        boot = json.dumps({"token": LAUNCH_TOKEN, "mock": st.settings.mock})
        index = WEB_DIST / "index.html"
        if not index.exists():
            return HTMLResponse("<h1>vgen-eval</h1><p>Frontend not built. Run <code>make web</code>.</p>", status_code=200)
        html = index.read_text(encoding="utf-8").replace("</head>", f"<script>window.__VGEN__={boot}</script></head>", 1)
        return HTMLResponse(html, headers={"cache-control": "no-store"})

    @app.get("/", response_class=HTMLResponse)
    def root(request: Request):
        return index_html(request)

    @app.get("/{path:path}", response_class=HTMLResponse)
    def spa(request: Request, path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "not found")
        static = (WEB_DIST / path).resolve()
        if path and static.is_file() and WEB_DIST.resolve() in static.parents:
            return FileResponse(static)
        return index_html(request)

    return app
