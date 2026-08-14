#!/usr/bin/env bash
# Laeuft auf der EC2, aufgerufen ueber den generischen Deploy-Wrapper
# (cgo-platform-Repo, /srv/platform/deploy-ssh-wrapper.sh). Anders als die
# Docker-Apps: kein GHCR-Image, sondern ein direktes "git checkout" plus
# systemd-Neustart -- uiflow laeuft nicht containerisiert (siehe
# uiflow-studio.service).
#
# Aufruf: ./deploy.sh [git-revision]   (ohne Argument: "latest" -> origin/main)
set -euo pipefail
cd "$(dirname "$0")/.."

NEW_REVISION="${1:-latest}"
PREVIOUS_REVISION="$(git rev-parse HEAD)"

git fetch origin main

if [ "$NEW_REVISION" = "latest" ]; then
  git reset --hard origin/main
else
  git reset --hard "$NEW_REVISION"
fi

echo "Deploy: $(git rev-parse HEAD) (zuvor lief: $PREVIOUS_REVISION)"

.venv/bin/pip install -q -e .
sudo systemctl restart uiflow-studio.service

echo "Warte auf Healthcheck..."
ok=0
for i in $(seq 1 15); do
  if curl -sf "http://127.0.0.1:8787/api/health" >/dev/null; then
    ok=1
    break
  fi
  sleep 2
done

if [ "$ok" != "1" ]; then
  echo "Healthcheck fehlgeschlagen." >&2
  echo "Rollback auf $PREVIOUS_REVISION..." >&2
  git reset --hard "$PREVIOUS_REVISION"
  .venv/bin/pip install -q -e .
  sudo systemctl restart uiflow-studio.service
  exit 1
fi

echo "OK — $(git rev-parse HEAD) läuft."
