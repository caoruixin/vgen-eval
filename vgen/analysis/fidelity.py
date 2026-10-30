"""First/last-frame fidelity for I2V and FLF2V: does the clip start/end on the image that was sent?

Method: strip letterbox bars, centre-crop the reference to the frame aspect, compare at 128 px
greyscale after ECC (affine) alignment, keep the best match inside a 0.5 s window.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from vgen.analysis.compliance import detect_bars

GRADES = ((0.85, "high"), (0.65, "medium"))


def _grade(score: float) -> str:
    for threshold, name in GRADES:
        if score >= threshold:
            return name
    return "low"


def _prep(img: np.ndarray, width: int = 128) -> np.ndarray:
    h, w = img.shape[:2]
    out = cv2.resize(img, (width, max(8, int(h * width / w))), interpolation=cv2.INTER_AREA)
    out = out.astype(np.float32)
    return (out - out.mean()) / (out.std() + 1e-6)


def _center_crop(img: np.ndarray, aspect: float) -> np.ndarray:
    h, w = img.shape[:2]
    if w / h > aspect:
        nw = int(h * aspect)
        x = (w - nw) // 2
        return img[:, x:x + nw]
    nh = int(w / aspect)
    y = (h - nh) // 2
    return img[y:y + nh, :]


def _similarity(ref_gray: np.ndarray, frame_gray: np.ndarray) -> float:
    bars = detect_bars([frame_gray])
    f = frame_gray[bars["top"]:frame_gray.shape[0] - bars["bottom"], bars["left"]:frame_gray.shape[1] - bars["right"]]
    if f.size == 0:
        return 0.0
    aspect = f.shape[1] / f.shape[0]
    ref = _center_crop(ref_gray, aspect)
    a = _prep(ref)
    b = _prep(f)
    b = cv2.resize(b, (a.shape[1], a.shape[0]))
    try:
        warp = np.eye(2, 3, dtype=np.float32)
        criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-4)
        cc, _ = cv2.findTransformECC(a, b, warp, cv2.MOTION_AFFINE, criteria, None, 3)
        return float(cc)
    except cv2.error:
        return float(np.mean(a * b))   # plain normalised correlation


def frame_fidelity(frames_gray: list[np.ndarray], times: list[float], ref_path: Path, which: str = "first",
                   window: float = 0.5) -> dict:
    if not frames_gray:
        return {"score": None, "grade": "n/a"}
    with Image.open(ref_path) as im:
        ref = np.asarray(im.convert("L"))
    end = times[-1]
    if which == "first":
        idx = [i for i, t in enumerate(times) if t <= times[0] + window] or [0]
    else:
        idx = [i for i, t in enumerate(times) if t >= end - window] or [len(times) - 1]
    best, best_t = -1.0, None
    for i in idx:
        s = _similarity(ref, frames_gray[i])
        if s > best:
            best, best_t = s, times[i]
    return {"score": round(best, 3), "grade": _grade(best), "t": best_t, "which": which}
