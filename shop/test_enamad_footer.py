"""Regression: eNamad trustseal must retain referrer for official logo display."""
from django.test import TestCase
from shop.models import TrustBadge

class TrustsealFooterTests(TestCase):
    def test_enamad_referrerpolicy_and_no_noopener(self):
        TrustBadge.objects.create(
            badge_type="enamad",
            target_url="https://trustseal.enamad.ir/?id=7609266&Code=EXAMPLE",
            image_url="https://trustseal.enamad.ir/logo.aspx?id=7609266&Code=EXAMPLE",
            is_active=True,
        )
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        enamad_anchor = html.split('class="trust-card" href="https://trustseal.enamad.ir/',1)[1].split("</a>",1)[0]
        self.assertIn('referrerpolicy="origin"', enamad_anchor)
        self.assertNotIn('rel="noopener"', enamad_anchor)
        self.assertIn("trustseal.enamad.ir/logo.aspx?", enamad_anchor)
        self.assertIn("7609266&amp;Code=EXAMPLE", enamad_anchor)

    def test_zarinpal_keeps_security_rel(self):
        TrustBadge.objects.create(
            badge_type="zarinpal",
            target_url="https://zarinpal.com/",
            image_url="https://zarinpal.com/logo.png",
            is_active=True,
        )
        response = self.client.get("/")
        self.assertContains(response, 'href="https://zarinpal.com/" target="_blank" rel="noopener"')
