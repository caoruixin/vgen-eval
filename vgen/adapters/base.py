"""Unified request spec, parameter snapping and asset delivery shared by all adapters."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from vgen.catalog.registry import RESOLUTION_ORDER, ModelInfo

Task = Literal["t2v", "i2v", "flf2v", "r2v", "v2v_edit", "extend", "a2v"]


class AssetRef(BaseModel):
    asset_id: str
    role: str = ""                       # first_frame | last_frame | reference | video | audio


class GenerationSpec(BaseModel):
    task: Task = "t2v"
    prompt: str = ""
    negative_prompt: str | None = None
    duration: int = 6
    resolution: str = "720p"
    ratio: str = "16:9"                  # "auto" = follow the input image
    audio: bool = False
    seed: int | None = None
    first_frame: AssetRef | None = None
    last_frame: AssetRef | None = None
    references: list[AssetRef] = Field(default_factory=list)
    source_video: AssetRef | None = None
    audio_input: AssetRef | None = None


class Unsupported(Exception):
    """The model cannot run this spec (task, input delivery or constraint)."""


@dataclass
class Adjustment:
    field: str
    requested: Any
    used: Any
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"field": self.field, "requested": self.requested, "used": self.used, "reason": self.reason}


@dataclass
class BuildResult:
    payload: dict[str, Any]
    adjustments: list[Adjustment] = field(default_factory=list)
    effective: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ResolvedAsset:
    asset_id: str
    kind: str                            # image | video | audio
    mime: str
    public_urls: list[str]
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    read_bytes: Callable[[], bytes] | None = None


class AssetResolver:
    """Turns asset ids into URLs or data URIs according to what a model accepts."""

    def __init__(self, lookup: Callable[[str], ResolvedAsset | None]):
        self._lookup = lookup

    def get(self, asset_id: str) -> ResolvedAsset:
        asset = self._lookup(asset_id)
        if asset is None:
            raise Unsupported(f"asset {asset_id[:12]} not found")
        return asset

    def deliver(self, model: ModelInfo, ref: AssetRef, result: BuildResult) -> str:
        asset = self.get(ref.asset_id)
        url_state = model.delivery_state(asset.kind, "url")
        data_state = model.delivery_state(asset.kind, "data_uri")
        if asset.public_urls and url_state in ("yes", "unknown"):
            return asset.public_urls[0]
        if data_state in ("yes", "unknown") and asset.read_bytes is not None:
            if data_state == "unknown":
                result.warnings.append(f"{asset.kind} sent as data URI, not yet verified for {model.id}")
            return f"data:{asset.mime};base64,{base64.b64encode(asset.read_bytes()).decode()}"
        if not asset.public_urls:
            raise Unsupported(f"{model.label} needs a public URL for {asset.kind} input")
        raise Unsupported(f"{model.label} cannot receive {asset.kind} input")


# ---------------------------------------------------------------------- snapping helpers

def snap_duration(model: ModelInfo, requested: int, resolution: str, result: BuildResult) -> int:
    allowed = model.allowed_durations(resolution)
    if requested in allowed:
        return requested
    used = min(allowed, key=lambda d: (abs(d - requested), -d))
    why = f"allowed at {resolution}: {allowed[0]}–{allowed[-1]} s" if len(allowed) > 4 else \
        f"allowed at {resolution}: {', '.join(str(a) for a in allowed)} s"
    result.adjustments.append(Adjustment("duration", requested, used, why))
    return used


def snap_resolution(model: ModelInfo, requested: str, result: BuildResult) -> str:
    if requested in model.resolutions:
        return requested
    order = {r: i for i, r in enumerate(RESOLUTION_ORDER)}
    target = order.get(requested, order["720p"])
    used = min(model.resolutions, key=lambda r: (abs(order.get(r, 0) - target), order.get(r, 0) > target))
    result.adjustments.append(Adjustment("resolution", requested, used, f"supported: {', '.join(model.resolutions)}"))
    return used


def ratio_value(ratio: str) -> float | None:
    m = re.fullmatch(r"(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)", ratio or "")
    return float(m.group(1)) / float(m.group(2)) if m else None


def snap_ratio(model: ModelInfo, requested: str, result: BuildResult, image: ResolvedAsset | None = None) -> str:
    """Returns the ratio to send ("auto" means: omit the field / follow the input image)."""
    allowed = [r for r in model.ratios if r not in ("auto",)]
    if requested == "auto":
        if "auto" in model.ratios or not allowed:
            return "auto"
        if "adaptive" in allowed:
            return "adaptive"
        # model needs an explicit ratio: pick the one closest to the input image (or 16:9)
        target = (image.width / image.height) if image and image.width and image.height else 16 / 9
        used = min((r for r in allowed if ratio_value(r)), key=lambda r: abs(_log_ratio(r) - _log(target)))
        result.adjustments.append(Adjustment("ratio", "auto", used, "model needs an explicit aspect ratio"))
        return used
    if requested in allowed or not allowed:
        return requested if allowed else "auto"
    target = ratio_value(requested) or 16 / 9
    candidates = [r for r in allowed if ratio_value(r)]
    used = min(candidates, key=lambda r: abs(_log_ratio(r) - _log(target)))
    result.adjustments.append(Adjustment("ratio", requested, used, f"supported: {', '.join(allowed)}"))
    return used


def _log(x: float) -> float:
    import math

    return math.log(x)


def _log_ratio(r: str) -> float:
    return _log(ratio_value(r) or 1.0)


def resolve_audio(model: ModelInfo, requested: bool, result: BuildResult) -> bool | None:
    """Returns True/False to send, or None when the model has no audio switch."""
    if model.audio == "optional":
        return requested
    if model.audio == "none" and requested:
        result.adjustments.append(Adjustment("audio", True, False, "model has no audio generation"))
    if model.audio == "always" and not requested:
        result.adjustments.append(Adjustment("audio", False, True, "model always generates audio"))
    if model.audio == "unknown" and requested:
        result.warnings.append("audio switch unknown for this model; output may be silent")
    return None


def check_task(model: ModelInfo, task: str, allow_experimental: bool) -> None:
    state = model.task_state(task)
    if state == "enabled":
        return
    if state == "experimental" and allow_experimental:
        return
    if state == "experimental":
        raise Unsupported(f"{task} is experimental for {model.label} (not yet verified by probe)")
    if state == "probe_failed":
        raise Unsupported(f"{task} failed the live probe for {model.label}")
    raise Unsupported(f"{model.label} does not support {task}")


def set_path(payload: dict[str, Any], dotted: str, value: Any) -> None:
    cur = payload
    parts = dotted.split(".")
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def render_refs(prompt: str, style: str) -> str:
    """Prompts may name references as {ref1}, {ref2}…; render them per family convention."""
    def sub(m: re.Match) -> str:
        n = m.group(1)
        if style == "alibaba":
            return f"[Image {n}]"
        if style == "zh":
            return f"图{n}"
        return f"reference image {n}"

    return re.sub(r"\{ref(\d+)\}", sub, prompt)


def redact_payload(payload: Any) -> Any:
    """Replace data URIs / long base64 blobs with a short marker for storage and display."""
    if isinstance(payload, dict):
        return {k: redact_payload(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [redact_payload(v) for v in payload]
    if isinstance(payload, str) and (payload.startswith("data:") or len(payload) > 2000):
        head = payload[:40]
        return f"{head}…<{len(payload)} chars>"
    return payload
