from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from enhancements.models import ProductAmazing
from shop.models import Banner, Product


class HomeAmazingAndBannerTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            name="محصول شگفت انگیز تست",
            price=1_000_000,
            stock=5,
            is_active=True,
        )

    def test_active_amazing_offer_renders_in_animated_home_box(self):
        ProductAmazing.objects.create(
            product=self.product,
            price=790_000,
            is_active=True,
            expires_at=timezone.now() + timedelta(hours=3),
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn('data-amazing-showcase', html)
        self.assertIn("شگفت‌انگیزها", html)
        self.assertIn(self.product.name, html)
        self.assertIn(self.product.get_absolute_url(), html)
        self.assertIn("790,000", html)
        self.assertIn("data-amazing-countdown", html)

    def test_expired_or_unavailable_amazing_offer_is_not_rendered(self):
        ProductAmazing.objects.create(
            product=self.product,
            price=790_000,
            is_active=True,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        response = self.client.get("/")
        self.assertNotIn('data-amazing-showcase', response.content.decode("utf-8"))

        offer = self.product.amazing_offer
        offer.expires_at = None
        offer.save(update_fields=["expires_at", "updated_at"])
        self.product.stock = 0
        self.product.save(update_fields=["stock", "updated_at"])
        response = self.client.get("/")
        self.assertNotIn('data-amazing-showcase', response.content.decode("utf-8"))

    def test_desktop_banner_link_is_clickable_and_mouse_drag_does_not_capture_it(self):
        target = "https://example.com/promo"
        Banner.objects.create(
            title="بنر لینک‌دار",
            image_url="https://example.com/banner.jpg",
            target_url=target,
            is_active=True,
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn(f'href="{target}"', html)
        self.assertIn("data-banner-link", html)
        self.assertIn("e.pointerType==='mouse'", html)
