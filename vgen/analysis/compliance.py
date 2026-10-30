"""Spec compliance: does the output match what was requested?"""

from __future__ import annotations

import numpy as np

from vgen.adapters.base import ratio_value

BROWSER_CODECS = {"h264": "ok", "vp9": "ok", "av1": "ok", "vp8": "ok", "hevc": "warn", "h265": "warn"}


TIER_AREA = {"360p": 640 * 360, "480p": 854 * 480, "540p": 960 * 540, "720p": 1280 * 720,
             "1080p": 1920 * 1080, "4k": 3840 * 2160}


def _tier_for(width: int, height: int) -> str:
    """Tier by pixel area, so portrait / square outputs (e.g. 834×1106 for 720p) are classified correctly."""
    area = width * height
    return min(TIER_AREA, key=lambda k: abs((TIER_AREA[k] ** 0.5) - area ** 0.5))


def detect_bars(frames_gray: list[np.ndarray], threshold: float = 18.0) -> dict:
    """Letterbox/pillarbox: rows/cols that stay near-black across the sampled frames."""
    if not frames_gray:
        return {"top": 0, "bottom": 0, "left": 0, "right": 0, "fraction": 0.0}
    stack = np.stack([f.astype(np.float32) for f in frames_gray[:: max(1, len(frames_gray) // 12)]])
    mean_img = stack.max(axis=0)          # a bar must be dark in every sampled frame
    rows = mean_img.mean(axis=1)
    cols = mean_img.mean(axis=0)

    def run(values: np.ndarray) -> int:
        n = 0
        for v in values:
            if v < threshold:
                n += 1
            else:
                break
        return n

    h, w = mean_img.shape
    top, bottom = run(rows), run(rows[::-1])
    left, right = run(cols), run(cols[::-1])
    # ignore 1-2 px encoder borders and fully black clips (handled by blank-frame check)
    if top + bottom >= h * 0.95 or left + right >= w * 0.95:
        return {"top": 0, "bottom": 0, "left": 0, "right": 0, "fraction": 0.0}
    top, bottom = (top if top > h * 0.02 else 0), (bottom if bottom > h * 0.02 else 0)
    left, right = (left if left > w * 0.02 else 0), (right if right > w * 0.02 else 0)
    frac = 1 - ((h - top - bottom) * (w - left - right)) / (h * w)
    return {"top": int(top), "bottom": int(bottom), "left": int(left), "right": int(right), "fraction": round(frac, 3)}


def blank_ratio(frames_gray: list[np.ndarray]) -> float:
    if not frames_gray:
        return 1.0
    blank = sum(1 for f in frames_gray if float(np.std(f)) < 4.0)
    return round(blank / len(frames_gray), 3)


def check(media: dict, effective: dict, task: str, model_audio: str, frames_gray: list[np.ndarray],
          audio: dict | None) -> list[dict]:
    items: list[dict] = []
    v = media.get("video") or {}
    w, h = v.get("width") or 0, v.get("height") or 0

    def add(key: str, ok: bool | None, expected, actual, note: str = "") -> None:
        items.append({"key": key, "ok": ok, "expected": expected, "actual": actual, "note": note})

    if not w or not h:
        add("video_stream", False, "video", "none", "no decodable video stream")
        return items

    req_res = effective.get("resolution")
    actual_tier = _tier_for(w, h)
    if req_res in TIER_AREA:
        ok = abs((w * h) ** 0.5 / TIER_AREA[req_res] ** 0.5 - 1) <= 0.12
        add("resolution", ok, req_res, f"{w}×{h} ({actual_tier})")
    else:
        add("resolution", None, req_res, f"{w}×{h} ({actual_tier})")

    ratio = effective.get("ratio")
    rv = ratio_value(ratio or "")
    actual_ratio = w / h
    if rv:
        add("aspect_ratio", abs(actual_ratio / rv - 1) <= 0.03, ratio, f"{actual_ratio:.3f}")
    else:
        add("aspect_ratio", None, ratio or "auto", f"{actual_ratio:.3f}", "follows input")

    dur = media.get("duration") or v.get("duration")
    exp_d = effective.get("duration")
    if task in ("v2v_edit", "extend") or not isinstance(exp_d, (int, float)) or exp_d <= 0:
        add("duration", None, exp_d, round(dur, 2) if dur else None)
    else:
        ok = dur is not None and abs(dur - exp_d) <= max(0.6, 0.1 * exp_d)
        add("duration", ok, exp_d, round(dur, 2) if dur else None)

    fps = v.get("fps")
    add("fps", (fps or 0) >= 15, "≥15", fps)

    codec = (v.get("codec") or "").lower()
    state = BROWSER_CODECS.get(codec, "fail")
    add("codec", True if state == "ok" else (None if state == "warn" else False), "h264/vp9/av1", codec,
        "may not play in every browser" if state == "warn" else "")

    has_audio = bool(audio and audio.get("has_audio"))
    audible = has_audio and (audio.get("silence_ratio") is not None and audio["silence_ratio"] < 0.9)
    if effective.get("audio"):
        add("audio", audible, "audible track", "silent" if has_audio and not audible else ("yes" if audible else "none"))
    else:
        note = "model may add sound by default" if model_audio in ("unknown", "always") else ""
        add("audio", None, "off", "yes" if audible else ("silent track" if has_audio else "none"), note)

    bars = detect_bars(frames_gray)
    add("black_bars", bars["fraction"] < 0.02, "none", f"{bars['fraction'] * 100:.0f}% of frame" if bars["fraction"] else "none")
    br = blank_ratio(frames_gray)
    add("blank_frames", br <= 0.1, "≤10%", f"{br * 100:.0f}%")
    return items
