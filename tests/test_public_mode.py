"""Public (hosted) mode: visitors generate with their own Tokenbay keys; the operator's key is never used."""

from __future__ import annotations

import io
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from vgen import db
from vgen.api import VISITOR_COOKIE, create_app
from vgen.catalog.registry import Registry
from vgen.cli import relocate_db
from vgen.config import load_settings
from vgen.mock_gateway import MockGateway
from vgen.security import LAUNCH_TOKEN

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
        while time.time() < deadline and not c.get(f"/api/runs/{run_id}").json()["jobs"][0]["task_id"]:
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
