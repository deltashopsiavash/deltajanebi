#!/usr/bin/env python3
"""One real Django email test; do not disclose provider credentials.

Can run from any directory via native-env-exec.py.
"""
import os
import sys
from pathlib import Path

# Executing a script in the scripts/ folder sets Python's first import path
# to scripts/, so it cannot find the sibling config/ Django package. Import
# the project root explicitly, even if the current directory is /root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

if len(sys.argv) == 2 and sys.argv[1] == "--check-imports":
    import django
    django.setup()
    from django.conf import settings
    if settings.ROOT_URLCONF != "config.urls":
        raise SystemExit("Unexpected Django settings module.")
    print("Native Django project imports: OK")
    raise SystemExit(0)

if len(sys.argv) != 2 or "@" not in sys.argv[1]:
    raise SystemExit("Usage: check-native-email.py recipient@example.com")

import django
django.setup()

from django.conf import settings
from django.core.mail import send_mail

sent = send_mail(
    "DeltaJanebi - email service test",
    "This is a test of verification-email delivery from DeltaJanebi.",
    settings.DEFAULT_FROM_EMAIL,
    [sys.argv[1]],
    fail_silently=False,
)
if sent != 1:
    raise SystemExit("Email provider did not accept the test email.")
print("✅ Resend accepted the test message; check inbox and spam for delivery.")
