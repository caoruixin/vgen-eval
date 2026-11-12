#!/usr/bin/env bash
# Incrementally publish a verified report revision; never replaces the database.
# Deploy matching code first. Usage: deploy/publish-report.sh path/to/revision.json
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${VGEN_DEPLOY_HOST:-llm-pro}"
MANIFEST="${1:?usage: publish-report.sh path/to/revision.json}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cd "$ROOT"
.venv/bin/python - "$MANIFEST" "$STAGE" <<'PY'
import shutil, sys
from pathlib import Path
from vgen.report_revisions import prepare_report
source, target = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
p = prepare_report(source)
for row in p["manifest"]["files"]:
    dest = target / row["path"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(p["root"] / row["path"], dest)
shutil.copyfile(source, target / "revision.json")
PY
REMOTE="$(ssh "$HOST" 'mktemp -d /var/lib/vgen/report-incoming.XXXXXX')"
case "$REMOTE" in /var/lib/vgen/report-incoming.*) ;; *) echo 'unexpected remote staging path' >&2; exit 1;; esac
rsync -az "$STAGE/" "$HOST:$REMOTE/"
ssh "$HOST" bash -s -- "$REMOTE" <<'REMOTE_SCRIPT'
set -euo pipefail
STAGE="$1"
cd /opt/vgen
chown -R vgenapp:vgenapp "$STAGE"
runuser -u vgenapp -- .venv/bin/vgen import-report "$STAGE/revision.json" --dry-run
runuser -u vgenapp -- .venv/bin/vgen import-report "$STAGE/revision.json" --data-dir /var/lib/vgen/data
rm -rf "$STAGE"
REMOTE_SCRIPT
