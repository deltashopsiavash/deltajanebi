#!/usr/bin/env sh
set -eu

if [ "${RUN_DJANGO_SETUP:-0}" = "1" ]; then
  # Only the web service performs schema/static setup. Workers wait for web health.
  python manage.py makemigrations shop --noinput
  python manage.py migrate --noinput
  python manage.py collectstatic --noinput
fi

exec "$@"
