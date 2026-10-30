"""AI judge: Gemini (native generateContent) watches the clip and checks the prompt's assertions."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from pathlib import Path

from PIL import Image

from vgen.db import JudgeCache, session
from vgen.gateway import Gateway

# Tokenbay's Gemini route rejects inline video (upstream 400, verified 2026-10-29) but accepts inline images,
# so the judge sees an ordered, timestamped frame sequence (~2 fps, 8–16 frames) instead of the mp4.
FRAME_FPS = 2.0
MIN_FRAMES, MAX_FRAMES = 8, 16
FRAME_SIDE = 448
MODE = "frames-v1"

DEFECT_TYPES = ["morphing", "anatomy", "physics", "text_garbled", "identity_drift", "lip_sync",
                "audio_artifact", "ignores_prompt", "flicker", "letterbox_watermark", "other"]

SYSTEM = (
    "You are a strict, impartial evaluator of AI-generated video. You see frames sampled in order from one "
    "generated clip, each labelled with its timestamp, and, when provided, the input images the clip had to "
    "follow. You cannot hear audio. Judge only what the frames show; infer motion from consecutive frames. "
    "Do not guess which model made it and do not reward prettiness unless the checklist asks for it. "
    "Cite timestamps in seconds."
)

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "checklist": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "item": {"type": "STRING"},
                    "assessable": {"type": "BOOLEAN"},
                    "pass": {"type": "BOOLEAN"},
                    "evidence": {"type": "STRING"},
                    "t": {"type": "NUMBER", "nullable": True},
                },
                "required": ["item", "assessable", "pass", "evidence"],
            },
        },
        "defects": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "type": {"type": "STRING", "enum": DEFECT_TYPES},
                    "severity": {"type": "STRING", "enum": ["minor", "major", "critical"]},
                    "t": {"type": "NUMBER", "nullable": True},
                    "description": {"type": "STRING"},
                },
                "required": ["type", "severity", "description"],
            },
        },
        "verdict": {"type": "STRING"},
    },
    "required": ["checklist", "defects", "verdict"],
}

TASK_HINT = {
    "t2v": "text-to-video",
    "i2v": "image-to-video: the clip must start from the attached image",
    "flf2v": "first/last-frame: the clip must start on the first attached image and end on the second",
    "r2v": "reference-to-video: subjects in the clip must match the attached reference images",
    "v2v_edit": "video edit of a source clip according to the instruction",
    "extend": "continuation of a source clip",
    "a2v": "audio-driven video: motion and lips should follow the audio",
}


def cache_key(video_sha: str, model: str, prompt: str, checklist: list[str], task: str, with_audio: bool) -> str:
    raw = json.dumps([MODE, video_sha, model, prompt, checklist, task, with_audio], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def sample_frames_jpeg(video: Path) -> list[tuple[float, bytes]]:
    from vgen.analysis.media import probe, sample_frames

    duration = float(probe(video).get("duration") or 5.0)
    n = max(MIN_FRAMES, min(MAX_FRAMES, round(duration * FRAME_FPS)))
    frames, times = sample_frames(video, fps=n / max(duration, 0.5), max_side=FRAME_SIDE, max_frames=n)
    out = []
    for img, t in zip(frames, times):
        buf = io.BytesIO()
        Image.fromarray(img).save(buf, "JPEG", quality=82)
        out.append((t, buf.getvalue()))
    return out


def build_body(frames: list[tuple[float, bytes]], prompt: str, checklist: list[str], task: str,
               refs: list[tuple[str, bytes]], with_audio: bool) -> dict:
    items = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(checklist)) or "1. The clip matches the prompt."
    text = (
        f"Task type: {TASK_HINT.get(task, task)}.\n"
        f"Generation prompt (verbatim):\n<<<\n{prompt}\n>>>\n\n"
        f"Checklist — answer every item, in order: assessable (false only if it cannot be judged from frames, "
        f"e.g. sound), pass true/false, short evidence and a timestamp:\n{items}\n\n"
        f"Then list visible defects (type, severity minor/major/critical, timestamp, description). "
        f"{'Audio matters for this clip but you cannot hear it: mark sound-only items not assessable.' if with_audio else ''} "
        f"Finish with a one-sentence verdict on whether this clip is usable for the prompt's purpose."
    )
    parts: list[dict] = [{"text": f"The clip: {len(frames)} frames in order."}]
    for t, data in frames:
        parts.append({"text": f"Frame at {t:.1f}s"})
        parts.append({"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(data).decode()}})
    for i, (mime, data) in enumerate(refs, 1):
        parts.append({"text": f"Input image {i} (what the clip had to follow)"})
        parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}})
    parts.append({"text": text})
    return {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": SCHEMA,
                             "maxOutputTokens": 8192},
    }


def parse_response(body: dict) -> tuple[dict, dict]:
    cands = (body or {}).get("candidates") or []
    if not cands:
        raise ValueError(f"judge returned no candidates: {str(body)[:300]}")
    parts = (cands[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError(f"judge output is not JSON: {text[:300]}")
        data = json.loads(m.group(0))
    usage = body.get("usageMetadata") or {}
    return data, usage


def summarize(data: dict) -> dict:
    checklist = data.get("checklist") or []
    judged = [c for c in checklist if c.get("assessable", True)]
    passed = sum(1 for c in judged if c.get("pass"))
    weights = {"minor": 1, "major": 3, "critical": 6}
    defects = data.get("defects") or []
    return {
        **data,
        "adherence": round(passed / len(judged), 3) if judged else None,
        "passed": passed,
        "total": len(judged),
        "not_assessable": len(checklist) - len(judged),
        "mode": MODE,
        "defect_score": sum(weights.get(d.get("severity"), 1) for d in defects),
    }


async def judge_clip(gateway: Gateway, model: str, video: Path, video_sha: str, prompt: str, checklist: list[str],
                     task: str, refs: list[Path], with_audio: bool, has_audio_stream: bool, work_dir: Path,
                     price: dict | None) -> dict:
    key = cache_key(video_sha, model, prompt, checklist, task, with_audio)
    with session() as s:
        hit = s.get(JudgeCache, key)
        if hit is not None:
            return {**hit.result, "cached": True}
    frames = sample_frames_jpeg(video)
    ref_bytes = []
    for p in refs:
        mime = "image/png" if p.suffix.lower() == ".png" else ("image/webp" if p.suffix.lower() == ".webp" else "image/jpeg")
        ref_bytes.append((mime, p.read_bytes()))
    body = build_body(frames, prompt, checklist, task, ref_bytes, with_audio)
    resp = await gateway.gemini_generate(model, body)
    data, usage = parse_response(resp)
    result = summarize(data)
    in_tok = int(usage.get("promptTokenCount") or 0)
    out_tok = int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0)
    cost = (in_tok * price["input"] + out_tok * price["output"]) / 1e6 if price else None
    result["usage"] = {"input_tokens": in_tok, "output_tokens": out_tok, "usd": round(cost, 5) if cost else None}
    result["model"] = model
    with session() as s:
        s.add(JudgeCache(key=key, result=result))
        s.commit()
    return result
