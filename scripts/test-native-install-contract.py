#!/usr/bin/env python3
"""Native 1GB installer contract and separation from SanaShop."""
from pathlib import Path

installer=Path("install-native.sh").read_text()
initial=Path("install-site.sh").read_text()
update=Path("update-site.sh").read_text()
updater=Path("scripts/update-native.sh").read_text()
for required in (
    "vm.swappiness=20", "fallocate -l 2G", "swapon /swapfile-deltajanebi",
    "postgresql-client caddy", "python3 -m venv", "createuser", "createdb",
    "--workers 1 --threads 2", "deltajanebi-syncjobs.service",
    "deltajanebi-reservations.service",
    "systemctl disable --now deltajanebi-auto-sync",
    'chown root:"$APP_USER" /etc/deltajanebi',
    'env_line DJANGO_ADMIN_EMAIL "$ADMIN_EMAIL"',
    'email=os.environ["DJANGO_ADMIN_EMAIL"]',
    "run_django collectstatic --noinput", "run_django migrate --noinput",
    "systemctl is-active --quiet", "native-env-exec.py",
):
    assert required in installer, required
for forbidden in ("docker compose", "docker buildx", "docker run", "docker rm",
                  "rm -rf /opt/deltajanebi", "DROP DATABASE"):
    assert forbidden not in installer, forbidden
assert "install-native.sh" in initial and "DELTA_INSTALL_MODE" in initial
assert "scripts/update-native.sh" in update
assert "docker compose" not in updater
assert "pg_dump -Fc deltajanebi" in updater
assert "os.execvpe" in Path("scripts/native-env-exec.py").read_text()
print("Native installer/upgrade isolation and low-memory contracts: OK")
