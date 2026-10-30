"""Per-clip analysis pipeline (CPU bound; run in a worker thread)."""

from __future__ import annotations

from pathlib import Path

from vgen.analysis import compliance, fidelity, media, signals


def analyze_video(video: Path, out_dir: Path, *, effective: dict, task: str, model_audio: str,
                  first_ref: Path | None = None, last_ref: Path | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = media.probe(video)
    frames_gray, times = media.sample_frames(video, fps=6.0, max_side=360, gray=True)
    audio = media.audio_stats(video) if meta.get("audio") else {"has_audio": False}
    comp = compliance.check(meta, effective, task, model_audio, frames_gray, audio)
    sig = signals.compute(frames_gray, times)
    sig["audio"] = audio
    fid: dict = {}
    if task in ("i2v", "flf2v") and first_ref and first_ref.exists():
        fid["first"] = fidelity.frame_fidelity(frames_gray, times, first_ref, "first")
    if task == "flf2v" and last_ref and last_ref.exists():
        fid["last"] = fidelity.frame_fidelity(frames_gray, times, last_ref, "last")
    poster = out_dir / "poster.jpg"
    strip = out_dir / "strip.jpg"
    media.write_poster_and_strip(video, poster, strip)
    return {
        "media": meta,
        "compliance": comp,
        "signals": sig,
        "fidelity": fid,
        "poster": str(poster) if poster.exists() else None,
        "strip": str(strip) if strip.exists() else None,
    }
