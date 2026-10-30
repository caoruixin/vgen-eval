#!/usr/bin/env bash
# One-off: publish this machine's runs and clips (data/) as the hosted demo's example runs.
#   deploy/upload-data.sh            # refuses if the server already has data; FORCE=1 replaces it (old copy kept)
# The SQLite snapshot is consistent even while the local app is running. Stored paths are absolute, so they
# are rewritten for the server with `vgen relocate`. Requires deploy/deploy.sh to have run once.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${VGEN_DEPLOY_HOST:-llm-pro}"
SRC="$ROOT/data"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

runs=$(ssh "$HOST" 'sqlite3 /var/lib/vgen/data/app.db "select count(*) from run" 2>/dev/null || echo 0')
if [ "${runs:-0}" != 0 ] && [ "${FORCE:-0}" != 1 ]; then
  echo "the server already has $runs runs in /var/lib/vgen/data; re-run with FORCE=1 to replace them" >&2
  exit 1
fi
sqlite3 "$SRC/app.db" ".backup '$STAGE/app.db'"
ssh "$HOST" 'rm -rf /var/lib/vgen/incoming && mkdir -p /var/lib/vgen/incoming'
rsync -az "$SRC/videos" "$SRC/frames" "$SRC/uploads" "$STAGE/app.db" "$HOST:/var/lib/vgen/incoming/"
ssh "$HOST" "set -e
  systemctl stop vgen
  cd /var/lib/vgen
  rm -rf data.prev && mv data data.prev && mv incoming data
  /opt/vgen/.venv/bin/vgen relocate --db /var/lib/vgen/data/app.db --map '$SRC=/var/lib/vgen/data' --map '$ROOT=/opt/vgen'
  chown -R vgenapp:vgenapp /var/lib/vgen/data
  systemctl start vgen
  echo \"uploaded: \$(find data/videos -name '*.mp4' | wc -l) clips, \$(du -sh data | cut -f1)\""
