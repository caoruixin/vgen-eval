from __future__ import annotations

import math
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest
from PIL import Image

from vgen.analysis import analyze_video, compliance, media, signals
from vgen.analysis.fidelity import frame_fidelity


def make_clip(path: Path, frame_fn, seconds: float = 3.0, fps: int = 24, w: int = 640, h: int = 360,
              tone: bool = False) -> Path:
    with av.open(str(path), "w", format="mp4") as out:
        vs = out.add_stream("libx264", rate=fps)
        vs.width, vs.height, vs.pix_fmt = w, h, "yuv420p"
        vs.options = {"preset": "ultrafast", "crf": "18"}
        aus = out.add_stream("aac", rate=44100) if tone else None
        for i in range(int(seconds * fps)):
            img = frame_fn(i / fps, w, h)
            for p in vs.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
                out.mux(p)
        for p in vs.encode():
            out.mux(p)
        if aus is not None:
            sr, n = 44100, int(seconds * 44100)
            for k in range(0, n, 1024):
                t = np.arange(k, min(n, k + 1024)) / sr
                af = av.AudioFrame.from_ndarray((0.3 * np.sin(2 * math.pi * 440 * t)).astype(np.float32).reshape(1, -1),
                                                format="fltp", layout="mono")
                af.sample_rate, af.pts, af.time_base = sr, k, Fraction(1, sr)
                for p in aus.encode(af):
                    out.mux(p)
            for p in aus.encode():
                out.mux(p)
    return path


def textured(w: int, h: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, (h // 8, w // 8, 3), dtype=np.uint8)
    return np.asarray(Image.fromarray(base).resize((w, h), Image.BILINEAR))


def static_frame(t, w, h):
    return textured(w, h, 1)


def moving_square(t, w, h):
    img = np.full((h, w, 3), 30, np.uint8)
    x = int((t / 3.0) * (w - 80))
    img[h // 2 - 40:h // 2 + 40, x:x + 80] = (240, 200, 40)
    return img


def letterboxed(t, w, h):
    img = np.zeros((h, w, 3), np.uint8)
    bar = int(h * 0.12)
    img[bar:h - bar] = textured(w, h - 2 * bar, 2)
    return img


def test_static_clip_is_frozen(tmp_path):
    clip = make_clip(tmp_path / "static.mp4", static_frame)
    frames, times = media.sample_frames(clip, fps=6, gray=True)
    sig = signals.compute(frames, times)
    assert sig["motion_pct_per_s"] < 0.5
    assert sig["frozen_flag"] is True


def test_moving_clip_has_motion(tmp_path):
    clip = make_clip(tmp_path / "move.mp4", moving_square)
    frames, times = media.sample_frames(clip, fps=6, gray=True)
    sig = signals.compute(frames, times)
    assert sig["motion_pct_per_s"] > 0.5
    assert sig["frozen_flag"] is False
    assert sig["scene_cuts"] == []


def test_letterbox_detected(tmp_path):
    clip = make_clip(tmp_path / "lb.mp4", letterboxed)
    frames, _ = media.sample_frames(clip, fps=4, gray=True)
    bars = compliance.detect_bars(frames)
    assert bars["fraction"] > 0.15
    assert bars["top"] > 0 and bars["bottom"] > 0


def test_probe_and_compliance(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", moving_square, seconds=5, w=1280, h=720, tone=True)
    meta = media.probe(clip)
    assert meta["video"]["width"] == 1280 and meta["video"]["codec"] == "h264"
    assert meta["audio"]
    audio = media.audio_stats(clip)
    assert audio["has_audio"] and audio["silence_ratio"] < 0.5
    frames, _ = media.sample_frames(clip, fps=4, gray=True)
    items = {c["key"]: c for c in compliance.check(meta, {"duration": 5, "resolution": "720p", "ratio": "16:9",
                                                         "audio": True}, "t2v", "optional", frames, audio)}
    assert items["resolution"]["ok"] and items["aspect_ratio"]["ok"] and items["duration"]["ok"]
    assert items["audio"]["ok"] and items["codec"]["ok"]
    bad = {c["key"]: c for c in compliance.check(meta, {"duration": 8, "resolution": "1080p", "ratio": "9:16"},
                                                 "t2v", "optional", frames, audio)}
    assert bad["resolution"]["ok"] is False and bad["aspect_ratio"]["ok"] is False and bad["duration"]["ok"] is False


def test_first_frame_fidelity(tmp_path):
    ref = textured(640, 360, 5)
    Image.fromarray(ref).save(tmp_path / "ref.png")
    other = textured(640, 360, 9)
    Image.fromarray(other).save(tmp_path / "other.png")
    clip = make_clip(tmp_path / "f.mp4", lambda t, w, h: ref if t < 1.0 else np.roll(ref, int(t * 40), axis=1))
    frames, times = media.sample_frames(clip, fps=6, gray=True)
    same = frame_fidelity(frames, times, tmp_path / "ref.png", "first")
    diff = frame_fidelity(frames, times, tmp_path / "other.png", "first")
    assert same["grade"] == "high"
    assert same["score"] > diff["score"] + 0.2


def test_full_pipeline_writes_poster_and_strip(tmp_path):
    clip = make_clip(tmp_path / "p.mp4", moving_square)
    out = analyze_video(clip, tmp_path / "frames", effective={"duration": 3, "resolution": "360p", "ratio": "16:9"},
                        task="t2v", model_audio="none")
    assert Path(out["poster"]).exists() and Path(out["strip"]).exists()
    assert out["signals"]["pairs"] > 5


def test_transcode_for_judge_drops_audio(tmp_path):
    clip = make_clip(tmp_path / "a.mp4", moving_square, w=1280, h=720, tone=True)
    small = media.transcode_for_judge(clip, tmp_path / "j.mp4", keep_audio=False)
    meta = media.probe(small)
    assert min(meta["video"]["width"], meta["video"]["height"]) <= 480
    assert "audio" not in meta
