"""Request safety: the per-launch token on state-changing API calls, and the hosted site's admin sign-in.

Admin sign-in exists only on the hosted site (VGEN_PUBLIC=1). The server holds a scrypt hash of a secret the
operator chose (VGEN_ADMIN_SECRET_HASH, made with `vgen admin-hash`) and keeps sessions in memory, so a
restart signs everyone out and reading the env file is not enough to mint a session.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import OrderedDict, deque
from typing import Callable

LAUNCH_TOKEN = secrets.token_urlsafe(24)
TOKEN_HEADER = "x-vgen-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

ADMIN_COOKIE = "vgen_admin"
ADMIN_SESSION_S = 12 * 3600
ADMIN_MAX_SESSIONS = 8
ADMIN_MAX_FAILURES = 5                 # wrong secrets per client ...
ADMIN_FAILURE_WINDOW_S = 15 * 60       # ... within this window
ADMIN_MAX_CLIENTS = 4096               # clients whose failures are remembered
ADMIN_MAX_WAITING = 8                  # sign-ins allowed to queue for the single hash slot
ADMIN_BUSY_RETRY_S = 1.0               # what a caller is told to wait when that queue is full
ADMIN_MIN_SECRET_CHARS = 16
ADMIN_MAX_SECRET_CHARS = 1024

SCRYPT_N, SCRYPT_R, SCRYPT_P = 16384, 8, 1
SCRYPT_DKLEN = 32
SCRYPT_SALT_BYTES = 16
# A stored hash is only accepted within these bounds, so a mistyped or hostile env value can neither weaken the
# check nor make one sign-in attempt use a large share of the service's 1 GB / one CPU.
SCRYPT_N_RANGE = (2 ** 14, 2 ** 16)
SCRYPT_R_RANGE = (8, 16)
SCRYPT_P_RANGE = (1, 4)
SCRYPT_MAX_N_TIMES_R = 2 ** 19         # 128 * n * r bytes: at most 64 MiB per hash
SCRYPT_MAXMEM = 80 * 1024 * 1024


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


class NoStoreApi:
    """Pure ASGI middleware: API data (JSON, CSV) differs per visitor and per admin session for the same URL,
    so neither a browser nor a proxy may keep it. Media and report files keep their own cache headers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        async def send_no_store(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                content_type = next((v for k, v in headers if k.lower() == b"content-type"), b"").lower()
                if b"json" in content_type or content_type.startswith(b"text/csv"):
                    current = next((v for k, v in headers if k.lower() == b"cache-control"), b"").lower()
                    if b"no-store" not in current:
                        headers = [(k, v) for k, v in headers if k.lower() != b"cache-control"]
                        headers.append((b"cache-control", b"no-store"))
                        message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_no_store)


# ---------------------------------------------------------------------- admin secret

def hash_admin_secret(secret: str, salt: bytes | None = None) -> str:
    """`scrypt:<n>:<r>:<p>:<salt-hex>:<hash-hex>`: the only form of the admin secret the server ever stores."""
    salt = salt if salt is not None else secrets.token_bytes(SCRYPT_SALT_BYTES)
    digest = hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                            dklen=SCRYPT_DKLEN, maxmem=SCRYPT_MAXMEM)
    return f"scrypt:{SCRYPT_N}:{SCRYPT_R}:{SCRYPT_P}:{salt.hex()}:{digest.hex()}"


def _parse_admin_hash(encoded: str) -> tuple[int, int, int, bytes, bytes] | None:
    try:
        scheme, n_text, r_text, p_text, salt_hex, digest_hex = (encoded or "").strip().split(":")
        if scheme != "scrypt" or not all(t.isascii() and t.isdigit() for t in (n_text, r_text, p_text)):
            return None
        n, r, p = int(n_text), int(r_text), int(p_text)
        salt, digest = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
    except (AttributeError, ValueError):
        return None
    if not (SCRYPT_N_RANGE[0] <= n <= SCRYPT_N_RANGE[1]) or n & (n - 1):
        return None
    if not (SCRYPT_R_RANGE[0] <= r <= SCRYPT_R_RANGE[1]) or not (SCRYPT_P_RANGE[0] <= p <= SCRYPT_P_RANGE[1]):
        return None
    if n * r > SCRYPT_MAX_N_TIMES_R or not 8 <= len(salt) <= 64 or not 16 <= len(digest) <= 64:
        return None
    return n, r, p, salt, digest


def verify_admin_secret(encoded: str, secret: str) -> bool:
    """Constant-time check of `secret` against a stored hash; anything malformed is simply not a match."""
    parsed = _parse_admin_hash(encoded)
    if parsed is None or not isinstance(secret, str):
        return False
    n, r, p, salt, digest = parsed
    try:
        candidate = hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(digest),
                                   maxmem=SCRYPT_MAXMEM)
    except (ValueError, OverflowError, MemoryError):
        return False
    return hmac.compare_digest(candidate, digest)


def _session_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "replace")).hexdigest()


class AdminThrottled(Exception):
    """A sign-in was turned away before its secret was looked at, so it says nothing about the secret and is not
    counted as a failed attempt. `busy` tells the two reasons apart: the sign-in queue is full right now (True),
    or this client has used up its attempts and is locked out (False)."""

    def __init__(self, retry_after: float, busy: bool):
        super().__init__("admin sign-in throttled")
        self.retry_after = retry_after
        self.busy = busy


class AdminAuth:
    """Admin sessions and sign-in throttling, all in this process's memory.

    A session is a random token handed to the browser in a cookie; the server keeps only sha256(token) and
    when it expires. Wrong secrets are counted per client (at most ADMIN_MAX_FAILURES in
    ADMIN_FAILURE_WINDOW_S); there is no lockout shared between clients. Only one scrypt hash runs at a
    time, and a client that is locked out is refused before any hashing.
    """

    def __init__(self, encoded_hash: str = "", clock: Callable[[], float] = time.monotonic):
        self._encoded = (encoded_hash or "").strip()
        self._usable = _parse_admin_hash(self._encoded) is not None
        self.clock = clock
        self._lock = threading.Lock()            # guards everything below
        self._hash_lock = threading.Lock()       # one scrypt computation at a time
        self._sessions: dict[str, float] = {}    # sha256(token) -> expiry (clock time)
        self._failures: OrderedDict[str, deque[float]] = OrderedDict()   # client -> recent wrong secrets
        self._pending: dict[str, int] = {}       # client -> attempts being checked right now
        self._waiting = 0

    @property
    def enabled(self) -> bool:
        """False when no (valid) hash is configured: nobody can sign in and nobody is admin."""
        return self._usable

    # ------------------------------------------------------------------ throttle
    def retry_after(self, client: str) -> float:
        """Seconds until this client may try again (0 = it may try now)."""
        with self._lock:
            wait = self._locked_out(self._client(client), self.clock())
            if wait <= 0 and self._waiting >= ADMIN_MAX_WAITING:
                return ADMIN_BUSY_RETRY_S        # the single hash slot has a full queue: come back in a moment
            return wait

    @staticmethod
    def _client(client: str) -> str:
        return str(client or "unknown")[:64]

    def _locked_out(self, client: str, now: float) -> float:
        fails = self._failures.get(client)
        if not fails:
            return 0.0
        while fails and now - fails[0] >= ADMIN_FAILURE_WINDOW_S:
            fails.popleft()
        if not fails:
            del self._failures[client]
            return 0.0
        if len(fails) < ADMIN_MAX_FAILURES:
            return 0.0
        return ADMIN_FAILURE_WINDOW_S - (now - fails[0])

    def _note_failure(self, client: str, now: float) -> None:
        fails = self._failures.get(client)
        if fails is None:
            fails = self._failures[client] = deque(maxlen=ADMIN_MAX_FAILURES)
            while len(self._failures) > ADMIN_MAX_CLIENTS:
                self._failures.popitem(last=False)
        else:
            self._failures.move_to_end(client)
        fails.append(now)

    # ------------------------------------------------------------------ sessions
    def login(self, client: str, secret: str) -> str | None:
        """A new session token if `secret` is right; None if it is wrong (which counts against the client).

        Raises AdminThrottled, without looking at the secret and without counting anything, when the client is
        locked out or when no more sign-ins can queue for the hash slot."""
        if not self._usable:
            return None
        client = self._client(client)
        with self._lock:
            wait = self._locked_out(client, self.clock())
            if wait > 0:
                raise AdminThrottled(wait, busy=False)
            # attempts already being checked count too, so parallel requests cannot exceed the limit
            in_flight = self._pending.get(client, 0)
            if len(self._failures.get(client, ())) + in_flight >= ADMIN_MAX_FAILURES or self._waiting >= ADMIN_MAX_WAITING:
                raise AdminThrottled(ADMIN_BUSY_RETRY_S, busy=True)
            self._pending[client] = in_flight + 1
            self._waiting += 1
        ok = False
        try:
            if isinstance(secret, str) and 0 < len(secret) <= ADMIN_MAX_SECRET_CHARS:
                with self._hash_lock:
                    ok = verify_admin_secret(self._encoded, secret)
        finally:
            with self._lock:
                self._waiting -= 1
                left = self._pending.get(client, 1) - 1
                if left > 0:
                    self._pending[client] = left
                else:
                    self._pending.pop(client, None)
                now = self.clock()
                token = None
                if ok:
                    self._failures.pop(client, None)
                    token = self._issue(now)
                else:
                    self._note_failure(client, now)
        return token

    def _issue(self, now: float) -> str:
        for key in [k for k, expiry in self._sessions.items() if expiry <= now]:
            del self._sessions[key]
        while len(self._sessions) >= ADMIN_MAX_SESSIONS:
            del self._sessions[min(self._sessions, key=self._sessions.__getitem__)]   # the one closest to expiry
        token = secrets.token_urlsafe(32)
        self._sessions[_session_key(token)] = now + ADMIN_SESSION_S
        return token

    def valid(self, token: str | None) -> bool:
        if not self._usable or not isinstance(token, str) or not token or len(token) > 128:
            return False
        key = _session_key(token)
        with self._lock:
            expiry = self._sessions.get(key)
            if expiry is None:
                return False
            if expiry <= self.clock():
                del self._sessions[key]
                return False
            return True

    def logout(self, token: str | None) -> None:
        if isinstance(token, str) and token and len(token) <= 128:
            with self._lock:
                self._sessions.pop(_session_key(token), None)
