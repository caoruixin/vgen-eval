"""Publish verified report revisions without re-importing experiment or visitor data."""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

from vgen.db import Experiment, session
from vgen.experiments import IDENTIFIER, digest, safe_path

REPORT_FILE = re.compile(r"report\.(?:md|docx|html)|provenance\.json|figures/[A-Za-z0-9_.-]+\.(?:png|svg|json)")


def prepare_report(manifest: Path) -> dict:
    data = json.loads(manifest.read_text())
    if data.get("schema_version") != 1:
        raise ValueError("unsupported report revision schema")
    for field in ("experiment_id", "revision"):
        if not IDENTIFIER.fullmatch(data.get(field, "")):
            raise ValueError(f"invalid {field}")
    if set(data.get("source_artifacts", {})) != {"report.md", "report.docx"}:
        raise ValueError("source report hashes are required")
    root = manifest.parent.resolve()
    names = set()
    for row in data.get("files", []):
        name = row["path"]
        if not REPORT_FILE.fullmatch(name) or name in names:
            raise ValueError(f"invalid or duplicate report file: {name}")
        path = safe_path(root, name)
        if not path.is_file() or path.is_symlink() or path.stat().st_size != row["size"] or digest(path) != row["sha256"]:
            raise ValueError(f"report verification failed: {name}")
        names.add(name)
    if not {"report.md", "report.docx", "report.html"}.issubset(names):
        raise ValueError("report revision requires Markdown, Word and HTML")
    return {"manifest": data, "root": root}


def publish_report(prepared: dict, data_dir: Path) -> dict:
    m, root = prepared["manifest"], prepared["root"]
    with session() as s:
        experiment = s.get(Experiment, m["experiment_id"])
        if experiment is None:
            raise ValueError("report experiment does not exist")
        original = dict(experiment.artifacts)
        for name, expected in m["source_artifacts"].items():
            path = original.get(f"archive-{name}", original.get(name))
            if not path or not Path(path).is_file() or digest(Path(path)) != expected:
                raise ValueError(f"original report mismatch: {name}")
        destination = data_dir / "reports" / m["experiment_id"] / m["revision"]
        if destination.exists():
            stored = destination / "revision.json"
            if not stored.is_file() or json.loads(stored.read_text()) != m:
                raise ValueError("report revision already exists with different content")
            for row in m["files"]:
                path = safe_path(destination, row["path"])
                if not path.is_file() or path.stat().st_size != row["size"] or digest(path) != row["sha256"]:
                    raise ValueError(f"published report verification failed: {row['path']}")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix=".report-", dir=destination.parent))
            try:
                for row in m["files"]:
                    target = safe_path(stage, row["path"])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(safe_path(root, row["path"]), target)
                    if target.stat().st_size != row["size"] or digest(target) != row["sha256"]:
                        raise ValueError(f"report changed during copy: {row['path']}")
                (stage / "revision.json").write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n")
                stage.rename(destination)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        artifacts = dict(original)
        for name in m["source_artifacts"]:
            artifacts.setdefault(f"archive-{name}", original[name])
        for row in m["files"]:
            artifacts[row["path"]] = str(destination / row["path"])
        artifacts["report-revision.json"] = str(destination / "revision.json")
        result = {"experiment_id": experiment.id, "revision": m["revision"], "files": len(m["files"])}
        if artifacts == original:
            return {**result, "already_published": True}
        # Consistent backup includes WAL, with only this experiment's artifact
        # references changed afterwards. Jobs, ratings and ledger remain intact.
        db_path = data_dir / "app.db"
        backup = db_path.with_name(f"{db_path.name}.pre-report-{time.time_ns()}.bak")
        with sqlite3.connect(db_path) as source, sqlite3.connect(backup) as target:
            source.backup(target)
        experiment.artifacts = artifacts
        s.add(experiment)
        s.commit()
        return {**result, "already_published": False, "backup": str(backup)}
