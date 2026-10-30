"""Objective signals from sampled frames. These are warnings/information, not quality scores."""

from __future__ import annotations

import math

import cv2
import numpy as np


def _small(f: np.ndarray, side: int = 256) -> np.ndarray:
    h, w = f.shape[:2]
    s = min(1.0, side / max(h, w))
    return cv2.resize(f, (max(8, int(w * s)), max(8, int(h * s))), interpolation=cv2.INTER_AREA) if s < 1 else f


def compute(frames_gray: list[np.ndarray], times: list[float]) -> dict:
    if len(frames_gray) < 2:
        return {"pairs": 0}
    small = [_small(f) for f in frames_gray]
    h, w = small[0].shape[:2]
    diag = math.hypot(w, h)
    motion: list[float] = []
    frozen = 0
    cuts: list[float] = []
    hists = [cv2.calcHist([f], [0], None, [64], [0, 256]) for f in small]
    for h_ in hists:
        cv2.normalize(h_, h_)
    for i in range(1, len(small)):
        a, b = small[i - 1], small[i]
        diff = float(np.mean(cv2.absdiff(a, b)))
        if diff < 0.6:
            frozen += 1
        corr = float(cv2.compareHist(hists[i - 1], hists[i], cv2.HISTCMP_CORREL))
        if corr < 0.55 and diff > 12:
            cuts.append(round(times[i], 2))
            motion.append(float("nan"))
            continue
        flow = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        mag = np.linalg.norm(flow, axis=2)
        dt = max(1e-3, times[i] - times[i - 1])
        motion.append(float(np.mean(mag)) / diag * 100 / dt)   # % of diagonal per second
    valid = [m for m in motion if not math.isnan(m)]
    pairs = len(small) - 1
    frozen_ratio = frozen / pairs
    return {
        "pairs": pairs,
        "motion_pct_per_s": round(float(np.mean(valid)), 2) if valid else 0.0,
        "motion_timeline": [None if math.isnan(m) else round(m, 2) for m in motion],
        "frozen_ratio": round(frozen_ratio, 3),
        "frozen_flag": pairs >= 6 and frozen_ratio >= 0.3,
        "scene_cuts": cuts,
    }
