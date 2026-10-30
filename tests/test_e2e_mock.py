"""End-to-end against the in-process mock gateway: no network, no spending."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vgen.api import create_app
from vgen.catalog.registry import Registry
from vgen.config import load_settings
from vgen.mock_gateway import MockGateway
from vgen.security import LAUNCH_TOKEN

H = {"x-vgen-token": LAUNCH_TOKEN}


def wait_run(client: TestClient, run_id: str, timeout: float = 90) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = client.get(f"/api/runs/{run_id}").json()
        if d["active"] == 0 and all(j["judge_status"] not in ("pending", "running") for j in d["jobs"]):
            return d
        time.sleep(0.5)
    raise AssertionError(f"run did not finish: {[ (j['model'], j['status']) for j in d['jobs']]}")


def run_request(models, task="t2v", **extra) -> dict:
    return {"title": "test", "task": task, "prompts": [{"key": "p1", "text": "A fox in snow",
                                                       "checklist": ["A fox", "Snow"]}],
            "models": models, "params": {"duration": 4, "resolution": "720p", "ratio": "16:9"}, "confirm": True,
            **extra}


def test_mock_run_lifecycle(mock_settings):
    app = create_app(mock_settings)
    with TestClient(app) as c:
        cfg = c.get("/api/config").json()
        assert cfg["mock"] is True
        models = c.get("/api/models").json()["models"]
        assert any(m["id"] == "seedance-2.0" for m in models)
        packs = c.get("/api/scenarios").json()
        assert any(p["id"] == "quick-tour" for p in packs["packs"])

        # token required for state changes
        assert c.post("/api/runs", json=run_request(["seedance-2.0"])).status_code == 403

        plan = c.post("/api/plan", json=run_request(["seedance-2.0-mini", "veo-3.1-fast-generate-preview",
                                                     "happyhorse-1.0-t2v"]), headers=H).json()
        assert plan["runnable"] == 3 and plan["total_usd"] > 0

        body = run_request(["seedance-2.0-mini", "veo-3.1-fast-generate-preview", "happyhorse-1.0-t2v"], judge=True)
        r = c.post("/api/runs", json=body, headers=H)
        assert r.status_code == 200, r.text
        run_id = r.json()["run"]["id"]
        d = wait_run(c, run_id)
        statuses = {j["model"]: j["status"] for j in d["jobs"]}
        assert set(statuses.values()) == {"succeeded"}, statuses
        job = d["jobs"][0]
        assert job["media"]["video"]["width"] > 0
        assert {i["key"] for i in job["compliance"]} >= {"resolution", "duration", "aspect_ratio"}
        assert job["judge_status"] == "done" and job["judge"]["total"] == 2
        assert job["poster_url"] and job["strip_url"]

        # range request on the video (Safari needs 206)
        v = c.get(job["video_url"], headers={"range": "bytes=0-99"})
        assert v.status_code == 206 and len(v.content) == 100

        # ratings + pick feed the leaderboard
        for j in d["jobs"]:
            assert c.put(f"/api/jobs/{j['id']}/rating", json={"usable": j["model"] != "happyhorse-1.0-t2v",
                                                               "overall": 4}, headers=H).status_code == 200
        assert c.put(f"/api/runs/{run_id}/pick", json={"prompt_key": "p1", "winner_job_id": job["id"]},
                     headers=H).status_code == 200
        lb = c.get("/api/leaderboard").json()
        assert {m["model"] for m in lb["models"]} == set(statuses)
        assert any("best_quality" in m["badges"] for m in lb["models"])
        lb_ratio = c.get("/api/leaderboard", params={"ratio": "21:9"}).json()
        veo = next(m for m in lb_ratio["models"] if m["model"] == "veo-3.1-fast-generate-preview")
        assert veo["eligible"] is False and "no 21:9" in veo["why_not"]
        hh = next(m for m in lb_ratio["models"] if m["model"] == "happyhorse-1.0-t2v")
        assert hh["eligible"] is True

        csv = c.get(f"/api/runs/{run_id}/export", params={"format": "csv"})
        assert csv.status_code == 200 and "seedance-2.0-mini" in csv.text
        ledger = c.get("/api/ledger").json()
        assert ledger["spent_usd"] > 0 and "judge" in ledger["by_kind"]


def test_business_errors_and_blocked_jobs(mock_settings):
    app = create_app(mock_settings)
    with TestClient(app) as c:
        body = run_request(["seedance-2.0-mini", "wan2.7-videoedit"])
        body["prompts"][0]["text"] = "[reject] something"
        r = c.post("/api/runs", json=body, headers=H)
        assert r.status_code == 200, r.text
        d = wait_run(c, r.json()["run"]["id"])
        by = {j["model"]: j for j in d["jobs"]}
        assert by["seedance-2.0-mini"]["status"] == "failed"
        assert "moderation" in by["seedance-2.0-mini"]["fail_reason"]
        assert by["wan2.7-videoedit"]["status"] == "blocked"      # t2v unsupported
        # generation failure reported by the gateway
        body = run_request(["seedance-2.0-mini"])
        body["prompts"][0]["text"] = "[fail] please"
        d = wait_run(c, c.post("/api/runs", json=body, headers=H).json()["run"]["id"])
        assert d["jobs"][0]["status"] == "failed" and d["jobs"][0]["fail_reason"]


def test_confirmation_required_above_threshold(mock_settings):
    app = create_app(mock_settings)
    with TestClient(app) as c:
        body = run_request(["veo-3.1-generate-preview"] * 1)
        body["params"]["duration"] = 8
        body["params"]["resolution"] = "4k"
        body["confirm"] = False
        r = c.post("/api/runs", json=body, headers=H)
        assert r.status_code == 409 and r.json()["needs_confirmation"]


def test_trusted_host_blocks_rebinding(mock_settings):
    app = create_app(mock_settings)
    with TestClient(app, base_url="http://evil.example") as c:
        assert c.get("/api/config").status_code == 400


def test_restart_resumes_polling(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("TOKENBAY_BASE_URL=http://mock.tokenbay.local\nTOKENBAY_API_KEY=sk-test\n")
    monkeypatch.delenv("VGEN_MOCK", raising=False)
    monkeypatch.setenv("VGEN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VGEN_POLL_MIN_S", "0.5")
    monkeypatch.setenv("VGEN_FIRST_POLL_CAP_S", "0.2")
    settings = load_settings(env)
    shared = MockGateway(Registry.load(), queued_s=1.0, running_s=4.0)

    app1 = create_app(settings, transport_factory=lambda key: shared.transport())
    with TestClient(app1) as c:
        run_id = c.post("/api/runs", json=run_request(["seedance-2.0-mini"]), headers=H).json()["run"]["id"]
        deadline = time.time() + 20
        while time.time() < deadline:
            job = c.get(f"/api/runs/{run_id}").json()["jobs"][0]
            if job["task_id"]:
                break
            time.sleep(0.2)
        assert job["task_id"] and job["status"] in ("queued", "in_progress")
    # app stopped mid-generation; a new process picks the task up again
    app2 = create_app(settings, transport_factory=lambda key: shared.transport())
    with TestClient(app2) as c:
        d = wait_run(c, run_id, timeout=60)
        assert d["jobs"][0]["status"] == "succeeded"
        assert d["jobs"][0]["task_id"] == job["task_id"]


def test_settings_update_and_validation(mock_settings):
    app = create_app(mock_settings)
    with TestClient(app) as c:
        r = c.put("/api/settings", json={"budget_usd": 12.5, "usd_cny": 7.1, "judge_model": "gemini-3.5-flash"},
                  headers=H)
        assert r.status_code == 200, r.text
        cfg = c.get("/api/config").json()
        assert cfg["budget_usd"] == 12.5 and cfg["judge_model"] == "gemini-3.5-flash"
        assert c.put("/api/settings", json={"judge_model": "nope"}, headers=H).status_code == 400
        assert c.put("/api/settings", json={"rpm": 500}, headers=H).status_code == 400
    # overrides persist across restarts
    with TestClient(create_app(mock_settings)) as c:
        assert c.get("/api/config").json()["budget_usd"] == 12.5
