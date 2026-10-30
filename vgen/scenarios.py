"""Scenario packs (scenarios/packs.yaml) and their example assets."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from vgen.assets import get_asset, store_bytes
from vgen.config import ROOT, Settings

log = logging.getLogger("vgen.scenarios")

PACKS_FILE = ROOT / "scenarios" / "packs.yaml"
ASSETS_DIR = ROOT / "scenarios" / "assets"


def public_urls(repo: str, sha: str, path: str) -> list[str]:
    p = quote(path)
    return [f"https://cdn.jsdelivr.net/gh/{repo}@{sha}/{p}", f"https://raw.githubusercontent.com/{repo}/{sha}/{p}"]


def load_packs(path: Path = PACKS_FILE) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def register_assets(settings: Settings, data: dict[str, Any]) -> dict[str, str]:
    """Store example assets (idempotent). Returns asset name -> asset id (sha256)."""
    ids: dict[str, str] = {}
    for name, meta in (data.get("assets") or {}).items():
        file = ASSETS_DIR / meta["file"]
        if not file.exists():
            if not meta.get("generated"):
                log.warning("example asset missing: %s", file)
            continue
        urls = public_urls(meta["repo"], meta["sha"], meta["path"]) if meta.get("repo") else []
        label = (meta.get("label") or {}).get("en", name)
        asset = store_bytes(settings.uploads_dir, file.read_bytes(), file.name, label=label,
                            source="generated" if meta.get("generated") else "example", public_urls=urls,
                            normalize_images=False)
        ids[name] = asset.id
    return ids


def _map_assets(assets: dict[str, Any] | None, ids: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    out: dict[str, Any] = {}
    missing: list[str] = []
    for key, value in (assets or {}).items():
        if isinstance(value, list):
            mapped = [ids.get(v) for v in value]
            missing += [v for v, m in zip(value, mapped) if m is None]
            out[key] = [m for m in mapped if m]
        else:
            if value not in ids:
                missing.append(value)
            elif value:
                out[key] = ids[value]
    return out, missing


def packs_for_api(data: dict[str, Any], ids: dict[str, str]) -> dict[str, Any]:
    packs = []
    for pack in data.get("packs") or []:
        run_assets, missing = _map_assets(pack.get("assets"), ids)
        prompts = []
        for pr in pack.get("prompts") or []:
            p_assets, p_missing = _map_assets(pr.get("assets"), ids)
            missing += p_missing
            prompts.append({**pr, "assets": p_assets, "missing_assets": p_missing})
        packs.append({**pack, "assets": run_assets, "prompts": prompts, "missing_assets": sorted(set(missing))})
    assets = {}
    for name, meta in (data.get("assets") or {}).items():
        a = get_asset(ids[name]) if name in ids else None
        assets[name] = {
            "name": name, "id": ids.get(name), "label": meta.get("label"), "generated": bool(meta.get("generated")),
            "kind": a.kind if a else None, "public_urls": a.public_urls if a else [],
            "width": a.width if a else None, "height": a.height if a else None,
            "duration": a.duration if a else None,
        }
    return {"categories": data.get("categories") or {}, "packs": packs, "assets": assets}
