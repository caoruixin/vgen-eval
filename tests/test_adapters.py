from __future__ import annotations

import pytest

from vgen.adapters import build_request
from vgen.adapters.base import AssetRef, AssetResolver, GenerationSpec, ResolvedAsset, Unsupported, redact_payload

URL_IMG = ResolvedAsset("img_url", "image", "image/jpeg", ["https://cdn.example/cat.jpg"], 832, 1104,
                        read_bytes=lambda: b"\xff\xd8\xffjpeg")
LOCAL_IMG = ResolvedAsset("img_local", "image", "image/png", [], 1024, 1024, read_bytes=lambda: b"\x89PNGdata")
VIDEO = ResolvedAsset("vid", "video", "video/mp4", ["https://cdn.example/clip.mp4"], 1280, 720, 6.8)
LOCAL_VIDEO = ResolvedAsset("vid_local", "video", "video/mp4", [], 1280, 720, 6.8, read_bytes=lambda: b"mp4")
AUDIO = ResolvedAsset("aud", "audio", "audio/wav", ["https://cdn.example/talk.wav"], duration=5.0)

ASSETS = AssetResolver({a.asset_id: a for a in (URL_IMG, LOCAL_IMG, VIDEO, LOCAL_VIDEO, AUDIO)}.get)


def ref(asset_id: str) -> AssetRef:
    return AssetRef(asset_id=asset_id)


def test_happyhorse_t2v_payload(registry):
    m = registry.get("happyhorse-1.0-t2v")
    r = build_request(m, GenerationSpec(task="t2v", prompt="a cat", duration=6, resolution="720p", ratio="16:9",
                                        seed=7), ASSETS)
    assert r.payload == {"model": "happyhorse-1.0-t2v", "prompt": "a cat", "size": "720P", "duration": 6,
                         "metadata": {"watermark": False, "seed": 7, "ratio": "16:9"}}
    # the live probe showed HappyHorse always adds a soundtrack; the adapter says so instead of hiding it
    assert [(a.field, a.used) for a in r.adjustments] == [("audio", True)]
    assert r.effective["duration"] == 6


def test_happyhorse_snaps_and_reports_adjustments(registry):
    m = registry.get("happyhorse-1.0-t2v")
    r = build_request(m, GenerationSpec(task="t2v", prompt="x", duration=20, resolution="480p", ratio="2:1",
                                        audio=True, negative_prompt="blur"), ASSETS)
    fields = {a.field: (a.requested, a.used) for a in r.adjustments}
    assert fields["duration"] == (20, 15)
    assert fields["resolution"] == ("480p", "720p")
    assert fields["ratio"][1] in ("16:9", "21:9")
    assert "audio" not in fields                      # audio requested and always produced
    assert "negative_prompt" in fields
    assert "negative_prompt" not in r.payload


def test_happyhorse_i2v_uses_url_and_omits_ratio(registry):
    m = registry.get("happyhorse-1.0-i2v")
    r = build_request(m, GenerationSpec(task="i2v", prompt="move", first_frame=ref("img_url")), ASSETS)
    assert r.payload["input_reference"] == "https://cdn.example/cat.jpg"
    assert "ratio" not in r.payload.get("metadata", {})


def test_happyhorse_i2v_data_uri_is_flagged_unverified(registry):
    m = registry.get("happyhorse-1.0-i2v")
    r = build_request(m, GenerationSpec(task="i2v", prompt="move", first_frame=ref("img_local")), ASSETS)
    assert r.payload["input_reference"].startswith("data:image/png;base64,")
    assert any("not yet verified" in w for w in r.warnings)


def test_happyhorse_r2v_media_and_ref_tokens(registry):
    m = registry.get("happyhorse-1.0-r2v")
    r = build_request(m, GenerationSpec(task="r2v", prompt="{ref1} hugs {ref2}",
                                        references=[ref("img_url"), ref("img_local")]), ASSETS)
    assert r.payload["prompt"] == "[Image 1] hugs [Image 2]"
    media = r.payload["metadata"]["input"]["media"]
    assert [x["type"] for x in media] == ["reference_image", "reference_image"]


def test_wan_sets_prompt_extend_off_and_negative_prompt(registry):
    m = registry.get("wan2.7-t2v")
    r = build_request(m, GenerationSpec(task="t2v", prompt="x", negative_prompt="blurry"), ASSETS)
    assert r.payload["metadata"]["prompt_extend"] is False
    assert r.payload["negative_prompt"] == "blurry"


def test_experimental_task_requires_opt_in(registry):
    m = registry.get("wan2.7-videoedit")
    spec = GenerationSpec(task="v2v_edit", prompt="watercolour", source_video=ref("vid"))
    with pytest.raises(Unsupported, match="experimental"):
        build_request(m, spec, ASSETS)
    r = build_request(m, spec, ASSETS, allow_experimental=True)
    assert r.payload["metadata"]["input"]["media"][0] == {"type": "video", "url": "https://cdn.example/clip.mp4"}
    assert "duration" not in r.payload


def test_veo_1080p_forces_8s_and_ratio_snaps(registry):
    m = registry.get("veo-3.1-fast-generate-preview")
    r = build_request(m, GenerationSpec(task="t2v", prompt="x", duration=6, resolution="1080p", ratio="1:1",
                                        audio=True), ASSETS)
    assert r.payload["duration"] == 8
    assert r.payload["aspect_ratio"] in ("16:9", "9:16")
    assert r.payload["generate_audio"] is True
    assert r.payload["enhance_prompt"] is False
    assert {a.field for a in r.adjustments} >= {"duration", "ratio"}


def test_veo_i2v_auto_ratio_follows_portrait_image(registry):
    m = registry.get("veo-3.1-lite-generate-preview")
    r = build_request(m, GenerationSpec(task="i2v", prompt="x", ratio="auto", first_frame=ref("img_url")), ASSETS)
    assert r.payload["aspect_ratio"] == "9:16"      # 832x1104 is portrait
    assert r.payload["image"] == "https://cdn.example/cat.jpg"


def test_veo_flf2v_sends_data_uris(registry):
    m = registry.get("veo-3.1-generate-preview")
    r = build_request(m, GenerationSpec(task="flf2v", prompt="x", first_frame=ref("img_local"),
                                        last_frame=ref("img_local")), ASSETS)
    assert r.payload["image"].startswith("data:image/png;base64,")
    assert r.payload["lastFrameImage"].startswith("data:")
    assert r.warnings == []          # documented: data URIs accepted


def test_veo_rejects_video_edit(registry):
    with pytest.raises(Unsupported):
        build_request(registry.get("veo-3.1-generate-preview"),
                      GenerationSpec(task="v2v_edit", prompt="x", source_video=ref("vid")), ASSETS)


def test_seedance_t2v_and_audio(registry):
    m = registry.get("seedance-2.0")
    r = build_request(m, GenerationSpec(task="t2v", prompt="x", duration=5, resolution="720p", ratio="9:16",
                                        audio=True, seed=1), ASSETS)
    assert r.payload == {"model": "seedance-2.0", "prompt": "x", "duration": 5,
                         "metadata": {"resolution": "720p", "watermark": False, "generate_audio": True, "seed": 1,
                                      "ratio": "9:16"}}


def test_seedance_flf2v_content_roles(registry):
    m = registry.get("seedance-2.0")
    r = build_request(m, GenerationSpec(task="flf2v", prompt="x", ratio="auto", first_frame=ref("img_url"),
                                        last_frame=ref("img_local")), ASSETS)
    content = r.payload["metadata"]["content"]
    assert [c["role"] for c in content] == ["first_frame", "last_frame"]
    assert r.payload["metadata"]["ratio"] == "adaptive"


def test_seedance_extend_needs_public_video_url(registry):
    m = registry.get("seedance-2.0")
    r = build_request(m, GenerationSpec(task="extend", prompt="continue", source_video=ref("vid")), ASSETS)
    assert r.payload["metadata"]["content"][0]["video_url"]["url"] == "https://cdn.example/clip.mp4"
    assert r.effective["has_video_input"] is True
    with pytest.raises(Unsupported, match="public URL"):
        build_request(m, GenerationSpec(task="extend", prompt="continue", source_video=ref("vid_local")), ASSETS)


def test_seedance_fast_has_no_1080p(registry):
    r = build_request(registry.get("seedance-2.0-fast"), GenerationSpec(task="t2v", prompt="x", resolution="1080p"),
                      ASSETS)
    assert r.payload["metadata"]["resolution"] == "720p"
    assert r.adjustments[0].field == "resolution"


def test_seedance_chinese_prompt_ref_tokens(registry):
    r = build_request(registry.get("seedance-2.0"),
                      GenerationSpec(task="r2v", prompt="{ref1}中的女孩和{ref2}中的蛇", references=[ref("img_url"), ref("img_url")]),
                      ASSETS)
    assert r.payload["prompt"] == "图1中的女孩和图2中的蛇"


def test_redact_payload_hides_data_uris():
    out = redact_payload({"image": "data:image/png;base64," + "A" * 5000, "prompt": "short"})
    assert out["prompt"] == "short"
    assert out["image"].startswith("data:image/png;base64,") and "chars>" in out["image"]


def test_wan_edit_estimates_on_source_length(registry):
    m = registry.get("wan2.7-videoedit")
    r = build_request(m, GenerationSpec(task="v2v_edit", prompt="watercolour", duration=3, source_video=ref("vid")),
                      ASSETS, allow_experimental=True)
    assert r.effective["duration"] == 7          # 6.8 s source, rounded up
