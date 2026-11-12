"""Public (hosted) mode: visitors generate with their own Tokenbay keys; the operator's key is never used.
Diagnostics (raw gateway errors and responses, task ids, spend, probe data) go to a signed-in admin only."""

from __future__ import annotations

import csv
import io
import sqlite3
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from vgen import db, public_view, security
from vgen.api import VISITOR_COOKIE, create_app
from vgen.catalog.registry import Registry
from vgen.cli import relocate_db
from vgen.config import Endpoint, load_settings
from vgen.gateway import Gateway
from vgen.mock_gateway import MockGateway
from vgen.security import ADMIN_COOKIE, LAUNCH_TOKEN, hash_admin_secret

H = {"x-vgen-token": LAUNCH_TOKEN}
OPERATOR_KEY = "sk-operator-key-must-never-be-used"
VISITOR_B = "visitor-b-0123456789abcdef"


def key(k: str) -> dict:
    return {**H, "x-tokenbay-key": k}


def be(c: TestClient, vid: str) -> None:
    c.cookies.clear()
    c.cookies.set(VISITOR_COOKIE, vid)


def wait_run(client: TestClient, run_id: str, timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = client.get(f"/api/runs/{run_id}").json()
        if d["active"] == 0 and all(j["judge_status"] not in ("pending", "running") for j in d["jobs"]):
            return d
        time.sleep(0.3)
    raise AssertionError(f"run did not finish: {[(j['model'], j['status']) for j in d['jobs']]}")


def run_request(models, **extra) -> dict:
    return {"title": "test", "task": "t2v", "prompts": [{"key": "p1", "text": "A fox in snow",
                                                       "checklist": ["A fox", "Snow"]}],
            "models": models, "params": {"duration": 4, "resolution": "720p", "ratio": "16:9"}, "confirm": True,
            **extra}


@pytest.fixture()
def public_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(f"TOKENBAY_BASE_URL=http://mock.tokenbay.local\nTOKENBAY_API_KEY={OPERATOR_KEY}\n")
    monkeypatch.delenv("VGEN_MOCK", raising=False)
    monkeypatch.setenv("VGEN_PUBLIC", "1")
    monkeypatch.setenv("VGEN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VGEN_POLL_MIN_S", "0.3")
    monkeypatch.setenv("VGEN_FIRST_POLL_CAP_S", "0.2")
    return env


@pytest.fixture()
def gw() -> MockGateway:
    return MockGateway(Registry.load(), queued_s=0.3, running_s=0.6)


def test_operator_key_is_never_loaded(public_env):
    settings = load_settings(public_env)
    assert settings.public and settings.endpoints["com"].api_key == ""


def test_visitor_key_pays_and_runs_stay_private(public_env, gw):
    app = create_app(load_settings(public_env), transport_factory=lambda k: gw.transport())
    with TestClient(app) as c:
        page = c.get("/")
        vid = c.cookies.get(VISITOR_COOKIE)
        assert vid and '"public": true' in page.text
        cfg = c.get("/api/config").json()
        assert cfg["public"] is True and cfg["spent_usd"] == 0 and cfg["endpoints"] == []

        body = run_request(["seedance-2.0-mini"], judge=True)
        plan = c.post("/api/plan", json=body, headers=H).json()
        assert plan["runnable"] == 1 and plan["budget"]["billed_to"] == "visitor"
        r = c.post("/api/runs", json=body, headers=H)
        assert r.status_code == 401 and "API key" in r.json()["detail"]
        r = c.post("/api/runs", json=body, headers=key("sk-bad"))
        assert r.status_code == 401 and "rejected" in r.json()["detail"]
        assert c.post("/api/key/check", headers=key("sk-bad")).status_code == 401
        assert c.post("/api/key/check", headers=key("sk-visitor-a")).json() == {"ok": True}

        r = c.post("/api/runs", json=body, headers=key("sk-visitor-a"))
        assert r.status_code == 200, r.text
        assert r.json()["mine"] is True and "owner" not in r.json()["run"]
        run_id = r.json()["run"]["id"]
        d = wait_run(c, run_id)
        job = d["jobs"][0]
        assert job["status"] == "succeeded" and job["judge_status"] == "done"

        # submit, poll, download and judge all carried the visitor's key, and nothing carried the operator's
        assert not any(OPERATOR_KEY in auth for _, auth in gw.auth)
        paths = {p for p, auth in gw.auth if auth == "Bearer sk-visitor-a"}
        assert {"/v1/models", "/v1/videos"} <= paths
        assert any(p.endswith("/content") for p in paths)
        assert any(p.endswith(":generateContent") for p in paths)
        assert not any(p.startswith("/v1/videos") for p, auth in gw.auth if auth == "Bearer sk-bad")

        # another visitor can't list, open, stream, export or change it
        be(c, VISITOR_B)
        assert all(x["id"] != run_id for x in c.get("/api/runs").json())
        assert c.get(f"/api/runs/{run_id}").status_code == 404
        assert c.get(job["video_url"]).status_code == 404
        assert c.get(f"/api/runs/{run_id}/export").status_code == 404
        assert c.put(f"/api/jobs/{job['id']}/rating", json={"usable": True}, headers=H).status_code == 404
        assert c.delete(f"/api/runs/{run_id}", headers=H).status_code == 404
        assert c.post(f"/api/runs/{run_id}/key", headers=key("sk-visitor-b")).status_code == 404

        # the owner can
        be(c, vid)
        assert c.get(job["video_url"], headers={"range": "bytes=0-99"}).status_code == 206
        assert c.put(f"/api/jobs/{job['id']}/rating", json={"usable": True, "overall": 4}, headers=H).status_code == 200
        assert c.put(f"/api/runs/{run_id}/pick", json={"prompt_key": "p1", "winner_job_id": job["id"]},
                     headers=H).status_code == 200
        assert c.post(f"/api/jobs/{job['id']}/judge", headers=H).status_code == 401      # paid: key needed
        assert c.delete(f"/api/runs/{run_id}", headers=H).status_code == 200


def test_showcase_is_read_only_and_operator_routes_are_closed(public_env, gw, monkeypatch):
    # the operator's runs, made before the site went public
    monkeypatch.setenv("VGEN_PUBLIC", "0")
    monkeypatch.setenv("VGEN_MOCK", "1")
    with TestClient(create_app(load_settings(public_env))) as c:
        shown = c.post("/api/runs", json=run_request(["seedance-2.0-mini"]), headers=H).json()["run"]["id"]
        probe = c.post("/api/runs", json=run_request(["seedance-2.0-mini"], kind="probe"), headers=H).json()["run"]["id"]
        wait_run(c, shown)
        wait_run(c, probe)
    monkeypatch.setenv("VGEN_PUBLIC", "1")
    monkeypatch.delenv("VGEN_MOCK")
    app = create_app(load_settings(public_env), transport_factory=lambda k: gw.transport())
    with TestClient(app) as c:
        c.get("/")
        listed = {x["id"]: x for x in c.get("/api/runs").json()}
        assert listed[shown]["mine"] is False and probe not in listed
        assert c.get(f"/api/runs/{probe}").status_code == 404
        d = c.get(f"/api/runs/{shown}").json()
        assert d["mine"] is False and d["needs_key"] is False
        job = d["jobs"][0]
        assert c.get(job["video_url"]).status_code == 200 and c.get(job["poster_url"]).status_code == 200
        for method, url, kw in [
            ("put", f"/api/jobs/{job['id']}/rating", {"json": {"usable": True}}),
            ("put", f"/api/runs/{shown}/pick", {"json": {"prompt_key": "p1", "winner_job_id": job["id"]}}),
            ("delete", f"/api/runs/{shown}", {}),
            ("post", f"/api/jobs/{job['id']}/judge", {}),
            ("post", f"/api/jobs/{job['id']}/repoll", {}),
            ("post", f"/api/jobs/{job['id']}/stop", {}),
        ]:
            assert getattr(c, method)(url, headers=key("sk-visitor-a"), **kw).status_code == 403, url
        assert c.get("/api/leaderboard").status_code == 200

        # re-running an example makes the visitor's own run, billed to their key
        r = c.post(f"/api/runs/{shown}/rerun", json={"confirm": True}, headers=key("sk-visitor-a"))
        assert r.status_code == 200, r.text
        assert r.json()["mine"] is True and r.json()["run"]["kind"] == "compare"

        for method, url, kw in [
            ("put", "/api/settings", {"json": {"budget_usd": 1}}),
            ("get", "/api/ledger", {}),
            ("post", "/api/probe", {"json": {"confirm": True}}),
            ("post", "/api/probe/resummarize", {}),
            ("post", "/api/models/refresh", {}),
            ("post", "/api/assets/url", {"json": {"url": "http://169.254.169.254/latest/meta-data/"}}),
        ]:
            assert getattr(c, method)(url, headers=H, **kw).status_code == 403, url


def test_restart_waits_for_the_key_then_resumes(public_env, gw):
    gw.queued_s, gw.running_s = 1.0, 3.0
    settings = load_settings(public_env)
    with TestClient(create_app(settings, transport_factory=lambda k: gw.transport())) as c:
        c.get("/")
        vid = c.cookies.get(VISITOR_COOKIE)
        run_id = c.post("/api/runs", json=run_request(["seedance-2.0-mini"]),
                        headers=key("sk-visitor-a")).json()["run"]["id"]
        deadline = time.time() + 20
        while time.time() < deadline and not c.get(f"/api/runs/{run_id}").json()["jobs"][0]["has_task"]:
            time.sleep(0.2)
    # the process restarted mid-generation: the key is gone, the job waits instead of failing
    with TestClient(create_app(settings, transport_factory=lambda k: gw.transport())) as c:
        be(c, vid)
        time.sleep(1.5)
        d = c.get(f"/api/runs/{run_id}").json()
        assert d["needs_key"] is True
        assert d["jobs"][0]["status"] in ("queued", "in_progress")
        r = c.post(f"/api/runs/{run_id}/key", headers=key("sk-visitor-a"))
        assert r.status_code == 200 and r.json()["needs_key"] is False
        assert wait_run(c, run_id)["jobs"][0]["status"] == "succeeded"


def test_active_run_limit_per_visitor(public_env, gw, monkeypatch):
    monkeypatch.setenv("VGEN_PUBLIC_MAX_ACTIVE_RUNS", "1")
    gw.running_s = 5.0
    with TestClient(create_app(load_settings(public_env), transport_factory=lambda k: gw.transport())) as c:
        c.get("/")
        assert c.post("/api/runs", json=run_request(["seedance-2.0-mini"]), headers=key("sk-a")).status_code == 200
        r = c.post("/api/runs", json=run_request(["seedance-2.0-mini"]), headers=key("sk-a"))
        assert r.status_code == 429 and "in progress" in r.json()["detail"]


def test_uploads_are_private(public_env, gw):
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(buf, "PNG")
    png = buf.getvalue()
    with TestClient(create_app(load_settings(public_env), transport_factory=lambda k: gw.transport())) as c:
        c.get("/")
        vid = c.cookies.get(VISITOR_COOKIE)
        a = c.post("/api/assets", files={"file": ("red.png", png, "image/png")}, headers=H).json()
        assert "owner" not in a
        mine = {x["id"] for x in c.get("/api/assets").json()}
        assert a["id"] in mine and len(mine) > 1                       # own upload + shared examples
        be(c, VISITOR_B)
        theirs = {x["id"] for x in c.get("/api/assets").json()}
        assert a["id"] not in theirs and theirs == mine - {a["id"]}
        assert c.get(a["url"]).status_code == 404
        b = c.post("/api/assets", files={"file": ("red.png", png, "image/png")}, headers=H).json()
        assert b["id"] != a["id"]                                     # same bytes, separate private row
        be(c, vid)
        assert c.get(a["url"]).status_code == 200


def test_relocate_rewrites_stored_paths(db_ready):
    old = "/Users/someone/poc/data"
    with db.session() as s:
        s.add(db.Job(id="job_x", run_id="run_x", model="m", video_path=f"{old}/videos/run_x/job_x.mp4",
                     spec={"first_frame": {"asset_id": "a"}, "note": f"{old}/uploads/a.png"}))
        s.add(db.Asset(id="a", local_path=f"{old}/uploads/a.png"))
        s.add(db.Asset(id="b", local_path="/Users/someone/poc/scenarios/assets/b.png"))
        s.commit()
    n = relocate_db(db_ready.db_path, [(old, "/var/lib/vgen/data"), ("/Users/someone/poc", "/opt/vgen")])
    assert n == 4
    with db.session() as s:
        job = s.get(db.Job, "job_x")
        assert job.video_path == "/var/lib/vgen/data/videos/run_x/job_x.mp4"
        assert job.spec["note"] == "/var/lib/vgen/data/uploads/a.png"
        assert s.get(db.Asset, "a").local_path == "/var/lib/vgen/data/uploads/a.png"
        assert s.get(db.Asset, "b").local_path == "/opt/vgen/scenarios/assets/b.png"


def test_old_database_gets_owner_columns(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE run (id VARCHAR PRIMARY KEY, title VARCHAR)")
    con.execute("CREATE TABLE asset (id VARCHAR PRIMARY KEY, label VARCHAR)")
    con.commit()
    con.close()
    db.init_db(path)
    con = sqlite3.connect(path)
    for table in ("run", "asset"):
        assert "owner" in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    con.close()


# ====================================================================== admin / public split
ADMIN_SECRET = "an admin secret for tests 42"
ADMIN_HASH = hash_admin_secret(ADMIN_SECRET)
CANARY = "CANARY-9f27c1d4-PRIVATE"            # written into every private text field
CANARY_NUMBER = 918273645                     # ... and into the numeric ones (quota, counters)
SPENT = 4.5679                                # the operator's ledger spend on the example run
FOX = "A fox in snow"
# keys that must not appear anywhere in a job or run a visitor receives
PRIVATE_KEYS = {"task_id", "submit_response", "last_detail", "payload", "fail_reason", "error_code", "quota",
                "poll_count", "submit_attempts", "next_poll_at", "remote_status", "endpoint", "case_fingerprint",
                "video_sha", "notes", "owner", "usage", "error", "price_snapshot", "price_checked_at", "request_id",
                "comment", "operator_memo", "config_sha256", "original_sha256", "actual_denoise_updates_source"}


def all_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in all_keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in all_keys(v)}
    return set()


@pytest.fixture()
def web_page(tmp_path, monkeypatch):
    """A stand-in for the built UI, so the boot payload can be read whether or not `web/dist` exists."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><html><head><title>t</title></head><body></body></html>")
    (dist / "robots.txt").write_text("User-agent: *\n")
    monkeypatch.setattr("vgen.api.WEB_DIST", dist)


@pytest.fixture()
def admin_env(public_env, monkeypatch, web_page):
    monkeypatch.setenv("VGEN_ADMIN_SECRET_HASH", ADMIN_HASH)
    return public_env


def hosted(env, gw, **kw) -> TestClient:
    """The hosted site as visitors reach it: behind https."""
    app = create_app(load_settings(env), transport_factory=lambda k: gw.transport())
    return TestClient(app, base_url="https://testserver", **kw)


def sign_in(c: TestClient, secret: str = ADMIN_SECRET):
    return c.post("/api/admin/login", json={"secret": secret}, headers=H)


def seed_showcase(c: TestClient) -> dict:
    """The operator's example data with CANARY in every private field. Written after start-up, straight into
    the database: terminal jobs (and one parked far in the future) are never touched by the engine.

    Some nested structures are shown whole and are deliberately NOT planted, because the app writes them
    itself and never copies gateway text into them:
      adjustments[].reason  the adapters' wording for a snapped setting or a warning (vgen/adapters)
      compliance[].note     written by vgen/analysis/compliance.py from the measured clip
      media                 container and codec facts read from the clip file, which is public itself
      effective, fidelity   the settings actually sent and the measured first/last-frame match
      benchmark summary groups / phases / telemetry / utilization   published as measured (a plan decision)
    Planted, because they are free text or gateway-provided: progress, Run.params (unknown keys), Rating.comment,
    an endpoint's last error, and everything in the experiment record outside its allowlists."""
    spec = {"task": "t2v", "prompt": FOX, "negative_prompt": None, "duration": 4, "resolution": "720p",
            "ratio": "16:9", "audio": False, "seed": None, "first_frame": None, "last_frame": None, "references": [],
            "source_video": None, "audio_input": None, "allow_experimental": False, "operator_note": CANARY}
    private = dict(
        task_id=f"task-{CANARY}", submit_response={"id": f"task-{CANARY}", "group": CANARY},
        last_detail={"status": "FAILURE", "error": {"message": CANARY}, "usage": {"totalTokens": CANARY_NUMBER}},
        payload={"model": "seedance-2.0-mini", "prompt": FOX, "metadata": {"trace": CANARY}},
        quota=CANARY_NUMBER, poll_count=CANARY_NUMBER, submit_attempts=CANARY_NUMBER, remote_status=CANARY,
        video_sha=CANARY, endpoint=f"ep-{CANARY}",
        provenance={"price_snapshot": {"note": CANARY}, "price_checked_at": CANARY, "request_id": CANARY,
                    "config_sha256": CANARY, "phase": "baseline"})
    common = dict(prompt_key="p1", spec=spec, effective={"duration": 4, "resolution": "720p", "ratio": "16:9"},
                  est_cost_usd=0.25, created_at=time.time() - 600, submitted_at=time.time() - 590)
    with db.session() as s:
        s.add(db.Run(id="run_showcase", title="Showcase", kind="compare", task="t2v", notes=CANARY,
                     endpoint=f"ep-{CANARY}", est_cost_usd=0.75, models=["seedance-2.0-mini", "happyhorse-1.0-t2v"],
                     prompts=[{"key": "p1", "title": "Fox", "text": FOX, "checklist": ["A fox"]}],
                     # whatever JSON the creator posted is stored; only the settings the app reads are shown
                     params={"duration": 4, "resolution": "720p", "ratio": "16:9", "operator_memo": CANARY}))
        s.add(db.Job(id="job_balance", run_id="run_showcase", model="seedance-2.0-mini", label="A",
                     deployment_id="tokenbay-com-seedance-2.0-mini", status=db.FAILED, done_at=time.time() - 500,
                     error_code="invalid_request", case_fingerprint=None, progress=f"queued behind {CANARY}",
                     fail_reason=f"Insufficient balance. Available: 24.118 credits, estimated cost: 119 credits. {CANARY}",
                     judge_status="error", judge={"error": f"[business/400] {CANARY}"},
                     signals={"error": f"analysis failed: {CANARY}"}, **private, **common))
        s.add(db.Job(id="job_done", run_id="run_showcase", model="happyhorse-1.0-t2v", label="B",
                     deployment_id=None, status=db.SUCCEEDED, done_at=time.time() - 400,
                     gw_submit_time=1000.0, gw_finish_time=1090.0,
                     media={"duration": 4.0, "video": {"codec": "h264", "width": 1280, "height": 720, "fps": 24.0}},
                     compliance=[{"key": "duration", "ok": True, "expected": 4, "actual": 4.0, "note": ""}],
                     signals={"pairs": 20, "motion_pct_per_s": 2.5, "frozen_flag": False, "error": CANARY,
                              "audio": {"has_audio": False}},
                     judge_status="done",
                     judge={"checklist": [{"item": "A fox", "assessable": True, "pass": True, "evidence": "seen",
                                           "t": 1.0, "raw": CANARY}],
                            "defects": [], "verdict": "Usable.", "adherence": 1.0, "passed": 1, "total": 1,
                            "not_assessable": 0, "defect_score": 0, "mode": "frames-v1", "model": "gemini-3.1-pro-preview",
                            "usage": {"input_tokens": CANARY_NUMBER, "usd": 0.03, "note": CANARY}, "error": CANARY},
                     # a row from before deployments existed: its comparison group is named after the endpoint key
                     **{**private, "endpoint": "com", "task_id": f"task-ok-{CANARY}"}, **common))
        s.add(db.Job(id="job_retrying", run_id="run_showcase", model="seedance-2.0-mini", label="C",
                     deployment_id="tokenbay-com-seedance-2.0-mini", status=db.IN_PROGRESS, progress="40%",
                     next_poll_at=time.time() + 10 ** 6, fail_reason=f"poll error (will retry): {CANARY}",
                     **private, **common))
        s.add(db.Job(id="job_blocked", run_id="run_showcase", model="wan2.7-videoedit", label="D",
                     deployment_id="tokenbay-com-wan2.7-videoedit", status=db.BLOCKED,
                     fail_reason=f"Wan 2.7 VideoEdit does not support t2v {CANARY}",
                     **{**private, "task_id": None}, **common))
        s.add(db.LedgerEntry(kind="video", job_id="job_done", run_id="run_showcase", model="happyhorse-1.0-t2v",
                             est_usd=SPENT, note=f"task-{CANARY}", quota=CANARY_NUMBER))
        s.add(db.Rating(job_id="job_done", run_id="run_showcase", usable=True, overall=4, defects=["flicker"],
                        comment=f"the operator's private note {CANARY}"))
        # an archived experiment: a self-hosted deployment, one imported clip of the same prompt, raw attempt records
        s.add(db.Deployment(id="dep_canary", model="canary-h3", label="Canary H3 · 2× Test GPU", provider="self",
                            timing_scope="server_local_download",
                            hardware={"gpu": "Test GPU", "gpu_count": 2, "memory_gb_per_gpu": 48,
                                      "instance_id": CANARY_NUMBER, "host": f"ssh://{CANARY}"}))
        s.add(db.Job(id="job_imported", run_id="run_showcase", model="canary-h3", label="E", origin="imported",
                     deployment_id="dep_canary", experiment_id="exp_canary", status=db.SUCCEEDED, judge_status="skipped",
                     done_at=time.time() - 300, video_sha=CANARY, endpoint="archive",
                     media={"duration": 5.0, "video": {"codec": "h264", "width": 1344, "height": 768, "fps": 24}},
                     provenance={"request_id": f"req-{CANARY}", "attempt": 1, "phase": "baseline", "variant": "bf16",
                                 "seed": 7, "preferred": True, "source": "self", "e2e_seconds": 12.5, "cost_usd": 0.5,
                                 "cost_basis": "phase_resource_estimate", "timing_scope": "server_local_download",
                                 "config_sha256": CANARY, "original_sha256": CANARY}, **common))
        attempt = {"case_id": "p1", "task": "t2v", "phase": "baseline", "provider": "self", "attempt": 1,
                   "e2e_seconds": 12.5, "estimated_cost_usd": 0.5, "requested_num_inference_steps": 50,
                   "actual_denoise_updates": 50, "variant_evidence_scope": "standard", "currency": "USD",
                   # private: ids, errors, ledger references, paths and hashes
                   "request_id": f"req-{CANARY}", "task_id": CANARY, "error": CANARY, "ledger_reservation_id": CANARY,
                   "record_path": f"runs/{CANARY}.json", "video_path": f"videos/{CANARY}.mp4", "config_sha256": CANARY,
                   "actual_denoise_updates_source": f"runs/{CANARY}/server-progress.txt", "usage_json": {"note": CANARY},
                   "api_reserved_usd": CANARY_NUMBER}
        s.add(db.BenchmarkAttempt(id="attempt_ok", experiment_id="exp_canary", deployment_id="dep_canary",
                                  case_id="canary-set:p1", request_id=f"req-{CANARY}", attempt=1, job_id="job_imported",
                                  metrics={**attempt, "status": "validated", "outcome": "technical_valid",
                                           "technical_valid": True}))
        s.add(db.BenchmarkAttempt(id="attempt_failed", experiment_id="exp_canary", deployment_id="dep_canary",
                                  case_id="canary-set:p1", request_id=f"req2-{CANARY}", attempt=2, job_id=None,
                                  metrics={**attempt, "attempt": 2, "status": "failed", "outcome": "failed",
                                           "technical_valid": False, "e2e_seconds": None}))
        s.add(db.Experiment(
            id="exp_canary", title="Canary experiment", date="2026-11-07", dataset_id="canary-set",
            content_sha256=CANARY, deployment_ids=["dep_canary"],
            config={"model_repo": "org/canary-h3", "num_inference_steps": 50, "gpu_count": 2,
                    "model_path": f"/data/models/{CANARY}", "limits": {"total_usd": CANARY_NUMBER},
                    "request_timeout_seconds": CANARY_NUMBER, "operator_memo": CANARY,
                    "turbo": {"repo": "org/canary-turbo", "num_inference_steps": 8, "path": f"/data/models/{CANARY}"}},
            summary={"groups": [{"provider": "self", "phase": "baseline", "task": "t2v", "e2e_p50_s": 12.5}],
                     "phases": [{"provider": "self", "phase": "baseline", "n_submitted_attempts": 2,
                                 "n_technical_valid": 1, "throughput_success_per_hour": 20.0,
                                 "cost_per_technical_success_usd": 0.5}],
                     "telemetry": {"phase_metrics": [{"phase": "baseline", "server_inference_p50_s": 11.0}]},
                     "utilization": [{"utilization": 0.25, "gpu_disk_cost_per_success_usd": 2.0}],
                     "scenes": [{"case_id": "p1", "title": "Fox", "task": "t2v", "baseline_n": 1, "baseline_p50_s": 12.5,
                                 "official_e2e_s": 9.0, "official_task_id": CANARY,
                                 "matched_baseline_request_id": f"req-{CANARY}"}],
                     "costs": {"generated_utc": "2026-11-07T00:00:00+00:00", "currency": "USD",
                               "api_usage_price_estimate_usd": 4.0, "self_technical_valid_outputs": 1,
                               "official_technical_valid_outputs": 0, "instance_id": CANARY_NUMBER,
                               "credit_before_usd": CANARY_NUMBER, "vast_observed_credit_decrease_usd": CANARY_NUMBER,
                               "final_invoice_status": CANARY, "report_markdown": CANARY,
                               "release_evidence": f"runs/control/{CANARY}.json"},
                     "invoices": [CANARY]},
            artifacts={"report.md": f"/var/lib/{CANARY}/report.md", "attempts.csv": f"/var/lib/{CANARY}/attempts.csv",
                       "provenance.json": f"/var/lib/{CANARY}/provenance.json",
                       "figures/latency.svg": f"/var/lib/{CANARY}/latency.svg",
                       "figures/figure-data.json": f"/var/lib/{CANARY}/figure-data.json"}))
        s.add(db.ProbeResult(case_id="sd20-t2v", model="seedance-2.0", ok=False, job_id="job_probe",
                             detail={"fail_reason": CANARY, "quota": CANARY_NUMBER}))
        s.add(db.Run(id="run_probe", title="Probe · sdmini-t2v-audio", kind="probe", notes=CANARY,
                     models=["seedance-2.0-mini"], prompts=[{"key": "sdmini-t2v-audio", "text": "A small dog barks"}]))
        s.add(db.Job(id="job_probe", run_id="run_probe", prompt_key="sdmini-t2v-audio", model="seedance-2.0-mini",
                     status=db.SUBMIT_UNKNOWN, error_code="server",
                     fail_reason=f"Internal server error {CANARY} — the task may exist and may be billed"))
        s.add(db.Run(id="run_other", title="Someone else's", owner=VISITOR_B, models=["seedance-2.0-mini"],
                     prompts=[{"key": "p1", "text": "Their private prompt"}]))
        s.add(db.Job(id="job_other", run_id="run_other", prompt_key="p1", model="seedance-2.0-mini",
                     status=db.FAILED, fail_reason=f"theirs {CANARY}"))
        s.commit()
    registry = c.app.state.vgen.registry
    overlay = {"unavailable": f"tokenbay.com returned '{CANARY}' on submit; re-check from the Models page",
               "notes": [f"480p rejected: '{CANARY}'"], "tasks": {"t2v": "fail"},
               "delivery": {"image": {"url": "yes", "data_uri": "no"}},
               "cases": {"veo-t2v": {"case": "veo-t2v", "status": "submit_unknown", "fail_reason": CANARY,
                                     "quota": CANARY_NUMBER}}}
    registry.models["veo-3.1-generate-preview"].probe = overlay
    registry.probe = {"models": {"veo-3.1-generate-preview": overlay}, "quota_note": CANARY, "updated_at": CANARY,
                      "quota_per_usd": CANARY_NUMBER, "judge": {"error": CANARY, "usage": {"usd": 0.03}}}
    registry.live_error = f"[auth/401] {CANARY}"
    # The hosted site holds no gateway today, so /api/config lists no endpoints. Should one ever exist there, its
    # last error and disabled reason are raw gateway text. (Disabled, on a dead transport: it can send nothing.)
    held = Gateway(Endpoint("com", "tokenbay.com", "http://mock.tokenbay.local", "sk-none", rpm=15),
                   transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    held.disabled_reason, held.last_error = f"Invalid API key {CANARY}", f"[auth/401] {CANARY}"
    c.app.state.vgen.gateways["com"] = held
    listed = c.get("/api/evaluations").json()
    case = next(x for d in listed if d["id"] == "tokenbay" for x in d["cases"] if x["prompt"] == FOX)
    return {"case": f"/api/evaluations/tokenbay/cases/{case['key']}"}


def public_get_urls(seeded: dict) -> list[str]:
    return ["/", "/admin", "/index.html", "/api/config", "/api/models", "/api/scenarios", "/api/assets", "/api/runs",
            "/api/runs?kind=compare", "/api/runs?kind=probe", "/api/runs/run_showcase",
            "/api/runs/run_showcase/export", "/api/runs/run_showcase/export?format=csv", "/api/leaderboard",
            "/api/leaderboard?rank_by=value&task=t2v", "/api/evaluations", seeded["case"], "/api/benchmarks",
            "/api/benchmarks/exp_canary"]


def test_anonymous_visitors_never_receive_private_fields(admin_env, gw):
    with hosted(admin_env, gw) as c:
        page = c.get("/")
        assert '"admin": false' in page.text and '"public": true' in page.text
        seeded = seed_showcase(c)
        for url in public_get_urls(seeded):
            r = c.get(url)
            assert r.status_code == 200, url
            assert CANARY not in r.text and str(CANARY_NUMBER) not in r.text, url
            # the run-level `spent_usd` from the operator's ledger is not served. (Per-clip cost stays public as a
            # comparison datum, by decision; this checks the field, not that a total could never be added up.)
            assert str(SPENT) not in r.text, url
        # operator tools are closed outright
        for url in ("/api/probe", "/api/evaluations/tokenbay/backfill-plan", "/api/evaluations/h3-v1/backfill-plan",
                    "/api/benchmarks/any/export", "/api/benchmarks/exp_canary/export",
                    "/api/benchmarks/exp_canary/export?format=csv", "/api/ledger"):
            r = c.get(url)
            assert r.status_code == 403 and CANARY not in r.text, url
        for name in ("attempts.csv", "provenance.json", "figures/figure-data.json", "report.md"):   # (no files behind them)
            r = c.get(f"/api/benchmarks/exp_canary/artifacts/{name}")
            assert r.status_code == 404 and CANARY not in r.text, name
        # every page is the injected one, so the UI never has to guess who is looking; other files are served as they are
        for url in ("/index.html", "/INDEX.HTML", "/./index.html", "/admin"):
            assert '"public": true, "admin": false' in c.get(url).text, url
        assert c.get("/robots.txt").text == "User-agent: *\n"
        # probe runs and other visitors' runs do not exist for this visitor
        for url in ("/api/runs/run_probe", "/api/runs/run_probe/export", "/api/runs/run_other",
                    "/api/jobs/job_probe/video", "/api/jobs/job_other/poster"):
            assert c.get(url).status_code == 404, url
        assert {x["id"] for x in c.get("/api/runs").json()} == {"run_showcase"}
        assert c.get("/api/runs?kind=probe").json() == []
        # (the example data does reach the aggregate views that were just checked)
        assert c.get("/api/leaderboard").json()["clips"] == 1
        # no interactive docs or schema on the hosted site (the SPA answers instead)
        for url in ("/openapi.json", "/docs", "/redoc"):
            r = c.get(url)
            assert "openapi" not in r.text.lower() and "swagger" not in r.text.lower(), url
            assert r.headers["content-type"].startswith("text/html"), url

        cfg = c.get("/api/config").json()
        assert set(cfg) == {"mock", "public", "admin", "admin_login", "endpoints", "spent_usd", "usd_cny",
                            "judge_model", "tasks"}
        assert cfg["admin"] is False and cfg["admin_login"] is True and cfg["spent_usd"] == 0
        assert cfg["endpoints"] == [{"key": "com", "name": "tokenbay.com", "rpm": 15}]

        listed = c.get("/api/runs").json()[0]
        assert listed["spent_usd"] is None and listed["mine"] is False and listed["est_cost_usd"] == 0.75

        d = c.get("/api/runs/run_showcase").json()
        assert not PRIVATE_KEYS & all_keys(d["jobs"]) and not PRIVATE_KEYS & all_keys(d["run"])
        assert set(d["run"]) == {"id", "created_at", "title", "kind", "scenario_id", "category", "task", "lang",
                                 "params", "prompts", "models", "judge", "est_cost_usd"}
        assert d["spent_usd"] is None and d["mine"] is False
        assert d["run"]["params"] == {"duration": 4, "resolution": "720p", "ratio": "16:9"}
        # someone else's rating: the scores, without the free-text comment
        assert d["ratings"] == {"job_done": {"job_id": "job_done", "run_id": "run_showcase", "usable": True, "overall": 4,
                                             "defects": ["flicker"], "updated_at": d["ratings"]["job_done"]["updated_at"]}}
        jobs = {j["id"]: j for j in d["jobs"]}
        # failures stay visible, as a category
        assert jobs["job_balance"]["failure"] == {"category": "insufficient_balance", "retrying": False}
        assert jobs["job_balance"]["progress"] == ""          # not a plain percentage: dropped
        assert jobs["job_balance"]["status"] == "failed" and jobs["job_balance"]["has_task"] is True
        assert jobs["job_balance"]["judge"] == {} and jobs["job_balance"]["judge_status"] == "error"
        assert jobs["job_balance"]["signals"] == {"analysis_failed": True}
        assert jobs["job_retrying"]["failure"] == {"category": "provider_error", "retrying": True}
        assert jobs["job_retrying"]["progress"] == "40%"
        assert jobs["job_blocked"]["failure"] == {"category": "unsupported", "retrying": False}
        assert jobs["job_blocked"]["has_task"] is False
        done = jobs["job_done"]
        assert done["failure"] is None and done["has_task"] is True and done["latency_s"] == 90
        assert done["judge"]["verdict"] == "Usable." and done["judge"]["checklist"][0] == {
            "item": "A fox", "assessable": True, "pass": True, "evidence": "seen", "t": 1.0}
        assert done["signals"]["motion_pct_per_s"] == 2.5 and done["signals"]["analysis_failed"] is True
        assert done["spec"]["prompt"] == FOX and "operator_note" not in done["spec"]
        assert set(done["cost"]) <= {"estimate_usd", "actual_usd", "per_second", "basis"}
        assert done["cost"]["estimate_usd"] == 0.25 and done["media"]["video"]["width"] == 1280
        assert all(j["provenance"] == {"phase": "baseline"} for j in d["jobs"] if j["origin"] == "generated")
        assert done["model_label"] and done["effective"]["duration"] == 4
        # the archived clip: its step counts are filled in from the attempt record first, then the same allowlist applies
        imported = jobs["job_imported"]
        assert imported["provenance"] == {
            "phase": "baseline", "variant": "bf16", "seed": 7, "preferred": True, "source": "self", "e2e_seconds": 12.5,
            "cost_usd": 0.5, "cost_basis": "phase_resource_estimate", "timing_scope": "server_local_download",
            "requested_num_inference_steps": 50, "actual_denoise_updates": 50, "variant_evidence_scope": "standard"}
        assert imported["cost"] == {"estimate_usd": 0.5, "actual_usd": None, "basis": "phase_resource_estimate",
                                    "per_second": 0.1}
        assert imported["latency_s"] == 12.5 and imported["latency_basis"] == "server_local_download"
        assert imported["model_label"] == "Canary H3 · 2× Test GPU" and imported["failure"] is None

        exported = c.get("/api/runs/run_showcase/export")
        assert exported.json()["jobs"] == d["jobs"] and "notes" not in exported.json()["run"]
        assert exported.json()["ratings"] == d["ratings"] and exported.json()["run"] == d["run"]
        rows = list(csv.reader(io.StringIO(c.get("/api/runs/run_showcase/export?format=csv").text)))
        assert rows[0] == ["run_id", "prompt_key", "label", "model", "status", "failure_category", "duration_s",
                           "width", "height", "has_audio", "latency_s", "est_usd", "compliance_ok",
                           "judge_adherence", "judge_defects", "usable", "overall", "picked"]
        by_label = {r[2]: dict(zip(rows[0], r)) for r in rows[1:]}
        assert by_label["A"]["failure_category"] == "insufficient_balance" and by_label["B"]["failure_category"] == ""
        assert by_label["D"]["failure_category"] == "unsupported" and by_label["B"]["est_usd"] == "0.25"
        assert by_label["B"]["usable"] == "True" and by_label["B"]["overall"] == "4"        # scores stay in the export

        case = c.get(seeded["case"]).json()
        samples = [j for g in case["groups"] for j in g["samples"]]
        assert {j["id"] for j in samples} == {"job_balance", "job_done", "job_retrying", "job_blocked", "job_imported"}
        assert not PRIVATE_KEYS & all_keys(case["groups"])
        assert {g["id"]: (g["provider"], g["hardware"]) for g in case["groups"]} == {
            "tokenbay-com-seedance-2.0-mini": ("tokenbay", {}), "tokenbay-com-happyhorse-1.0-t2v": ("tokenbay", {}),
            "tokenbay-com-wan2.7-videoedit": ("tokenbay", {}),
            "dep_canary": ("self", {"gpu": "Test GPU", "gpu_count": 2, "memory_gb_per_gpu": 48})}
        assert all(set(g) == {"id", "model", "label", "provider", "hardware", "samples", "default_job_id"}
                   for g in case["groups"])
        assert all(j["editable"] is False for j in samples)
        assert {j["id"]: j["rating"] for j in samples if j["rating"]} == {"job_done": d["ratings"]["job_done"]}

        # the archived experiment: settings, timings and the cost estimate, without the account figures and raw records
        catalog = c.get("/api/benchmarks").json()
        assert catalog["experiments"] == [{"id": "exp_canary", "title": "Canary experiment", "date": "2026-11-07",
                                           "dataset_id": "canary-set", "deployment_ids": ["dep_canary"],
                                           "n_attempts": 2, "n_videos": 1}]
        shown_deployment = {"id": "dep_canary", "model": "canary-h3", "label": "Canary H3 · 2× Test GPU",
                            "provider": "self", "timing_scope": "server_local_download",
                            "hardware": {"gpu": "Test GPU", "gpu_count": 2, "memory_gb_per_gpu": 48}}
        assert catalog["deployments"] == [shown_deployment]
        b = c.get("/api/benchmarks/exp_canary").json()
        assert set(b) == {"id", "title", "date", "dataset_id", "deployment_ids", "config", "summary", "deployments",
                          "artifacts", "attempts"}
        assert b["config"] == {"model_repo": "org/canary-h3", "num_inference_steps": 50, "gpu_count": 2,
                               "turbo": {"repo": "org/canary-turbo", "num_inference_steps": 8}}
        assert set(b["summary"]) == {"groups", "phases", "telemetry", "utilization", "scenes", "costs"}
        assert b["summary"]["phases"][0]["cost_per_technical_success_usd"] == 0.5
        assert b["summary"]["scenes"] == [{"case_id": "p1", "title": "Fox", "task": "t2v", "baseline_n": 1,
                                           "baseline_p50_s": 12.5, "official_e2e_s": 9.0}]
        assert b["summary"]["costs"] == {"generated_utc": "2026-11-07T00:00:00+00:00", "currency": "USD",
                                         "api_usage_price_estimate_usd": 4.0, "self_technical_valid_outputs": 1,
                                         "official_technical_valid_outputs": 0}
        assert b["deployments"] == [shown_deployment]
        assert b["artifacts"] == {"report.md": "/api/benchmarks/exp_canary/artifacts/report.md",
                                  "figures/latency.svg": "/api/benchmarks/exp_canary/artifacts/figures/latency.svg"}
        assert [set(a) for a in b["attempts"]] == [{*public_view.PUBLIC_ATTEMPT_KEYS, "metrics"}] * 2
        assert all(set(a["metrics"]) <= set(public_view.PUBLIC_ATTEMPT_METRIC_KEYS) for a in b["attempts"])
        ok = next(a for a in b["attempts"] if a["id"] == "attempt_ok")
        assert ok["job_id"] == "job_imported" and ok["metrics"]["e2e_seconds"] == 12.5
        assert ok["metrics"]["estimated_cost_usd"] == 0.5 and ok["metrics"]["requested_num_inference_steps"] == 50
        veo = next(x for x in case["coverage"] if x["model"] == "veo-3.1-generate-preview")
        assert veo["reason"] == "" and veo["task_state"] == "probe_failed"
        assert all(x["reason"] == "" for x in case["coverage"])

        catalog = c.get("/api/models").json()
        assert catalog["unregistered"] == []
        veo = next(m for m in catalog["models"] if m["id"] == "veo-3.1-generate-preview")
        assert veo["probe"] == {"tasks": {"t2v": "fail"}, "delivery": {"image": {"url": "yes", "data_uri": "no"}},
                                "unavailable": True}
        assert veo["task_states"]["t2v"] == "probe_failed" and veo["label"] and veo["price"]
        assert all(set(m["probe"]) <= {"tasks", "delivery", "unavailable"} for m in catalog["models"])

        plan = c.post("/api/plan", json=run_request(["seedance-2.0-mini", "wan2.7-videoedit"]), headers=H)
        assert plan.status_code == 200 and "payload" not in all_keys(plan.json())
        items = {i["model"]: i for i in plan.json()["items"]}
        assert items["seedance-2.0-mini"]["ok"] and items["seedance-2.0-mini"]["estimate"]["usd"] > 0
        assert items["seedance-2.0-mini"]["effective"] and not items["wan2.7-videoedit"]["ok"]

        # API data is never cached (the same URL differs per visitor and per admin session); media is untouched
        for url in ("/api/config", "/api/runs", "/api/runs/run_showcase", "/api/runs/run_showcase/export?format=csv",
                    "/api/probe", seeded["case"]):
            assert c.get(url).headers["cache-control"] == "no-store", url
        assert c.post("/api/plan", json={}, headers=H).headers["cache-control"] == "no-store"      # also errors
        assert c.post("/api/plan", json={}).headers["cache-control"] == "no-store"                 # ... and 403s


def test_admin_sign_in_reveals_diagnostics_and_logout_revokes(admin_env, gw):
    with hosted(admin_env, gw) as c:
        c.get("/")
        seeded = seed_showcase(c)
        # needs the launch token like every other state change
        assert c.post("/api/admin/login", json={"secret": ADMIN_SECRET}).status_code == 403
        # a wrong secret, a missing one, and a forged cookie
        r = sign_in(c, "not the secret at all")
        assert r.status_code == 401 and "set-cookie" not in r.headers and "launch token" not in r.text
        assert c.post("/api/admin/login", json={}, headers=H).status_code == 401
        c.cookies.set(ADMIN_COOKIE, "forged-session-token")
        assert c.get("/api/config").json()["admin"] is False and c.get("/api/probe").status_code == 403
        c.cookies.delete(ADMIN_COOKIE)

        r = sign_in(c)
        assert r.status_code == 200 and r.json() == {"ok": True}
        cookie = r.headers["set-cookie"]
        flags = {part.strip().lower() for part in cookie.split(";")}
        # Lax, so the cookie comes along when the operator follows a link to the site and the page boots as admin
        # (Strict would leave it in the visitor view until a reload); state changes need the launch token anyway
        assert cookie.startswith(f"{ADMIN_COOKIE}=") and {"httponly", "secure", "samesite=lax", "path=/"} <= flags
        assert "max-age=43200" in flags and "samesite=strict" not in flags
        token = c.cookies.get(ADMIN_COOKIE)
        assert token and ADMIN_SECRET not in cookie and ADMIN_HASH not in cookie

        assert '"admin": true' in c.get("/").text
        cfg = c.get("/api/config").json()
        assert cfg["admin"] is True and cfg["admin_login"] is True and CANARY in cfg["live_error"]
        assert cfg["quota_note"] == CANARY and "budget_usd" in cfg
        assert CANARY in cfg["endpoints"][0]["last_error"] and CANARY in cfg["endpoints"][0]["disabled_reason"]
        assert "used_last_minute" in cfg["endpoints"][0]
        # the same endpoints now carry the raw fields
        d = c.get("/api/runs/run_showcase").json()
        jobs = {j["id"]: j for j in d["jobs"]}
        bal = jobs["job_balance"]
        assert CANARY in bal["fail_reason"] and bal["error_code"] == "invalid_request" and bal["quota"] == CANARY_NUMBER
        assert bal["task_id"] == f"task-{CANARY}" and bal["submit_response"]["group"] == CANARY
        assert bal["last_detail"]["error"]["message"] == CANARY and bal["payload"]["metadata"]["trace"] == CANARY
        assert bal["judge"]["error"] and bal["signals"]["error"] and bal["provenance"]["request_id"] == CANARY
        assert bal["failure"] == {"category": "insufficient_balance", "retrying": False} and bal["has_task"] is True
        assert jobs["job_done"]["judge"]["usage"]["note"] == CANARY and jobs["job_done"]["cost"]["quota"] == CANARY_NUMBER
        assert d["run"]["notes"] == CANARY and "owner" not in d["run"] and d["spent_usd"] == SPENT
        assert d["mine"] is False and d["run"]["params"]["operator_memo"] == CANARY
        assert CANARY in d["ratings"]["job_done"]["comment"] and d["ratings"]["job_done"]["overall"] == 4
        assert jobs["job_balance"]["progress"] == f"queued behind {CANARY}"
        assert jobs["job_imported"]["provenance"]["request_id"] == f"req-{CANARY}"
        assert CANARY in jobs["job_imported"]["provenance"]["actual_denoise_updates_source"]
        listed = {x["id"]: x for x in c.get("/api/runs").json()}
        assert listed["run_showcase"]["spent_usd"] == SPENT
        rows = list(csv.reader(io.StringIO(c.get("/api/runs/run_showcase/export?format=csv").text)))
        assert rows[0][5] == "fail_reason" and rows[0][12] == "quota" and rows[0][-1] == "failure_category"
        assert any(CANARY in r[5] and r[-1] == "insufficient_balance" for r in rows[1:])
        assert CANARY in c.get("/api/runs/run_showcase/export").text
        case = c.get(seeded["case"]).json()
        assert CANARY in next(x for x in case["coverage"] if x["model"] == "veo-3.1-generate-preview")["reason"]
        assert all("task_id" in j and "fail_reason" in j for g in case["groups"] for j in g["samples"])
        assert {g["id"]: g["hardware"] for g in case["groups"]}["dep_canary"] == {
            "gpu": "Test GPU", "gpu_count": 2, "memory_gb_per_gpu": 48}          # the reduced view for every viewer
        rated = next(j for g in case["groups"] for j in g["samples"] if j["id"] == "job_done")
        assert CANARY in rated["rating"]["comment"]
        veo = next(m for m in c.get("/api/models").json()["models"] if m["id"] == "veo-3.1-generate-preview")
        assert CANARY in veo["probe"]["unavailable"] and veo["probe"]["cases"]["veo-t2v"]["fail_reason"] == CANARY
        assert c.get("/api/models").json()["unregistered"] == {"com": []}
        probe = c.get("/api/probe")
        assert probe.status_code == 200 and probe.json()["results"][0]["detail"]["fail_reason"] == CANARY
        assert "payload" in all_keys(c.post("/api/plan", json=run_request(["seedance-2.0-mini"]), headers=H).json())
        assert c.get("/api/evaluations/tokenbay/backfill-plan").status_code == 404      # past the gate: no such dataset
        assert c.get("/api/benchmarks/any/export").status_code == 404
        b = c.get("/api/benchmarks/exp_canary").json()
        assert b["content_sha256"] == CANARY and b["config"]["limits"] == {"total_usd": CANARY_NUMBER}
        assert b["summary"]["costs"]["instance_id"] == CANARY_NUMBER and b["summary"]["invoices"] == [CANARY]
        assert b["summary"]["scenes"][0]["official_task_id"] == CANARY and "attempts.csv" in b["artifacts"]
        assert all(CANARY in a["request_id"] and a["metrics"]["error"] == CANARY for a in b["attempts"])
        assert c.get("/api/benchmarks").json()["deployments"][0]["hardware"]["instance_id"] == CANARY_NUMBER
        assert c.get("/api/benchmarks/exp_canary/export?format=csv").text.startswith("\ufeffexperiment_id")
        # ... including the operator's probe runs
        assert "run_probe" in listed and listed["run_probe"]["kind"] == "probe"
        assert {x["id"] for x in c.get("/api/runs?kind=probe").json()} == {"run_probe"}
        p = c.get("/api/runs/run_probe")
        assert p.status_code == 200 and CANARY in p.json()["jobs"][0]["fail_reason"]
        assert p.json()["jobs"][0]["failure"] == {"category": "provider_error", "retrying": False}

        # admin is read visibility only: other visitors' runs stay invisible, examples stay read-only,
        # and the operator-only routes stay closed
        assert "run_other" not in listed
        for url in ("/api/runs/run_other", "/api/runs/run_other/export", "/api/jobs/job_other/video"):
            assert c.get(url).status_code == 404, url
        for method, url, kw in [
            ("put", "/api/jobs/job_done/rating", {"json": {"usable": True}}),
            ("put", "/api/runs/run_showcase/pick", {"json": {"prompt_key": "p1", "winner_job_id": "job_done"}}),
            ("delete", "/api/runs/run_showcase", {}),
            ("delete", "/api/runs/run_probe", {}),
            ("post", "/api/jobs/job_balance/resubmit", {"json": {"confirm": True}}),
            ("post", "/api/jobs/job_retrying/stop", {}),
            ("post", "/api/jobs/job_probe/repoll", {}),
            ("put", "/api/settings", {"json": {"budget_usd": 1}}),
            ("get", "/api/ledger", {}),
            ("post", "/api/probe", {"json": {"confirm": True}}),
            ("post", "/api/probe/resummarize", {}),
            ("post", "/api/models/refresh", {}),
            ("post", "/api/assets/url", {"json": {"url": "http://169.254.169.254/latest/meta-data/"}}),
        ]:
            assert getattr(c, method)(url, headers=key("sk-visitor-a"), **kw).status_code == 403, url
        for method, url in [("delete", "/api/runs/run_other"), ("post", "/api/runs/run_other/rerun"),
                            ("post", "/api/runs/run_other/key")]:
            assert getattr(c, method)(url, headers=key("sk-visitor-a")).status_code == 404, url
        # a rejected key: the admin sees the gateway's wording, a visitor would not
        r = c.post("/api/key/check", headers=key("sk-bad"))
        assert r.status_code == 401 and r.json()["detail"] == "Tokenbay rejected this key; check that it is active (Invalid API key)"

        # another browser is not signed in
        visitor = TestClient(c.app, base_url="https://testserver")
        assert visitor.get("/api/config").json()["admin"] is False
        assert CANARY not in visitor.get("/api/runs/run_showcase").text
        r = visitor.post("/api/key/check", headers=key("sk-bad"))
        assert r.status_code == 401 and r.json()["detail"] == "Tokenbay rejected this key; check that it is active"

        # logout revokes the session on the server, not just in the browser
        assert c.post("/api/admin/logout").status_code == 403                  # launch token
        r = c.post("/api/admin/logout", headers=H)
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert f"{ADMIN_COOKIE}=" in r.headers["set-cookie"] and "max-age=0" in r.headers["set-cookie"].lower()
        assert c.cookies.get(ADMIN_COOKIE) is None
        assert c.get("/api/config").json()["admin"] is False
        c.cookies.set(ADMIN_COOKIE, token)                                     # a copy of the old cookie
        assert c.get("/api/config").json()["admin"] is False
        assert CANARY not in c.get("/api/runs/run_showcase").text and c.get("/api/probe").status_code == 403
        assert c.get("/api/runs/run_probe").status_code == 404
        assert c.post("/api/admin/logout", headers=H).status_code == 200       # signing out twice is fine

    # a restart signs the admin out
    with hosted(admin_env, gw) as c:
        c.cookies.set(ADMIN_COOKIE, token)
        assert c.get("/api/config").json()["admin"] is False


def test_admin_sign_in_is_throttled_per_client(admin_env, gw):
    with hosted(admin_env, gw) as c:
        c.get("/")
        # no room to queue for the hash slot: the right secret is told to come back, not that it is wrong,
        # and however often that happens it is not counted as a failed attempt
        auth = c.app.state.vgen.admin
        auth._waiting = security.ADMIN_MAX_WAITING
        for secret in [ADMIN_SECRET] * (security.ADMIN_MAX_FAILURES + 2) + ["a wrong guess, again"]:
            r = sign_in(c, secret)
            assert r.status_code == 429 and r.headers["retry-after"] == "1" and "set-cookie" not in r.headers
            assert "busy" in r.json()["detail"] and "launch token" not in r.text and "not accepted" not in r.text
        auth._waiting = 0
        assert auth.retry_after("testclient") == 0
        assert sign_in(c).status_code == 200 and c.get("/api/config").json()["admin"] is True
        assert c.post("/api/admin/logout", headers=H).status_code == 200
        for _ in range(5):
            r = sign_in(c, "a wrong guess, again")
            assert r.status_code == 401 and "launch token" not in r.text and "retry-after" not in r.headers
        for secret in ("a wrong guess, again", ADMIN_SECRET):                  # locked: the right one too
            r = sign_in(c, secret)
            assert r.status_code == 429 and "launch token" not in r.text and "set-cookie" not in r.headers
            assert 1 <= int(r.headers["retry-after"]) <= 900 and "too many" in r.json()["detail"]
        assert c.get("/api/config").json()["admin"] is False
        # keyed on the client address, so nobody else is locked out
        elsewhere = TestClient(c.app, base_url="https://testserver", client=("203.0.113.7", 40000))
        assert sign_in(elsewhere).status_code == 200
        assert elsewhere.get("/api/config").json()["admin"] is True


def test_admin_sign_in_needs_https_except_locally(admin_env, gw, monkeypatch):
    monkeypatch.setenv("VGEN_ALLOWED_HOSTS", "video.example")
    app = create_app(load_settings(admin_env), transport_factory=lambda k: gw.transport())
    with TestClient(app, base_url="http://video.example") as c:
        r = sign_in(c)
        assert r.status_code == 400 and "https" in r.json()["detail"] and "set-cookie" not in r.headers
        cfg = c.get("/api/config").json()
        assert cfg["admin"] is False and cfg["admin_login"] is True
        secure = TestClient(app, base_url="https://video.example")
        assert sign_in(secure).status_code == 200 and secure.get("/api/config").json()["admin"] is True
        # a local check of the hosted mode over plain http still works; the cookie stays Secure
        local = TestClient(app, base_url="http://testserver")
        r = sign_in(local)
        assert r.status_code == 200 and "secure" in r.headers["set-cookie"].lower()
        token = r.headers["set-cookie"].split(";")[0].split("=", 1)[1]
        assert local.get("/api/config", headers={"cookie": f"{ADMIN_COOKIE}={token}"}).json()["admin"] is True


def test_admin_sign_in_is_off_without_a_valid_hash(public_env, gw, monkeypatch, web_page):
    for configured in (None, "", "not-a-hash", "scrypt:1024:8:1:00:00", ADMIN_SECRET):
        if configured is None:
            monkeypatch.delenv("VGEN_ADMIN_SECRET_HASH", raising=False)
        else:
            monkeypatch.setenv("VGEN_ADMIN_SECRET_HASH", configured)
        with hosted(public_env, gw) as c:
            assert '"admin": false' in c.get("/").text
            cfg = c.get("/api/config").json()
            assert cfg["admin"] is False and cfg["admin_login"] is False
            for secret in (ADMIN_SECRET, configured or "", ""):
                assert sign_in(c, secret).status_code == 404
            assert c.post("/api/admin/logout", headers=H).status_code == 404
            c.cookies.set(ADMIN_COOKIE, "anything")
            assert c.get("/api/config").json()["admin"] is False and c.get("/api/probe").status_code == 403


def test_a_visitors_own_failed_clip_shows_the_category_only(admin_env, gw):
    with hosted(admin_env, gw) as c:
        c.get("/")
        # refused at submit (no task), and failed after submit (task exists)
        refused = run_request(["seedance-2.0-mini"])
        refused["prompts"][0]["text"] = "[reject] a fox"
        failed = run_request(["seedance-2.0-mini"])
        failed["prompts"][0]["text"] = "[fail] a fox"
        seen = {}
        for name, body in (("refused", refused), ("failed", failed)):
            r = c.post("/api/runs", json=body, headers=key("sk-visitor-a"))
            assert r.status_code == 200, r.text
            assert not PRIVATE_KEYS & all_keys(r.json()["jobs"]) and "endpoint" not in r.json()["run"]
            wait_run(c, r.json()["run"]["id"])
            detail = c.get(f"/api/runs/{r.json()['run']['id']}")
            for raw in ("moderation", "mock generation failure", "task_mock_", "FAILURE", "invalid_request_error"):
                assert raw not in detail.text, raw
            seen[name] = detail.json()
            assert seen[name]["mine"] is True and not PRIVATE_KEYS & all_keys(seen[name]["jobs"])
        job = seen["refused"]["jobs"][0]
        assert job["status"] == "failed" and job["has_task"] is False
        assert job["failure"] == {"category": "content_policy", "retrying": False}
        assert seen["refused"]["spent_usd"] == 0                               # their own run: a number, not hidden
        job = seen["failed"]["jobs"][0]
        assert job["status"] == "failed" and job["has_task"] is True
        assert job["failure"] == {"category": "provider_error", "retrying": False}
        run_id = seen["refused"]["run"]["id"]
        # their own rating keeps its comment, in the run and in the comparison view
        own = seen["refused"]["jobs"][0]["id"]
        r = c.put(f"/api/jobs/{own}/rating", json={"usable": False, "overall": 2, "comment": "my own note"}, headers=H)
        assert r.status_code == 200 and r.json()["comment"] == "my own note"
        mine = c.get(f"/api/runs/{run_id}").json()["ratings"][own]
        assert mine["comment"] == "my own note" and mine["overall"] == 2 and mine["usable"] is False
        listed = c.get("/api/evaluations").json()
        case = next(x for d in listed if d["id"] == "tokenbay" for x in d["cases"] if x["prompt"] == "[reject] a fox")
        sample = c.get(f"/api/evaluations/tokenbay/cases/{case['key']}").json()["groups"][0]["samples"][0]
        assert sample["id"] == own and sample["editable"] is True and sample["rating"]["comment"] == "my own note"
        text = c.get(f"/api/runs/{run_id}/export?format=csv").text
        assert "content_policy" in text and "moderation" not in text and "fail_reason" not in text
        # the action routes answer with the same projection
        r = c.post(f"/api/jobs/{seen['failed']['jobs'][0]['id']}/resubmit", json={}, headers=key("sk-visitor-a"))
        assert r.status_code == 409 and "already created a task" in r.json()["detail"]
        r = c.post(f"/api/jobs/{job['id']}/stop", headers=H)
        assert r.status_code == 200 and not PRIVATE_KEYS & all_keys(r.json()) and r.json()["failure"]["category"] == "provider_error"
        r = c.post(f"/api/jobs/{seen['refused']['jobs'][0]['id']}/resubmit", json={}, headers=key("sk-visitor-a"))
        assert r.status_code == 200 and not PRIVATE_KEYS & all_keys(r.json()) and r.json()["failure"] is None
        # a plan that needs confirmation is echoed without request payloads
        c.app.state.vgen.registry.prices["models"].pop("happyhorse-1.0-t2v")
        r = c.post("/api/runs", json=run_request(["happyhorse-1.0-t2v"]), headers=key("sk-visitor-a"))
        assert r.status_code == 409 and r.json()["needs_confirmation"] and r.json()["plan"]["items"][0]["ok"]
        assert "payload" not in all_keys(r.json())
        # key errors carry no gateway wording
        r = c.post("/api/key/check", headers=key("sk-bad"))
        assert r.status_code == 401 and r.json()["detail"] == "Tokenbay rejected this key; check that it is active"


def test_local_mode_is_unchanged(mock_settings, web_page):
    assert mock_settings.public is False
    with TestClient(create_app(mock_settings)) as c:
        assert '"admin": true' in c.get("/").text and '"public": false' in c.get("/").text
        assert '"public": false, "admin": true' in c.get("/index.html").text and c.get("/robots.txt").text == "User-agent: *\n"
        cfg = c.get("/api/config")
        assert cfg.json()["admin"] is True and cfg.json()["admin_login"] is False
        assert {"budget_usd", "hard_cap_usd", "confirm_above_usd", "max_inflight", "judge_models", "probe_updated_at",
                "quota_per_usd", "quota_calibrated", "quota_note", "live_checked_at", "live_error", "endpoints",
                "spent_usd"} <= set(cfg.json())
        assert cfg.json()["endpoints"][0]["key"] == "com" and "cache-control" not in cfg.headers
        # no sign-in here: everyone on the local instance is the operator
        assert sign_in(c).status_code == 404 and sign_in(c, "").status_code == 404
        assert c.post("/api/admin/logout", headers=H).status_code == 404
        assert c.get("/openapi.json").json()["info"]["title"] == "vgen-eval" and c.get("/docs").status_code == 200

        body = run_request(["seedance-2.0-mini", "wan2.7-videoedit"])
        body["prompts"][0]["text"] = "[reject] something"
        plan = c.post("/api/plan", json=body, headers=H).json()
        assert next(i for i in plan["items"] if i["ok"])["payload"]["model"] == "seedance-2.0-mini"
        d = wait_run(c, c.post("/api/runs", json=body, headers=H).json()["run"]["id"])
        assert {"notes", "endpoint"} <= set(d["run"]) and d["spent_usd"] == 0 and d["mine"] is True
        by = {j["model"]: j for j in d["jobs"]}
        job = by["seedance-2.0-mini"]
        for field in ("task_id", "submit_response", "last_detail", "payload", "fail_reason", "error_code", "quota",
                      "poll_count", "submit_attempts", "next_poll_at", "remote_status", "endpoint",
                      "case_fingerprint", "video_sha"):
            assert field in job, field
        assert "moderation" in job["fail_reason"] and job["error_code"] == "content_policy"
        assert job["payload"]["prompt"] == "[reject] something" and "price_snapshot" in job["provenance"]
        assert "quota" in job["cost"] and job["spec"]["allow_experimental"] is False
        # the new fields are there for the operator too
        assert job["failure"] == {"category": "content_policy", "retrying": False} and job["has_task"] is False
        assert by["wan2.7-videoedit"]["failure"] == {"category": "unsupported", "retrying": False}
        assert "does not support" in by["wan2.7-videoedit"]["fail_reason"]
        rows = list(csv.reader(io.StringIO(c.get(f"/api/runs/{d['run']['id']}/export?format=csv").text)))
        assert rows[0] == ["run_id", "prompt_key", "label", "model", "status", "fail_reason", "duration_s", "width",
                           "height", "has_audio", "latency_s", "est_usd", "quota", "compliance_ok", "judge_adherence",
                           "judge_defects", "usable", "overall", "picked", "failure_category"]
        assert any("moderation" in r[5] and r[-1] == "content_policy" for r in rows[1:])
        assert "fail_reason" in c.get(f"/api/runs/{d['run']['id']}/export").json()["jobs"][0]

        ok = wait_run(c, c.post("/api/runs", json=run_request(["seedance-2.0-mini"]), headers=H).json()["run"]["id"])
        job = ok["jobs"][0]
        assert job["status"] == "succeeded" and job["task_id"] and job["has_task"] is True and job["failure"] is None
        assert job["last_detail"]["status"] == "SUCCESS" and job["quota"] == 150000 and ok["spent_usd"] > 0
        assert "analysis_failed" not in job["signals"] and job["signals"]["pairs"] > 0
        listed = {x["id"]: x for x in c.get("/api/runs").json()}
        assert listed[ok["run"]["id"]]["spent_usd"] == ok["spent_usd"]

        # probe data, the planning tool and the model overlay are open as before
        registry = c.app.state.vgen.registry
        registry.models["veo-3.1-generate-preview"].probe = {"unavailable": "tokenbay.com returned 'x'", "notes": ["n"]}
        models = c.get("/api/models").json()
        veo = next(m for m in models["models"] if m["id"] == "veo-3.1-generate-preview")
        assert veo["probe"] == {"unavailable": "tokenbay.com returned 'x'", "notes": ["n"]}
        assert isinstance(models["unregistered"], dict) and "com" in models["unregistered"]
        probe = c.get("/api/probe").json()
        assert {"status", "results", "running", "log", "cases"} <= set(probe)
        probe_run = c.post("/api/runs", json=run_request(["seedance-2.0-mini"], kind="probe"), headers=H).json()["run"]["id"]
        assert probe_run in {x["id"] for x in c.get("/api/runs").json()}
        assert c.get(f"/api/runs/{probe_run}").status_code == 200
        listed = c.get("/api/evaluations").json()
        case = next(x for d in listed if d["id"] == "tokenbay" for x in d["cases"] if x["prompt"] == "A fox in snow")
        detail = c.get(f"/api/evaluations/tokenbay/cases/{case['key']}").json()
        assert all("task_id" in j for g in detail["groups"] for j in g["samples"])
        assert next(x for x in detail["coverage"] if x["model"] == "veo-3.1-generate-preview")["reason"].startswith("tokenbay.com")
        assert c.get("/api/ledger").status_code == 200
