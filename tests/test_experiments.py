"""Archive import, comparison identity, authorization, and metric regression tests (no network)."""
from __future__ import annotations

import copy
import json
import sqlite3
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from vgen import db
from vgen.api import create_app
from vgen.catalog.registry import Registry
from vgen.db import BenchmarkAttempt, EvaluationCase, Experiment, Job, LedgerEntry, Run, session
from vgen.experiments import case_fingerprint, import_prepared, package_experiment, prepare, spec_fingerprint
from vgen.report_revisions import prepare_report, publish_report
from vgen.security import LAUNCH_TOKEN

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "experiments/h3-pro6000.json"
H = {"x-vgen-token": LAUNCH_TOKEN}


@pytest.fixture(scope="module")
def prepared():
    return prepare(MANIFEST)


@pytest.fixture(scope="module")
def archive(tmp_path_factory, prepared):
    root = tmp_path_factory.mktemp("imported-archive")
    db.init_db(root / "app.db")
    result = import_prepared(prepared, root)
    assert result["videos"] == 87
    yield root


@pytest.fixture()
def imported(archive, mock_settings):
    # Copy only mutable state. Immutable media remains shared and verified.
    root = mock_settings.data_dir
    root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(archive / "app.db") as source, sqlite3.connect(root / "app.db") as dest:
        source.backup(dest)
    (root / "experiments").symlink_to(archive / "experiments", target_is_directory=True)
    db.init_db(root / "app.db")
    return mock_settings


def test_identity_is_exact_and_role_sensitive():
    a = case_fingerprint("fl2va", "Same prompt", {"first_frame": "abc"})
    assert a == case_fingerprint("i2v", "Same prompt", {"first_frame": {"asset_id": "abc", "role": "first_frame"}})
    assert a != case_fingerprint("i2v", "Same prompt ", {"first_frame": "abc"})
    assert a != case_fingerprint("i2v", "Same prompt", {"first_frame": "def"})
    assert a != case_fingerprint("i2v", "Same prompt", {"last_frame": "abc"})
    assert case_fingerprint("r2v", "p", {"references": ["a", "b"]}) != case_fingerprint("r2v", "p", {"references": ["b", "a"]})


def test_archive_counts_preferred_samples_and_idempotency(imported, prepared):
    with session() as s:
        before = [j.id for j in s.exec(select(Job))]
    result = import_prepared(prepared, imported.data_dir)
    assert result["already_imported"] and result["attempts"] == 89 and result["cases"] == 10
    with session() as s:
        assert [j.id for j in s.exec(select(Job))] == before
        assert not list(s.exec(select(LedgerEntry)))
        counts = Counter((a.metrics["phase"], a.metrics["outcome"]) for a in s.exec(select(BenchmarkAttempt)))
        assert counts[("baseline", "technical_valid")] == 42
        assert counts[("stability", "technical_valid")] == 19
        assert counts[("stability", "test_window_cancelled")] == 1
        assert counts[("comparison", "technical_valid")] == 10
        assert counts[("turbo", "technical_valid")] == 6
        assert counts[("concurrency_c2", "technical_valid")] + counts[("concurrency_c4", "technical_valid")] == 6
    with TestClient(create_app(imported)) as c:
        for case in prepared["cases"]:
            d = c.get(f"/api/evaluations/h3-v1/cases/{case.key}").json()
            assert len(d["groups"]) == 2
            for group in d["groups"]:
                chosen = group["samples"][0]
                assert chosen["provenance"]["preferred"]
                assert chosen["rating"] is None
                assert c.get(chosen["video_url"], headers={"range": "bytes=0-99"}).status_code == 206
        b = c.get("/api/benchmarks/h3-pro6000-2026-11-07").json()
        baseline = next(p for p in b["summary"]["phases"] if p["phase"] == "baseline")
        assert baseline["throughput_success_per_hour"] == pytest.approx(19.7445799164)
        assert baseline["cost_per_technical_success_usd"] == pytest.approx(.70933671999)
        assert all(g["deliverable_rate"] is None for g in b["summary"]["groups"])
        assert c.get("/api/benchmarks/h3-pro6000-2026-11-07/export?format=csv").text.startswith("\ufeffexperiment_id")
        assert c.get("/api/benchmarks/h3-pro6000-2026-11-07/export?format=json").status_code == 200
        assert c.get("/api/benchmarks/h3-pro6000-2026-11-07/artifacts/secret.env").status_code == 404
        assert c.get("/api/leaderboard").json()["clips"] == 0


def test_imported_results_cannot_schedule_or_bill(imported):
    with TestClient(create_app(imported)) as c:
        detail = c.get("/api/evaluations/h3-v1/cases/T01").json()
        job = detail["groups"][0]["samples"][0]
        for operation in ("stop", "repoll", "resubmit", "judge"):
            assert c.post(f"/api/jobs/{job['id']}/{operation}", json={"confirm": True}, headers=H).status_code == 409
        assert c.post(f"/api/runs/{job['run_id']}/rerun", json={"confirm": True}, headers=H).status_code == 409
        assert c.delete(f"/api/runs/{job['run_id']}", headers=H).status_code == 409
        assert c.put(f"/api/jobs/{job['id']}/rating", json={"overall": 4}, headers=H).status_code == 200
        # Even corrupt pending state must not be picked up after restart or by the engine loop.
        db.update_job(job["id"], status=db.PENDING, judge_status="pending")
        c.app.state.vgen.engine.recover()
        c.app.state.vgen.engine._tick()
        for fn in ("repoll", "resubmit", "rejudge", "stop_job"):
            with pytest.raises(ValueError):
                getattr(c.app.state.vgen.engine, fn)(job["id"])
        assert not any(path.startswith("/v1/videos") for path, _ in c.app.state.vgen.mock.auth)
        assert c.get("/api/ledger").json()["spent_usd"] == 0


def test_inference_step_evidence_survives_legacy_imports_without_rewriting(imported):
    fields = ("requested_num_inference_steps", "actual_denoise_updates",
              "actual_denoise_updates_source", "variant_evidence_scope")
    with session() as s:
        jobs = list(s.exec(select(Job).where(Job.origin == "imported")))
        baseline = next(j for j in jobs if j.provenance["phase"] == "baseline")
        assert baseline.provenance["requested_num_inference_steps"] == 50
        assert baseline.provenance["actual_denoise_updates"] == 50
        # Simulate a deployed archive imported before steps were exposed on Job.
        for j in jobs:
            j.provenance = {k: v for k, v in j.provenance.items() if k not in fields}
            s.add(j)
        s.commit()
    with TestClient(create_app(imported)) as c:
        eight_step_cases = set()
        for case in ("T01", "T02", "T03", "T04", "T05", "T06", "F01", "F02", "F03", "F04"):
            d = c.get(f"/api/evaluations/h3-v1/cases/{case}").json()
            for group in d["groups"]:
                for j in group["samples"]:
                    p = j["provenance"]
                    if group["provider"] == "official_api":
                        assert p["requested_num_inference_steps"] is None
                        assert p["actual_denoise_updates"] is None
                    elif p["phase"] == "turbo":
                        assert p["requested_num_inference_steps"] == p["actual_denoise_updates"] == 8
                        assert p["actual_denoise_updates_source"].endswith("/server-progress.txt")
                        assert p["turbo_activation_verified"] is False
                        eight_step_cases.add(case)
                    else:
                        assert p["requested_num_inference_steps"] == 50
                        if p["phase"].startswith("concurrency_"):
                            # Overlapping progress counters cannot prove per-request updates.
                            assert p["actual_denoise_updates"] is None
            if case == "F02":
                self_group = next(g for g in d["groups"] if g["provider"] == "self")
                chosen = next(j for j in self_group["samples"] if j["id"] == self_group["default_job_id"])
                assert chosen["provenance"]["phase"] == "baseline"
                assert chosen["provenance"]["requested_num_inference_steps"] == 50
        assert eight_step_cases == {"T01", "T04", "T06", "F01", "F02", "F03"}
    with session() as s:
        assert all(not any(f in j.provenance for f in fields) for j in s.exec(select(Job)))
        assert len(list(s.exec(select(Job)))) == 87
        assert not list(s.exec(select(LedgerEntry)))


def test_public_scope_matching_and_unknown_cost(imported):
    visitor = "visitor-a-0123456789abcdef"
    with session() as s:
        case = s.get(EvaluationCase, "h3-v1:T01")
        # Same input is matched across runs; unrelated text with the same key must never match.
        for id_, owner, prompt in (("own", visitor, case.prompt), ("hidden", "visitor-b-0123456789abcdef", case.prompt),
                                   ("unrelated", None, "Another prompt")):
            spec = {"task": "t2v", "prompt": prompt, "duration": 5, "audio": True}
            s.add(Run(id=id_, owner=owner, title=id_, prompts=[{"key": "T01", "text": prompt}], models=["seedance-2.0"]))
            s.add(Job(id=id_, run_id=id_, prompt_key="T01", model="seedance-2.0", status=db.SUCCEEDED,
                      spec=spec, case_fingerprint=spec_fingerprint(spec)))
        archive_job = s.exec(select(Job).where(Job.origin == "imported")).first()
        archive_job.provenance = {**archive_job.provenance, "cost_usd": None}
        s.add(archive_job)
        s.commit()
    with TestClient(create_app(replace(imported, public=True))) as c:
        c.get("/")
        c.cookies.set("vgen_vid", visitor)
        d = c.get("/api/evaluations/h3-v1/cases/T01").json()
        jobs = [j for g in d["groups"] for j in g["samples"]]
        assert "own" in {j["id"] for j in jobs}
        assert "hidden" not in {j["id"] for j in jobs}
        assert "unrelated" not in {j["id"] for j in jobs}
        assert not next(j for j in jobs if j["origin"] == "imported")["editable"]
        assert c.get("/api/runs/hidden").status_code == 404
        assert c.get("/api/jobs/hidden/video").status_code == 404
        assert c.put(f"/api/jobs/{archive_job.id}/rating", json={"overall": 4}, headers=H).status_code == 403
        out = c.get(f"/api/runs/{archive_job.run_id}").json()
        item = next(j for j in out["jobs"] if j["id"] == archive_job.id)
        assert item["cost"]["estimate_usd"] is None and item["cost"]["per_second"] is None
        assert out["spent_usd"] is None


def test_backfill_plan_preserves_inputs_and_does_not_submit(imported, monkeypatch):
    monkeypatch.setenv("VGEN_PROBE_STATUS", str(ROOT / "vgen/catalog/probe_status.json"))
    with TestClient(create_app(imported)) as c:
        plan = c.get("/api/evaluations/h3-v1/backfill-plan").json()
        assert plan["runnable"] == 66 and plan["total_usd"] == pytest.approx(42.904, abs=.001)
        assert not plan["submitted"] and not plan["judge"]
        with session() as s:
            assert len(list(s.exec(select(Job)))) == 87
            assert not list(s.exec(select(LedgerEntry)))
        packs = [p for p in c.get("/api/scenarios").json()["packs"] if p["id"].startswith("h3-v1")]
        assert sorted(len(p["prompts"]) for p in packs) == [4, 6]
        for pack in packs:
            assert pack["judge"] is False and pack["params"]["audio"] is True
            for prompt in pack["prompts"]:
                with session() as s:
                    case = s.get(EvaluationCase, f"h3-v1:{prompt['key']}")
                assert prompt["text"]["en"] == prompt["text"]["zh"] == case.prompt
                assert prompt["assets"] == case.assets
        assert not any(path.startswith("/v1/videos") for path, _ in c.app.state.vgen.mock.auth)
        # A newly verified experimental capability must enter the plan without a code change.
        c.app.state.vgen.registry.models["wan3.0"].probe = {"tasks": {"i2v": "ok"}}
        refreshed = c.get("/api/evaluations/h3-v1/backfill-plan").json()
        assert len([i for i in refreshed["items"] if i["model"] == "wan3.0" and i["task"] == "i2v" and i["ready"]]) == 4


def test_portable_bundle_detects_damage_and_rejects_conflict(tmp_path, prepared, imported):
    package = tmp_path / "bundle"
    package_experiment(prepared, package)
    manifest = package / "experiment.json"
    assert prepare(manifest)["content_sha"] == prepared["content_sha"]
    file = package / "payload/cases/cases.json"
    original = file.read_bytes()
    file.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="verification failed"):
        prepare(manifest)
    file.unlink()
    with pytest.raises(ValueError, match="verification failed"):
        prepare(manifest)
    file.write_bytes(original)
    changed = copy.deepcopy(prepared)
    changed["content_sha"] = "different"
    with pytest.raises(ValueError, match="different content"):
        import_prepared(changed, imported.data_dir)
    assert len(list(imported.data_dir.glob(".import-*"))) == 0


def test_future_platform_uses_same_importer(tmp_path, prepared):
    manifest = copy.deepcopy(prepared["manifest"])
    manifest["id"] = "h3-pro5500-test-only"
    manifest["source_root"] = str(prepared["root"])
    manifest["title"] = "Synthetic PRO5500 import fixture (not measured)"
    manifest["deployments"]["self"].update(id="h3-pro5500", label="H3 · PRO5500 test fixture", hardware={"gpu": "PRO5500", "gpu_count": 8})
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(manifest))
    p = prepare(path)
    target = tmp_path / "data"
    db.init_db(target / "app.db")
    import_prepared(p, target)
    with session() as s:
        assert len(list(s.exec(select(Job).where(Job.deployment_id == "h3-pro5500")))) == 77
        assert s.get(Experiment, manifest["id"]).title.startswith("Synthetic")


def test_old_database_is_backed_up_before_migration(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as c:
        c.execute("create table job (id varchar primary key, status varchar)")
        c.execute("insert into job values ('existing', 'succeeded')")
    db.init_db(path)
    backups = list(tmp_path.glob("legacy.db.pre-migration-*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as c:
        assert c.execute("select id from job").fetchone()[0] == "existing"
        assert "origin" not in {r[1] for r in c.execute("pragma table_info(job)")}
    with sqlite3.connect(path) as c:
        assert c.execute("select id, origin from job").fetchone() == ("existing", "generated")
    db.init_db(path)
    assert len(list(tmp_path.glob("legacy.db.pre-migration-*.bak"))) == 1


def report_fixture(tmp_path, imported):
    from vgen.experiments import digest
    with session() as s:
        experiment = s.get(Experiment, "h3-pro6000-2026-11-07")
        sources = {name: digest(Path(experiment.artifacts[name])) for name in ("report.md", "report.docx")}
    root = tmp_path / "report-revision"
    root.mkdir()
    contents = {"report.md": "# H3 inference\n", "report.docx": "Word fixture",
                "report.html": "<!doctype html><html><body><h1>H3 inference</h1></body></html>",
                "figures/flow.svg": '<svg xmlns="http://www.w3.org/2000/svg"><title>Flow</title></svg>'}
    rows = []
    for name, text in contents.items():
        path = root / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        rows.append({"path": name, "size": path.stat().st_size, "sha256": digest(path)})
    manifest = root / "revision.json"
    manifest.write_text(json.dumps({"schema_version": 1, "experiment_id": experiment.id,
        "revision": "architecture-test", "source_artifacts": sources, "files": rows}))
    return manifest


def test_report_publication_preserves_experiment_and_visitor_data(imported, tmp_path):
    manifest = report_fixture(tmp_path, imported)
    with session() as s:
        before = s.get(Experiment, "h3-pro6000-2026-11-07").model_dump()
        jobs = [j.model_dump() for j in s.exec(select(Job))]
        attempts = [a.model_dump() for a in s.exec(select(BenchmarkAttempt))]
    p = prepare_report(manifest)
    result = publish_report(p, imported.data_dir)
    assert not result["already_published"] and Path(result["backup"]).is_file()
    assert publish_report(p, imported.data_dir)["already_published"]
    assert len(list(imported.data_dir.glob("*.pre-report-*.bak"))) == 1
    with session() as s:
        after = s.get(Experiment, before["id"]).model_dump()
        assert {k: v for k, v in before.items() if k != "artifacts"} == {k: v for k, v in after.items() if k != "artifacts"}
        assert after["artifacts"]["archive-report.docx"] == before["artifacts"]["report.docx"]
        assert [j.model_dump() for j in s.exec(select(Job))] == jobs
        assert [a.model_dump() for a in s.exec(select(BenchmarkAttempt))] == attempts
        assert not list(s.exec(select(LedgerEntry)))
    with TestClient(create_app(replace(imported, public=True))) as c:
        report = c.get(f"/api/benchmarks/{before['id']}/artifacts/report.html")
        assert report.status_code == 200 and report.headers["content-type"].startswith("text/html")
        assert report.headers["content-disposition"].startswith("inline;")
        assert "script-src 'none'" in report.headers["content-security-policy"]
        assert "default-src 'none'" in report.headers["content-security-policy"]
        base = f"/api/benchmarks/{before['id']}/artifacts/"
        assert c.get(base + "figures/flow.svg").status_code == 200
        assert c.get(base + "secret.env").status_code == 404
        assert c.get(base + "figures/%2e%2e%2f%2e%2e%2fapp.db").status_code == 404
        assert c.get(base + "report.docx").headers["content-disposition"].startswith("attachment;")
    assert (Path(result["backup"])).stat().st_size > 0


def test_report_publication_rejects_damage_source_mismatch_and_conflicts(imported, tmp_path):
    manifest = report_fixture(tmp_path, imported)
    p = prepare_report(manifest)
    bad = copy.deepcopy(p)
    bad["manifest"]["source_artifacts"]["report.docx"] = "0" * 64
    with pytest.raises(ValueError, match="original report mismatch"):
        publish_report(bad, imported.data_dir)
    assert not (imported.data_dir / "reports").exists()
    publish_report(p, imported.data_dir)
    (manifest.parent / "report.md").write_text("corrupt")
    with pytest.raises(ValueError, match="verification failed"):
        prepare_report(manifest)
    bad = copy.deepcopy(p)
    bad["manifest"]["files"][0]["sha256"] = "1" * 64
    with pytest.raises(ValueError, match="different content"):
        publish_report(bad, imported.data_dir)
    stored = imported.data_dir / "reports" / p["manifest"]["experiment_id"] / p["manifest"]["revision"]
    (stored / "report.html").write_text("damaged after publication")
    with pytest.raises(ValueError, match="published report verification failed"):
        publish_report(p, imported.data_dir)
