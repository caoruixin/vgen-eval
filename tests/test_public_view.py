"""The public projection (allowlists, failure categories) and the admin sign-in primitives. No server, no network."""

from __future__ import annotations

import re
import threading
import time

import pytest

from vgen import cli, db, jobs, public_view, security
from vgen.config import load_settings
from vgen.public_view import failure_category, job_failure, public_job
from vgen.security import AdminAuth, hash_admin_secret, verify_admin_secret

SECRET = "correct horse battery staple"
HASH = hash_admin_secret(SECRET)
PRIVATE = "PRIVATE-do-not-show"


# ------------------------------------------------------------------ failure categories
# (status, error_code, fail_reason) as written by vgen/jobs.py, the mock gateway, the stored probe results and
# the 2026-11-08 backfill evidence.
OBSERVED = [
    ("blocked", "", "Wan 2.7 VideoEdit does not support t2v", "unsupported"),
    ("blocked", "", "t2v failed the live probe for Veo 3.1", "unsupported"),
    ("timeout", "", "gave up waiting; use 'check again' to resume", "timeout"),
    ("failed", "invalid_request",
     "Insufficient balance. Available: 24.118 credits, estimated cost: 119 credits. Try reducing the request size, "
     "adjusting parameters, canceling ongoing tasks, switching to another model, or topping up and trying again.",
     "insufficient_balance"),
    ("stopped", "",
     "Backfill paused before submit: Tokenbay returned insufficient balance. Resume this combination after funding; "
     "no task was created.", "insufficient_balance"),
    ("failed", "insufficient_user_quota", "用户额度不足", "insufficient_balance"),
    ("failed", "", "账户余额不足，请充值", "insufficient_balance"),
    ("failed", "content_safety_policy_triggered",
     "The request failed because the input image 'content[1]' may contain real person.", "content_policy"),
    ("failed", "", "The request failed because the input image 'content[1]' may contain real person.", "content_policy"),
    ("failed", "content_policy_violation", "The request was rejected by content safety policy", "content_policy"),
    ("failed", "content_policy", "prompt rejected by moderation", "content_policy"),
    ("failed", "auth", "auth failed: Invalid API key", "auth"),
    ("failed", "invalid_api_key", "Invalid API key", "auth"),
    ("stopped", "", "auth failed while polling: token expired", "auth"),
    ("failed", "auth", "auth failed: endpoint disabled: Invalid API key", "auth"),
    ("failed", "", "not submitted: Rate limit exceeded, retry later", "rate_limited"),
    ("pending", "", "retrying: rate limit reached for this key", "rate_limited"),
    ("failed", "rate_limit_exceeded", "not submitted: slow down", "rate_limited"),
    # this app's own failures carry its marker code ...
    ("in_progress", "vgen_internal", "internal error: KeyError('duration')", "internal"),
    ("submit_unknown", "vgen_internal", "app stopped while submitting; the task may exist and may be billed", "internal"),
    ("failed", "vgen_internal", "endpoint 'me' is not enabled", "internal"),
    # ... and rows stored before the marker existed are recognised by the app's exact wording
    ("in_progress", "", "internal error: KeyError('duration')", "internal"),
    ("submit_unknown", "", "app stopped while submitting; the task may exist and may be billed", "internal"),
    ("failed", "", "endpoint 'me' is not enabled", "internal"),
    # the gateway's own "internal error" is the provider's, whatever its casing
    ("failed", "", "Internal error", "provider_error"),
    ("failed", "", "Internal Server Error", "provider_error"),
    ("failed", "", "internal error: upstream connect error", "provider_error"),
    ("failed", "generation_failed", "Internal error: please retry later", "provider_error"),
    ("submit_unknown", "server", "Internal error — the task may exist and may be billed", "provider_error"),
    ("failed", "", "generated but download failed: content endpoint returned JSON: {}", "download_failed"),
    ("downloading", "", "download retry: download interrupted: ReadTimeout('')", "download_failed"),
    ("stopped", "", "stopped before submit (not billed)", "stopped"),
    ("stopped", "", "tracking stopped — no cancel API, the gateway keeps generating and billing", "stopped"),
    ("stopped", "", "hard spend cap $40 reached; not submitted", "stopped"),
    ("stopped", "", "endpoint disabled; polling stopped", "stopped"),
    ("failed", "", "not submitted: connection failed: ConnectError('All connection attempts failed')", "network"),
    ("pending", "", "retrying: connection failed: ConnectTimeout('')", "network"),
    ("submit_unknown", "after_send",
     "no response after sending: ReadTimeout('') — the task may exist and may be billed", "timeout"),
    ("failed", "invalid_parameter", "audio role=reference_audio is required", "invalid_request"),
    ("failed", "invalid_parameter", "resolution must be one of [720p, 1080p, 720, 1080], got '480P'", "invalid_request"),
    ("failed", "invalid_model", "Unsupported video model: nope", "invalid_request"),
    ("failed", "business", "content[0].text is required", "invalid_request"),
    ("submit_unknown", "server", "Internal server error — the task may exist and may be billed", "provider_error"),
    ("failed", "generation_failed",
     "Video generation failed, please retry or contact support with your task id", "provider_error"),
    ("failed", "", "mock generation failure", "provider_error"),
    ("failed", "", "generation failed", "provider_error"),
    ("failed", "", "task not found: task not found: task_mock_1", "provider_error"),
    ("failed", "not_found", "404 page not found", "provider_error"),
    ("in_progress", "", "poll error (will retry): unexpected task body", "provider_error"),
    ("queued", "", "something nobody wrote a rule for", "unknown"),
    ("", "", "", "unknown"),
    (None, None, None, "unknown"),
]


@pytest.mark.parametrize("status,code,reason,expected", OBSERVED)
def test_failure_category_for_observed_strings(status, code, reason, expected):
    assert failure_category(status, code, reason) == expected
    assert expected in public_view.FAILURE_CATEGORIES


def test_failure_rules_apply_in_order():
    # status first: a blocked or timed-out job is never re-read from its text
    assert failure_category("blocked", "invalid_request", "Insufficient balance") == "unsupported"
    assert failure_category("timeout", "auth", "auth failed: x") == "timeout"
    # balance beats "stopped" and "invalid_request" (Tokenbay's code for it is invalid_request)
    assert failure_category("stopped", "invalid_request", "Insufficient credits") == "insufficient_balance"
    # a safety refusal that arrives with an invalid_* code is still a safety refusal
    assert failure_category("failed", "invalid_request", "rejected by content safety policy") == "content_policy"
    # Tokenbay's 5xx text starts with "Internal server error": that is the provider, not this site
    assert failure_category("submit_unknown", "server", "Internal server error") == "provider_error"
    assert failure_category("failed", "", "Internal error: boom") == "provider_error"
    # auth wins over stopped; a network retry that hit the rate limit is a rate limit
    assert failure_category("stopped", "", "Auth failed while polling: no") == "auth"
    assert failure_category("failed", "", "not submitted: Too many requests") == "rate_limited"
    # the word "content" alone (request shape, download endpoint) is not a safety refusal
    assert failure_category("failed", "", "content[1].image_url is required") == "provider_error"


def test_only_the_apps_own_failures_are_reported_as_internal():
    """Gateway messages are stored verbatim as the reason, so "an error occurred on this site, not in the model"
    must never be inferred from wording an upstream could produce."""
    marker = db.INTERNAL_ERROR_CODE
    assert marker == "vgen_internal"
    # the marker decides, also when the quoted exception happens to mention policy, balance or a rate limit
    for reason in ("internal error: RuntimeError('rate limit policy')", "internal error: ValueError('Insufficient balance')",
                   "internal error: KeyError('safety_filter')", "", "anything at all"):
        assert failure_category("in_progress", marker, reason) == "internal", reason
        assert failure_category("failed", marker, reason) == "internal", reason
    # ... but not over the status rules
    assert failure_category("blocked", marker, "x") == "unsupported"
    assert failure_category("timeout", marker, "x") == "timeout"

    # upstream wording, in every shape it is stored: verbatim, prefixed by the engine, any casing
    upstream = [
        ("failed", "", "Internal error"), ("failed", "", "INTERNAL ERROR"), ("failed", "", "Internal Error: boom"),
        ("failed", "", "Internal server error"), ("failed", "", "internal error"), ("failed", "", "internal error."),
        ("failed", "server_error", "internal error: upstream timeout"),          # exact prefix, but a gateway code
        ("failed", "", "internal error: upstream timeout"),                      # ... or a finished job
        ("submit_unknown", "", "internal error: no response"),
        ("submit_unknown", "server", "Internal error — the task may exist and may be billed"),
        ("failed", "", "Endpoint 'video' is not enabled"), ("failed", "", "endpoint video is not enabled for this key"),
        ("failed", "", "endpoint 'video' is not enabled for this key"),
        ("failed", "business", "app stopped while submitting"),
    ]
    for status, code, reason in upstream:
        assert failure_category(status, code, reason) != "internal", (status, code, reason)
    assert failure_category("failed", "", "Internal error") == "provider_error"
    assert failure_category("failed", "", "Internal Server Error") == "provider_error"
    assert failure_category("failed", "business", "internal error: x") == "invalid_request"
    # an active job with wording nobody recognises is simply unknown, not "this site"
    assert failure_category("in_progress", "", "Internal error: boom") == "unknown"
    assert job_failure("in_progress", marker, "internal error: KeyError('x')") == {"category": "internal", "retrying": True}
    assert job_failure("in_progress", marker, "") == {"category": "internal", "retrying": True}


def test_policy_wording_does_not_swallow_auth_and_rate_limit_messages():
    # "policy" alone is not a safety refusal: these mention one and are what they say they are
    assert failure_category("failed", "auth", "auth failed: this key is blocked by an access policy") == "auth"
    assert failure_category("stopped", "", "auth failed while polling: denied by organization policy") == "auth"
    assert failure_category("failed", "invalid_api_key", "The key policy does not allow video models") == "auth"
    assert failure_category("failed", "", "not submitted: rate-limit policy exceeded for this key") == "rate_limited"
    assert failure_category("pending", "", "retrying: Rate limit policy: 15 requests per minute") == "rate_limited"
    assert failure_category("failed", "rate_limit_exceeded", "not submitted: see the fair use policy") == "rate_limited"
    assert failure_category("failed", "", "not submitted: rate limit reached (fair usage policy)") == "rate_limited"
    assert failure_category("failed", "", "The retention policy removed this task") == "provider_error"
    assert failure_category("failed", "invalid_parameter", "policy must be one of [fast, quality]") == "invalid_request"
    # content / safety / usage policy wording, moderation and the known phrases still are
    for reason in ("The request was rejected by content safety policy", "Rejected: content policy violation",
                   "This prompt violates our usage policies", "Blocked by our Acceptable Use Policy",
                   "Your request was blocked by the safety system", "output removed by content filter",
                   "prompt rejected by moderation", "The image was flagged by content moderation",
                   "The request failed because the input image 'content[1]' may contain real person.",
                   "提示词未通过内容安全审核", "包含敏感内容"):
        assert failure_category("failed", "", reason) == "content_policy", reason
        assert failure_category("failed", "invalid_request", reason) == "content_policy", reason
    # the gateway's codes for it are unchanged
    for code in ("content_policy_violation", "content_safety_policy_triggered", "content_filter", "policy_violation",
                 "moderation_blocked", "safety_block", "sensitive_content"):
        assert failure_category("failed", code, "no") == "content_policy", code
    # a code that merely contains "policy" is not one of them
    assert failure_category("failed", "access_policy_denied", "access policy denies this key") == "provider_error"
    assert failure_category("failed", "rate_limit_policy", "slow down") == "rate_limited"
    assert failure_category("failed", "usage_policy", "no") == "content_policy"


def test_the_engine_marks_its_own_failures_and_never_leaves_the_marker_stale(db_ready, registry):
    import asyncio

    marker = db.INTERNAL_ERROR_CODE
    engine = jobs.JobEngine(db_ready, registry, {})
    with db.session() as s:
        s.add(db.Run(id="run_e", title="engine"))
        s.add(db.Job(id="job_restart", run_id="run_e", model="seedance-2.0-mini", status=db.SUBMITTING))
        s.add(db.Job(id="job_endpoint", run_id="run_e", model="seedance-2.0-mini", status=db.PENDING, endpoint="me"))
        s.add(db.Job(id="job_crash", run_id="run_e", model="seedance-2.0-mini", status=db.IN_PROGRESS, task_id="t1"))
        s.commit()

    async def crash():
        raise RuntimeError("boom")

    async def steps():
        await engine._submit("job_endpoint")              # no gateway for endpoint "me"
        await engine._guard("job_crash", crash())         # a step blew up

    engine.recover()                                      # the process died while a submit was in flight
    asyncio.run(steps())
    engine._pool.shutdown(wait=False)
    restart, endpoint, crashed = db.get_job("job_restart"), db.get_job("job_endpoint"), db.get_job("job_crash")
    assert restart.status == db.SUBMIT_UNKNOWN and restart.fail_reason.startswith("app stopped while submitting")
    assert endpoint.status == db.FAILED and endpoint.fail_reason == "endpoint 'me' is not enabled"
    assert crashed.status == db.IN_PROGRESS and crashed.fail_reason == "internal error: RuntimeError('boom')"
    assert restart.error_code == endpoint.error_code == crashed.error_code == marker
    for j, retrying in ((restart, False), (endpoint, False), (crashed, True)):
        assert job_failure(j.status, j.error_code, j.fail_reason) == {"category": "internal", "retrying": retrying}

    # the crashed step is retried; whatever it then writes replaces the marker together with the reason
    polled = jobs.update_job("job_crash", fail_reason="", poll_count=1)                 # a clean poll
    assert polled.error_code == "" and job_failure(polled.status, polled.error_code, polled.fail_reason) is None
    asyncio.run(engine._guard("job_crash", crash()))
    failed = jobs.update_job("job_crash", status=db.FAILED, fail_reason="Internal error")   # the gateway's verdict
    assert failed.error_code == ""
    assert job_failure(failed.status, failed.error_code, failed.fail_reason) == {"category": "provider_error", "retrying": False}
    # a step that names a code keeps it, and updates that do not touch the reason leave the marker alone
    asyncio.run(engine._guard("job_crash", crash()))
    assert jobs.update_job("job_crash", poll_count=2).error_code == marker
    assert jobs.update_job("job_crash", fail_reason="no", error_code="invalid_request").error_code == "invalid_request"
    assert jobs.update_job("job_crash", fail_reason="still no").error_code == "invalid_request"
    assert jobs.update_job("job_missing", fail_reason="x") is None
    # the user's own actions clear it as well
    asyncio.run(engine._guard("job_crash", crash()))
    assert engine.resubmit("job_crash").error_code == ""


def test_job_failure_reports_only_when_there_is_something_to_report():
    assert job_failure("succeeded", "", "") is None
    assert job_failure("succeeded", "x", "left over") is None
    for active in ("pending", "submitting", "queued", "in_progress", "downloading", "analyzing"):
        assert job_failure(active, "", "") is None
    assert job_failure("pending", "", "retrying: connection failed") == {"category": "network", "retrying": True}
    assert job_failure("in_progress", "", "poll error (will retry): x") == {"category": "provider_error", "retrying": True}
    assert job_failure("failed", "content_policy", "no") == {"category": "content_policy", "retrying": False}
    assert job_failure("stopped", "", "") == {"category": "stopped", "retrying": False}
    assert job_failure("blocked", "", "") == {"category": "unsupported", "retrying": False}
    assert job_failure("submit_unknown", "", "") == {"category": "provider_error", "retrying": False}


# ------------------------------------------------------------------ projections
def full_job() -> dict:
    return {
        "id": "job_1", "run_id": "run_1", "prompt_key": "p1", "model": "seedance-2.0", "label": "A",
        "origin": "generated", "experiment_id": None, "deployment_id": "tokenbay-com-seedance-2.0",
        "status": "failed", "progress": "40%", "judge_status": "error", "est_cost_usd": 0.42,
        "adjustments": [{"field": "duration", "requested": 6, "used": 5, "reason": "allowed: 5, 10 s"}],
        "effective": {"duration": 5, "resolution": "720p"}, "created_at": 1.0, "submitted_at": 2.0,
        "gw_submit_time": 3.0, "gw_start_time": 4.0, "gw_finish_time": 5.0, "done_at": 6.0,
        "media": {"duration": 5.0}, "compliance": [{"key": "duration", "ok": True}], "fidelity": {},
        "model_label": "Seedance 2.0", "family": "seedance", "vendor": "ByteDance", "video_url": None,
        "poster_url": None, "strip_url": None, "latency_s": None, "latency_basis": "client_total",
        "has_task": True, "failure": {"category": "content_policy", "retrying": False},
        "spec": {"task": "i2v", "prompt": "A fox", "negative_prompt": None, "duration": 6, "resolution": "720p",
                 "ratio": "16:9", "audio": False, "seed": 7,
                 "first_frame": {"asset_id": "abc", "role": "first_frame", "local_path": PRIVATE},
                 "last_frame": None, "references": [{"asset_id": "r1", "role": "reference", "url": PRIVATE}],
                 "source_video": None, "audio_input": None, "allow_experimental": True, "probe_override": True,
                 "note": PRIVATE},
        "signals": {"pairs": 12, "motion_pct_per_s": 3.1, "motion_timeline": [1.0, None], "frozen_ratio": 0.0,
                    "frozen_flag": False, "scene_cuts": [],
                    "audio": {"has_audio": True, "rms_dbfs": -21.5, "silence_ratio": 0.1, "seconds": 5.0, "trace": PRIVATE},
                    "error": f"analysis failed: {PRIVATE}", "debug": PRIVATE},
        "judge": {"checklist": [{"item": "A fox", "assessable": True, "pass": True, "evidence": "seen", "t": 1.0,
                                 "raw": PRIVATE}],
                  "defects": [{"type": "flicker", "severity": "minor", "t": 2.0, "description": "brief", "raw": PRIVATE}],
                  "verdict": "usable", "adherence": 1.0, "passed": 1, "total": 1, "not_assessable": 0,
                  "defect_score": 1, "model": "gemini-3.1-pro-preview", "mode": "frames-v1", "cached": True,
                  "usage": {"input_tokens": 9000, "usd": 0.03, "note": PRIVATE}, "error": PRIVATE,
                  "thoughts": PRIVATE},
        "cost": {"estimate_usd": 0.42, "actual_usd": None, "per_second": None, "basis": "official_list_price",
                 "quota": 150000, "ledger": PRIVATE},
        "provenance": {"phase": "baseline", "variant": "bf16", "seed": 1101, "preferred": True, "source": "self",
                       "e2e_seconds": 178.5, "server_inference_time_s": 170.0, "model_revision": "42ed",
                       "turbo_activation_verified": None, "requested_num_inference_steps": 50,
                       "actual_denoise_updates": 50, "variant_evidence_scope": "standard BF16", "cost_usd": 0.7,
                       "cost_basis": "phase_resource_estimate", "timing_scope": "server_local_download",
                       "request_id": PRIVATE, "config_sha256": PRIVATE, "original_sha256": PRIVATE,
                       "actual_denoise_updates_source": f"runs/{PRIVATE}/server-progress.txt",
                       "price_snapshot": {"per_second": {"720p": 0.1}, "note": PRIVATE}, "attempt": 1},
        # admin-only columns
        "task_id": f"task-{PRIVATE}", "submit_response": {"group": PRIVATE}, "last_detail": {"error": PRIVATE},
        "payload": {"prompt": "A fox", "trace": PRIVATE}, "fail_reason": f"rejected: {PRIVATE}",
        "error_code": PRIVATE, "quota": 150000, "poll_count": 3, "submit_attempts": 1, "next_poll_at": 9.0,
        "remote_status": PRIVATE, "endpoint": "com", "case_fingerprint": PRIVATE, "video_sha": PRIVATE,
        # a column added after this projection was written
        "brand_new_column": PRIVATE,
    }


def test_public_job_keeps_only_listed_fields():
    job = full_job()
    out = public_job(job)
    assert PRIVATE not in repr(out) and "150000" not in repr(out)
    assert set(out) == set(public_view.PUBLIC_JOB_KEYS) | {"spec", "signals", "judge", "cost", "provenance"}
    for gone in ("task_id", "submit_response", "last_detail", "payload", "fail_reason", "error_code", "quota",
                 "poll_count", "submit_attempts", "next_poll_at", "remote_status", "endpoint", "case_fingerprint",
                 "video_sha", "brand_new_column"):
        assert gone not in out
    assert out["has_task"] is True and out["failure"] == {"category": "content_policy", "retrying": False}
    assert out["progress"] == "40%"
    assert set(out["spec"]) == set(public_view.PUBLIC_SPEC_KEYS)
    assert out["spec"]["first_frame"] == {"asset_id": "abc", "role": "first_frame"}
    assert out["spec"]["references"] == [{"asset_id": "r1", "role": "reference"}] and out["spec"]["seed"] == 7
    assert out["signals"]["analysis_failed"] is True and "error" not in out["signals"]
    assert out["signals"]["motion_pct_per_s"] == 3.1
    assert out["signals"]["audio"] == {"has_audio": True, "rms_dbfs": -21.5, "silence_ratio": 0.1, "seconds": 5.0}
    assert out["judge"]["checklist"] == [{"item": "A fox", "assessable": True, "pass": True, "evidence": "seen", "t": 1.0}]
    assert out["judge"]["defects"] == [{"type": "flicker", "severity": "minor", "t": 2.0, "description": "brief"}]
    assert out["judge"]["verdict"] == "usable" and out["judge"]["cached"] is True
    assert "usage" not in out["judge"] and "error" not in out["judge"]
    assert out["cost"] == {"estimate_usd": 0.42, "actual_usd": None, "per_second": None, "basis": "official_list_price"}
    assert set(out["provenance"]) == set(public_view.PUBLIC_PROVENANCE_KEYS)
    assert out["provenance"]["requested_num_inference_steps"] == 50 and out["provenance"]["preferred"] is True
    assert job["task_id"] == f"task-{PRIVATE}" and "note" in job["spec"]          # the input is not modified


@pytest.mark.parametrize("stored,shown", [
    ("0%", "0%"), ("7%", "7%"), ("40%", "40%"), ("100%", "100%"), ("", ""),
    # anything else the gateway may put there is dropped, not shown
    ("1000%", ""), ("50", ""), ("50.5%", ""), (" 50%", ""), ("50% ", ""), ("50%\n", ""), ("-5%", ""), ("٥٠%", ""),
    (f"queued behind {PRIVATE}", ""), (f"40% {PRIVATE}", ""), (f"{PRIVATE}\n40%", ""), (None, ""), (40, ""),
    ({"pct": 40}, ""), (["40%"], ""),
])
def test_public_progress_is_a_plain_percentage_or_nothing(stored, shown):
    assert public_job({"id": "j", "progress": stored})["progress"] == shown
    assert "progress" not in public_job({"id": "j"})


def test_public_job_tolerates_missing_and_odd_values():
    out = public_job({"id": "j", "status": "pending", "provenance": None, "spec": None, "judge": None, "signals": None})
    assert out == {"id": "j", "status": "pending", "spec": {}, "signals": {"analysis_failed": False}, "judge": {},
                   "cost": {}, "provenance": {}}
    assert public_view.public_signals({"pairs": 0}) == {"pairs": 0, "analysis_failed": False}
    assert public_view.public_judge({"checklist": "broken", "defects": [1, {"type": "other"}]}) == {
        "checklist": [], "defects": [{"type": "other"}]}


def test_run_plan_config_and_probe_projections():
    run = {"id": "r", "created_at": 1.0, "title": "t", "kind": "compare", "scenario_id": None, "category": None,
           "task": "t2v", "lang": "en", "prompts": [], "models": ["m"], "judge": False,
           "params": {"duration": 5, "resolution": "720p", "ratio": "16:9", "audio": True, "seed": 3,
                      "negative_prompt": "blur", "model_overrides": {"m": {"duration": 4}}, "assets": {"first_frame": "abc"},
                      "allow_experimental": False, "experiment_id": "e", "operator_memo": PRIVATE},
           "est_cost_usd": 1.0, "notes": PRIVATE, "endpoint": "com", "owner": PRIVATE, "later_column": PRIVATE}
    shown = public_view.public_run(run)
    assert set(shown) == set(public_view.PUBLIC_RUN_KEYS) and PRIVATE not in repr(shown)
    assert shown["params"] == {k: v for k, v in run["params"].items() if k != "operator_memo"}
    assert public_view.public_run({"id": "r", "params": None}) == {"id": "r", "params": {}}

    plan = {"items": [{"prompt_key": "p1", "model": "m", "ok": True, "spec": {}, "payload": {"prompt": PRIVATE},
                       "adjustments": [], "warnings": [], "effective": {}, "estimate": {"usd": 1.0},
                       "task_state": "enabled", "judge_estimate_usd": 0.1},
                      {"prompt_key": "p1", "model": "n", "ok": False, "reason": "n does not support t2v"}],
            "total_usd": 1.1, "unknown_prices": 0, "runnable": 1}
    shown = public_view.public_plan(plan)
    assert PRIVATE not in repr(shown) and all("payload" not in i for i in shown["items"])
    assert shown["total_usd"] == 1.1 and shown["runnable"] == 1 and shown["items"][1]["reason"]
    assert "payload" in plan["items"][0]

    config = {k: 1 for k in ("mock", "public", "admin", "admin_login", "endpoints", "budget_usd", "hard_cap_usd",
                             "confirm_above_usd", "spent_usd", "usd_cny", "judge_model", "max_inflight",
                             "judge_models", "tasks", "probe_updated_at", "quota_per_usd", "quota_calibrated",
                             "quota_note", "live_checked_at", "live_error")}
    assert set(public_view.public_config(config)) == {"mock", "public", "admin", "admin_login", "endpoints",
                                                      "spent_usd", "usd_cny", "judge_model", "tasks"}
    # an endpoint's last error and disabled reason are raw gateway text
    endpoint = {"key": "com", "name": "tokenbay.com", "rpm": 15, "used_last_minute": 3,
                "disabled_reason": f"Invalid API key {PRIVATE}", "last_error": f"[auth/401] {PRIVATE}", "later": PRIVATE}
    shown = public_view.public_config({"public": True, "endpoints": [endpoint, "odd", None]})
    assert shown == {"public": True, "endpoints": [{"key": "com", "name": "tokenbay.com", "rpm": 15}, {}, {}]}
    assert public_view.public_config({"endpoints": {"com": endpoint}}) == {"endpoints": []}
    assert public_view.public_config({"endpoints": []}) == {"endpoints": []} and "last_error" in endpoint

    rating = {"job_id": "j", "run_id": "r", "usable": True, "overall": 4, "defects": ["flicker"], "comment": PRIVATE,
              "updated_at": 5.0, "later_column": PRIVATE}
    assert public_view.public_rating(rating) == {"job_id": "j", "run_id": "r", "usable": True, "overall": 4,
                                                 "defects": ["flicker"], "updated_at": 5.0}
    assert public_view.public_rating(None) is None and rating["comment"] == PRIVATE

    probe = {"tasks": {"t2v": "ok", "i2v": "fail", "r2v": PRIVATE}, "delivery": {"image": {"url": "yes", "data_uri": PRIVATE}},
             "unavailable": f"tokenbay.com returned '{PRIVATE}' on submit", "notes": [PRIVATE],
             "cases": {"c": {"fail_reason": PRIVATE}}}
    assert public_view.public_probe_overlay(probe) == {"tasks": {"t2v": "ok", "i2v": "fail"},
                                                       "delivery": {"image": {"url": "yes"}}, "unavailable": True}
    assert public_view.public_probe_overlay({"tasks": {"t2v": "ok"}, "unavailable": ""}) == {"tasks": {"t2v": "ok"}}
    assert public_view.public_probe_overlay({}) == {} and public_view.public_probe_overlay(None) == {}


def test_benchmark_projections():
    deployment = {"id": "h3-pro6000", "model": "MiniMax-H3", "label": "H3", "provider": "self",
                  "timing_scope": "server_local_download",
                  "hardware": {"gpu": "RTX PRO 6000", "gpu_count": 8, "memory_gb_per_gpu": 96, "instance_id": PRIVATE}}
    assert public_view.public_deployment(deployment) == {
        "id": "h3-pro6000", "model": "MiniMax-H3", "label": "H3", "provider": "self",
        "timing_scope": "server_local_download",
        "hardware": {"gpu": "RTX PRO 6000", "gpu_count": 8, "memory_gb_per_gpu": 96}}
    assert public_view.public_deployment({"id": "x"}) == {"id": "x", "hardware": {}}

    config = {"model_repo": "MiniMaxAI/MiniMax-H3", "model_path": f"/data/{PRIVATE}", "num_inference_steps": 50,
              "limits": {"total_usd": 150}, "request_timeout_seconds": 1800,
              "turbo": {"repo": "lightx2v/x", "path": f"/data/{PRIVATE}", "rank": 128}}
    assert public_view.public_benchmark_config(config) == {
        "model_repo": "MiniMaxAI/MiniMax-H3", "num_inference_steps": 50, "turbo": {"repo": "lightx2v/x", "rank": 128}}

    summary = {"groups": [{"phase": "baseline"}], "phases": [{"cost_per_technical_success_usd": 0.7}],
               "telemetry": {"phase_metrics": []}, "utilization": [{"utilization": 0.25}],
               "scenes": [{"case_id": "T01", "title": "cat", "baseline_p50_s": 178.2, "official_e2e_s": 103.0,
                           "official_task_id": PRIVATE, "matched_baseline_request_id": PRIVATE}],
               "costs": {"generated_utc": "2026-11-07", "currency": "USD", "api_usage_price_estimate_usd": 4.0,
                         "self_technical_valid_outputs": 77, "official_technical_valid_outputs": 10,
                         "instance_id": 59566113, "credit_before_usd": 120, "report_markdown": PRIVATE},
               "invoices": [PRIVATE]}
    shown = public_view.public_benchmark_summary(summary)
    assert PRIVATE not in repr(shown) and "59566113" not in repr(shown)
    assert set(shown) == {"groups", "phases", "telemetry", "utilization", "scenes", "costs"}
    assert shown["phases"] == summary["phases"] and shown["utilization"] == summary["utilization"]
    assert shown["scenes"] == [{"case_id": "T01", "title": "cat", "baseline_p50_s": 178.2, "official_e2e_s": 103.0}]
    assert set(shown["costs"]) == set(public_view.PUBLIC_COSTS_KEYS)

    attempt = {"id": "attempt_1", "experiment_id": "e", "deployment_id": "d", "case_id": "ds:T01",
               "request_id": PRIVATE, "attempt": 1, "job_id": None,
               "metrics": {"case_id": "T01", "phase": "baseline", "e2e_seconds": 178.5, "estimated_cost_usd": 0.7,
                           "request_id": PRIVATE, "task_id": PRIVATE, "error": PRIVATE, "record_path": PRIVATE,
                           "ledger_reservation_id": PRIVATE, "config_sha256": PRIVATE, "video_path": PRIVATE}}
    assert public_view.public_attempt(attempt) == {
        "id": "attempt_1", "experiment_id": "e", "deployment_id": "d", "case_id": "ds:T01", "attempt": 1,
        "job_id": None,
        "metrics": {"case_id": "T01", "phase": "baseline", "e2e_seconds": 178.5, "estimated_cost_usd": 0.7}}
    assert len(public_view.PUBLIC_ATTEMPT_METRIC_KEYS) == len(set(public_view.PUBLIC_ATTEMPT_METRIC_KEYS)) == 38

    detail = {"id": "e", "title": "t", "date": "2026-11-07", "dataset_id": "ds", "content_sha256": PRIVATE,
              "deployment_ids": ["d"], "config": config, "summary": summary, "deployments": [deployment],
              "artifacts": {"report.md": "/a/report.md", "attempts.csv": "/a/attempts.csv"}, "attempts": [attempt]}
    shown = public_view.public_experiment(detail)
    assert PRIVATE not in repr(shown)
    assert set(shown) == {"id", "title", "date", "dataset_id", "deployment_ids", "config", "summary", "deployments",
                          "artifacts", "attempts"}
    assert shown["artifacts"] == {"report.md": "/a/report.md"}
    assert set(public_view.public_experiment_row({**detail, "n_attempts": 1, "n_videos": 0})) == {
        "id", "title", "date", "dataset_id", "deployment_ids", "n_attempts", "n_videos"}


@pytest.mark.parametrize("name,public", [
    ("report.md", True), ("report.docx", True), ("report.html", True), ("licenses.md", True),
    ("figures/flow.svg", True), ("figures/gpu-telemetry.png", True),
    ("attempts.csv", False), ("provenance.json", False), ("report-revision.json", False),
    ("archive-report.md", False), ("figures/figure-data-and-provenance.json", False), ("secret.env", False),
    ("figures/../../app.db", False), ("figures/a/b.png", False), ("report.md.bak", False), ("xreport.md", False),
    ("", False),
])
def test_public_artifact_names(name, public):
    assert public_view.is_public_artifact(name) is public


# ------------------------------------------------------------------ the admin secret
def test_hash_round_trip_and_format():
    assert re.fullmatch(r"scrypt:16384:8:1:[0-9a-f]{32}:[0-9a-f]{64}", HASH)
    assert SECRET not in HASH
    assert verify_admin_secret(HASH, SECRET)
    assert not verify_admin_secret(HASH, SECRET + " ")
    assert not verify_admin_secret(HASH, "")
    assert hash_admin_secret(SECRET) != HASH                                  # a fresh salt every time
    assert hash_admin_secret(SECRET, salt=b"0123456789abcdef") == hash_admin_secret(SECRET, salt=b"0123456789abcdef")


def test_malformed_or_out_of_bounds_hashes_never_match(monkeypatch):
    _, n, r, p, salt, digest = HASH.split(":")
    calls = []
    real = security.hashlib.scrypt
    monkeypatch.setattr(security.hashlib, "scrypt", lambda *a, **k: calls.append(k) or real(*a, **k))
    for bad in ["", "   ", "plain-secret", SECRET, f"bcrypt:{n}:{r}:{p}:{salt}:{digest}", f"scrypt:{n}:{r}:{p}:{salt}",
                f"scrypt:{n}:{r}:{p}:{salt}:{digest}:extra", f"scrypt:{n}:{r}:{p}:zz:{digest}",
                f"scrypt:{n}:{r}:{p}:{salt}:nothex", f"scrypt:{n}:{r}:{p}::{digest}", f"scrypt:{n}:{r}:{p}:{salt}:",
                f"scrypt:-1:{r}:{p}:{salt}:{digest}", f"scrypt:16385:{r}:{p}:{salt}:{digest}",
                f"scrypt:1024:{r}:{p}:{salt}:{digest}",                       # weaker than this app ever writes
                f"scrypt:1073741824:{r}:{p}:{salt}:{digest}",                 # would need a terabyte
                f"scrypt:65536:16:{p}:{salt}:{digest}",                       # 128 MiB per attempt
                f"scrypt:{n}:1:{p}:{salt}:{digest}", f"scrypt:{n}:1024:{p}:{salt}:{digest}",
                f"scrypt:{n}:{r}:0:{salt}:{digest}", f"scrypt:{n}:{r}:64:{salt}:{digest}",
                f"scrypt:{n}:{r}:{p}:{salt}:{digest[:8]}", None]:
        assert verify_admin_secret(bad, SECRET) is False, bad
        assert AdminAuth(bad or "").enabled is False, bad
    assert calls == []                                                        # refused before any hashing
    assert verify_admin_secret(HASH, None) is False and verify_admin_secret(HASH, 12345) is False
    assert verify_admin_secret(f"  {HASH}\n", SECRET) is True                 # stray whitespace from an env file


class Clock:
    def __init__(self):
        self.t = 5000.0

    def __call__(self) -> float:
        return self.t


def test_admin_sessions_expire_and_logout_revokes():
    clock = Clock()
    auth = AdminAuth(HASH, clock=clock)
    assert auth.enabled
    token = auth.login("203.0.113.5", SECRET)
    assert token and auth.valid(token)
    assert token not in repr(vars(auth)) and SECRET not in repr(vars(auth))    # only a digest of the token is kept
    for forged in (None, "", "x", token + "x", token[:-1], "a" * 500, 123):
        assert auth.valid(forged) is False
    clock.t += security.ADMIN_SESSION_S - 1
    assert auth.valid(token)
    clock.t += 2
    assert not auth.valid(token)                                              # 12 hours, then sign in again
    second = auth.login("203.0.113.5", SECRET)
    assert second != token and auth.valid(second)
    auth.logout(second)
    assert not auth.valid(second)
    auth.logout(second)
    auth.logout(None)                                                         # harmless
    # a new process (restart) knows no sessions
    third = auth.login("203.0.113.5", SECRET)
    assert auth.valid(third) and not AdminAuth(HASH, clock=clock).valid(third)


def test_admin_session_count_is_capped():
    auth = AdminAuth(HASH, clock=Clock())
    tokens = [auth.login(f"198.51.100.{i}", SECRET) for i in range(security.ADMIN_MAX_SESSIONS + 2)]
    assert all(tokens)
    assert [auth.valid(t) for t in tokens] == [False, False] + [True] * security.ADMIN_MAX_SESSIONS


def test_admin_throttle_is_per_client_and_checked_before_hashing(monkeypatch):
    clock = Clock()
    auth = AdminAuth(HASH, clock=clock)
    hashed = []
    real = security.verify_admin_secret
    monkeypatch.setattr(security, "verify_admin_secret", lambda h, s: hashed.append(1) or real(h, s))
    for _ in range(security.ADMIN_MAX_FAILURES):
        assert auth.retry_after("a") == 0
        assert auth.login("a", "not the secret, sorry") is None
        clock.t += 1
    assert len(hashed) == 5
    wait = auth.retry_after("a")
    assert wait == pytest.approx(security.ADMIN_FAILURE_WINDOW_S - 5)
    with pytest.raises(security.AdminThrottled) as locked:                    # locked: even the right secret,
        auth.login("a", SECRET)                                               # and it is not called a wrong one
    assert locked.value.busy is False and locked.value.retry_after == pytest.approx(wait)
    assert len(hashed) == 5                                                   # ... and nothing was hashed for it
    assert auth.retry_after("a") == pytest.approx(wait)                       # ... nor counted
    # nobody else is affected (no global lockout)
    assert auth.retry_after("b") == 0
    assert auth.valid(auth.login("b", SECRET))
    # the window slides: the first failure ages out, one more try is allowed
    clock.t += wait - 1
    assert auth.retry_after("a") == pytest.approx(1)
    clock.t += 1
    assert auth.retry_after("a") == 0
    assert auth.login("a", "still wrong, sorry") is None
    assert auth.retry_after("a") == pytest.approx(1)                          # until the second failure ages out
    clock.t += security.ADMIN_FAILURE_WINDOW_S
    assert auth.valid(auth.login("a", SECRET))
    # a success clears the count
    for _ in range(security.ADMIN_MAX_FAILURES - 1):
        assert auth.login("c", "wrong again, sorry") is None
    assert auth.valid(auth.login("c", SECRET))
    for _ in range(security.ADMIN_MAX_FAILURES - 1):
        assert auth.login("c", "wrong again, sorry") is None
    assert auth.retry_after("c") == 0


def test_admin_rejects_unusable_secrets_without_hashing(monkeypatch):
    auth = AdminAuth(HASH, clock=Clock())
    monkeypatch.setattr(security, "verify_admin_secret", lambda h, s: pytest.fail("must not hash this"))
    for bad in (None, "", 42, ["x"], "x" * (security.ADMIN_MAX_SECRET_CHARS + 1)):
        assert auth.login(f"client-{bad!r}"[:40], bad) is None
    for _ in range(security.ADMIN_MAX_FAILURES):                              # and they count as failures
        assert auth.login("d", "") is None
    assert auth.retry_after("d") > 0
    disabled = AdminAuth("", clock=Clock())
    assert not disabled.enabled and disabled.login("e", SECRET) is None and not disabled.valid("anything")


def test_admin_forgets_the_oldest_clients_beyond_the_cap(monkeypatch):
    monkeypatch.setattr(security, "ADMIN_MAX_CLIENTS", 3)
    auth = AdminAuth(HASH, clock=Clock())
    for client in ("a", "b", "c", "d"):
        for _ in range(security.ADMIN_MAX_FAILURES):
            assert auth.login(client, "") is None
    assert auth.retry_after("a") == 0                                         # evicted to make room
    assert all(auth.retry_after(c) > 0 for c in ("b", "c", "d"))
    assert len(auth._failures) == 3


def test_parallel_attempts_from_one_client_cannot_exceed_the_limit(monkeypatch):
    auth = AdminAuth(HASH)
    release, hashed, results = threading.Event(), [], []
    monkeypatch.setattr(security, "verify_admin_secret",
                        lambda h, s: (hashed.append(1), release.wait(10), False)[-1])
    def attempt():
        try:
            results.append(auth.login("a", "guess number whatever"))
        except security.AdminThrottled as hold:
            results.append(hold)

    threads = [threading.Thread(target=attempt) for _ in range(12)]
    for t in threads:
        t.start()
    deadline = time.time() + 10
    while len(results) < 12 - security.ADMIN_MAX_FAILURES and time.time() < deadline:
        time.sleep(0.01)
    assert len(results) == 12 - security.ADMIN_MAX_FAILURES                    # the surplus was refused at once,
    assert all(isinstance(r, security.AdminThrottled) and r.busy for r in results)   # as "busy", not as "wrong"
    assert len(hashed) <= 1                                                   # one hash at a time
    release.set()
    for t in threads:
        t.join(10)
    assert results[12 - security.ADMIN_MAX_FAILURES:] == [None] * security.ADMIN_MAX_FAILURES
    assert len(hashed) == security.ADMIN_MAX_FAILURES
    assert auth.retry_after("a") > 0


def test_a_full_sign_in_queue_is_reported_as_busy_and_never_counted(monkeypatch):
    monkeypatch.setattr(security, "ADMIN_MAX_WAITING", 2)
    auth = AdminAuth(HASH)
    release, entered, results = threading.Event(), threading.Event(), []
    real = security.verify_admin_secret
    monkeypatch.setattr(security, "verify_admin_secret",
                        lambda h, s: (entered.set(), release.wait(10), real(h, s))[-1])
    threads = [threading.Thread(target=lambda c=c: results.append(auth.login(c, "a wrong guess, sorry"))) for c in ("a", "b")]
    for t in threads:
        t.start()
    assert entered.wait(10)
    deadline = time.time() + 10
    while auth._waiting < 2 and time.time() < deadline:
        time.sleep(0.01)
    # the queue is full: the operator's correct secret is not checked, and it is not called wrong
    assert auth.retry_after("operator") == security.ADMIN_BUSY_RETRY_S
    for _ in range(security.ADMIN_MAX_FAILURES + 3):
        with pytest.raises(security.AdminThrottled) as busy:
            auth.login("operator", SECRET)
        assert busy.value.busy is True and busy.value.retry_after == security.ADMIN_BUSY_RETRY_S
    release.set()
    for t in threads:
        t.join(10)
    assert results == [None, None]
    # none of those refusals counted against the operator
    assert auth.retry_after("operator") == 0 and "operator" not in auth._failures
    assert auth.valid(auth.login("operator", SECRET))


# ------------------------------------------------------------------ settings and CLI
def test_settings_load_the_hash_without_printing_it(tmp_path, monkeypatch):
    monkeypatch.setenv("VGEN_MOCK", "1")
    monkeypatch.setenv("VGEN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("VGEN_ADMIN_SECRET_HASH", raising=False)
    env = tmp_path / ".env"
    env.write_text("TOKENBAY_REQUESTS_PER_MINUTE=15\n")
    assert load_settings(env).admin_secret_hash == ""
    env.write_text(f"TOKENBAY_REQUESTS_PER_MINUTE=15\nVGEN_ADMIN_SECRET_HASH={HASH}\n")
    settings = load_settings(env)
    assert settings.admin_secret_hash == HASH and verify_admin_secret(settings.admin_secret_hash, SECRET)
    assert HASH not in repr(settings) and HASH.split(":")[-1] not in repr(settings)
    monkeypatch.setenv("VGEN_ADMIN_SECRET_HASH", f"  {HASH}  ")               # the process environment wins
    assert load_settings(tmp_path / "missing.env").admin_secret_hash == HASH


def fake_getpass(monkeypatch, *answers):
    import getpass

    prompts, replies = [], iter(answers)

    def ask(prompt=""):
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr(getpass, "getpass", ask)
    return prompts


def test_admin_hash_cli_prints_the_env_line_and_never_the_secret(monkeypatch, capsys):
    secret = "a secret for the cli test 123"
    prompts = fake_getpass(monkeypatch, secret, secret)
    monkeypatch.setattr(cli, "_settings", lambda *a, **k: pytest.fail("admin-hash must not load settings"))
    monkeypatch.setattr("vgen.config.load_settings", lambda *a, **k: pytest.fail("admin-hash must not load settings"))
    assert cli.main(["admin-hash"]) == 0
    out, err = capsys.readouterr()
    assert len(prompts) == 2
    lines = out.splitlines()
    assert len(lines) == 1 and re.fullmatch(r"VGEN_ADMIN_SECRET_HASH=scrypt:16384:8:1:[0-9a-f]{32}:[0-9a-f]{64}", lines[0])
    assert secret not in out and secret not in err
    assert verify_admin_secret(lines[0].split("=", 1)[1], secret)


def test_admin_hash_cli_without_a_terminal_says_so_instead_of_crashing(monkeypatch, capsys):
    def no_terminal(prompt=""):
        raise EOFError

    monkeypatch.setattr("getpass.getpass", no_terminal)
    assert cli.main(["admin-hash"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and "interactive terminal" in err and "Traceback" not in err


@pytest.mark.parametrize("first,second", [
    ("a secret for the cli test 123", "a secret for the cli test 124"),      # typo
    ("too short", "too short"),                                              # under 16 characters
    ("", ""),
])
def test_admin_hash_cli_refuses_mismatch_and_short_secrets(monkeypatch, capsys, first, second):
    fake_getpass(monkeypatch, first, second)
    assert cli.main(["admin-hash"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and err.strip()
    assert (not first or first not in err) and (not second or second not in err)
