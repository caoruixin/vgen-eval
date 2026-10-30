#!/usr/bin/env bash
# Ship the code and the built UI to the llm-pro ECS and restart the hosted demo. Never copies .env or data/.
#   deploy/deploy.sh                 # host: $VGEN_DEPLOY_HOST, default the `llm-pro` alias in ~/.ssh/config
# A restart drops the visitor keys held in memory; open pages hand theirs back automatically (see vgen/keys.py).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${VGEN_DEPLOY_HOST:-llm-pro}"
cd "$ROOT"

pnpm -C web build
rsync -az --delete deploy/ "$HOST:/tmp/vgen-deploy/"
ssh "$HOST" 'bash /tmp/vgen-deploy/provision.sh'
rsync -az --delete --exclude '__pycache__' vgen scenarios pyproject.toml uv.lock .python-version README.md "$HOST:/opt/vgen/"
rsync -az --delete web/dist/ "$HOST:/opt/vgen/web/dist/"
ssh "$HOST" 'set -e
  cd /opt/vgen
  UV_PYTHON_INSTALL_DIR=/opt/uv/python UV_CACHE_DIR=/opt/uv/cache /usr/local/bin/uv sync --frozen --no-dev --quiet
  chmod -R a+rX /opt/vgen
  systemctl restart vgen
  for i in $(seq 1 30); do curl -fsS -o /dev/null http://127.0.0.1:8790/api/config && break; sleep 1; done
  echo "vgen: $(systemctl is-active vgen) · public=$(curl -fsS http://127.0.0.1:8790/api/config | grep -o "\"public\": *[a-z]*")"'
