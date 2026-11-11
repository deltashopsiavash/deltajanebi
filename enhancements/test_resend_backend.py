"""Resend HTTPS tests: OTP, reset, privacy and fail-closed credentials."""
from unittest.mock import Mock, patch
from django.core.mail import EmailMultiAlternatives, send_mail
from django.test import SimpleTestCase, override_settings
from enhancements.resend_backend import ResendDeliveryError


@override_settings(
    EMAIL_BACKEND="enhancements.resend_backend.ResendEmailBackend",
    RESEND_API_KEY="re_test_fake_key",
    DEFAULT_FROM_EMAIL="support@mail.example.com",
)
class ResendEmailTests(SimpleTestCase):
    @staticmethod
    def accepted(status=200):
        response = Mock(status_code=status)
        response.json.return_value = {"id": "email-test-id"}
        return response

    @patch("enhancements.resend_backend.requests.post")
    def test_otp_with_html(self, post):
        post.return_value = self.accepted()
        msg = EmailMultiAlternatives("OTP", "Code 123456", None, ["person@gmail.com"])
        msg.attach_alternative("<b>123456</b>", "text/html")
        self.assertEqual(msg.send(), 1)
        self.assertEqual(post.call_args.args[0], "https://api.resend.com/emails")
        self.assertEqual(post.call_args.kwargs["json"]["to"], ["person@gmail.com"])
        self.assertEqual(post.call_args.kwargs["json"]["html"], "<b>123456</b>")

    @patch("enhancements.resend_backend.requests.post")
    def test_reset_email_plain_text(self, post):
        post.return_value = self.accepted()
        self.assertEqual(send_mail("Reset", "Reset link", None, ["person@gmail.com"]), 1)

    @patch("enhancements.resend_backend.requests.post")
    def test_broadcast_bcc_recipient_privacy(self, post):
        post.return_value = self.accepted()
        msg = EmailMultiAlternatives("News", "Text", None, [], bcc=["one@example.com", "two@example.com"])
        self.assertEqual(msg.send(), 1)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0].kwargs["json"]["to"], ["one@example.com"])
        self.assertEqual(post.call_args_list[1].kwargs["json"]["to"], ["two@example.com"])
        self.assertNotIn("bcc", post.call_args_list[0].kwargs["json"])

    @override_settings(RESEND_API_KEY="")
    @patch("enhancements.resend_backend.requests.post")
    def test_blank_key_never_fakes_delivery(self, post):
        with self.assertRaisesRegex(ResendDeliveryError, "key is missing"):
            send_mail("OTP", "123456", None, ["person@gmail.com"])
        post.assert_not_called()

    @patch("enhancements.resend_backend.requests.post")
    def test_unverified_domain_403_has_safe_specific_diagnostic(self, post):
        response = self.accepted(403)
        response.json.return_value = {
            "name": "validation_error",
            "message": "The deltajanebishop.ir domain is not verified.",
        }
        post.return_value = response
        with self.assertRaises(ResendDeliveryError) as exc:
            send_mail("OTP", "123456", None, ["person@gmail.com"])
        self.assertIn("HTTP 403", str(exc.exception))
        self.assertIn("Sender domain is not verified", str(exc.exception))
        self.assertNotIn("re_test_fake_key", str(exc.exception))

    @patch("enhancements.resend_backend.requests.post")
    def test_provider_401_does_not_log_secret(self, post):
        post.return_value = self.accepted(401)
        with self.assertRaises(ResendDeliveryError) as captured:
            send_mail("OTP", "123456", None, ["person@gmail.com"])
        self.assertIn("HTTP 401", str(captured.exception))
        self.assertNotIn("re_test_fake_key", str(captured.exception))
