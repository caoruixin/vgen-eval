"""Decode videos with PyAV: metadata, sampled frames, poster/strip images and audio stats."""

from __future__ import annotations

import math
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image


def probe(path: Path) -> dict:
    out: dict = {"size_bytes": path.stat().st_size}
    with av.open(str(path)) as c:
        out["container"] = c.format.name
        out["duration"] = float(c.duration / 1e6) if c.duration else None
        out["bit_rate"] = int(c.bit_rate) if c.bit_rate else None
        vs = next((s for s in c.streams if s.type == "video"), None)
        if vs is not None:
            cc = vs.codec_context
            fps = float(vs.average_rate) if vs.average_rate else (float(vs.guessed_rate) if vs.guessed_rate else None)
            vdur = float(vs.duration * vs.time_base) if vs.duration and vs.time_base else None
            out["video"] = {
                "codec": cc.name,
                "profile": getattr(cc, "profile", None),
                "pix_fmt": getattr(cc, "pix_fmt", None) or (vs.format.name if vs.format else None),
                "width": cc.width,
                "height": cc.height,
                "fps": round(fps, 3) if fps else None,
                "frames": int(vs.frames) if vs.frames else None,
                "duration": vdur,
            }
            if out["duration"] is None:
                out["duration"] = vdur
        aus = next((s for s in c.streams if s.type == "audio"), None)
        if aus is not None:
            acc = aus.codec_context
            out["audio"] = {"codec": acc.name, "sample_rate": acc.sample_rate,
                            "channels": getattr(acc, "channels", None) or (acc.layout.nb_channels if acc.layout else None)}
    return out


def sample_frames(path: Path, fps: float = 6.0, max_side: int = 360, gray: bool = False,
                  max_frames: int = 240) -> tuple[list[np.ndarray], list[float]]:
    """Decode frames at roughly `fps`, downscaled so the long side is <= max_side."""
    frames: list[np.ndarray] = []
    times: list[float] = []
    step = 1.0 / fps
    next_t = 0.0
    with av.open(str(path)) as c:
        vs = next((s for s in c.streams if s.type == "video"), None)
        if vs is None:
            return frames, times
        vs.thread_type = "AUTO"
        for frame in c.decode(vs):
            t = float(frame.pts * frame.time_base) if frame.pts is not None else len(times) * step
            if t + 1e-6 < next_t:
                continue
            next_t = t + step
            w, h = frame.width, frame.height
            scale = min(1.0, max_side / max(w, h))
            tw, th = max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2)
            img = frame.to_ndarray(format="gray" if gray else "rgb24", width=tw, height=th)
            frames.append(img)
            times.append(t)
            if len(frames) >= max_frames:
                break
    return frames, times


def frame_at(path: Path, t: float, max_side: int = 960) -> np.ndarray | None:
    with av.open(str(path)) as c:
        vs = next((s for s in c.streams if s.type == "video"), None)
        if vs is None:
            return None
        best = None
        for frame in c.decode(vs):
            ft = float(frame.pts * frame.time_base) if frame.pts is not None else 0.0
            best = frame
            if ft >= t:
                break
        if best is None:
            return None
        scale = min(1.0, max_side / max(best.width, best.height))
        return best.to_ndarray(format="rgb24", width=max(2, int(best.width * scale) // 2 * 2),
                               height=max(2, int(best.height * scale) // 2 * 2))


def write_poster_and_strip(path: Path, poster_out: Path, strip_out: Path, n: int = 8) -> None:
    frames, times = sample_frames(path, fps=4.0, max_side=480)
    if not frames:
        return
    poster_out.parent.mkdir(parents=True, exist_ok=True)
    mid = frames[min(len(frames) - 1, max(0, len(frames) // 3))]
    Image.fromarray(mid).save(poster_out, "JPEG", quality=85)
    idx = [round(i * (len(frames) - 1) / max(1, n - 1)) for i in range(n)] if len(frames) > 1 else [0]
    thumbs = [Image.fromarray(frames[i]) for i in idx]
    th = 135
    resized = [im.resize((max(2, int(im.width * th / im.height)), th)) for im in thumbs]
    total_w = sum(im.width for im in resized) + 4 * (len(resized) - 1)
    strip = Image.new("RGB", (total_w, th), (16, 16, 20))
    x = 0
    for im in resized:
        strip.paste(im, (x, 0))
        x += im.width + 4
    strip.save(strip_out, "JPEG", quality=80)


def audio_stats(path: Path, max_seconds: float = 30.0) -> dict:
    """Loudness (dBFS RMS) and silence ratio over 50 ms windows."""
    with av.open(str(path)) as c:
        aus = next((s for s in c.streams if s.type == "audio"), None)
        if aus is None:
            return {"has_audio": False}
        resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
        chunks: list[np.ndarray] = []
        total = 0
        for frame in c.decode(aus):
            for rf in resampler.resample(frame):
                arr = rf.to_ndarray().reshape(-1)
                chunks.append(arr)
                total += arr.size
            if total > max_seconds * 16000:
                break
    if not chunks:
        return {"has_audio": True, "rms_dbfs": None, "silence_ratio": 1.0}
    pcm = np.concatenate(chunks).astype(np.float64)
    rms = math.sqrt(float(np.mean(pcm ** 2))) if pcm.size else 0.0
    win = 800
    n = pcm.size // win
    if n == 0:
        silence = 1.0
    else:
        w = pcm[: n * win].reshape(n, win)
        wr = np.sqrt(np.mean(w ** 2, axis=1))
        silence = float(np.mean(wr < 10 ** (-45 / 20)))
    return {"has_audio": True, "rms_dbfs": round(20 * math.log10(rms), 1) if rms > 0 else -120.0,
            "silence_ratio": round(silence, 3), "seconds": round(pcm.size / 16000, 2)}


def transcode_for_judge(src: Path, dst: Path, max_short_side: int = 480, keep_audio: bool = False,
                        fps_cap: float = 24.0) -> Path:
    """Re-encode to H.264 at <=480p (and optionally drop audio) to keep judge payloads small."""
    with av.open(str(src)) as inp:
        vin = next(s for s in inp.streams if s.type == "video")
        ain = next((s for s in inp.streams if s.type == "audio"), None) if keep_audio else None
        w, h = vin.codec_context.width, vin.codec_context.height
        scale = min(1.0, max_short_side / min(w, h))
        tw, th = max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2)
        rate = vin.average_rate or Fraction(24, 1)
        if float(rate) > fps_cap:
            rate = Fraction(int(fps_cap), 1)
        with av.open(str(dst), "w", format="mp4") as out:
            vout = out.add_stream("libx264", rate=rate)
            vout.width, vout.height, vout.pix_fmt = tw, th, "yuv420p"
            vout.options = {"crf": "30", "preset": "veryfast"}
            aout = out.add_stream("aac", rate=44100) if ain is not None else None
            streams = [vin] + ([ain] if ain is not None else [])
            for packet in inp.demux(*streams):
                if packet.stream.type == "video":
                    for frame in packet.decode():
                        frame = frame.reformat(width=tw, height=th, format="yuv420p")
                        frame.pts = None
                        for p in vout.encode(frame):
                            out.mux(p)
                elif aout is not None:
                    for frame in packet.decode():
                        frame.pts = None
                        for p in aout.encode(frame):
                            out.mux(p)
            for p in vout.encode():
                out.mux(p)
            if aout is not None:
                for p in aout.encode():
                    out.mux(p)
    return dst
