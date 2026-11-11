#!/usr/bin/env python3
"""Deploy builds must avoid Compose Bake, preserve persistent volumes and reuse wheels."""
from pathlib import Path

builder = Path("scripts/build-site-image.sh").read_text()
updater = Path("update-site.sh").read_text()
dockerignore = Path(".dockerignore").read_text()

assert "docker buildx build" in builder
assert "--builder default" in builder
assert "--load" in builder
assert '--build-context "wheelhouse=$WHEELS_DIR"' in builder
assert "COMPOSE_BAKE=false BUILDX_BUILDER=default docker compose build" in builder
assert "docker image inspect deltajanebi-app:latest" in builder
assert "docker compose down -v" not in builder
assert "docker builder prune" not in builder
assert "rm -rf /var/lib/docker" not in builder
assert "docker compose up -d --no-build --force-recreate web" in updater
assert "docker compose up -d --no-build --force-recreate syncjobs sync reservations caddy" in updater
assert "backups/" in dockerignore and "*.deltabackup" in dockerignore
print("Safe direct Buildx/no-Bake fallback, wheel cache and volume preservation: OK")
