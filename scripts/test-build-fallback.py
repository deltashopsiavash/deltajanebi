#!/usr/bin/env python3
"""Regressions for a BuildKit-free, secret-safe Delta site image."""
from pathlib import Path

builder = Path("scripts/build-site-image.sh").read_text()
updater = Path("update-site.sh").read_text()
for expected in (
    "docker run -d", "docker cp -", "docker exec", "docker commit",
    "docker image tag", "git ls-files -z", "--network none",
    "--no-index --find-links=/opt/delta-wheels",
    "trap cleanup_image_assembly EXIT", "timeout --signal=TERM",
    "--exclude='.env'", "--exclude='media/*'", "--exclude='backups/*'",
):
    assert expected in builder, expected
assert "docker compose build " not in builder
assert "docker buildx build " not in builder
assert "docker compose down -v" not in builder
assert "rm -rf /var/lib/docker" not in builder
assert builder.index('docker commit') < builder.index('docker image tag "$PENDING_IMAGE" deltajanebi-app:latest')
assert "docker compose up -d --no-build --force-recreate web" in updater
assert "docker compose up -d --no-build --force-recreate syncjobs sync reservations caddy" in updater
print("BuildKit-free image assembly and persistent-data isolation contract: OK")
