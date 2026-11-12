#!/usr/bin/env bash
# Incrementally publish one verified experiment. Never replaces app.db or existing visitor data.
# Deploy the matching application code with deploy/deploy.sh first.
# Usage: deploy/upload-experiment.sh experiments/h3-pro6000.json
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${VGEN_DEPLOY_HOST:-llm-pro}"
MANIFEST="${1:?usage: upload-experiment.sh MANIFEST}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cd "$ROOT"
.venv/bin/python -m vgen.cli package-experiment "$MANIFEST" --output "$STAGE/bundle"
REMOTE="$(ssh "$HOST" 'mktemp -d /var/lib/vgen/experiment-incoming.XXXXXX')"
case "$REMOTE" in /var/lib/vgen/experiment-incoming.*) ;; *) echo 'unexpected remote staging path' >&2; exit 1;; esac
rsync -az "$STAGE/bundle/" "$HOST:$REMOTE/"
ssh "$HOST" bash -s -- "$REMOTE" <<'REMOTE_SCRIPT'
set -euo pipefail
STAGE="$1"
cd /opt/vgen
.venv/bin/vgen import-experiment "$STAGE/experiment.json" --dry-run
trap 'systemctl start vgen' EXIT
systemctl stop vgen
if [ -f /var/lib/vgen/data/app.db ]; then
  sqlite3 /var/lib/vgen/data/app.db ".backup '/var/lib/vgen/data/app.db.pre-import-$(date +%s).bak'"
fi
.venv/bin/vgen import-experiment "$STAGE/experiment.json" --data-dir /var/lib/vgen/data
chown -R vgenapp:vgenapp /var/lib/vgen/data/experiments
find /var/lib/vgen/data -maxdepth 1 -name 'app.db*' -exec chown vgenapp:vgenapp {} +
systemctl start vgen
trap - EXIT
rm -rf "$STAGE"
for i in $(seq 1 30); do
  if curl -fsS -o /dev/null http://127.0.0.1:8790/api/benchmarks; then exit 0; fi
  sleep 1
done
exit 1
REMOTE_SCRIPT
