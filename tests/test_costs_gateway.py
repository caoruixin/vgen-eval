from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from vgen.config import Endpoint
from vgen.costs import estimate_job, quota_to_usd, video_tokens
from vgen.gateway import (AFTER_SEND, AUTH, BUSINESS, NETWORK_BEFORE_SEND, NOT_FOUND, RATE_LIMIT, SERVER, Gateway,
                          GatewayError, RateLimiter, classify)


# ------------------------------------------------------------------ costs
def test_per_second_estimate(registry):
    est = estimate_job(registry, "happyhorse-1.0-t2v", {"duration": 5, "resolution": "720p"})
    assert est.usd == pytest.approx(0.595)


def test_veo_audio_worst_case_doubles(registry):
    base = estimate_job(registry, "veo-3.1-fast-generate-preview", {"duration": 4, "resolution": "720p"}).usd
    audio = estimate_job(registry, "veo-3.1-fast-generate-preview", {"duration": 4, "resolution": "720p", "audio": True}).usd
    assert base == pytest.approx(0.40)
    assert audio == pytest.approx(0.80)


def test_seedance_token_formula(registry):
    tokens = video_tokens("720p", "16:9", 5)
    assert tokens == int(1280 * 720 * (24 * 5 + 1) / 1024)
    # usage.totalTokens reported by tokenbay.com for real clips (2026-10-29)
    assert video_tokens("720p", "16:9", 6) == 130_500
    assert video_tokens("480p", "16:9", 4) == 40_594
    est = estimate_job(registry, "seedance-2.0-mini", {"duration": 5, "resolution": "720p", "ratio": "16:9"})
    assert est.usd == pytest.approx(tokens * 3.50 / 1e6)


def test_seedance_adaptive_and_auto_duration_are_worst_case(registry):
    worst = estimate_job(registry, "seedance-2.0", {"duration": -1, "resolution": "720p", "ratio": "adaptive"})
    explicit = estimate_job(registry, "seedance-2.0", {"duration": 15, "resolution": "720p", "ratio": "16:9"})
    assert worst.usd >= explicit.usd


def test_seedance_with_video_counts_input_tokens(registry):
    est = estimate_job(registry, "seedance-2.0-mini", {"duration": 4, "resolution": "480p", "ratio": "16:9",
                                                       "has_video_input": True, "input_video_s": 6.8})
    expected = (video_tokens("480p", "16:9", 4) + video_tokens("480p", "16:9", 6.8)) * 2.10 / 1e6
    assert est.usd == pytest.approx(expected)


def test_unknown_price(registry):
    assert estimate_job(registry, "not-a-model", {"duration": 5}).usd is None


def test_existing_clip_uses_its_original_price_after_catalog_change(registry):
    from vgen.db import Job
    from vgen.runs import actual_cost

    job = Job(model="seedance-2.0-mini", effective={"resolution": "720p"},
              last_detail={"usage": {"totalTokens": 100_000}},
              provenance={"price_snapshot": {"per_mtok": {
                  "without_video": {"720p": 1.40}, "with_video": {"720p": .84}}}})
    assert registry.price(job.model)["per_mtok"]["without_video"]["720p"] == 3.50
    assert actual_cost(registry, job)[0] == pytest.approx(.14)
    job.model = "wan3.0"
    job.effective = {"resolution": "720p", "duration": 5}
    job.last_detail = {}
    job.provenance = {"price_snapshot": {"per_second": {"720p": .05}}}
    assert actual_cost(registry, job)[0] == pytest.approx(.25)


def test_quota_conversion(registry):
    assert quota_to_usd(registry, 500000) == pytest.approx(1.0)


# ------------------------------------------------------------------ error classification
def test_classify_http200_error_is_business():
    err = classify(200, {"error": {"message": "Unsupported video model: x", "code": "invalid_model"}}, "")
    assert err.kind == BUSINESS and err.code == "invalid_model"


def test_classify_rate_limit_401_vs_auth_401():
    rl = classify(401, {"error": {"message": "Account request rate limit exceeded for the current window"}}, "")
    auth = classify(401, {"error": {"message": "invalid api key"}}, "")
    assert rl.kind == RATE_LIMIT
    assert auth.kind == AUTH


def test_classify_other_statuses():
    assert classify(404, {"error": {"message": "task not found"}}, "").kind == NOT_FOUND
    assert classify(503, None, "bad gateway").kind == SERVER
    assert classify(429, None, "").kind == RATE_LIMIT
    assert classify(400, {"error": {"message": "bad duration"}}, "").kind == BUSINESS
    assert classify(200, {"task_id": "t"}, "") is None


# ------------------------------------------------------------------ rate limiter
def test_rate_limiter_rolling_window():
    clock = {"t": 0.0}
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)
        clock["t"] += s

    async def go():
        rl = RateLimiter(3, window=60, clock=lambda: clock["t"], sleep=fake_sleep)
        for _ in range(3):
            await rl.acquire()
        assert slept == []
        await rl.acquire()          # 4th must wait for the window
        return rl

    rl = asyncio.run(go())
    assert clock["t"] >= 60
    assert rl.used() <= 3


def test_rate_limiter_high_priority_goes_first():
    async def go():
        rl = RateLimiter(1, window=0.3)
        order: list[str] = []
        await rl.acquire()

        async def low():
            await rl.acquire()
            order.append("low")

        async def high():
            await asyncio.sleep(0.01)
            await rl.acquire(high=True)
            order.append("high")

        await asyncio.gather(low(), high())
        return order

    assert asyncio.run(go())[0] == "high"


# ------------------------------------------------------------------ gateway behaviour
def _gw(handler) -> Gateway:
    ep = Endpoint("com", "test", "http://gw.test", "sk-test", rpm=1000, trust_env=False)
    return Gateway(ep, transport=httpx.MockTransport(handler))


def test_submit_business_error_on_http200():
    def handler(req):
        return httpx.Response(200, json={"error": {"message": "bad", "code": "invalid_request"}})

    async def go():
        gw = _gw(handler)
        with pytest.raises(GatewayError) as e:
            await gw.submit_video({"model": "x"})
        await gw.aclose()
        return e.value

    assert asyncio.run(go()).kind == BUSINESS


def test_submit_timeout_after_send_is_unknown():
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    async def go():
        gw = _gw(handler)
        with pytest.raises(GatewayError) as e:
            await gw.submit_video({"model": "x"})
        await gw.aclose()
        return e.value

    assert asyncio.run(go()).kind == AFTER_SEND


def test_connect_error_is_safe():
    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    async def go():
        gw = _gw(handler)
        with pytest.raises(GatewayError) as e:
            await gw.get_video("task_1")
        await gw.aclose()
        return e.value

    assert asyncio.run(go()).kind == NETWORK_BEFORE_SEND


def test_rate_limit_pauses_and_auth_disables():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if req.url.path.endswith("rl"):
            return httpx.Response(401, json={"error": {"message": "Account request rate limit exceeded"}})
        return httpx.Response(401, json={"error": {"message": "invalid token"}})

    async def go():
        gw = _gw(handler)
        with pytest.raises(GatewayError):
            await gw.request_json("GET", "/v1/rl")
        paused = gw.limiter.wait_time() > 0
        gw.limiter.paused_until = 0
        with pytest.raises(GatewayError):
            await gw.request_json("GET", "/v1/models")
        disabled = gw.disabled_reason
        with pytest.raises(GatewayError) as e:
            await gw.request_json("GET", "/v1/models")
        await gw.aclose()
        return paused, disabled, e.value.kind, calls["n"]

    paused, disabled, kind, n = asyncio.run(go())
    assert paused
    assert disabled == "invalid token"
    assert kind == AUTH
    assert n == 2                   # third call refused locally


def test_download_streams_to_file(tmp_path):
    payload = b"\x00\x00\x00\x18ftypmp42" + b"x" * 1000

    def handler(req):
        return httpx.Response(200, content=payload, headers={"content-type": "video/mp4"})

    async def go():
        gw = _gw(handler)
        size = await gw.download_video("task_1", tmp_path / "v.mp4")
        await gw.aclose()
        return size

    assert asyncio.run(go()) == len(payload)
    assert (tmp_path / "v.mp4").read_bytes() == payload
