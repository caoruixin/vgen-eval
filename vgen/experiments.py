"""Verified, offline experiment imports. No gateway, credentials, or generation code runs here."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlmodel import select

from vgen import db
from vgen.db import Asset, BenchmarkAttempt, Deployment, EvaluationCase, Experiment, Job, Run, session

TASKS = {"t2va": "t2v", "fl2va": "i2v", "t2v": "t2v", "i2v": "i2v"}
IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}")
NUMBERS = set("attempt seed e2e_seconds submit_to_terminal_seconds server_inference_time_s server_peak_memory_mb "
              "poll_interval_seconds poll_detection_bound_seconds requested_num_inference_steps actual_denoise_updates "
              "api_reserved_usd api_cost_estimate_usd estimated_cost_usd estimated_cost_per_video_second_usd "
              "actual_settled_cost_usd width height fps frames duration_seconds video_duration_seconds "
              "audio_duration_seconds audio_channels audio_sample_rate size_bytes probe_exit_code decode_exit_code "
              "validation_seconds http_status baseline_n baseline_p50_s baseline_p95_s "
              "baseline_min_s baseline_max_s matched_baseline_seed matched_baseline_e2e_s official_e2e_s utilization "
              "observed_baseline_success_interval_s gpu_disk_cost_per_success_usd n_submitted_attempts n_technical_valid "
              "e2e_p50_s e2e_p95_s e2e_min_s e2e_max_s n_human_reviewed deliverable_rate observed_phase_windows "
              "window_seconds throughput_success_per_hour phase_gpu_disk_cost_estimate_usd cost_per_technical_success_usd "
              "http_download_transfer_seconds prompt_adherence_1to5 visual_motion_1to5 first_frame_retention_1to5 "
              "audio_1to5 av_sync_1to5".split())
BOOLS = {"submitted", "technical_valid", "complete_for_requested_matrix", "turbo_activation_verified",
         "local_preexisting_at_observer_start"}
INFERENCE_EVIDENCE_FIELDS = ("requested_num_inference_steps", "actual_denoise_updates",
                             "actual_denoise_updates_source", "variant_evidence_scope")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def stable_id(prefix: str, *parts: Any) -> str:
    return prefix + "_" + hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:24]


def case_fingerprint(task: str, prompt: str, assets: dict | None = None) -> str:
    """Exact text bytes and ordered, role-specific asset hashes. Specs intentionally excluded."""
    refs = []
    for role, value in sorted((assets or {}).items()):
        if not value:
            continue
        values = value if isinstance(value, list) else [value]
        for i, item in enumerate(values):
            sha = item.get("asset_id") if isinstance(item, dict) else item
            if sha:
                refs.append([role, i, sha])
    identity = [TASKS.get(task, task), hashlib.sha256(prompt.encode("utf-8")).hexdigest(), refs]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def spec_fingerprint(spec: dict) -> str:
    return case_fingerprint(spec.get("task", "t2v"), spec.get("prompt", ""), {
        k: spec[k] for k in ("first_frame", "last_frame", "references", "source_video", "audio_input") if spec.get(k)
    })


def safe_path(root: Path, relative: str) -> Path:
    p = (root / relative).resolve()
    if not p.is_relative_to(root.resolve()) or Path(relative).is_absolute():
        raise ValueError(f"path escapes experiment root: {relative}")
    return p


def typed(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if v in ("", "NA", "N/A") or v is None:
            out[k] = None
        elif k in BOOLS:
            if str(v).lower() not in ("true", "false"):
                raise ValueError(f"invalid boolean {k}: {v}")
            out[k] = str(v).lower() == "true"
        elif k in NUMBERS:
            n = float(v)
            if not math.isfinite(n):
                raise ValueError(f"nonfinite metric {k}")
            out[k] = int(n) if n.is_integer() else n
        elif k in ("outcomes", "stage_durations", "versions_json", "usage_json", "spec_deviations"):
            out[k] = json.loads(v)
        else:
            out[k] = v
    return out


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [typed(row) for row in csv.DictReader(f)]


def prepare(manifest: Path) -> dict:
    """Full preflight, including all packaged hashes, before any DB or output mutation."""
    m = json.loads(manifest.read_text())
    if m.get("schema_version") != 1:
        raise ValueError("unsupported experiment manifest schema")
    for value in [m["id"], m["dataset"]["id"], *(d["id"] for d in m["deployments"].values())]:
        if not IDENTIFIER.fullmatch(value):
            raise ValueError(f"invalid identifier: {value}")
    root = (manifest.parent / m["source_root"]).resolve()
    package = json.loads(safe_path(root, m["files"]["package_manifest"]).read_text())
    verified = {}
    for item in package["files"]:
        rel = item["path"]
        p = safe_path(root, rel)
        if rel in verified or not p.is_file() or p.stat().st_size != item["packaged_size"] or digest(p) != item["packaged_sha256"]:
            raise ValueError(f"package verification failed: {rel}")
        verified[rel] = item["packaged_sha256"]
    used = set()

    def need(rel: str) -> Path:
        if rel not in verified:
            raise ValueError(f"required file is not in verified manifest: {rel}")
        used.add(rel)
        return safe_path(root, rel)

    def read(name: str):
        p = need(m["files"][name])
        return read_csv(p) if p.suffix == ".csv" else json.loads(p.read_text())

    config = read("config")
    cases = read("cases")["cases"]
    assets = read("assets")["assets"]
    by_asset = {a["case_id"]: a for a in assets}
    case_objects = []
    for c in cases:
        task = TASKS[c["task"]]
        a = by_asset.get(c["id"])
        refs = {}
        if a:
            if digest(need(a["file"])) != a["sha256"]:
                raise ValueError(f"input hash mismatch: {c['id']}")
            refs["first_frame"] = a["sha256"]
        if task == "i2v" and not a:
            raise ValueError(f"missing first frame: {c['id']}")
        case_objects.append(EvaluationCase(id=f"{m['dataset']['id']}:{c['id']}", dataset_id=m["dataset"]["id"],
            dataset_title=m["dataset"]["title"], key=c["id"], title=c["title"], task=task, prompt=c["prompt"],
            fingerprint=case_fingerprint(task, c["prompt"], refs), checklist=c.get("checklist", []), assets=refs))
    case_map = {c.key: c for c in case_objects}
    if len(case_map) != len(cases):
        raise ValueError("duplicate case id")
    rows = read("attempts")
    summary = {name: read(name) for name in ("groups", "phases", "scenes", "telemetry", "costs", "utilization")}
    artifact_paths = dict(m.get("artifacts", {}))
    # Serve only explicitly declared artifacts, never the entire source directory.
    for rel in artifact_paths.values():
        need(rel)
    for rel in m.get("support_files", []):
        need(rel)
    preferred = {r["matched_baseline_request_id"] for r in summary["scenes"] if r.get("matched_baseline_request_id")}
    identities = set()
    for row in rows:
        identity = (row["provider"], row["request_id"], row["attempt"])
        if identity in identities:
            raise ValueError(f"duplicate attempt: {identity}")
        identities.add(identity)
        c = case_map[row["case_id"]]
        if row["provider"] not in m["deployments"]:
            raise ValueError(f"unknown deployment provider: {row['provider']}")
        if TASKS[row["task"]] != c.task or row["prompt_sha256"] != hashlib.sha256(c.prompt.encode()).hexdigest():
            raise ValueError(f"prompt/task mismatch: {row['request_id']}")
        if row.get("asset_sha256") != c.assets.get("first_frame"):
            raise ValueError(f"first-frame mismatch: {row['request_id']}")
        if row["technical_valid"]:
            rel = f"{m['deployments'][row['provider']]['output_root']}/{row['video_path']}"
            if digest(need(rel)) != row["sha256"]:
                raise ValueError(f"media hash mismatch: {row['request_id']}")
            row["media_relative_path"] = rel
        row["preferred"] = row["request_id"] in preferred or row["provider"] == "official_api"
    valid_requests = {r["request_id"] for r in rows if r["technical_valid"]}
    if not preferred.issubset(valid_requests):
        raise ValueError("preferred baseline sample missing")
    # Identity is portable across full archive, incremental package and destination path.
    identity_manifest = {k: v for k, v in m.items() if k != "source_root"}
    content_sha = hashlib.sha256(json.dumps([identity_manifest, sorted((p, verified[p]) for p in used)],
                                           sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"manifest": m, "root": root, "verified": verified, "used": used, "cases": case_objects,
            "assets": assets, "rows": rows, "config": config, "summary": summary, "content_sha": content_sha}


def preview(prepared: dict) -> dict:
    return {"id": prepared["manifest"]["id"], "verified_files": len(prepared["verified"]),
            "cases": len(prepared["cases"]), "attempts": len(prepared["rows"]),
            "videos": sum(bool(r["technical_valid"]) for r in prepared["rows"]),
            "content_sha256": prepared["content_sha"], "paid_requests": 0}


def _timestamp(value: str | None) -> float | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() if value else None


def _poster(video: Path, output: Path) -> None:
    import av
    with av.open(str(video)) as container:
        frame = next(container.decode(video=0))
        im = frame.to_image()
        im.thumbnail((640, 360))
        im.save(output, "JPEG", quality=85)


def import_prepared(p: dict, data_dir: Path) -> dict:
    """Caller owns the engine lock. All source checks and conflicts precede writes."""
    m = p["manifest"]
    nominal_seconds = p["config"].get("nominal_seconds", 5)
    requested_resolution = f'{p["config"].get("height", 768)}p'
    deployments = [Deployment(id=d["id"], model=d["model"], label=d["label"], provider=provider,
        hardware=d.get("hardware", {}), timing_scope=d["timing_scope"]) for provider, d in m["deployments"].items()]
    destination = data_dir.resolve() / "experiments" / m["id"]
    with session() as s:
        existing = s.get(Experiment, m["id"])
        if existing:
            if existing.content_sha256 != p["content_sha"]:
                raise ValueError("experiment id already exists with different content; use a new experiment id")
            for rel in p["used"]:
                if not (destination / rel).is_file() or digest(destination / rel) != p["verified"][rel]:
                    raise ValueError(f"imported file missing or changed: {rel}")
            return {**preview(p), "already_imported": True}
        for cls, objects in ((Deployment, deployments), (EvaluationCase, p["cases"])):
            for obj in objects:
                old = s.get(cls, obj.id)
                if old and old.model_dump() != obj.model_dump():
                    raise ValueError(f"conflicting {cls.__name__}: {obj.id}")
        for a in p["assets"]:
            old = s.get(Asset, a["sha256"])
            if old and (old.owner is not None or not Path(old.local_path).is_file() or digest(Path(old.local_path)) != a["sha256"]):
                raise ValueError(f"conflicting or unavailable existing input asset: {a['sha256']}")
    if destination.exists():
        raise ValueError(f"unregistered destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".import-", dir=destination.parent))
    moved = False
    try:
        for rel in p["used"]:
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p["root"] / rel, target)
            if digest(target) != p["verified"][rel]:
                raise ValueError(f"copy verification failed: {rel}")
        (stage / "posters").mkdir()
        jobs, attempts, imported_runs = [], [], {}
        cases = {c.key: c for c in p["cases"]}
        phase_cost = {(r["provider"], r["phase"]): r.get("cost_per_technical_success_usd") for r in p["summary"]["phases"]}
        for row in p["rows"]:
            deploy = m["deployments"][row["provider"]]
            case = cases[row["case_id"]]
            job_id = stable_id("import", m["id"], row["provider"], row["request_id"], row["attempt"]) if row["technical_valid"] else None
            if job_id:
                run_id = stable_id("experiment", m["id"], case.task)
                if run_id not in imported_runs:
                    matching = [c for c in p["cases"] if c.task == case.task]
                    imported_runs[run_id] = Run(id=run_id, created_at=_timestamp(row["submitted_utc"]) or db.now(),
                        title=f"{m['title']} · {case.task}", kind="experiment", scenario_id=m["dataset"]["id"],
                        task=case.task, lang="zh", endpoint="archive", category="h3", models=sorted({d.model for d in deployments}),
                        params={"experiment_id": m["id"], "duration": nominal_seconds, "resolution": requested_resolution, "audio": True},
                        prompts=[{"key": c.key, "title": c.title, "text": c.prompt, "checklist": c.checklist,
                                  "assets": c.assets} for c in matching])
                cost = row.get("api_cost_estimate_usd") if row["provider"] == "official_api" else phase_cost.get((row["provider"], row["phase"]))
                basis = "official_list_price" if row["provider"] == "official_api" else "phase_resource_estimate"
                media = {"duration": row["duration_seconds"], "size_bytes": row["size_bytes"],
                    "video": {"codec": row["video_codec"], "width": row["width"], "height": row["height"], "fps": row["fps"]},
                    "audio": {"codec": row["audio_codec"], "sample_rate": row["audio_sample_rate"]} if row.get("audio_codec") else None}
                rel = row["media_relative_path"]
                poster_rel = f"posters/{job_id}.jpg"
                _poster(stage / rel, stage / poster_rel)
                provenance = {k: row.get(k) for k in ("request_id", "attempt", "phase", "variant", "seed", "preferred",
                    "e2e_seconds", "server_inference_time_s", "config_sha256", "model_revision", "turbo_activation_verified",
                    *INFERENCE_EVIDENCE_FIELDS)}
                provenance.update({"source": row["provider"], "cost_usd": cost, "cost_basis": basis,
                                   "timing_scope": deploy["timing_scope"], "original_sha256": row["sha256"]})
                spec = {"task": case.task, "prompt": case.prompt, "duration": nominal_seconds, "resolution": requested_resolution, "audio": True,
                        "seed": row["seed"], "ratio": "auto" if case.task == "i2v" else "16:9",
                        **{role: {"asset_id": sha, "role": role} for role, sha in case.assets.items()}}
                jobs.append(Job(id=job_id, run_id=run_id, prompt_key=case.key, model=deploy["model"],
                    endpoint="archive", origin="imported", case_fingerprint=case.fingerprint,
                    deployment_id=deploy["id"], experiment_id=m["id"], provenance=provenance, status=db.SUCCEEDED,
                    label=job_id[-5:].upper(), spec=spec, effective={k: spec[k] for k in ("duration", "resolution", "audio", "ratio")},
                    created_at=_timestamp(row["submitted_utc"]) or db.now(), submitted_at=_timestamp(row["submitted_utc"]),
                    done_at=_timestamp(row["downloaded_utc"]), video_path=str(destination / rel),
                    poster_path=str(destination / poster_rel), video_sha=row["sha256"], media=media, judge_status="skipped",
                    compliance=[{"key": "video_stream", "ok": True, "expected": "full decode", "actual": "passed", "note": "archive evidence"},
                                {"key": "audio", "ok": bool(media["audio"]), "expected": True, "actual": bool(media["audio"]), "note": "not a quality score"}]))
            attempts.append(BenchmarkAttempt(id=stable_id("attempt", m["id"], row["provider"], row["request_id"], row["attempt"]),
                experiment_id=m["id"], deployment_id=deploy["id"], case_id=case.id, request_id=row["request_id"],
                attempt=row["attempt"], job_id=job_id, metrics={k: v for k, v in row.items() if k != "media_relative_path"}))
        experiment = Experiment(id=m["id"], title=m["title"], date=m["date"], dataset_id=m["dataset"]["id"],
            content_sha256=p["content_sha"], deployment_ids=[d.id for d in deployments], config=p["config"],
            summary=p["summary"], artifacts={name: str(destination / rel) for name, rel in m.get("artifacts", {}).items()})
        stage.rename(destination)
        moved = True
        with session() as s:
            for obj in [*deployments, *p["cases"]]:
                if not s.get(type(obj), obj.id):
                    s.add(obj)
            for a in p["assets"]:
                if not s.get(Asset, a["sha256"]):
                    s.add(Asset(id=a["sha256"], kind="image", label=a.get("title", a["case_id"]),
                        filename=Path(a["file"]).name, mime="image/png", width=a["width"], height=a["height"],
                        size=(destination / a["file"]).stat().st_size, local_path=str(destination / a["file"]), source="experiment"))
            s.add_all([experiment, *imported_runs.values(), *jobs, *attempts])
            s.commit()
        return {**preview(p), "already_imported": False}
    except Exception:
        shutil.rmtree(destination if moved else stage, ignore_errors=True)
        raise


def package_experiment(prepared: dict, output: Path) -> dict:
    """Build a portable incremental bundle, excluding databases, credentials and unused logs."""
    if output.exists():
        raise ValueError("package output must be a new directory")
    output.mkdir(parents=True)
    m = {**prepared["manifest"], "source_root": "payload"}
    root = output / "payload"
    root.mkdir()
    files = []
    for rel in sorted(prepared["used"]):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(prepared["root"] / rel, target)
        files.append({"path": rel, "packaged_size": target.stat().st_size, "packaged_sha256": digest(target)})
    package_path = safe_path(root, m["files"]["package_manifest"])
    package_path.parent.mkdir(parents=True, exist_ok=True)
    package_path.write_text(json.dumps({"files": files}, indent=2))
    manifest = output / "experiment.json"
    manifest.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n")
    checked = prepare(manifest)
    if checked["content_sha"] != prepared["content_sha"]:
        raise ValueError("portable package content identity changed")
    return {**preview(checked), "manifest": str(manifest.resolve())}
