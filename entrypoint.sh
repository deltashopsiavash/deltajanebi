#!/usr/bin/env sh
set -eu

if [ "${RUN_DJANGO_SETUP:-0}" = "1" ]; then
  # Migrations are committed to Git; never generate schema changes at runtime.
  python manage.py migrate --noinput
  python manage.py collectstatic --noinput
fi

exec "$@"
