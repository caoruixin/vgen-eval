"""Command line: vgen serve | probe | gen-assets | models."""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import os
import sys


def _settings(mock: bool = False):
    if mock:
        os.environ["VGEN_MOCK"] = "1"
    from vgen.config import load_settings

    return load_settings()


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from vgen.api import create_app

    settings = _settings(args.mock)
    host = args.host or settings.host
    port = args.port or settings.port
    if host not in ("127.0.0.1", "localhost"):
        print("refusing to bind outside localhost: the app can spend money on your API key", file=sys.stderr)
        return 2
    mode = "MOCK gateway (no spending)" if settings.mock else ", ".join(e.name for e in settings.endpoints.values())
    print(f"vgen-eval on http://{host}:{port}  ·  {mode}")
    uvicorn.run(create_app(settings), host=host, port=port, log_level=args.log_level)
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    from vgen.probe import cases, run_probe

    if args.list:
        for c in cases():
            print(f"{c.id:26s} {c.model:32s} {c.task:8s} {c.params}")
        return 0
    if args.resummarize:
        from vgen.api import EngineLock, build_runtime
        from vgen.probe import resummarize

        settings = _settings(args.mock)
        lock = EngineLock(settings.data_dir / "engine.lock")
        lock.acquire()
        try:
            resummarize(build_runtime(settings))
        finally:
            lock.release()
        return 0
    settings = _settings(args.mock)
    selected = [s.strip() for s in args.cases.split(",")] if args.cases and args.cases != "all" else None
    return asyncio.run(run_probe(settings, selected, args.budget, args.yes, judge_case=not args.no_judge,
                                 dry_run=args.dry_run))


def cmd_models(args: argparse.Namespace) -> int:
    from vgen.catalog.registry import Registry
    from vgen.config import load_settings
    from vgen.gateway import Gateway

    settings = load_settings()
    reg = Registry.load()

    async def go() -> None:
        for key, ep in settings.endpoints.items():
            gw = Gateway(ep)
            try:
                ids = set(await gw.list_models())
            finally:
                await gw.aclose()
            print(f"== {ep.name}: {len(ids)} models")
            for m in reg.models.values():
                print(f"  {'✓' if m.id in ids else '✗'} {m.id}")
            reg.set_live(key, ids)
            extra = reg.unregistered(key)
            if extra:
                print("  unregistered video-like:", ", ".join(extra))

    asyncio.run(go())
    return 0


def cmd_gen_assets(args: argparse.Namespace) -> int:
    """Generate scenario images marked `generated: true` with a Tokenbay image model (a few cents)."""
    import httpx
    import yaml

    from vgen.config import load_settings
    from vgen.scenarios import ASSETS_DIR, PACKS_FILE

    settings = load_settings()
    ep = settings.endpoints["com"]
    data = yaml.safe_load(PACKS_FILE.read_text(encoding="utf-8"))
    todo = [(n, m) for n, m in data["assets"].items() if m.get("generated") and (args.force or not (ASSETS_DIR / m["file"]).exists())]
    if not todo:
        print("nothing to generate")
        return 0
    with httpx.Client(base_url=ep.base_url, headers={"Authorization": f"Bearer {ep.api_key}"}, timeout=240,
                      trust_env=ep.trust_env) as c:
        for name, meta in todo:
            print(f"generating {name} with {args.model} …")
            resp = c.post("/v1/images/generations", json={"model": args.model, "prompt": meta["prompt"],
                                                          "size": "1024x1024", "n": 1})
            body = resp.json()
            if resp.status_code >= 400 or "error" in body:
                print("  failed:", body.get("error") or resp.text[:300])
                return 1
            item = (body.get("data") or [{}])[0]
            if item.get("b64_json"):
                raw = base64.b64decode(item["b64_json"])
            elif item.get("url"):
                raw = httpx.get(item["url"], timeout=120, follow_redirects=True).content
            else:
                print("  no image in response:", str(body)[:300])
                return 1
            out = ASSETS_DIR / meta["file"]
            from io import BytesIO

            from PIL import Image

            with Image.open(BytesIO(raw)) as im:
                im.convert("RGB").save(out, "PNG")
            print(f"  saved {out} ({out.stat().st_size // 1024} KB)")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="vgen", description="Video-generation evaluation platform (Tokenbay)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--mock", action="store_true", help="use the built-in fake gateway (no spending)")
    s.add_argument("--log-level", default="info")
    s.set_defaults(fn=cmd_serve)
    pr = sub.add_parser("probe", help="paid live capability probe at the cheapest settings")
    pr.add_argument("--cases", default="all", help="comma-separated case ids (default: all)")
    pr.add_argument("--budget", type=float, default=10.0, help="max estimated USD for this probe run")
    pr.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    pr.add_argument("--list", action="store_true", help="list cases and exit")
    pr.add_argument("--mock", action="store_true")
    pr.add_argument("--no-judge", action="store_true")
    pr.add_argument("--dry-run", action="store_true", help="print the plan and payloads, submit nothing")
    pr.add_argument("--resummarize", action="store_true", help="rebuild probe_status.json from existing probe jobs")
    pr.set_defaults(fn=cmd_probe)
    m = sub.add_parser("models", help="check live model availability")
    m.set_defaults(fn=cmd_models)
    g = sub.add_parser("gen-assets", help="generate scenario images marked generated: true")
    g.add_argument("--model", default="gpt-image-2")
    g.add_argument("--force", action="store_true")
    g.set_defaults(fn=cmd_gen_assets)
    args = p.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
