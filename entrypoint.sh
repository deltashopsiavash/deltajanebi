#!/usr/bin/env sh
set -e
python manage.py makemigrations shop --noinput
python manage.py migrate --noinput
python manage.py collectstatic --noinput
exec "$@"
