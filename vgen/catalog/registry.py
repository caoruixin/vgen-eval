"""Model registry: static YAML facts + probe overlay + live availability."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

CATALOG_DIR = Path(__file__).resolve().parent
PROBE_STATUS_FILE = CATALOG_DIR / "probe_status.json"

RESOLUTION_ORDER = ["360p", "480p", "540p", "720p", "1080p", "4k"]
TIER_SHORT_SIDE = {"360p": 360, "480p": 480, "540p": 540, "720p": 720, "1080p": 1080, "4k": 2160}

# Seedance output sizes per (resolution, ratio); used for token estimates. Other families use the
# same table as an approximation of pixel count.
PIXELS: dict[str, dict[str, tuple[int, int]]] = {
    # 16:9 sizes as observed from Seedance 2.x on tokenbay.com (2026-10-29); other ratios from the Seedance docs
    "480p": {"16:9": (864, 496), "4:3": (736, 544), "1:1": (640, 640), "3:4": (544, 736),
             "9:16": (496, 864), "21:9": (960, 416)},
    "720p": {"16:9": (1280, 720), "4:3": (1120, 832), "1:1": (960, 960), "3:4": (832, 1120),
             "9:16": (720, 1280), "21:9": (1504, 640)},
    "1080p": {"16:9": (1920, 1088), "4:3": (1664, 1248), "1:1": (1440, 1440), "3:4": (1248, 1664),
              "9:16": (1088, 1920), "21:9": (2176, 928)},
}

TASKS = ["t2v", "i2v", "flf2v", "r2v", "v2v_edit", "extend", "a2v"]


class ModelInfo(BaseModel):
    id: str
    family: str
    vendor: str = ""
    adapter: str = ""
    label: str
    endpoints: list[str]
    doc: str = "inferred"
    tasks: list[str] = Field(default_factory=list)
    experimental_tasks: list[str] = Field(default_factory=list)
    durations: Any = None
    by_resolution: dict[str, list[int]] = Field(default_factory=dict)
    resolutions: list[str] = Field(default_factory=list)
    ratios: list[str] = Field(default_factory=list)
    audio: str = "unknown"                     # none | optional | always | unknown
    delivery: dict[str, dict[str, str]] = Field(default_factory=dict)
    max_refs: int = 0
    latency_s: float = 120
    watermark_param: str | None = None
    prompt_rewrite_param: str | None = None
    notes: dict[str, str] = Field(default_factory=dict)
    # filled from probe overlay
    probe: dict[str, Any] = Field(default_factory=dict)

    @field_validator("delivery", mode="before")
    @classmethod
    def _yaml_booleans(cls, value: Any) -> Any:
        """YAML 1.1 parses yes/no as booleans; keep them as the strings the code compares against."""
        if isinstance(value, dict):
            return {k: {m: ("yes" if s is True else "no" if s is False else str(s)) for m, s in (v or {}).items()}
                    for k, v in value.items()}
        return value

    def allowed_durations(self, resolution: str | None = None) -> list[int]:
        if resolution and resolution in self.by_resolution:
            return list(self.by_resolution[resolution])
        d = self.durations
        if isinstance(d, list):
            return [int(x) for x in d]
        if isinstance(d, dict):
            return list(range(int(d.get("min", 1)), int(d.get("max", 15)) + 1))
        return list(range(1, 16))

    def task_state(self, task: str) -> str:
        """enabled | experimental | probe_failed | unsupported."""
        if self.probe.get("unavailable") and task in (*self.tasks, *self.experimental_tasks):
            return "probe_failed"
        probe_tasks = self.probe.get("tasks", {})
        if task in self.tasks:
            return "probe_failed" if probe_tasks.get(task) == "fail" else "enabled"
        if task in self.experimental_tasks:
            state = probe_tasks.get(task)
            if state == "ok":
                return "enabled"
            if state == "fail":
                return "probe_failed"
            return "experimental"
        return "unsupported"

    def delivery_state(self, kind: str, method: str) -> str:
        """yes | no | unknown — probe overlay wins over the YAML value."""
        over = self.probe.get("delivery", {}).get(kind, {}).get(method)
        if over:
            return over
        return self.delivery.get(kind, {}).get(method, "no")

    def note(self, lang: str) -> str:
        return self.notes.get(lang) or self.notes.get("en", "")


class Registry:
    def __init__(self, models: dict[str, ModelInfo], prices: dict[str, Any], probe: dict[str, Any]):
        self.models = models
        self.prices = prices
        self.probe = probe
        self.live: dict[str, set[str]] = {}       # endpoint -> model ids from /v1/models
        self.live_checked_at: float | None = None
        self.live_error: str | None = None

    @classmethod
    def load(cls, catalog_dir: Path = CATALOG_DIR) -> "Registry":
        raw = yaml.safe_load((catalog_dir / "models.yaml").read_text(encoding="utf-8"))
        prices = yaml.safe_load((catalog_dir / "prices.yaml").read_text(encoding="utf-8"))
        probe_file = Path(os.environ.get("VGEN_PROBE_STATUS", catalog_dir / "probe_status.json"))
        probe = json.loads(probe_file.read_text(encoding="utf-8")) if probe_file.exists() else {}
        families = raw.get("families", {})
        models: dict[str, ModelInfo] = {}
        for item in raw.get("models", []):
            fam = families.get(item["family"], {})
            info = ModelInfo(vendor=fam.get("vendor", ""), adapter=fam.get("adapter", item["family"]), **item)
            info.probe = probe.get("models", {}).get(info.id, {})
            models[info.id] = info
        return cls(models, prices, probe)

    # ------------------------------------------------------------------ lookups
    def get(self, model_id: str) -> ModelInfo:
        if model_id not in self.models:
            raise KeyError(f"Unknown model '{model_id}'")
        return self.models[model_id]

    def price(self, model_id: str) -> dict[str, Any] | None:
        return (self.prices.get("models") or {}).get(model_id)

    def judge_price(self, model_id: str) -> dict[str, float] | None:
        return (self.prices.get("judge") or {}).get(model_id)

    @property
    def quota_per_usd(self) -> float:
        return float(self.probe.get("quota_per_usd") or self.prices.get("quota_per_usd") or 500000)

    @property
    def quota_calibrated(self) -> bool:
        return bool(self.probe.get("quota_calibrated") or self.prices.get("quota_calibrated"))

    def set_live(self, endpoint: str, ids: set[str]) -> None:
        self.live[endpoint] = ids
        self.live_checked_at = time.time()
        self.live_error = None

    def available(self, model_id: str, endpoint: str) -> bool | None:
        """True/False when the live list is known; None if it has not been fetched."""
        if endpoint not in self.live:
            return None
        return model_id in self.live[endpoint]

    def unregistered(self, endpoint: str) -> list[str]:
        ids = self.live.get(endpoint, set())
        video_like = ("t2v", "i2v", "r2v", "video", "veo", "seedance", "wan", "kling", "hailuo", "vidu",
                      "pixverse", "happyhorse", "sora", "runway")
        return sorted(i for i in ids if i not in self.models and any(k in i.lower() for k in video_like)
                      and "image" not in i.lower())


def save_probe_status(data: dict[str, Any], path: Path = PROBE_STATUS_FILE) -> None:
    data = dict(data)
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
