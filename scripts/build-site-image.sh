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
        echo "ERROR: No Python package repository is reachable through the server host network." >&2
        exit 1
      '; then
    echo "ERROR: The server cannot download Python wheels even with host networking." >&2
    echo "Check outbound TCP 443, server proxy, firewall and DNS. Do not change Django's version." >&2
    exit 1
  fi
  printf '%s\n' "$WHEEL_KEY" >"$WHEELS_DIR/.requirements.sha256"
fi

echo "==> Assembling DeltaJanebi image WITHOUT Docker BuildKit or Buildx"
docker compose config -q

# Direct Buildx and Compose Bake may hang for hours with "forwarding Ping:
# no such job" on this VPS. Assemble identical image with normal Docker run,
# cp, exec and commit; these actions never involve BuildKit at any stage.
# DO NOT send secret .env files, untracked media, or live databases to an image.
# The existing app image remains unchanged until the new candidate is verified.
TMP_CONTAINER="deltajanebi-image-assembly-$$"
PENDING_IMAGE="deltajanebi-app:pending-$$"
CONTAINER_CREATED=0
cleanup_image_assembly() {
  if [[ "$CONTAINER_CREATED" == "1" ]]; then
    docker rm -f "$TMP_CONTAINER" >/dev/null 2>&1 || true
  fi
  docker image rm "$PENDING_IMAGE" >/dev/null 2>&1 || true
}
trap cleanup_image_assembly EXIT

echo "==> Starting isolated Python container; Docker run only"
timeout --signal=TERM --kill-after=15s 120s \
  docker run -d --name "$TMP_CONTAINER" --network none \
    --workdir /app \
    --volume "$WHEELS_DIR:/opt/delta-wheels:ro" \
    --entrypoint /bin/sh \
    python:3.12-slim -c 'while :; do sleep 600; done'
CONTAINER_CREATED=1

echo "==> Copying Git-tracked source only; .env, backups, media excluded"
# Only tracked Git files are copied, with sensitive paths explicitly excluded.
git ls-files -z --cached | tar --null \
  --exclude='.env' --exclude='.env.*' \
  --exclude='*.sqlite3' --exclude='*.deltabackup' \
  --exclude='.build-wheels/*' --exclude='media/*' \
  --exclude='backups/*' --exclude='staticfiles/*' \
  -T - -cf - | timeout --signal=TERM --kill-after=15s 120s \
  docker cp - "$TMP_CONTAINER:/app"

echo "==> Installing Python dependencies from wheel cache, fully offline"
timeout --signal=TERM --kill-after=15s 900s \
  docker exec "$TMP_CONTAINER" /bin/sh -ec '
    python -m pip install --no-cache-dir --no-index --find-links=/opt/delta-wheels -r /app/requirements.txt
    chmod +x /app/entrypoint.sh
    python -c "import django, gunicorn; print(\"Offline Python environment OK\")"
  '

echo "==> Committing assembled image without Dockerfile build"
timeout --signal=TERM --kill-after=15s 240s \
  docker commit \
    --change 'WORKDIR /app' \
    --change 'ENV PYTHONDONTWRITEBYTECODE=1' \
    --change 'ENV PYTHONUNBUFFERED=1' \
    --change 'ENV PIP_DISABLE_PIP_VERSION_CHECK=1' \
    --change 'ENV PIP_ROOT_USER_ACTION=ignore' \
    --change 'ENTRYPOINT ["/app/entrypoint.sh"]' \
    --change 'CMD ["gunicorn","config.wsgi:application","--bind","0.0.0.0:8000","--workers","3","--timeout","60"]' \
    "$TMP_CONTAINER" "$PENDING_IMAGE"

echo "==> Checking candidate image before replacing the previous image tag"
timeout --signal=TERM --kill-after=15s 90s \
  docker run --rm --network none --entrypoint python "$PENDING_IMAGE" \
    -c 'import django, gunicorn; from pathlib import Path; assert Path("/app/manage.py").is_file(); print("Built image verified")'
docker image tag "$PENDING_IMAGE" deltajanebi-app:latest
docker image inspect deltajanebi-app:latest >/dev/null

echo "✅ New DeltaJanebi image assembled WITHOUT Docker BuildKit."
echo "✅ PostgreSQL, live containers, media, backups, and .env were not changed."
