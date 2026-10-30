"""Veo 3.1 (generate / fast / lite) — documented fields."""

from __future__ import annotations

from vgen.adapters.base import (
    Adjustment,
    AssetResolver,
    BuildResult,
    GenerationSpec,
    Unsupported,
    check_task,
    render_refs,
    resolve_audio,
    snap_duration,
    snap_ratio,
    snap_resolution,
)
from vgen.catalog.registry import ModelInfo


def build(model: ModelInfo, spec: GenerationSpec, assets: AssetResolver, allow_experimental: bool = False) -> BuildResult:
    check_task(model, spec.task, allow_experimental)
    r = BuildResult(payload={})
    res = snap_resolution(model, spec.resolution, r)
    dur = snap_duration(model, spec.duration, res, r)
    first = assets.get(spec.first_frame.asset_id) if spec.first_frame else None
    ratio = snap_ratio(model, spec.ratio, r, image=first)
    payload: dict = {
        "model": model.id,
        "prompt": render_refs(spec.prompt, "en"),
        "resolution": res,
        "duration": dur,
        "aspect_ratio": ratio,
        "enhance_prompt": False,
    }
    audio = resolve_audio(model, spec.audio, r)
    payload["generate_audio"] = bool(audio)
    if spec.negative_prompt:
        payload["negative_prompt"] = spec.negative_prompt
    if spec.seed is not None:
        payload["seed"] = int(spec.seed)

    task = spec.task
    if task == "i2v":
        if not spec.first_frame:
            raise Unsupported("i2v needs a first-frame image")
        payload["image"] = assets.deliver(model, spec.first_frame, r)
    elif task == "flf2v":
        if not (spec.first_frame and spec.last_frame):
            raise Unsupported("flf2v needs first and last frames")
        payload["image"] = assets.deliver(model, spec.first_frame, r)
        payload["lastFrameImage"] = assets.deliver(model, spec.last_frame, r)
    elif task == "r2v":
        refs = spec.references or ([spec.first_frame] if spec.first_frame else [])
        if not refs:
            raise Unsupported("r2v needs a reference image")
        if len(refs) > model.max_refs:
            r.adjustments.append(Adjustment("references", len(refs), model.max_refs, "Veo takes one reference image here"))
        payload["input_reference"] = assets.deliver(model, refs[0], r)
    elif task in ("v2v_edit", "extend", "a2v"):
        raise Unsupported(f"{model.label} does not support {task} on this gateway")

    r.payload = payload
    r.effective = {"duration": dur, "resolution": res, "ratio": ratio, "audio": bool(audio), "has_video_input": False}
    return r
