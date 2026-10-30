"""Seedance 2.0 / fast (documented) and mini / 2.5 (inferred, same shape)."""

from __future__ import annotations

import re

from vgen.adapters.base import (
    Adjustment,
    AssetResolver,
    BuildResult,
    GenerationSpec,
    Unsupported,
    check_task,
    render_refs,
    resolve_audio,
    set_path,
    snap_duration,
    snap_ratio,
    snap_resolution,
)
from vgen.catalog.registry import ModelInfo

_CJK = re.compile(r"[一-鿿]")


def _image_item(url: str, role: str) -> dict:
    return {"type": "image_url", "image_url": {"url": url}, "role": role}


def build(model: ModelInfo, spec: GenerationSpec, assets: AssetResolver, allow_experimental: bool = False) -> BuildResult:
    check_task(model, spec.task, allow_experimental)
    r = BuildResult(payload={})
    res = snap_resolution(model, spec.resolution, r)
    dur = snap_duration(model, spec.duration, res, r)
    style = "zh" if _CJK.search(spec.prompt or "") else "en"
    payload: dict = {"model": model.id, "prompt": render_refs(spec.prompt, style), "duration": dur}
    set_path(payload, "metadata.resolution", res)
    if model.watermark_param:
        set_path(payload, model.watermark_param, False)
    audio = resolve_audio(model, spec.audio, r)
    if audio is not None:
        set_path(payload, "metadata.generate_audio", bool(audio))
    if spec.seed is not None:
        set_path(payload, "metadata.seed", int(spec.seed))
    if spec.negative_prompt:
        r.adjustments.append(Adjustment("negative_prompt", "set", "dropped", "not supported by Seedance"))

    task = spec.task
    content: list[dict] = []
    first = assets.get(spec.first_frame.asset_id) if spec.first_frame else None
    ratio_req = spec.ratio
    has_video = False
    input_video_s = 0.0
    if task == "i2v":
        if not spec.first_frame:
            raise Unsupported("i2v needs a first-frame image")
        payload["image"] = assets.deliver(model, spec.first_frame, r)
    elif task == "flf2v":
        if not (spec.first_frame and spec.last_frame):
            raise Unsupported("flf2v needs first and last frames")
        content.append(_image_item(assets.deliver(model, spec.first_frame, r), "first_frame"))
        content.append(_image_item(assets.deliver(model, spec.last_frame, r), "last_frame"))
    elif task == "r2v":
        refs = spec.references or ([spec.first_frame] if spec.first_frame else [])
        if not refs:
            raise Unsupported("r2v needs at least one reference image")
        if model.max_refs and len(refs) > model.max_refs:
            r.adjustments.append(Adjustment("references", len(refs), model.max_refs, "too many references"))
            refs = refs[: model.max_refs]
        content.extend(_image_item(assets.deliver(model, ref, r), "reference_image") for ref in refs)
    elif task == "extend":
        if not spec.source_video:
            raise Unsupported("extension needs a source video")
        src = assets.get(spec.source_video.asset_id)
        content.append({"type": "video_url", "video_url": {"url": assets.deliver(model, spec.source_video, r)},
                        "role": "reference_video"})
        has_video = True
        input_video_s = float(src.duration or 0.0)
    elif task == "a2v":
        if not spec.audio_input:
            raise Unsupported("audio-driven video needs an audio input")
        if spec.first_frame:      # multimodal-reference mode: the image is a reference, not a hard first frame
            content.append(_image_item(assets.deliver(model, spec.first_frame, r), "reference_image"))
        content.append({"type": "audio_url", "audio_url": {"url": assets.deliver(model, spec.audio_input, r)},
                        "role": "reference_audio"})
    elif task == "v2v_edit":
        raise Unsupported(f"{model.label} does not support instruction-based video editing")

    if content:
        set_path(payload, "metadata.content", content)
    if task in ("i2v", "flf2v", "a2v") and ratio_req in ("auto", "adaptive"):
        ratio = "adaptive"
    else:
        ratio = snap_ratio(model, ratio_req, r, image=first)
        if ratio == "auto":
            ratio = "adaptive"
    set_path(payload, "metadata.ratio", ratio)
    r.payload = payload
    r.effective = {
        "duration": dur,
        "resolution": res,
        "ratio": ratio,
        "audio": bool(audio),
        "has_video_input": has_video,
        "input_video_s": input_video_s,
    }
    return r
