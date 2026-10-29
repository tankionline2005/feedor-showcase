#!/usr/bin/env bash
# Ships the current commit to the server and rebuilds the containers.
#   scripts/deploy.sh
# Server layout: $DIR/.env (secrets, created once by hand) and $DIR/src (replaced on every deploy).
set -euo pipefail

HOST="${DEPLOY_HOST:-root@SERVER_IP}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/kindergarten_vps}"
DIR="${DEPLOY_DIR:-/opt/kindergarten-monitoring}"
SSH=(ssh -i "$KEY" -o BatchMode=yes "$HOST")

cd "$(dirname "$0")/.."
if [ -n "$(git status --porcelain)" ]; then
  echo "There are uncommitted changes — commit them first, only HEAD is deployed." >&2
  exit 1
fi

"${SSH[@]}" "test -f $DIR/.env" || { echo "$DIR/.env is missing on the server" >&2; exit 1; }

echo "Uploading $(git rev-parse --short HEAD)..."
git archive --format=tar HEAD | "${SSH[@]}" "
  set -e
  rm -rf $DIR/src.new && mkdir -p $DIR/src.new
  tar -x -C $DIR/src.new
  ln -s ../.env $DIR/src.new/.env
  rm -rf $DIR/src && mv $DIR/src.new $DIR/src
"

echo "Building and restarting..."
"${SSH[@]}" "
  set -e
  cd $DIR/src
  docker compose -p kindergarten up -d --build --remove-orphans
  docker image prune -f >/dev/null
  docker compose -p kindergarten ps --format 'table {{.Service}}\t{{.Status}}'
"
