"""Tokenbay HTTP client: one shared rate limiter per endpoint and strict error classification."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from vgen.config import Endpoint

log = logging.getLogger("vgen.gateway")

# Error kinds. "safe" kinds guarantee that no task was created.
NETWORK_BEFORE_SEND = "network_before_send"   # safe to resend
RATE_LIMIT = "rate_limit"                     # safe to resend after the window
BUSINESS = "business"                         # gateway rejected the request (HTTP 200 + error / 4xx)
AUTH = "auth"
NOT_FOUND = "not_found"
SERVER = "server"                             # 5xx: outcome unknown for submits
AFTER_SEND = "after_send"                     # read timeout / dropped connection: outcome unknown

SAFE_TO_RESEND = {NETWORK_BEFORE_SEND, RATE_LIMIT}


class GatewayError(Exception):
    def __init__(self, kind: str, message: str, status: int | None = None, code: str = "", body: Any = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status
        self.code = code
        self.body = body

    def __str__(self) -> str:  # pragma: no cover - formatting
        return f"[{self.kind}{'/' + str(self.status) if self.status else ''}] {self.message}"


class RateLimiter:
    """At most `rpm` request starts in any rolling `window` seconds; high-priority callers go first."""

    def __init__(self, rpm: int, window: float = 60.0, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.rpm = max(1, int(rpm))
        self.window = window
        self.clock = clock
        self.sleep = sleep
        self._starts: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._waiting_high = 0
        self.paused_until = 0.0

    def _prune(self, now: float) -> None:
        while self._starts and now - self._starts[0] >= self.window:
            self._starts.popleft()

    def wait_time(self, now: float | None = None) -> float:
        now = self.clock() if now is None else now
        self._prune(now)
        if now < self.paused_until:
            return self.paused_until - now
        if len(self._starts) < self.rpm:
            return 0.0
        return self.window - (now - self._starts[0])

    def used(self) -> int:
        self._prune(self.clock())
        return len(self._starts)

    async def acquire(self, high: bool = False) -> None:
        if high:
            self._waiting_high += 1
        try:
            while True:
                async with self._lock:
                    now = self.clock()
                    wait = self.wait_time(now)
                    if wait <= 0 and (high or self._waiting_high == 0):
                        self._starts.append(now)
                        return
                await self.sleep(min(max(wait, 0.05), 5.0) if wait > 0 else 0.2)
        finally:
            if high:
                self._waiting_high -= 1

    def pause_until_next_minute(self) -> float:
        """Account windows are clock minutes: block until the next minute boundary (+1 s)."""
        delay = 60 - (time.time() % 60) + 1.0
        self.paused_until = max(self.paused_until, self.clock() + delay)
        return delay


def _error_from_body(body: Any) -> tuple[str, str] | None:
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        err = body["error"]
        return str(err.get("message") or "error"), str(err.get("code") or err.get("type") or "")
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return body["error"], ""
    return None


def classify(status: int, body: Any, text: str) -> GatewayError | None:
    """Map an HTTP response to a GatewayError (None = success)."""
    err = _error_from_body(body)
    message, code = err if err else (text[:300] or f"HTTP {status}", "")
    lowered = f"{message} {text[:500]}".lower()
    if status == 429 or (status == 401 and "rate limit" in lowered):
        return GatewayError(RATE_LIMIT, message, status, code, body)
    if status in (401, 403):
        return GatewayError(AUTH, message, status, code, body)
    if status == 404:
        return GatewayError(NOT_FOUND, message, status, code, body)
    if status >= 500:
        return GatewayError(SERVER, message, status, code, body)
    if status >= 400:
        return GatewayError(BUSINESS, message, status, code, body)
    if err:  # HTTP 200 with an error object: business failure, nothing was created
        if "rate limit" in lowered:
            return GatewayError(RATE_LIMIT, message, status, code, body)
        return GatewayError(BUSINESS, message, status, code, body)
    return None


class Gateway:
    def __init__(self, endpoint: Endpoint, transport: httpx.AsyncBaseTransport | None = None,
                 submit_read_timeout: float = 150.0):
        self.endpoint = endpoint
        self.limiter = RateLimiter(endpoint.rpm)
        self.submit_read_timeout = submit_read_timeout
        self.disabled_reason: str | None = None
        self.last_error: str | None = None
        self.client = httpx.AsyncClient(
            base_url=endpoint.base_url,
            headers={"Authorization": f"Bearer {endpoint.api_key}", "User-Agent": "vgen-eval/0.1"},
            timeout=httpx.Timeout(connect=15.0, read=60.0, write=120.0, pool=30.0),
            trust_env=endpoint.trust_env,
            transport=transport,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    # ------------------------------------------------------------------ core
    async def request_json(self, method: str, path: str, *, json_body: Any = None, high: bool = False,
                           read_timeout: float | None = None) -> Any:
        if self.disabled_reason:
            raise GatewayError(AUTH, f"endpoint disabled: {self.disabled_reason}")
        await self.limiter.acquire(high=high)
        timeout = httpx.Timeout(connect=15.0, read=read_timeout or 60.0, write=120.0, pool=30.0)
        try:
            resp = await self.client.request(method, path, json=json_body, timeout=timeout)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise GatewayError(NETWORK_BEFORE_SEND, f"connection failed: {exc!r}") from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError, httpx.ReadError,
                httpx.WriteError) as exc:
            raise GatewayError(AFTER_SEND, f"no response after sending: {exc!r}") from exc
        text = resp.text
        try:
            body = resp.json()
        except (json.JSONDecodeError, ValueError):
            body = None
        err = classify(resp.status_code, body, text)
        if err:
            self._note_error(err)
            raise err
        return body

    def _note_error(self, err: GatewayError) -> None:
        self.last_error = str(err)
        if err.kind == RATE_LIMIT:
            delay = self.limiter.pause_until_next_minute()
            log.warning("%s rate limited; pausing %.0fs", self.endpoint.name, delay)
        elif err.kind == AUTH:
            self.disabled_reason = err.message
            log.error("%s auth failure: %s (endpoint disabled)", self.endpoint.name, err.message)

    # ------------------------------------------------------------------ API
    async def list_models(self) -> list[str]:
        body = await self.request_json("GET", "/v1/models")
        return [m.get("id") for m in (body or {}).get("data", []) if isinstance(m, dict) and m.get("id")]

    async def submit_video(self, payload: dict) -> dict:
        body = await self.request_json("POST", "/v1/videos", json_body=payload, high=True,
                                       read_timeout=self.submit_read_timeout)
        if not isinstance(body, dict) or not (body.get("task_id") or body.get("id")):
            raise GatewayError(AFTER_SEND, f"submit returned no task id: {str(body)[:200]}", body=body)
        return body

    async def get_video(self, task_id: str) -> dict:
        body = await self.request_json("GET", f"/v1/videos/{task_id}")
        if not isinstance(body, dict):
            raise GatewayError(SERVER, "unexpected task body")
        return body

    async def download_video(self, task_id: str, dest: Path, max_bytes: int = 512 * 1024 * 1024) -> int:
        """One streaming GET (Range chunks would each count against the rate limit)."""
        await self.limiter.acquire()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        size = 0
        try:
            async with self.client.stream("GET", f"/v1/videos/{task_id}/content",
                                          timeout=httpx.Timeout(connect=15, read=120, write=60, pool=30)) as resp:
                if resp.status_code >= 400:
                    text = (await resp.aread()).decode("utf-8", "replace")
                    try:
                        body = json.loads(text)
                    except ValueError:
                        body = None
                    err = classify(resp.status_code, body, text) or GatewayError(SERVER, text[:200], resp.status_code)
                    self._note_error(err)
                    raise err
                ctype = resp.headers.get("content-type", "")
                with tmp.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(1 << 16):
                        size += len(chunk)
                        if size > max_bytes:
                            raise GatewayError(SERVER, "download exceeds size limit")
                        fh.write(chunk)
                if "json" in ctype:
                    text = tmp.read_text("utf-8", "replace")
                    tmp.unlink(missing_ok=True)
                    raise GatewayError(SERVER, f"content endpoint returned JSON: {text[:200]}")
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            tmp.unlink(missing_ok=True)
            raise GatewayError(NETWORK_BEFORE_SEND, f"download connection failed: {exc!r}") from exc
        except (httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.ReadError) as exc:
            tmp.unlink(missing_ok=True)
            raise GatewayError(AFTER_SEND, f"download interrupted: {exc!r}") from exc
        tmp.replace(dest)
        return size

    async def gemini_generate(self, model: str, body: dict, read_timeout: float = 240.0) -> dict:
        return await self.request_json("POST", f"/v1beta/models/{model}:generateContent", json_body=body,
                                       read_timeout=read_timeout)

    async def post_json(self, path: str, body: dict, read_timeout: float = 120.0) -> Any:
        return await self.request_json("POST", path, json_body=body, read_timeout=read_timeout)

    async def get_bytes(self, url: str) -> tuple[bytes, str]:
        """Fetch an absolute URL (e.g. a generated image) without auth headers."""
        async with httpx.AsyncClient(timeout=60, follow_redirects=True, trust_env=self.endpoint.trust_env) as c:
            resp = await c.get(url)
            resp.raise_for_status()
            return resp.content, resp.headers.get("content-type", "")
