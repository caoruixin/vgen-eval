"""What a viewer who is not an admin receives from the API.

The API builds the full object first (what the operator sees on the local instance) and passes it through
one of these functions for everyone else. Each function is a pure dict -> dict projection driven by an
allowlist, so a column or key added later stays private until it is listed here. Nothing in this module
imports FastAPI or reads the database.

Private by design: Tokenbay's raw error text and codes, raw gateway responses, task ids, request payloads,
quota, the operator's spend, probe details, the self-hosted run's account figures, raw attempt records,
file paths and hashes.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from vgen.db import BLOCKED, FAILED, INTERNAL_ERROR_CODE, STOPPED, SUBMIT_UNKNOWN, SUCCEEDED, TERMINAL, TIMEOUT


def _pick(source: Any, keys: Iterable[str]) -> dict[str, Any]:
    return {k: source[k] for k in keys if k in source} if isinstance(source, dict) else {}


# ---------------------------------------------------------------------- why a clip is missing

FAILURE_CATEGORIES = ("unsupported", "timeout", "insufficient_balance", "content_policy", "auth", "rate_limited",
                      "internal", "download_failed", "stopped", "network", "invalid_request", "provider_error",
                      "unknown")

_BALANCE_TEXT = re.compile(r"insufficient[\s_-]*(?:balance|credits?|quota|funds)|余额不足")
_BALANCE_CODE = re.compile(r"insufficient[_-]?(?:user[_-]?)?(?:balance|credits?|quota|funds)"
                           r"|(?:balance|quota|credits?)[_-]not[_-]enough|^insufficient$")
# In the text only content / safety / usage policy wording counts. A bare "content" ("content[1].text is
# required", "content endpoint returned JSON") or a bare "policy" ("access policy", "rate-limit policy", "fair use
# policy") is not a safety refusal, and this rule runs before the auth and rate-limit rules.
_POLICY_TEXT = re.compile(r"\b(?:content|safety)[\s_-]+polic|(?<!fair )\busage[\s_-]+polic"
                          r"|\bacceptable[\s_-]+use[\s_-]+polic"
                          r"|\bcontent[\s_-]+(?:safety|filter|moderation)|\bsafety[\s_-]+(?:filter|system|check)"
                          r"|moderat|real person|内容安全|敏感")
# The same holds for codes: "access_policy_denied" or "rate_limit_policy" is not a safety refusal.
_POLICY_CODE = re.compile(r"content_(?:policy|safety|filter|moderation|violation|blocked)|safety|moderat|sensitive"
                          r"|(?:usage|use)_policy|policy_violation")
_AUTH_CODE = re.compile(r"^auth$|^auth[_-]|authentication|authorization|unauthori[sz]ed|invalid_api_key|invalid_key"
                        r"|invalid_token|token_invalid|token_expired|access_denied|forbidden|permission")
_RATE_TEXT = re.compile(r"rate[\s_-]?limit|too many requests")
_RATE_CODE = re.compile(r"rate_limit|too_many_requests")
# The app's own wording for its own failures, exactly as vgen/jobs.py writes it (case-sensitive). Only used for
# rows stored before INTERNAL_ERROR_CODE existed; see _own_failure_text.
_OWN_CRASH_PREFIX = "internal error: "
_OWN_RESTART_PREFIX = "app stopped while submitting"
_OWN_ENDPOINT_OFF = re.compile(r"endpoint '[^']*' is not enabled")


def _own_failure_text(status: str, code: str, reason: str) -> bool:
    """Whether a row stored before the marker code existed is one of this app's own failures.

    Gateway messages are stored verbatim, so wording alone must not clear the model: an upstream "Internal
    error" or "Internal Server Error" is the provider's. The match is therefore on the app's exact strings, and
    only where the app could have written them: with no error code (gateway rejections carry one), and for the
    crash text only while the job is still active (a finished job's reason is the provider's outcome)."""
    if code:
        return False
    if reason.startswith(_OWN_RESTART_PREFIX) or _OWN_ENDPOINT_OFF.fullmatch(reason):
        return True
    return reason.startswith(_OWN_CRASH_PREFIX) and status not in TERMINAL


def failure_category(status: str | None, error_code: str | None, fail_reason: str | None) -> str:
    """The public reason category for a job without a clip. Computed when read (stored rows need no migration)
    from the job's status, the gateway's error code and the stored reason text; the first matching rule wins."""
    status = (status or "").strip().lower()
    code = (error_code or "").strip().lower()
    reason = (fail_reason or "").strip()
    text = reason.lower()
    if status == BLOCKED:                                                    # 1
        return "unsupported"
    if status == TIMEOUT:                                                    # 2
        return "timeout"
    # 7, for rows the app marked itself: that code is unambiguous, so it is read before any wording is
    # interpreted (the stored reason may quote an exception that happens to mention "policy" or "rate limit")
    if code == INTERNAL_ERROR_CODE:
        return "internal"
    # 3 comes before "stopped" and "invalid_request": Tokenbay reports it with code invalid_request and a
    # message that discloses the account balance, and a paused backfill leaves such jobs as stopped.
    if _BALANCE_CODE.search(code) or _BALANCE_TEXT.search(text):             # 3
        return "insufficient_balance"
    if _POLICY_CODE.search(code) or _POLICY_TEXT.search(text):               # 4
        return "content_policy"
    if _AUTH_CODE.search(code) or text.startswith("auth failed"):            # 5
        return "auth"
    if _RATE_CODE.search(code) or _RATE_TEXT.search(text):                   # 6
        return "rate_limited"
    if _own_failure_text(status, code, reason):                              # 7, rows stored before the marker
        return "internal"
    if "generated but download failed" in text or "download retry" in text:  # 8
        return "download_failed"
    if status == STOPPED:                                                    # 9
        return "stopped"
    if text.startswith("not submitted:") or text.startswith("retrying:"):    # 10
        return "network"
    if code == "after_send":                                                 # 11
        return "timeout"
    if code.startswith("invalid") or code == "business":                     # 12
        return "invalid_request"
    if status in (FAILED, SUBMIT_UNKNOWN) or "poll error" in text or "task not found" in text:   # 13
        return "provider_error"
    return "unknown"                                                         # 14


def job_failure(status: str | None, error_code: str | None, fail_reason: str | None) -> dict[str, Any] | None:
    """`{"category", "retrying"}` for a job that failed or is retrying after an error; None otherwise."""
    if status == SUCCEEDED:
        return None
    terminal = status in TERMINAL
    if not terminal and not (fail_reason or error_code):
        return None
    return {"category": failure_category(status, error_code, fail_reason), "retrying": not terminal}


# ---------------------------------------------------------------------- jobs and runs

PUBLIC_JOB_KEYS = (
    "id", "run_id", "prompt_key", "model", "label", "origin", "experiment_id", "deployment_id", "status",
    "progress", "judge_status", "adjustments", "effective", "est_cost_usd", "created_at", "submitted_at",
    "gw_submit_time", "gw_start_time", "gw_finish_time", "done_at", "media", "compliance", "fidelity",
    "model_label", "family", "vendor", "video_url", "poster_url", "strip_url", "latency_s", "latency_basis",
    "has_task", "failure")
PUBLIC_SPEC_KEYS = ("task", "prompt", "negative_prompt", "duration", "resolution", "ratio", "audio", "seed",
                    "first_frame", "last_frame", "references", "source_video", "audio_input")
_SPEC_ASSET_KEYS = ("first_frame", "last_frame", "source_video", "audio_input")
PUBLIC_SIGNAL_KEYS = ("pairs", "motion_pct_per_s", "motion_timeline", "frozen_ratio", "frozen_flag", "scene_cuts",
                      "audio")
PUBLIC_SIGNAL_AUDIO_KEYS = ("has_audio", "rms_dbfs", "silence_ratio", "seconds")
PUBLIC_JUDGE_KEYS = ("checklist", "defects", "verdict", "adherence", "passed", "total", "not_assessable",
                     "defect_score", "model", "mode", "cached")
PUBLIC_JUDGE_CHECK_KEYS = ("item", "assessable", "pass", "evidence", "t")
PUBLIC_JUDGE_DEFECT_KEYS = ("type", "severity", "t", "description")
PUBLIC_COST_KEYS = ("estimate_usd", "actual_usd", "per_second", "basis")
PUBLIC_PROVENANCE_KEYS = (
    "phase", "variant", "seed", "preferred", "source", "e2e_seconds", "server_inference_time_s", "model_revision",
    "turbo_activation_verified", "requested_num_inference_steps", "actual_denoise_updates",
    "variant_evidence_scope", "cost_usd", "cost_basis", "timing_scope")
PUBLIC_RUN_KEYS = ("id", "created_at", "title", "kind", "scenario_id", "category", "task", "lang", "params",
                   "prompts", "models", "judge", "est_cost_usd")
# Run.params is whatever JSON the creator posted; only the settings the app itself reads are shown
PUBLIC_RUN_PARAM_KEYS = ("duration", "resolution", "ratio", "audio", "seed", "negative_prompt", "model_overrides",
                         "assets", "allow_experimental", "experiment_id")
PUBLIC_RATING_KEYS = ("job_id", "run_id", "usable", "overall", "defects", "updated_at")
_PROGRESS = re.compile(r"[0-9]{1,3}%")            # ASCII digits only; used with fullmatch
PUBLIC_PLAN_ITEM_KEYS = ("prompt_key", "model", "ok", "reason", "spec", "adjustments", "warnings", "effective",
                         "estimate", "task_state", "judge_estimate_usd")


def _asset_ref(value: Any) -> Any:
    if isinstance(value, dict):
        return _pick(value, ("asset_id", "role"))
    return value if isinstance(value, str) else None


def public_spec(spec: Any) -> dict[str, Any]:
    out = _pick(spec, PUBLIC_SPEC_KEYS)
    for key in _SPEC_ASSET_KEYS:
        if key in out:
            out[key] = _asset_ref(out[key])
    if "references" in out:
        refs = out["references"]
        out["references"] = [_asset_ref(r) for r in refs] if isinstance(refs, list) else []
    return out


def public_signals(signals: Any) -> dict[str, Any]:
    """The measured signals; an analysis error becomes a plain flag (its text may quote file paths)."""
    out = _pick(signals, PUBLIC_SIGNAL_KEYS)
    if "audio" in out:
        out["audio"] = _pick(out["audio"], PUBLIC_SIGNAL_AUDIO_KEYS)
    out["analysis_failed"] = bool(isinstance(signals, dict) and signals.get("error"))
    return out


def public_judge(judge: Any) -> dict[str, Any]:
    """Verdict, checklist, defects and scores. No token usage, and no error text (it quotes the gateway)."""
    out = _pick(judge, PUBLIC_JUDGE_KEYS)
    for key, keys in (("checklist", PUBLIC_JUDGE_CHECK_KEYS), ("defects", PUBLIC_JUDGE_DEFECT_KEYS)):
        if key in out:
            rows = out[key]
            out[key] = [_pick(row, keys) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    return out


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    """A job as built by the API's `job_out`, reduced to what a visitor may see (also for their own clips)."""
    out = _pick(job, PUBLIC_JOB_KEYS)
    if "progress" in out:        # stored verbatim from the gateway: only a plain percentage passes
        progress = out["progress"]
        out["progress"] = progress if isinstance(progress, str) and _PROGRESS.fullmatch(progress) else ""
    out["spec"] = public_spec(job.get("spec"))
    out["signals"] = public_signals(job.get("signals"))
    out["judge"] = public_judge(job.get("judge"))
    out["cost"] = _pick(job.get("cost"), PUBLIC_COST_KEYS)
    out["provenance"] = _pick(job.get("provenance"), PUBLIC_PROVENANCE_KEYS)
    return out


def public_run(run: dict[str, Any]) -> dict[str, Any]:
    out = _pick(run, PUBLIC_RUN_KEYS)
    if "params" in out:
        out["params"] = _pick(out["params"], PUBLIC_RUN_PARAM_KEYS)
    return out


def public_rating(rating: Any) -> dict[str, Any] | None:
    """Someone else's rating: the scores without the free-text comment. (A viewer's own ratings are not reduced.)"""
    return _pick(rating, PUBLIC_RATING_KEYS) if isinstance(rating, dict) else None


def public_plan(planned: dict[str, Any]) -> dict[str, Any]:
    """A run plan without the request payloads that would be sent to the gateway."""
    return {**planned, "items": [_pick(item, PUBLIC_PLAN_ITEM_KEYS) for item in planned.get("items") or []]}


# ---------------------------------------------------------------------- config and models

PUBLIC_CONFIG_KEYS = ("mock", "public", "admin", "admin_login", "endpoints", "spent_usd", "usd_cny", "judge_model",
                      "tasks")
PUBLIC_ENDPOINT_KEYS = ("key", "name", "rpm")      # not last_error / disabled_reason: raw gateway text


def public_config(config: dict[str, Any]) -> dict[str, Any]:
    out = _pick(config, PUBLIC_CONFIG_KEYS)
    if "endpoints" in out:
        endpoints = out["endpoints"]
        out["endpoints"] = [_pick(e, PUBLIC_ENDPOINT_KEYS) for e in endpoints] if isinstance(endpoints, list) else []
    return out


def public_probe_overlay(probe: Any) -> dict[str, Any]:
    """Probe results for one model: which tasks and input deliveries work, and whether it is unavailable.
    The stored reason, notes and per-case records quote the gateway and stay private."""
    out: dict[str, Any] = {}
    if not isinstance(probe, dict):
        return out
    tasks = probe.get("tasks")
    if isinstance(tasks, dict):
        out["tasks"] = {str(k): v for k, v in tasks.items() if v in ("ok", "fail")}
    delivery = probe.get("delivery")
    if isinstance(delivery, dict):
        out["delivery"] = {str(kind): {str(m): s for m, s in methods.items() if s in ("yes", "no", "unknown")}
                           for kind, methods in delivery.items() if isinstance(methods, dict)}
    if probe.get("unavailable"):
        out["unavailable"] = True
    return out


# ---------------------------------------------------------------------- deployments and benchmarks

PUBLIC_HARDWARE_KEYS = ("gpu", "gpu_count", "memory_gb_per_gpu")
PUBLIC_DEPLOYMENT_KEYS = ("id", "model", "label", "provider", "timing_scope")
PUBLIC_EXPERIMENT_KEYS = ("id", "title", "date", "dataset_id", "deployment_ids", "n_attempts", "n_videos")
PUBLIC_BENCH_CONFIG_KEYS = (
    "model_repo", "model_revision", "partition", "vllm_omni_tag", "source_commit", "image", "gpu_count",
    "tensor_parallel_size", "ulysses", "ring", "text_encoder_tp_size", "vae_patch_parallel_size",
    "vae_parallel_mode", "vae_use_tiling", "attention_backend", "width", "height", "fps", "nominal_seconds",
    "num_inference_steps", "flow_shift", "audio_flow_shift", "poll_seconds")
PUBLIC_BENCH_TURBO_KEYS = ("repo", "revision", "filename", "sha256", "size_bytes", "num_inference_steps",
                           "intended_denoise_updates", "flow_shift", "audio_flow_shift", "backend", "rank",
                           "declared_alpha", "scale")
PUBLIC_SUMMARY_PASSTHROUGH = ("groups", "phases", "telemetry", "utilization")
# no task ids and no request ids
PUBLIC_SCENE_KEYS = ("case_id", "title", "task", "baseline_n", "baseline_p50_s", "baseline_p95_s", "baseline_min_s",
                     "baseline_max_s", "matched_baseline_seed", "matched_baseline_e2e_s", "official_e2e_s")
PUBLIC_COSTS_KEYS = ("generated_utc", "currency", "api_usage_price_estimate_usd", "self_technical_valid_outputs",
                     "official_technical_valid_outputs")
PUBLIC_ATTEMPT_KEYS = ("id", "experiment_id", "deployment_id", "case_id", "attempt", "job_id")
PUBLIC_ATTEMPT_METRIC_KEYS = (
    # identity
    "case_id", "task", "phase", "provider", "variant", "seed", "attempt", "status", "outcome", "technical_valid",
    "preferred",
    # timing
    "e2e_seconds", "submit_to_terminal_seconds", "server_inference_time_s", "server_peak_memory_mb",
    "poll_interval_seconds", "poll_detection_bound_seconds", "http_download_transfer_seconds",
    # settings
    "requested_num_inference_steps", "actual_denoise_updates", "turbo_activation_verified",
    "variant_evidence_scope",
    # media
    "width", "height", "fps", "frames", "duration_seconds", "video_codec", "audio_codec", "audio_sample_rate",
    "audio_channels", "size_bytes",
    # cost estimate
    "estimated_cost_usd", "estimated_cost_per_video_second_usd", "cost_source", "api_cost_estimate_usd",
    "api_cost_source", "currency")
PUBLIC_ARTIFACT = re.compile(r"report\.(?:md|docx|html)|licenses\.md|figures/[A-Za-z0-9_.-]+\.(?:png|svg)")


def public_hardware(hardware: Any) -> dict[str, Any]:
    return _pick(hardware, PUBLIC_HARDWARE_KEYS)


def public_deployment(deployment: dict[str, Any]) -> dict[str, Any]:
    return {**_pick(deployment, PUBLIC_DEPLOYMENT_KEYS), "hardware": public_hardware(deployment.get("hardware"))}


def public_experiment_row(row: dict[str, Any]) -> dict[str, Any]:
    return _pick(row, PUBLIC_EXPERIMENT_KEYS)


def public_benchmark_config(config: Any) -> dict[str, Any]:
    out = _pick(config, PUBLIC_BENCH_CONFIG_KEYS)
    if isinstance(config, dict) and isinstance(config.get("turbo"), dict):
        out["turbo"] = _pick(config["turbo"], PUBLIC_BENCH_TURBO_KEYS)
    return out


def public_benchmark_summary(summary: Any) -> dict[str, Any]:
    out = _pick(summary, PUBLIC_SUMMARY_PASSTHROUGH)
    if not isinstance(summary, dict):
        return out
    if "scenes" in summary:
        scenes = summary["scenes"]
        out["scenes"] = [_pick(s, PUBLIC_SCENE_KEYS) for s in scenes] if isinstance(scenes, list) else []
    if "costs" in summary:
        out["costs"] = _pick(summary["costs"], PUBLIC_COSTS_KEYS)
    return out


def public_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
    return {**_pick(attempt, PUBLIC_ATTEMPT_KEYS), "metrics": _pick(attempt.get("metrics"), PUBLIC_ATTEMPT_METRIC_KEYS)}


def is_public_artifact(name: str) -> bool:
    """Report files and figures anyone may download; raw records (attempts.csv, provenance, figure data) are not."""
    return bool(PUBLIC_ARTIFACT.fullmatch(name or ""))


def public_experiment(detail: dict[str, Any]) -> dict[str, Any]:
    """A benchmark experiment as built by the API (config, summary, deployments, artifacts, attempts)."""
    artifacts = detail.get("artifacts")
    return {
        **_pick(detail, ("id", "title", "date", "dataset_id", "deployment_ids")),
        "config": public_benchmark_config(detail.get("config")),
        "summary": public_benchmark_summary(detail.get("summary")),
        "deployments": [public_deployment(d) for d in detail.get("deployments") or []],
        "artifacts": {name: url for name, url in artifacts.items() if is_public_artifact(name)}
        if isinstance(artifacts, dict) else {},
        "attempts": [public_attempt(a) for a in detail.get("attempts") or []],
    }
