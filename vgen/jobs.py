"""Job engine: submit, poll, download, analyze and judge — resumable across restarts."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlmodel import select

from vgen import db
from vgen.adapters import build_request
from vgen.adapters.base import GenerationSpec, Unsupported
from vgen.analysis import analyze_video
from vgen.assets import get_asset, resolver
from vgen.catalog.registry import Registry
from vgen.config import Settings
from vgen.db import (ANALYZING, DOWNLOADING, FAILED, IN_PROGRESS, PENDING, QUEUED, STOPPED, SUBMIT_UNKNOWN,
                     SUBMITTING, SUCCEEDED, TIMEOUT, Job, Run, session, update_job)
from vgen.gateway import AUTH, BUSINESS, NOT_FOUND, SAFE_TO_RESEND, Gateway, GatewayError
from vgen.judge import judge_clip
from vgen.keys import KeyRing
from vgen.runs import record_judge_spend, record_unknown_spend, record_video_spend, settle_actual, settle_quota, spent_usd

log = logging.getLogger("vgen.jobs")

MAX_SAFE_RESENDS = 4
MAX_DOWNLOAD_ATTEMPTS = 3


def field(d: dict, *names: str):
    """The gateway answers in camelCase although the docs show snake_case; accept both."""
    for n in names:
        if d.get(n) is not None:
            return d[n]
    return None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class JobEngine:
    def __init__(self, settings: Settings, registry: Registry, gateways: dict[str, Gateway],
                 keys: KeyRing | None = None):
        self.settings = settings
        self.registry = registry
        self.gateways = gateways
        self.keys = keys                         # public mode: visitor keys, one gateway per key
        self._owners: dict[str, str | None] = {}  # run id -> visitor id (None = operator run); never changes
        self._last_sweep = 0.0
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._busy: set[str] = set()
        self._judging: set[str] = set()
        self._download_attempts: dict[str, int] = {}
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vgen-analysis")
        self._judge_sem = asyncio.Semaphore(2)
        self._stopping = False
        self._children: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.recover()
        self._task = asyncio.create_task(self._loop(), name="vgen-engine")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task:
            await asyncio.wait([self._task], timeout=5)
        for t in list(self._children):
            t.cancel()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def wake(self) -> None:
        self._wake.set()

    def recover(self) -> None:
        now = time.time()
        with session() as s:
            for job in s.exec(select(Job).where(Job.status.in_([SUBMITTING, QUEUED, IN_PROGRESS, DOWNLOADING, ANALYZING]))):
                if job.status == SUBMITTING:
                    job.status = SUBMIT_UNKNOWN
                    job.fail_reason = "app stopped while submitting; the task may exist and may be billed"
                elif job.status in (QUEUED, IN_PROGRESS, DOWNLOADING):
                    job.status = IN_PROGRESS if job.task_id else SUBMIT_UNKNOWN
                    job.next_poll_at = now
                s.add(job)
            for job in s.exec(select(Job).where(Job.judge_status == "running")):
                job.judge_status = "pending"
                s.add(job)
            s.commit()

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._children.add(t)
        t.add_done_callback(self._children.discard)

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                self._tick()
            except Exception:  # pragma: no cover - keep the engine alive
                log.exception("engine tick failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    # ------------------------------------------------------------------ gateways
    def owner(self, run_id: str) -> str | None:
        if run_id not in self._owners:
            with session() as s:
                run = s.get(Run, run_id)
            self._owners[run_id] = run.owner if run else None
        return self._owners[run_id]

    def gateway(self, job: Job) -> Gateway | None:
        """A visitor's run uses the key that visitor entered (None until it is attached); others the endpoint."""
        if self.owner(job.run_id):
            return self.keys.for_run(job.run_id) if self.keys else None
        return self.gateways.get(job.endpoint)

    def waiting_for_key(self, job: Job) -> bool:
        return bool(self.owner(job.run_id)) and self.gateway(job) is None

    # ------------------------------------------------------------------ scheduling
    def _tick(self) -> None:
        now = time.time()
        with session() as s:
            jobs = list(s.exec(select(Job).where(Job.status.in_(
                [PENDING, SUBMITTING, QUEUED, IN_PROGRESS, DOWNLOADING, ANALYZING])).order_by(Job.created_at)))
            judge_jobs = list(s.exec(select(Job).where(Job.status == SUCCEEDED,
                                                       Job.judge_status.in_(["pending", "running"]))))
        if self.keys is not None:
            self._release_finished_runs({j.run_id for j in jobs} | {j.run_id for j in judge_jobs})
        # in-flight tasks are limited per gateway: per endpoint, or per visitor key in public mode
        inflight: dict[int, int] = {}
        for j in jobs:
            gw = self.gateway(j)
            if gw is not None and j.status in (SUBMITTING, QUEUED, IN_PROGRESS, DOWNLOADING):
                inflight[id(gw)] = inflight.get(id(gw), 0) + 1
        for j in jobs:
            if j.id in self._busy:
                continue
            if j.status != ANALYZING and self.waiting_for_key(j):
                continue                                         # resumes when the visitor re-attaches the key
            if j.status == PENDING:
                slot = id(self.gateway(j))
                if inflight.get(slot, 0) >= self.settings.max_inflight:
                    continue
                if j.next_poll_at and j.next_poll_at > now:      # backoff after a safe failure
                    continue
                inflight[slot] = inflight.get(slot, 0) + 1
                self._busy.add(j.id)
                self._spawn(self._guard(j.id, self._submit(j.id)))
            elif j.status in (QUEUED, IN_PROGRESS) and (j.next_poll_at or 0) <= now:
                self._busy.add(j.id)
                self._spawn(self._guard(j.id, self._poll(j.id)))
            elif j.status == DOWNLOADING and (j.next_poll_at or 0) <= now:
                self._busy.add(j.id)
                self._spawn(self._guard(j.id, self._download(j.id)))
            elif j.status == ANALYZING:
                self._busy.add(j.id)
                self._spawn(self._guard(j.id, self._analyze(j.id)))
        for j in judge_jobs:
            if j.judge_status == "pending" and j.id not in self._judging and not self.waiting_for_key(j):
                self._judging.add(j.id)
                self._spawn(self._judge_guard(j.id))

    def _release_finished_runs(self, busy_runs: set[str]) -> None:
        """Forget a visitor's key for runs with nothing left to submit, poll, download or judge."""
        for run_id in self.keys.attached():
            if run_id not in busy_runs:
                self.keys.release(run_id)
        if time.monotonic() - self._last_sweep > 60:
            self._last_sweep = time.monotonic()
            self._spawn(self.keys.close_idle())

    async def _guard(self, job_id: str, coro) -> None:
        try:
            await coro
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("job %s step failed", job_id)
            update_job(job_id, fail_reason=f"internal error: {exc!r}")
        finally:
            self._busy.discard(job_id)
            self._wake.set()

    def poll_interval(self, job: Job, gw: Gateway) -> float:
        """Spread polls so that every job sharing this gateway's rate limit fits into ~60% of it."""
        q = select(Job.id).where(Job.status.in_([QUEUED, IN_PROGRESS]))
        if self.owner(job.run_id) and self.keys is not None:
            q = q.where(Job.run_id.in_(self.keys.runs_sharing(job.run_id)))
        else:
            q = q.where(Job.endpoint == job.endpoint)
        with session() as s:
            active = len(list(s.exec(q)))
        return max(self.settings.poll_min_s, active * 60.0 / (0.6 * gw.limiter.rpm))

    def _timeout_for(self, job: Job) -> float:
        return self.settings.edit_timeout_s if (job.spec or {}).get("task") in ("v2v_edit", "extend") else self.settings.gen_timeout_s

    # ------------------------------------------------------------------ steps
    def rebuild_payload(self, job: Job) -> dict:
        model = self.registry.get(job.model)
        if (job.spec or {}).get("probe_override"):
            model = model.model_copy(update={"probe": {}})
        spec = GenerationSpec(**{k: v for k, v in (job.spec or {}).items() if k not in ("allow_experimental", "probe_override")})
        built = build_request(model, spec, resolver(), allow_experimental=bool((job.spec or {}).get("allow_experimental")))
        return built.payload

    async def _submit(self, job_id: str) -> None:
        job = db.get_job(job_id)
        if job is None or job.status != PENDING:
            return
        gw = self.gateway(job)
        if gw is None:
            if not self.owner(job.run_id):
                update_job(job_id, status=FAILED, fail_reason=f"endpoint '{job.endpoint}' is not enabled",
                           done_at=time.time())
            return
        cap = self.settings.hard_cap_usd
        visitor_pays = bool(self.owner(job.run_id))
        if cap is not None and not visitor_pays and spent_usd() + (job.est_cost_usd or 0) > cap:
            update_job(job_id, status=STOPPED, fail_reason=f"hard spend cap ${cap:g} reached; not submitted", done_at=time.time())
            return
        try:
            payload = self.rebuild_payload(job)
        except (Unsupported, KeyError) as exc:
            update_job(job_id, status=db.BLOCKED, fail_reason=str(exc), done_at=time.time())
            return
        update_job(job_id, status=SUBMITTING, submitted_at=time.time(), submit_attempts=job.submit_attempts + 1)
        try:
            resp = await gw.submit_video(payload)
        except GatewayError as err:
            self._on_submit_error(job_id, err, job.submit_attempts + 1)
            return
        task_id = str(field(resp, "task_id", "taskId", "id"))
        first_delay = min(self.settings.first_poll_cap_s, self.registry.get(job.model).latency_s * 0.5)
        job = update_job(job_id, status=QUEUED, task_id=task_id, submit_response=resp,
                         remote_status=str(resp.get("status") or "QUEUED"), next_poll_at=time.time() + first_delay,
                         fail_reason="")
        if job:
            record_video_spend(job)

    def _on_submit_error(self, job_id: str, err: GatewayError, attempts: int) -> None:
        now = time.time()
        if err.kind in SAFE_TO_RESEND and attempts < MAX_SAFE_RESENDS:
            update_job(job_id, status=PENDING, fail_reason=f"retrying: {err.message}",
                       next_poll_at=now + (5 if err.kind != "rate_limit" else 1) * attempts)
        elif err.kind in SAFE_TO_RESEND:
            update_job(job_id, status=FAILED, fail_reason=f"not submitted: {err.message}", error_code=err.code, done_at=now)
        elif err.kind in (BUSINESS, NOT_FOUND):
            update_job(job_id, status=FAILED, fail_reason=err.message, error_code=err.code or err.kind, done_at=now)
        elif err.kind == AUTH:
            update_job(job_id, status=FAILED, fail_reason=f"auth failed: {err.message}", error_code="auth", done_at=now)
        else:   # server error / timeout after send: outcome unknown, never resend automatically
            job = update_job(job_id, status=SUBMIT_UNKNOWN, error_code=err.kind,
                             fail_reason=f"{err.message} — the task may exist and may be billed", done_at=now)
            if job:
                record_unknown_spend(job)     # count it against budgets/caps until proven otherwise

    async def _poll(self, job_id: str) -> None:
        job = db.get_job(job_id)
        if job is None or job.status not in (QUEUED, IN_PROGRESS) or not job.task_id:
            return
        gw = self.gateway(job)
        now = time.time()
        if gw is None:
            if not self.owner(job.run_id):
                update_job(job_id, status=STOPPED, fail_reason="endpoint disabled; polling stopped", done_at=now)
            return
        interval = self.poll_interval(job, gw)
        try:
            detail = await gw.get_video(job.task_id)
        except GatewayError as err:
            if err.kind == NOT_FOUND and job.poll_count >= 3:
                update_job(job_id, status=FAILED, fail_reason=f"task not found: {err.message}", done_at=now)
            elif err.kind == BUSINESS and job.poll_count >= 1:
                # an explicit error body for a task is terminal, e.g. "Video generation failed, please retry or
                # contact support" or "The request was rejected by content safety policy"
                update_job(job_id, status=FAILED, fail_reason=err.message, error_code=err.code or "generation_failed",
                           done_at=now, poll_count=job.poll_count + 1)
                settle_quota(self.registry, job_id, 0, failed=True)
            elif err.kind == AUTH:
                update_job(job_id, status=STOPPED, fail_reason=f"auth failed while polling: {err.message}", done_at=now)
            else:
                update_job(job_id, next_poll_at=time.time() + interval, poll_count=job.poll_count + 1,
                           fail_reason=f"poll error (will retry): {err.message}")
            return
        status = str(detail.get("status") or "").upper()
        fields = {
            "last_detail": detail, "remote_status": status, "progress": str(detail.get("progress") or ""),
            "poll_count": job.poll_count + 1, "fail_reason": "",
            "gw_submit_time": field(detail, "submit_time", "submitTime") or job.gw_submit_time,
            "gw_start_time": field(detail, "start_time", "startTime") or job.gw_start_time,
            "gw_finish_time": field(detail, "finish_time", "finishTime") or job.gw_finish_time,
        }
        if field(detail, "quota") is not None:
            fields["quota"] = int(detail["quota"])
        if status == "SUCCESS":
            job = update_job(job_id, status=DOWNLOADING, next_poll_at=time.time(), **fields)
            settle_quota(self.registry, job_id, fields.get("quota"), failed=False)
            settle_actual(self.registry, job)
        elif status == "FAILURE":
            err = detail.get("error") or {}
            reason = field(detail, "fail_reason", "failReason") or (err.get("message") if isinstance(err, dict) else "") \
                or "generation failed"
            fields["fail_reason"] = reason
            update_job(job_id, status=FAILED, done_at=time.time(), **fields)
            settle_quota(self.registry, job_id, fields.get("quota"), failed=True)
        else:
            new_status = QUEUED if status in ("SUBMITTED", "QUEUED", "NOT_START", "PENDING") else IN_PROGRESS
            if job.submitted_at and time.time() - job.submitted_at > self._timeout_for(job):
                update_job(job_id, status=TIMEOUT, done_at=time.time(),
                           **{**fields, "fail_reason": "gave up waiting; use 'check again' to resume"})
            else:
                update_job(job_id, status=new_status, next_poll_at=time.time() + interval, **fields)

    async def _download(self, job_id: str) -> None:
        job = db.get_job(job_id)
        if job is None or job.status != DOWNLOADING:
            return
        gw = self.gateway(job)
        if gw is None:
            if not self.owner(job.run_id):
                update_job(job_id, status=STOPPED, fail_reason="endpoint disabled; download stopped",
                           done_at=time.time())
            return
        dest = self.settings.videos_dir / job.run_id / f"{job.id}.mp4"
        try:
            await gw.download_video(job.task_id, dest)
        except GatewayError as err:
            n = self._download_attempts.get(job_id, 0) + 1
            self._download_attempts[job_id] = n
            if n >= MAX_DOWNLOAD_ATTEMPTS:
                update_job(job_id, status=FAILED, done_at=time.time(),
                           fail_reason=f"generated but download failed: {err.message}")
            else:
                update_job(job_id, next_poll_at=time.time() + 10 * n, fail_reason=f"download retry: {err.message}")
            return
        update_job(job_id, status=ANALYZING, video_path=str(dest), video_sha=_sha256_file(dest), fail_reason="")
        await self._analyze(job_id)

    async def _analyze(self, job_id: str) -> None:
        job = db.get_job(job_id)
        if job is None or not job.video_path:
            return
        with session() as s:
            run = s.get(Run, job.run_id)
        spec = job.spec or {}
        first = get_asset((spec.get("first_frame") or {}).get("asset_id", "")) if spec.get("first_frame") else None
        last = get_asset((spec.get("last_frame") or {}).get("asset_id", "")) if spec.get("last_frame") else None
        model = self.registry.models.get(job.model)
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                self._pool, lambda: analyze_video(
                    Path(job.video_path), self.settings.frames_dir / job.id, effective=job.effective or {},
                    task=spec.get("task", "t2v"), model_audio=model.audio if model else "unknown",
                    first_ref=Path(first.local_path) if first else None,
                    last_ref=Path(last.local_path) if last else None))
            update_job(job_id, status=SUCCEEDED, done_at=time.time(), media=result["media"],
                       compliance=result["compliance"], signals=result["signals"], fidelity=result["fidelity"],
                       poster_path=result["poster"], strip_path=result["strip"],
                       judge_status="pending" if run and run.judge else "")
        except Exception as exc:
            log.exception("analysis failed for %s", job_id)
            update_job(job_id, status=SUCCEEDED, done_at=time.time(), signals={"error": f"analysis failed: {exc!r}"},
                       judge_status="pending" if run and run.judge else "")

    async def _judge_guard(self, job_id: str) -> None:
        try:
            async with self._judge_sem:
                await self.judge(job_id)
        finally:
            self._judging.discard(job_id)

    async def judge(self, job_id: str) -> None:
        job = db.get_job(job_id)
        if job is None or not job.video_path:
            return
        if self.owner(job.run_id):
            gw = self.keys.for_run(job.run_id) if self.keys else None   # the visitor's key pays for the judge too
            if gw is None:
                return                                                  # stays pending until the key is back
        else:
            gw = self.gateways.get(self.settings.judge_endpoint)
        if gw is None:
            update_job(job_id, judge_status="error", judge={"error": "judge endpoint not enabled"})
            return
        with session() as s:
            run = s.get(Run, job.run_id)
        prompt = next((p for p in (run.prompts if run else []) if p.get("key") == job.prompt_key), {})
        spec = job.spec or {}
        ref_ids = [(spec.get("first_frame") or {}).get("asset_id"), (spec.get("last_frame") or {}).get("asset_id")]
        ref_ids += [r.get("asset_id") for r in spec.get("references") or []]
        refs = [Path(a.local_path) for a in (get_asset(i) for i in ref_ids if i) if a and a.kind == "image"]
        with_audio = bool((job.effective or {}).get("audio")) or spec.get("task") == "a2v"
        update_job(job_id, judge_status="running")
        model = self.settings.judge_model
        result = None
        for attempt in range(3):
            try:
                result = await judge_clip(
                    gw, model, Path(job.video_path), job.video_sha or "", spec.get("prompt", ""),
                    list(prompt.get("checklist") or []), spec.get("task", "t2v"), refs, with_audio,
                    bool((job.media or {}).get("audio")), self.settings.cache_dir / job.id,
                    self.registry.judge_price(model))
                break
            except GatewayError as exc:
                transient = exc.kind in (*SAFE_TO_RESEND, "server", "after_send")
                if transient and attempt < 2:
                    await asyncio.sleep(10 * (attempt + 1))
                    continue
                log.warning("judge failed for %s: %s", job_id, exc)
                update_job(job_id, judge_status="error", judge={"error": str(exc)[:500]})
                return
            except Exception as exc:
                log.warning("judge failed for %s: %s", job_id, exc)
                update_job(job_id, judge_status="error", judge={"error": str(exc)[:500]})
                return
        update_job(job_id, judge_status="done", judge=result)
        if not result.get("cached"):
            record_judge_spend(job, (result.get("usage") or {}).get("usd"), model)

    # ------------------------------------------------------------------ user actions
    def stop_job(self, job_id: str) -> Job | None:
        job = db.get_job(job_id)
        if job is None:
            return None
        if job.status in (PENDING,):
            return update_job(job_id, status=STOPPED, fail_reason="stopped before submit (not billed)", done_at=time.time())
        if job.status in (QUEUED, IN_PROGRESS):
            return update_job(job_id, status=STOPPED, done_at=time.time(),
                              fail_reason="tracking stopped — no cancel API, the gateway keeps generating and billing")
        return job

    def repoll(self, job_id: str) -> Job | None:
        job = db.get_job(job_id)
        if job is None or not job.task_id:
            return job
        job = update_job(job_id, status=IN_PROGRESS, next_poll_at=time.time(), done_at=None, fail_reason="")
        self.wake()
        return job

    def resubmit(self, job_id: str) -> Job | None:
        job = db.get_job(job_id)
        if job is None:
            return None
        job = update_job(job_id, status=PENDING, task_id=None, fail_reason="", error_code="", remote_status="",
                         progress="", done_at=None, next_poll_at=None, video_path=None, quota=None,
                         submit_response={}, last_detail={}, judge={}, judge_status="")
        self.wake()
        return job

    def rejudge(self, job_id: str) -> Job | None:
        job = update_job(job_id, judge_status="pending", judge={})
        self.wake()
        return job
