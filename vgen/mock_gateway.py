"""In-process fake Tokenbay gateway (VGEN_MOCK=1 and tests): async tasks + synthetic mp4s."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import time
import uuid
from fractions import Fraction

import av
import cv2
import httpx
import numpy as np

from vgen.catalog.registry import PIXELS, Registry

TIER_SIZES = {"480p": (854, 480), "720p": (1280, 720), "1080p": (1920, 1080), "4k": (3840, 2160)}


def _size(body: dict) -> tuple[int, int]:
    meta = body.get("metadata") or {}
    res = str(body.get("resolution") or meta.get("resolution") or body.get("size") or "720p").lower()
    ratio = str(body.get("aspect_ratio") or meta.get("ratio") or "16:9")
    if ratio in ("adaptive", "auto"):
        ratio = "16:9"
    table = PIXELS.get(res if res in PIXELS else "720p")
    if ratio in table:
        return table[ratio]
    return TIER_SIZES.get(res, (1280, 720))


def synth_video(model: str, prompt: str, width: int, height: int, seconds: float, audio: bool, fps: int = 24) -> bytes:
    """A moving gradient + bouncing disc + labels; scaled down 2x for speed but reported at full size."""
    seed = int(hashlib.md5((model + prompt).encode()).hexdigest()[:6], 16)
    hue = seed % 180
    w, h = max(2, width // 2 * 2), max(2, height // 2 * 2)
    small_w, small_h = max(2, (w // 4) // 2 * 2), max(2, (h // 4) // 2 * 2)
    buf = io.BytesIO()
    with av.open(buf, "w", format="mp4") as out:
        vs = out.add_stream("libx264", rate=fps)
        vs.width, vs.height, vs.pix_fmt = w, h, "yuv420p"
        vs.options = {"preset": "ultrafast", "crf": "32"}
        aus = out.add_stream("aac", rate=44100) if audio else None
        n = int(seconds * fps)
        yy, xx = np.mgrid[0:small_h, 0:small_w]
        for i in range(n):
            t = i / fps
            base = ((xx + yy + i * 2) % 180).astype(np.uint8)
            hsv = np.dstack([(base // 2 + hue) % 180, np.full_like(base, 120), np.full_like(base, 200)]).astype(np.uint8)
            img = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
            cx = int((0.5 + 0.35 * math.sin(t * 1.3)) * small_w)
            cy = int((0.5 + 0.3 * math.cos(t * 0.9)) * small_h)
            cv2.circle(img, (cx, cy), max(4, small_h // 8), (255, 255, 255), -1)
            img = cv2.resize(img, (w, h), interpolation=cv2.INTER_NEAREST)
            cv2.putText(img, f"MOCK {model}", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (20, 20, 20), 3)
            cv2.putText(img, f"{t:4.1f}s", (20, h - 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2)
            frame = av.VideoFrame.from_ndarray(img, format="rgb24")
            for p in vs.encode(frame):
                out.mux(p)
        for p in vs.encode():
            out.mux(p)
        if aus is not None:
            sr, total = 44100, int(seconds * 44100)
            samples_per = 1024
            for k in range(0, total, samples_per):
                tt = (np.arange(k, min(total, k + samples_per)) / sr)
                tone = (0.2 * np.sin(2 * math.pi * 440 * tt) * (0.5 + 0.5 * np.sin(2 * math.pi * 2 * tt))).astype(np.float32)
                af = av.AudioFrame.from_ndarray(tone.reshape(1, -1), format="fltp", layout="mono")
                af.sample_rate = sr
                af.pts = k
                af.time_base = Fraction(1, sr)
                for p in aus.encode(af):
                    out.mux(p)
            for p in aus.encode():
                out.mux(p)
    return buf.getvalue()


class MockGateway:
    def __init__(self, registry: Registry, queued_s: float = 1.5, running_s: float = 3.0):
        self.registry = registry
        self.queued_s = queued_s
        self.running_s = running_s
        self.tasks: dict[str, dict] = {}
        self.videos: dict[str, bytes] = {}
        self.requests: list[tuple[str, str]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        if path == "/v1/models":
            ids = list(self.registry.models) + ["gemini-3.1-pro-preview", "gemini-3.5-flash", "seedream-5.0-lite"]
            return httpx.Response(200, json={"object": "list", "data": [{"id": i, "object": "model"} for i in ids]})
        if request.method == "POST" and path in ("/v1/videos", "/v1/video/generations"):
            return self._submit(json.loads(request.content or b"{}"))
        m = re.fullmatch(r"/v1/(?:videos|video/generations)/([^/]+)(/content)?", path)
        if m and request.method == "GET":
            task = self.tasks.get(m.group(1))
            if task is None:
                return httpx.Response(404, json={"error": {"message": f"task not found: {m.group(1)}",
                                                           "type": "invalid_request_error", "code": "resource_not_found"}})
            return self._content(task) if m.group(2) else self._detail(task)
        if request.method == "POST" and re.fullmatch(r"/v1(beta)?/models/[^/:]+:generateContent", path):
            return self._judge(json.loads(request.content or b"{}"))
        if request.method == "POST" and path == "/v1/images/generations":
            return self._image(json.loads(request.content or b"{}"))
        return httpx.Response(404, text="404 page not found")

    def _submit(self, body: dict) -> httpx.Response:
        model = body.get("model", "")
        if model not in self.registry.models:
            return httpx.Response(200, json={"error": {"message": f"Unsupported video model: {model}",
                                                       "type": "invalid_request_error", "code": "invalid_model"}})
        prompt = str(body.get("prompt") or "")
        if "[reject]" in prompt:
            return httpx.Response(200, json={"error": {"message": "prompt rejected by moderation",
                                                       "type": "invalid_request_error", "code": "content_policy"}})
        tid = "task_mock_" + uuid.uuid4().hex[:16]
        self.tasks[tid] = {"id": tid, "model": model, "body": body, "created": time.time(), "fail": "[fail]" in prompt}
        return httpx.Response(200, json={"id": tid, "task_id": tid, "object": "video", "model": model,
                                         "status": "QUEUED", "progress": 0, "created_at": int(time.time())})

    def _state(self, task: dict) -> tuple[str, int]:
        age = time.time() - task["created"]
        if age < self.queued_s:
            return "QUEUED", 0
        if age < self.queued_s + self.running_s:
            return "IN_PROGRESS", int(100 * (age - self.queued_s) / self.running_s)
        return ("FAILURE" if task["fail"] else "SUCCESS"), 100

    def _detail(self, task: dict) -> httpx.Response:
        status, progress = self._state(task)
        created = int(task["created"])
        body = {"task_id": task["id"], "action": "generate", "status": status, "progress": f"{progress}%",
                "submit_time": created, "created_at": created, "updated_at": int(time.time())}
        if status != "QUEUED":
            body["start_time"] = created + int(self.queued_s)
        if status == "SUCCESS":
            body.update(result_url=f"/v1/videos/{task['id']}/content", finish_time=int(time.time()), quota=150000)
        if status == "FAILURE":
            body.update(fail_reason="mock generation failure", finish_time=int(time.time()), quota=0)
        return httpx.Response(200, json=body)

    def _content(self, task: dict) -> httpx.Response:
        status, _ = self._state(task)
        if status != "SUCCESS":
            return httpx.Response(400, json={"error": {"message": "task not finished", "code": "not_ready"}})
        if task["id"] not in self.videos:
            body = task["body"]
            meta = body.get("metadata") or {}
            w, h = _size(body)
            dur = float(body.get("duration") or meta.get("duration") or 5)
            audio = bool(body.get("generate_audio") or meta.get("generate_audio"))
            self.videos[task["id"]] = synth_video(task["model"], str(body.get("prompt", "")), w, h, dur, audio)
        return httpx.Response(200, content=self.videos[task["id"]], headers={"content-type": "video/mp4"})

    def _judge(self, body: dict) -> httpx.Response:
        text = ""
        for part in (body.get("contents") or [{}])[0].get("parts", []):
            text += part.get("text", "")
        items = re.findall(r"^\d+\.\s+(.+)$", text.split("Checklist", 1)[-1], re.M)
        checklist = [{"item": it, "pass": (hashlib.md5(it.encode()).digest()[0] % 4 != 0),
                      "evidence": "mock evidence", "t": 1.0} for it in items]
        result = {"checklist": checklist,
                  "defects": [{"type": "other", "severity": "minor", "t": 2.0, "description": "mock defect"}],
                  "verdict": "Mock verdict: plausible but synthetic."}
        return httpx.Response(200, json={
            "candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(result)}]}}],
            "usageMetadata": {"promptTokenCount": 9000, "candidatesTokenCount": 400, "thoughtsTokenCount": 300}})

    def _image(self, body: dict) -> httpx.Response:
        import base64

        img = np.zeros((1024, 1024, 3), np.uint8)
        img[:] = (40, 40, 48)
        cv2.rectangle(img, (412, 300), (612, 760), (200, 170, 90), -1)
        cv2.rectangle(img, (462, 220), (562, 300), (230, 200, 120), -1)
        ok, png = cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        return httpx.Response(200, json={"created": int(time.time()),
                                         "data": [{"b64_json": base64.b64encode(png.tobytes()).decode()}]})
