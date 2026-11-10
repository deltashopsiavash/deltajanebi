#!/usr/bin/env bash
set -Eeuo pipefail

# Build a complete Python wheelhouse outside BuildKit using host networking.
# Docker's regular bridge network may lack outbound HTTPS even when the host works.
cd "$(dirname "${BASH_SOURCE[0]}")/.."
WHEELS_DIR="$PWD/.build-wheels"
mkdir -p "$WHEELS_DIR"
WHEEL_KEY="$(sha256sum requirements.txt | cut -d' ' -f1)"
PIP_INDEXES="${DELTA_PIP_INDEXES:-https://mirrors.aliyun.com/pypi/simple/ https://pypi.tuna.tsinghua.edu.cn/simple/ https://pypi.org/simple/}"

if [[ -f "$WHEELS_DIR/.requirements.sha256" ]] \
   && [[ "$(cat "$WHEELS_DIR/.requirements.sha256")" == "$WHEEL_KEY" ]] \
   && compgen -G "$WHEELS_DIR/*.whl" >/dev/null; then
  echo "==> Reusing verified Python wheel cache"
else
  echo "==> Downloading Python packages using the SERVER network (not Docker build network)"
  rm -f "$WHEELS_DIR"/*.whl "$WHEELS_DIR/.requirements.sha256"
  proxy_flags=()
  for proxy in HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy; do
    if [[ -n "${!proxy:-}" ]]; then proxy_flags+=(-e "$proxy"); fi
  done
  if ! docker run --rm --network host \
      -v "$PWD:/src" -w /src \
      "${proxy_flags[@]}" -e "PIP_INDEXES=$PIP_INDEXES" \
      python:3.12-slim sh -ec '
        for index in $PIP_INDEXES; do
          echo "==> Trying Python wheels via $index (host network)"
          if python -m pip wheel --no-cache-dir --timeout 20 --retries 1 \
              --index-url "$index" --wheel-dir /src/.build-wheels \
              -r requirements.txt; then
            echo "==> All dependency wheels downloaded successfully"
            exit 0
          fi
          echo "WARNING: Cannot download all wheels from $index" >&2
        done
        echo "ERROR: None of the Python package repositories are reachable from the server's host network." >&2
        exit 1
      '; then
    echo "ERROR: The server cannot download Python wheels even with host networking." >&2
    echo "Check outbound TCP 443, server proxy, firewall and DNS. Do not change Django's version." >&2
    exit 1
  fi
  printf '%s\n' "$WHEEL_KEY" >"$WHEELS_DIR/.requirements.sha256"
fi

echo "==> Docker image build: install cached Python wheels OFFLINE"
docker compose config -q
docker compose build --pull --no-cache web
