"""Local-only safety: a per-launch token required on state-changing API calls."""

from __future__ import annotations

import json
import secrets

LAUNCH_TOKEN = secrets.token_urlsafe(24)
TOKEN_HEADER = "x-vgen-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class TokenGuard:
    """Pure ASGI middleware (keeps streaming/range responses untouched)."""

    def __init__(self, app, token: str = LAUNCH_TOKEN):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/api/") and scope["method"] not in SAFE_METHODS:
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            if not secrets.compare_digest(headers.get(TOKEN_HEADER, ""), self.token):
                body = json.dumps({"detail": "missing or invalid launch token — reload the page"}).encode()
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
