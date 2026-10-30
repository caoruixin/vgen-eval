"""Content-addressed asset store (images, source videos, audio) with optional public URLs."""

from __future__ import annotations

import hashlib
import io
import mimetypes
from pathlib import Path

from PIL import Image
from sqlmodel import select

from vgen.adapters.base import AssetResolver, ResolvedAsset
from vgen.db import Asset, session

IMAGE_MAX_SIDE = 2048
KIND_BY_MIME = {"image": "image", "video": "video", "audio": "audio"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _guess_mime(filename: str, data: bytes) -> str:
    mime = mimetypes.guess_type(filename.lower())[0]
    if mime:
        return mime
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[4:8] == b"ftyp":
        return "video/mp4"
    return "application/octet-stream"


def _media_meta(path: Path, kind: str) -> tuple[int | None, int | None, float | None]:
    if kind == "image":
        with Image.open(path) as im:
            return im.width, im.height, None
    try:
        import av

        with av.open(str(path)) as c:
            dur = float(c.duration / 1e6) if c.duration else None
            vs = next((s for s in c.streams if s.type == "video"), None)
            if vs is not None:
                return vs.codec_context.width, vs.codec_context.height, dur
            return None, None, dur
    except Exception:  # pragma: no cover - unreadable media is still stored
        return None, None, None


def store_bytes(uploads_dir: Path, data: bytes, filename: str, *, label: str = "", source: str = "upload",
                public_urls: list[str] | None = None, normalize_images: bool = True,
                owner: str | None = None) -> Asset:
    mime = _guess_mime(filename, data)
    kind = KIND_BY_MIME.get(mime.split("/")[0], "image")
    if kind == "image" and normalize_images:
        data, mime = _normalize_image(data, mime)
    digest = sha256_bytes(data)
    # a visitor's upload is their own row (and only theirs to list), even if someone else uploaded the same bytes
    asset_id = digest if owner is None else sha256_bytes(owner.encode() + b":" + data)
    ext = mimetypes.guess_extension(mime) or Path(filename).suffix or ".bin"
    if ext == ".jpe":
        ext = ".jpg"
    path = uploads_dir / f"{digest[:24]}{ext}"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(data)
    width, height, duration = _media_meta(path, kind)
    with session() as s:
        asset = s.get(Asset, asset_id)
        if asset is None:
            asset = Asset(id=asset_id, kind=kind, label=label or filename, filename=filename, mime=mime,
                          size=len(data), width=width, height=height, duration=duration,
                          local_path=str(path), public_urls=list(public_urls or []), source=source, owner=owner)
        else:
            merged = list(dict.fromkeys([*(asset.public_urls or []), *(public_urls or [])]))
            asset.public_urls = merged
            if label and not asset.label:
                asset.label = label
        s.add(asset)
        s.commit()
        s.refresh(asset)
        return asset


def _normalize_image(data: bytes, mime: str) -> tuple[bytes, str]:
    """Keep originals when acceptable; otherwise convert to JPEG/PNG within size limits."""
    with Image.open(io.BytesIO(data)) as im:
        ok_format = mime in ("image/jpeg", "image/png", "image/webp")
        if ok_format and max(im.size) <= IMAGE_MAX_SIDE and len(data) <= 10 * 1024 * 1024:
            return data, mime
        im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
        im.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
        out = io.BytesIO()
        if im.mode == "RGBA":
            im.save(out, "PNG", optimize=True)
            return out.getvalue(), "image/png"
        im.save(out, "JPEG", quality=92)
        return out.getvalue(), "image/jpeg"


def get_asset(asset_id: str) -> Asset | None:
    with session() as s:
        return s.get(Asset, asset_id)


def list_assets(viewer: str | None = None, scoped: bool = False) -> list[Asset]:
    """All assets; with `scoped`, only the shared ones plus the viewer's own uploads."""
    with session() as s:
        q = select(Asset).order_by(Asset.created_at.desc())
        if scoped:
            q = q.where((Asset.owner == None) | (Asset.owner == viewer))  # noqa: E711
        return list(s.exec(q))


def resolver() -> AssetResolver:
    def lookup(asset_id: str) -> ResolvedAsset | None:
        a = get_asset(asset_id)
        if a is None:
            return None
        path = Path(a.local_path)
        return ResolvedAsset(asset_id=a.id, kind=a.kind, mime=a.mime, public_urls=list(a.public_urls or []),
                             width=a.width, height=a.height, duration=a.duration,
                             read_bytes=(lambda p=path: p.read_bytes()))

    return AssetResolver(lookup)
