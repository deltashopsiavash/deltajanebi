#!/usr/bin/env python3
"""One real Django email delivery test; never print the provider API key."""
import os
import sys

if len(sys.argv) != 2 or "@" not in sys.argv[1]:
    raise SystemExit("Usage: check-native-email.py recipient@example.com")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
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
    raise SystemExit("Provider did not accept the test email.")
print("✅ Resend accepted the test message; check inbox and spam for delivery.")
