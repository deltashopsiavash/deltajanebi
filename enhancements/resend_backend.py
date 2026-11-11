"""Django email backend using Resend HTTPS API (no SMTP ports on Iranian VPS)."""
import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend


class ResendDeliveryError(RuntimeError):
    """Safe provider error: never includes message bodies or API credentials."""


class ResendEmailBackend(BaseEmailBackend):
    url = "https://api.resend.com/emails"

    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)

    def _send(self, payload, key):
        try:
            response = requests.post(
                self.url,
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                json=payload,
                timeout=(6, 16),
            )
        except requests.RequestException:
            raise ResendDeliveryError("Resend HTTPS connection failed: check outbound TCP/443.") from None
        if response.status_code not in (200, 201):
            raise ResendDeliveryError(
                "Resend rejected email (HTTP %s); check API key, verified sender, and provider dashboard."
                % response.status_code
            )
        try:
            accepted = response.json().get("id")
        except (ValueError, TypeError, AttributeError):
            accepted = None
        if not accepted:
            raise ResendDeliveryError("Resend response had no accepted email id.")

    @staticmethod
    def _message_parts(message):
        if message.attachments:
            raise ResendDeliveryError("Attachments are unsupported for native Resend email.")
        data = {
            "from": message.from_email or settings.DEFAULT_FROM_EMAIL,
            "subject": message.subject,
            "text": message.body or "",
        }
        for alt in getattr(message, "alternatives", []):
            # Django 5.1 stores alternatives as (body, mime) tuples; newer
            # versions may expose namedtuple content/mimetype attributes.
            content, mime = alt
            if mime == "text/html":
                data["html"] = content
                break
        if message.reply_to:
            data["reply_to"] = list(message.reply_to)
        return data

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        key = (getattr(settings, "RESEND_API_KEY", "") or "").strip()
        if not key:
            if self.fail_silently:
                return 0
            raise ResendDeliveryError("Resend API key is missing; configure native email.")
        sent = 0
        for message in email_messages:
            try:
                data = self._message_parts(message)
                to, cc, bcc = list(message.to or []), list(message.cc or []), list(message.bcc or [])
                if not (to or cc or bcc):
                    continue
                if bcc and not to and not cc:
                    # Admin broadcasts are BCC-only. Send privately one by one;
                    # Resend requires a nonempty TO field.
                    for recipient in bcc:
                        self._send({**data, "to": [recipient]}, key)
                else:
                    if not to:
                        to, cc = cc, []
                    data["to"] = to
                    if cc:
                        data["cc"] = cc
                    if bcc:
                        data["bcc"] = bcc
                    self._send(data, key)
                sent += 1
            except Exception:
                if not self.fail_silently:
                    raise
        return sent
