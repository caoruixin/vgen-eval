"""Command line: vgen serve | probe | gen-assets | models | relocate | admin-hash."""

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
    if settings.public:
        mode += "  ·  public: visitors use their own keys"
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


def relocate_db(db_path, pairs: list[tuple[str, str]]) -> int:
    """Rewrite path prefixes in every text column (paths are stored absolute, also inside JSON)."""
    import sqlite3

    con = sqlite3.connect(db_path)
    changed = 0
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')
                    if r[2].upper() not in ("INTEGER", "FLOAT", "REAL", "BOOLEAN", "NUMERIC")]
            for col in cols:
                for old, new in pairs:
                    cur = con.execute(f'UPDATE "{table}" SET "{col}" = REPLACE("{col}", ?, ?) '
                                      f'WHERE instr("{col}", ?) > 0', (old, new, old))
                    changed += cur.rowcount
        con.commit()
    finally:
        con.close()
    return changed


def cmd_relocate(args: argparse.Namespace) -> int:
    from pathlib import Path

    from vgen.api import EngineLock

    pairs = []
    for m in args.map:
        old, sep, new = m.partition("=")
        if not sep or not old:
            print(f"--map must look like OLD=NEW, got {m!r}", file=sys.stderr)
            return 2
        pairs.append((old, new))
    db_path = Path(args.db) if args.db else _settings().db_path
    lock = EngineLock(db_path.parent / "engine.lock")
    lock.acquire()
    try:
        n = relocate_db(db_path, pairs)
    finally:
        lock.release()
    print(f"rewrote {n} values in {db_path}")
    return 0


def cmd_import_experiment(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path
    from vgen import db
    from vgen.api import EngineLock
    from vgen.experiments import import_prepared, prepare, preview

    prepared = prepare(Path(args.manifest).resolve())
    if args.dry_run:
        print(json.dumps({**preview(prepared), "dry_run": True}, indent=2))
        return 0
    data_dir = Path(args.data_dir).resolve() if args.data_dir else _settings().data_dir
    lock = EngineLock(data_dir / "engine.lock")
    lock.acquire()
    try:
        db.init_db(data_dir / "app.db")
        print(json.dumps(import_prepared(prepared, data_dir), indent=2))
    finally:
        lock.release()
    return 0


def cmd_package_experiment(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path
    from vgen.experiments import package_experiment, prepare
    print(json.dumps(package_experiment(prepare(Path(args.manifest).resolve()), Path(args.output).resolve()), indent=2))
    return 0


def cmd_import_report(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path
    from vgen import db
    from vgen.api import EngineLock
    from vgen.report_revisions import prepare_report, publish_report

    prepared = prepare_report(Path(args.manifest).resolve())
    if args.dry_run:
        m = prepared["manifest"]
        print(json.dumps({"experiment_id": m["experiment_id"], "revision": m["revision"],
                          "files": len(m["files"]), "dry_run": True}, indent=2))
        return 0
    data_dir = Path(args.data_dir).resolve() if args.data_dir else _settings().data_dir
    # Report metadata can be published while generation continues. Serialize
    # report publishers independently; SQLite provides the atomic row update.
    lock = EngineLock(data_dir / "report-publication.lock")
    lock.acquire()
    try:
        db.init_db(data_dir / "app.db")
        print(json.dumps(publish_report(prepared, data_dir), indent=2))
    finally:
        lock.release()
    return 0


def cmd_admin_hash(args: argparse.Namespace) -> int:
    """Print the env line that enables admin sign-in on the hosted site. Reads no settings and no key file;
    the secret is typed twice without echo and is never printed or stored, only its scrypt hash."""
    import getpass

    from vgen.security import ADMIN_MAX_SECRET_CHARS, ADMIN_MIN_SECRET_CHARS, hash_admin_secret

    try:
        secret = getpass.getpass(f"Admin secret (at least {ADMIN_MIN_SECRET_CHARS} characters): ")
        again = getpass.getpass("Repeat the admin secret: ")
    except EOFError:
        # no terminal (run from a script, an editor or an agent shell): getpass has nothing to read from
        print("\nno terminal to read the secret from; run `vgen admin-hash` in an interactive terminal window; "
              "nothing was generated", file=sys.stderr)
        return 1
    if secret != again:
        print("the two entries do not match; nothing was generated", file=sys.stderr)
        return 1
    if not ADMIN_MIN_SECRET_CHARS <= len(secret) <= ADMIN_MAX_SECRET_CHARS:
        print(f"the admin secret must be {ADMIN_MIN_SECRET_CHARS} to {ADMIN_MAX_SECRET_CHARS} characters long; "
              "nothing was generated", file=sys.stderr)
        return 1
    print(f"VGEN_ADMIN_SECRET_HASH={hash_admin_secret(secret)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="vgen", description="Video-generation evaluation platform (Tokenbay)")
    sub = p.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import-experiment", help="verify and import an offline experiment (never generates)")
    imp.add_argument("manifest")
    imp.add_argument("--dry-run", action="store_true", help="validate files without changing the database")
    imp.add_argument("--data-dir")
    imp.set_defaults(fn=cmd_import_experiment)
    pkg = sub.add_parser("package-experiment", help="build a portable incremental experiment bundle")
    pkg.add_argument("manifest")
    pkg.add_argument("--output", required=True)
    pkg.set_defaults(fn=cmd_package_experiment)
    report = sub.add_parser("import-report", help="publish a verified report revision without changing experiment results")
    report.add_argument("manifest")
    report.add_argument("--dry-run", action="store_true")
    report.add_argument("--data-dir")
    report.set_defaults(fn=cmd_import_report)
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
    rl = sub.add_parser("relocate", help="rewrite stored file paths after moving the data dir (server stopped)")
    rl.add_argument("--map", action="append", required=True, metavar="OLD=NEW",
                    help="path prefix to replace; repeat, applied in order")
    rl.add_argument("--db", help="database file (default: the configured data dir's app.db)")
    rl.set_defaults(fn=cmd_relocate)
    ah = sub.add_parser("admin-hash", help="hash an admin secret for the hosted site (prints the VGEN_ADMIN_SECRET_HASH line)")
    ah.set_defaults(fn=cmd_admin_hash)
    args = p.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
