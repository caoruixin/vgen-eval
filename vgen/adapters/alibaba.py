"""HappyHorse (documented) and Wan 2.7 / Wan 3.0 (inferred, same Alibaba channel shape)."""

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
    set_path,
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
    payload: dict = {
        "model": model.id,
        "prompt": render_refs(spec.prompt, "alibaba"),
        "size": res.upper(),
        "duration": dur,
    }
    if model.watermark_param:
        set_path(payload, model.watermark_param, False)
    if model.prompt_rewrite_param:
        set_path(payload, model.prompt_rewrite_param, False)
    if spec.seed is not None:
        set_path(payload, "metadata.seed", int(spec.seed))
    if spec.negative_prompt:
        if model.family == "wan":
            payload["negative_prompt"] = spec.negative_prompt
        else:
            r.adjustments.append(Adjustment("negative_prompt", "set", "dropped", "not supported by HappyHorse"))
    resolve_audio(model, spec.audio, r)

    media: list[dict] = []
    ratio = spec.ratio
    task = spec.task
    if task == "i2v":
        if not spec.first_frame:
            raise Unsupported("i2v needs a first-frame image")
        payload["input_reference"] = assets.deliver(model, spec.first_frame, r)
        ratio = "auto"
    elif task == "flf2v":
        if not (spec.first_frame and spec.last_frame):
            raise Unsupported("flf2v needs first and last frames")
        media.append({"type": "first_frame", "url": assets.deliver(model, spec.first_frame, r)})
        media.append({"type": "last_frame", "url": assets.deliver(model, spec.last_frame, r)})
        ratio = "auto"
    elif task == "r2v":
        refs = spec.references or ([spec.first_frame] if spec.first_frame else [])
        if not refs:
            raise Unsupported("r2v needs at least one reference image")
        if model.max_refs and len(refs) > model.max_refs:
            r.adjustments.append(Adjustment("references", len(refs), model.max_refs, "too many references"))
            refs = refs[: model.max_refs]
        media.extend({"type": "reference_image", "url": assets.deliver(model, ref, r)} for ref in refs)
    elif task == "v2v_edit":
        if not spec.source_video:
            raise Unsupported("video edit needs a source video")
        src = assets.get(spec.source_video.asset_id)
        media.append({"type": "video", "url": assets.deliver(model, spec.source_video, r)})
        for ref in spec.references[:1]:
            media.append({"type": "reference_image", "url": assets.deliver(model, ref, r)})
        ratio = "auto"
        payload.pop("duration", None)
        if src.duration:
            dur = int(-(-src.duration // 1))      # billed on the output, which keeps the source length
        r.adjustments.append(Adjustment("duration", spec.duration, f"source ({dur}s)", "edits keep the source length"))
    elif task == "a2v":
        if not spec.audio_input:
            raise Unsupported("audio-driven video needs an audio input")
        image = spec.first_frame or (spec.references[0] if spec.references else None)
        if image:
            media.append({"type": "reference_image", "url": assets.deliver(model, image, r)})
        media.append({"type": "audio", "url": assets.deliver(model, spec.audio_input, r)})
    elif task == "extend":
        raise Unsupported(f"{model.label} does not support video extension")

    if media:
        set_path(payload, "metadata.input.media", media)
    if ratio != "auto" and task in ("t2v", "r2v", "a2v"):
        used = snap_ratio(model, ratio, r)
        if used != "auto":
            set_path(payload, "metadata.ratio", used)
            ratio = used
    r.payload = payload
    r.effective = {
        "duration": dur,
        "resolution": res,
        "ratio": ratio,
        "audio": False,
        "has_video_input": task == "v2v_edit",
    }
    return r
