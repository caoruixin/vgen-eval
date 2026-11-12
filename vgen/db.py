"""SQLite persistence (SQLModel). JSON columns are replaced wholesale on update."""

from __future__ import annotations

import time
import uuid
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import JSON, Column, event
from sqlmodel import Field, Session, SQLModel, create_engine, select

# Job lifecycle. Terminal states never change again unless the user acts.
PENDING = "pending"            # waiting for a submit slot
SUBMITTING = "submitting"      # POST in flight (on restart -> submit_unknown)
QUEUED = "queued"
IN_PROGRESS = "in_progress"
DOWNLOADING = "downloading"
ANALYZING = "analyzing"
SUCCEEDED = "succeeded"
FAILED = "failed"
SUBMIT_UNKNOWN = "submit_unknown"
TIMEOUT = "timeout"
STOPPED = "stopped"
BLOCKED = "blocked"            # payload could not be built; never submitted

ACTIVE_REMOTE = {QUEUED, IN_PROGRESS}
TERMINAL = {SUCCEEDED, FAILED, SUBMIT_UNKNOWN, TIMEOUT, STOPPED, BLOCKED}

# Job.error_code for a failure that this app caused itself (a crash in a step, a restart mid-submit, a disabled
# endpoint). Every other code comes from the gateway. The public "an error occurred on this site" reason keys on
# this code, never on wording, because gateway messages are stored verbatim and may read "Internal error" too.
INTERNAL_ERROR_CODE = "vgen_internal"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


class Run(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("run"), primary_key=True)
    created_at: float = Field(default_factory=now, index=True)
    title: str = ""
    kind: str = "compare"                      # compare | pack | probe
    scenario_id: str | None = Field(default=None, index=True)
    category: str | None = None
    task: str = "t2v"
    lang: str = "en"
    endpoint: str = "com"
    params: dict = Field(default_factory=dict, sa_column=Column(JSON))
    prompts: list = Field(default_factory=list, sa_column=Column(JSON))   # [{key,title,text,checklist}]
    models: list = Field(default_factory=list, sa_column=Column(JSON))
    judge: bool = False
    est_cost_usd: float = 0.0
    notes: str = ""
    owner: str | None = Field(default=None, index=True)   # public mode: visitor id; None = operator/showcase


class Job(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("job"), primary_key=True)
    run_id: str = Field(index=True)
    prompt_key: str = ""
    model: str = Field(index=True)
    endpoint: str = "com"
    origin: str = "generated"                 # generated | imported (never scheduled)
    case_fingerprint: str | None = Field(default=None, index=True)
    deployment_id: str | None = Field(default=None, index=True)
    experiment_id: str | None = Field(default=None, index=True)
    provenance: dict = Field(default_factory=dict, sa_column=Column(JSON))
    label: str = ""                              # anonymous label for blind mode
    status: str = Field(default=PENDING, index=True)
    spec: dict = Field(default_factory=dict, sa_column=Column(JSON))
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))  # redacted
    adjustments: list = Field(default_factory=list, sa_column=Column(JSON))
    effective: dict = Field(default_factory=dict, sa_column=Column(JSON))
    est_cost_usd: float = 0.0
    task_id: str | None = Field(default=None, index=True)
    progress: str = ""
    remote_status: str = ""
    submit_response: dict = Field(default_factory=dict, sa_column=Column(JSON))
    last_detail: dict = Field(default_factory=dict, sa_column=Column(JSON))
    fail_reason: str = ""
    error_code: str = ""
    quota: int | None = None
    created_at: float = Field(default_factory=now)
    submitted_at: float | None = None
    gw_submit_time: float | None = None
    gw_start_time: float | None = None
    gw_finish_time: float | None = None
    done_at: float | None = None
    next_poll_at: float | None = None
    poll_count: int = 0
    submit_attempts: int = 0
    video_path: str | None = None
    poster_path: str | None = None
    strip_path: str | None = None
    video_sha: str | None = None
    media: dict = Field(default_factory=dict, sa_column=Column(JSON))
    compliance: list = Field(default_factory=list, sa_column=Column(JSON))
    signals: dict = Field(default_factory=dict, sa_column=Column(JSON))
    fidelity: dict = Field(default_factory=dict, sa_column=Column(JSON))
    judge: dict = Field(default_factory=dict, sa_column=Column(JSON))
    judge_status: str = ""                       # "", pending, running, done, error, skipped


class Rating(SQLModel, table=True):
    job_id: str = Field(primary_key=True)
    run_id: str = Field(index=True)
    usable: bool | None = None
    overall: int | None = None
    defects: list = Field(default_factory=list, sa_column=Column(JSON))
    comment: str = ""
    updated_at: float = Field(default_factory=now)


class EvaluationCase(SQLModel, table=True):
    id: str = Field(primary_key=True)
    dataset_id: str = Field(index=True)
    dataset_title: dict = Field(default_factory=dict, sa_column=Column(JSON))
    key: str
    title: str
    task: str
    prompt: str
    fingerprint: str = Field(index=True)
    checklist: list = Field(default_factory=list, sa_column=Column(JSON))
    assets: dict = Field(default_factory=dict, sa_column=Column(JSON))


class Deployment(SQLModel, table=True):
    id: str = Field(primary_key=True)
    model: str
    label: str
    provider: str
    hardware: dict = Field(default_factory=dict, sa_column=Column(JSON))
    timing_scope: str = ""


class Experiment(SQLModel, table=True):
    id: str = Field(primary_key=True)
    title: str
    date: str
    dataset_id: str
    content_sha256: str
    deployment_ids: list = Field(default_factory=list, sa_column=Column(JSON))
    config: dict = Field(default_factory=dict, sa_column=Column(JSON))
    summary: dict = Field(default_factory=dict, sa_column=Column(JSON))
    artifacts: dict = Field(default_factory=dict, sa_column=Column(JSON))


class BenchmarkAttempt(SQLModel, table=True):
    id: str = Field(primary_key=True)
    experiment_id: str = Field(index=True)
    deployment_id: str = Field(index=True)
    case_id: str
    request_id: str
    attempt: int
    job_id: str | None = None
    metrics: dict = Field(default_factory=dict, sa_column=Column(JSON))


class Pick(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("pick"), primary_key=True)
    run_id: str = Field(index=True)
    prompt_key: str = ""
    winner_job_id: str = ""
    created_at: float = Field(default_factory=now)


class LedgerEntry(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("led"), primary_key=True)
    ts: float = Field(default_factory=now, index=True)
    kind: str = "video"                          # video | judge | image
    job_id: str | None = Field(default=None, index=True)
    run_id: str | None = None
    model: str = ""
    endpoint: str = "com"
    est_usd: float = 0.0
    actual_usd: float | None = None
    quota: int | None = None
    note: str = ""


class Asset(SQLModel, table=True):
    id: str = Field(primary_key=True)            # sha256 of the bytes
    kind: str = "image"                          # image | video | audio
    label: str = ""
    filename: str = ""
    mime: str = ""
    size: int = 0
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    local_path: str = ""
    public_urls: list = Field(default_factory=list, sa_column=Column(JSON))
    source: str = ""                             # upload | url | example | generated
    created_at: float = Field(default_factory=now)
    owner: str | None = Field(default=None, index=True)   # public mode: the visitor who uploaded it


class ProbeResult(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("probe"), primary_key=True)
    ts: float = Field(default_factory=now, index=True)
    case_id: str = Field(index=True)
    model: str = ""
    ok: bool = False
    job_id: str | None = None
    detail: dict = Field(default_factory=dict, sa_column=Column(JSON))


class JudgeCache(SQLModel, table=True):
    key: str = Field(primary_key=True)
    created_at: float = Field(default_factory=now)
    result: dict = Field(default_factory=dict, sa_column=Column(JSON))


class KV(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: Any = Field(default=None, sa_column=Column(JSON))


_engine = None

# Columns added after the first release. create_all() never alters an existing table.
ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "run": [("owner", "VARCHAR")], "asset": [("owner", "VARCHAR")],
    "job": [("origin", "VARCHAR NOT NULL DEFAULT 'generated'"), ("case_fingerprint", "VARCHAR"),
            ("deployment_id", "VARCHAR"), ("experiment_id", "VARCHAR"),
            ("provenance", "JSON NOT NULL DEFAULT '{}' ")],
}


def backup_before_migration(db_path: Path) -> Path | None:
    """Make a consistent SQLite backup before altering an existing schema, including WAL."""
    if not db_path.exists():
        return None
    with sqlite3.connect(db_path) as source:
        tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = any(table in tables and any(name not in {
            r[1] for r in source.execute(f'PRAGMA table_info("{table}")')
        } for name, _ in columns) for table, columns in ADDED_COLUMNS.items())
        if not missing:
            return None
        dest = db_path.with_name(f"{db_path.name}.pre-migration-{time.time_ns()}.bak")
        with sqlite3.connect(dest) as target:
            source.backup(target)
        return dest


def _add_missing_columns(engine) -> None:
    with engine.begin() as conn:
        for table, columns in ADDED_COLUMNS.items():
            have = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, ddl in columns:
                if name not in have:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                    conn.exec_driver_sql(f"CREATE INDEX IF NOT EXISTS ix_{table}_{name} ON {table} ({name})")


def init_db(db_path: Path):
    global _engine
    db_path.parent.mkdir(parents=True, exist_ok=True)
    backup_before_migration(db_path)
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)
    _engine = engine
    return engine


def get_engine():
    if _engine is None:
        raise RuntimeError("init_db() has not been called")
    return _engine


@contextmanager
def session() -> Iterator[Session]:
    with Session(get_engine(), expire_on_commit=False) as s:
        yield s


def update_job(job_id: str, **fields) -> Job | None:
    with session() as s:
        job = s.get(Job, job_id)
        if job is None:
            return None
        for k, v in fields.items():
            setattr(job, k, v)
        s.add(job)
        s.commit()
        s.refresh(job)
        return job


def get_job(job_id: str) -> Job | None:
    with session() as s:
        return s.get(Job, job_id)


def jobs_for_run(run_id: str) -> list[Job]:
    with session() as s:
        return list(s.exec(select(Job).where(Job.run_id == run_id).order_by(Job.prompt_key, Job.label)))


def kv_get(key: str, default: Any = None) -> Any:
    with session() as s:
        row = s.get(KV, key)
        return default if row is None else row.value


def kv_set(key: str, value: Any) -> None:
    with session() as s:
        row = s.get(KV, key) or KV(key=key)
        row.value = value
        s.add(row)
        s.commit()
