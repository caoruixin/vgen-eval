"""Worst-case cost estimates from public prices, and quota -> USD conversion."""

from __future__ import annotations

from dataclasses import dataclass

from vgen.catalog.registry import PIXELS, Registry

SEEDANCE_FPS = 24
AUTO_DURATION_WORST_S = 15


@dataclass
class Estimate:
    usd: float | None            # None = price unknown
    basis: str
    per_second: float | None = None

    def as_dict(self) -> dict:
        return {"usd": None if self.usd is None else round(self.usd, 4), "basis": self.basis,
                "per_second": None if self.per_second is None else round(self.per_second, 4)}


def _tier(res: str, table: dict) -> str | None:
    if res in table:
        return res
    order = ["480p", "720p", "1080p", "4k"]
    if res in order:
        later = [r for r in order[order.index(res):] if r in table]
        if later:
            return later[0]
    return max(table, key=lambda r: table[r]) if table else None


def video_tokens(resolution: str, ratio: str, seconds: float) -> int:
    sizes = PIXELS.get(resolution) or PIXELS["1080p"]
    if ratio in sizes:
        w, h = sizes[ratio]
    else:  # adaptive / auto / unknown ratio -> worst case pixel count
        w, h = max(sizes.values(), key=lambda wh: wh[0] * wh[1])
    # Seedance bills fps x seconds + 1 frames (usage.totalTokens of all 2026-10-29 clips matched exactly)
    return int(w * h * (SEEDANCE_FPS * seconds + 1) / 1024) if seconds else 0


def estimate_job(registry: Registry, model_id: str, effective: dict, *, price: dict | None = None) -> Estimate:
    price = price if price is not None else registry.price(model_id)
    if not price:
        return Estimate(None, "no public price")
    dur = effective.get("duration") or AUTO_DURATION_WORST_S
    if dur in (-1, "auto"):
        dur = AUTO_DURATION_WORST_S
    dur = float(dur)
    res = effective.get("resolution", "720p")
    audio = bool(effective.get("audio"))
    if "per_second" in price:
        tier = _tier(res, price["per_second"])
        if tier is None:
            return Estimate(None, f"no price for {res}")
        rate = float(price["per_second"][tier])
        mult = float(price.get("audio_multiplier", 1.0)) if audio else 1.0
        usd = rate * dur * mult
        basis = f"${rate:g}/s × {dur:g}s @ {tier}" + (f" × {mult:g} (audio, worst case)" if mult != 1 else "")
        return Estimate(usd, basis, per_second=usd / dur if dur else None)
    if "per_mtok" in price:
        has_video = bool(effective.get("has_video_input"))
        table = price["per_mtok"]["with_video" if has_video else "without_video"]
        tier = _tier(res, table)
        if tier is None:
            return Estimate(None, f"no price for {res}")
        rate = float(table[tier])
        ratio = effective.get("ratio", "16:9")
        tokens = video_tokens(tier if tier in PIXELS else "1080p", ratio, dur)
        in_tokens = video_tokens(tier if tier in PIXELS else "1080p", ratio, float(effective.get("input_video_s") or 0)) if has_video else 0
        usd = (tokens + in_tokens) * rate / 1e6
        basis = f"{tokens + in_tokens:,} tok × ${rate:g}/M @ {tier}"
        return Estimate(usd, basis, per_second=usd / dur if dur else None)
    return Estimate(None, "unrecognised price format")


def quota_to_usd(registry: Registry, quota: int | None) -> float | None:
    if quota is None:
        return None
    return quota / registry.quota_per_usd


def estimate_judge(registry: Registry, judge_model: str, video_seconds: float, fps: float = 6, images: int = 0,
                   with_audio: bool = False) -> float | None:
    price = registry.judge_price(judge_model)
    if not price:
        return None
    # Frame-sequence judge: ~2 fps (8–16 frames) at ≤448 px ≈ 258 tokens each, plus prompt; thinking counts as output.
    frames = max(8, min(16, round(video_seconds * 2)))
    in_tokens = 258 * (frames + images) * 1.5 + 900
    out_tokens = 2500
    return in_tokens * price["input"] / 1e6 + out_tokens * price["output"] / 1e6
