"""Visitor-supplied Tokenbay keys (public mode).

Keys live only in this process's memory: never in the database, logs or API responses.
Each key gets its own Gateway (client, rate limiter, auth state), because Tokenbay limits
and bills per account and one visitor's bad key must not disable anyone else's runs.
After a restart the keys are gone; a visitor's page re-attaches its key to resume.
"""

from __future__ import annotations

import dataclasses
import hashlib
import time
from typing import Callable

import httpx

from vgen.config import Endpoint
from vgen.gateway import Gateway

KEY_HEADER = "x-tokenbay-key"
CHECK_TTL_S = 600.0      # a key that listed models recently is not re-checked on every request
IDLE_CLOSE_S = 900.0     # close a key's client once no run uses it and nobody touched it for this long


def key_id(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


class KeyRing:
    def __init__(self, endpoint: Endpoint,
                 transport_factory: Callable[[], httpx.AsyncBaseTransport | None] | None = None,
                 submit_read_timeout: float = 150.0, clock: Callable[[], float] = time.monotonic):
        self.endpoint = endpoint
        self.transport_factory = transport_factory
        self.submit_read_timeout = submit_read_timeout
        self.clock = clock
        self._gateways: dict[str, Gateway] = {}      # key id -> gateway
        self._runs: dict[str, str] = {}              # run id -> key id
        self._checked: dict[str, float] = {}         # key id -> last successful check
        self._touched: dict[str, float] = {}         # key id -> last attach/check

    def gateway_for(self, api_key: str) -> Gateway:
        kid = key_id(api_key)
        gw = self._gateways.get(kid)
        if gw is None:
            transport = self.transport_factory() if self.transport_factory else None
            gw = Gateway(dataclasses.replace(self.endpoint, api_key=api_key), transport=transport,
                         submit_read_timeout=self.submit_read_timeout)
            self._gateways[kid] = gw
        self._touched[kid] = self.clock()
        return gw

    async def check(self, api_key: str) -> None:
        """Raise GatewayError unless Tokenbay accepts the key (GET /v1/models, free)."""
        kid = key_id(api_key)
        gw = self._gateways.get(kid)
        if gw is not None and gw.disabled_reason:
            # a rejected key is re-tried on an explicit check (e.g. it was topped up or re-enabled)
            self._gateways.pop(kid)
            self._checked.pop(kid, None)
            await gw.aclose()
        if self.clock() - self._checked.get(kid, -CHECK_TTL_S) < CHECK_TTL_S:
            self._touched[kid] = self.clock()
            return
        await self.gateway_for(api_key).list_models()
        self._checked[kid] = self.clock()

    def attach(self, run_id: str, api_key: str) -> Gateway:
        gw = self.gateway_for(api_key)
        self._runs[run_id] = key_id(api_key)
        return gw

    def for_run(self, run_id: str) -> Gateway | None:
        kid = self._runs.get(run_id)
        return self._gateways.get(kid) if kid else None

    def runs_sharing(self, run_id: str) -> list[str]:
        """Runs that use the same key as `run_id` (they share its rate limit)."""
        kid = self._runs.get(run_id)
        return [r for r, k in self._runs.items() if k == kid] if kid else [run_id]

    def attached(self) -> list[str]:
        return list(self._runs)

    def release(self, run_id: str) -> None:
        kid = self._runs.pop(run_id, None)
        if kid:
            self._touched[kid] = self.clock()

    async def close_idle(self) -> None:
        in_use = set(self._runs.values())
        now = self.clock()
        for kid in [k for k in self._gateways if k not in in_use and now - self._touched.get(k, 0) > IDLE_CLOSE_S]:
            gw = self._gateways.pop(kid)
            self._checked.pop(kid, None)
            self._touched.pop(kid, None)
            await gw.aclose()

    async def aclose(self) -> None:
        for gw in self._gateways.values():
            await gw.aclose()
        self._gateways.clear()
        self._runs.clear()
