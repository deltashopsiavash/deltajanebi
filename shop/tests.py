from datetime import timedelta

from bs4 import BeautifulSoup
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Banner, Category, Product, SiteSetting, User
from .services.source_sync import (
    _clean_source_description,
    _clean_source_name,
    _extract_gallery,
    sync_category_path,
)


class UnicodeSlugTests(TestCase):
    def test_product_with_persian_slug_reverses_and_renders(self):
        product = Product.objects.create(name="کابل شارژ تایپ سی", price=260000, stock=1, is_active=True)
        url = reverse("product_detail", args=[product.slug])
        self.assertIn("/p/", url)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_home_renders_when_product_slug_is_persian(self):
        Product.objects.create(name="بند گردنی و کابل شارژر موبایل", price=260000, stock=1, is_active=True)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_category_with_persian_slug_reverses_and_renders(self):
        category = Category.objects.create(name="کابل و شارژر", slug="کابل-و-شارژر", is_active=True)
        url = reverse("category", args=[category.slug])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


class CategoryHierarchyTests(TestCase):
    def test_sync_category_path_reuses_existing_tree(self):
        leaf1 = sync_category_path(["جانبی موبایل", "کابل", "کابل شارژ موبایل"])
        leaf2 = sync_category_path(["جانبی موبایل", "کابل", "کابل شارژ موبایل"])
        self.assertEqual(leaf1.pk, leaf2.pk)
        self.assertEqual(Category.objects.count(), 3)
        self.assertEqual(leaf1.parent.name, "کابل")
        self.assertEqual(leaf1.parent.parent.name, "جانبی موبایل")

    def test_parent_category_page_contains_descendant_product(self):
        leaf = sync_category_path(["جانبی موبایل", "کابل", "کابل شارژ موبایل"])
        root = leaf.parent.parent
        Product.objects.create(name="کابل تست", category=leaf, price=100000, stock=2)
        response = self.client.get(reverse("category", args=[root.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "کابل تست")

    def test_product_image_seeds_empty_category_artwork(self):
        leaf = sync_category_path(["جانبی موبایل", "کابل"])
        Product.objects.create(name="کابل تصویردار", category=leaf, image_url="https://example.com/cable.jpg", price=100, stock=1)
        leaf.refresh_from_db()
        root = leaf.parent
        root.refresh_from_db()
        self.assertEqual(leaf.image_url, "https://example.com/cable.jpg")
        self.assertEqual(root.image_url, "https://example.com/cable.jpg")


class ProductManagementTests(TestCase):
    def test_product_gets_human_search_code(self):
        product = Product.objects.create(name="کابل", price=100000, stock=1)
        self.assertEqual(product.public_code, f"DJ-{product.pk:06d}")

    def test_limited_offer_changes_effective_price(self):
        product = Product.objects.create(name="شارژر", price=300000, stock=1)
        product.sale_price = 250000
        product.sale_starts_at = timezone.now()
        product.sale_ends_at = timezone.now() + timedelta(hours=1)
        product.save(update_fields=["sale_price", "sale_starts_at", "sale_ends_at"])
        self.assertTrue(product.is_sale_active)
        self.assertEqual(product.effective_price, 250000)


class StorefrontExperienceTests(TestCase):
    def setUp(self):
        self.leaf = sync_category_path(["جانبی موبایل", "کابل", "کابل شارژ موبایل"])
        Product.objects.create(name="کابل B930", category=self.leaf, price=260000, stock=2)

    def test_home_contains_mega_menu_and_auth_modal(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "mega-menu")
        self.assertContains(response, "ورود / ثبت‌نام")
        self.assertContains(response, "کابل شارژ موبایل")
        self.assertContains(response, "auth-overlay")

    def test_home_banner_carousel_replaces_static_hero_when_banners_exist(self):
        Banner.objects.create(title="بنر اول", image_url="https://example.com/banner-1.jpg", is_active=True)
        Banner.objects.create(title="بنر دوم", image_url="https://example.com/banner-2.jpg", is_active=True)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "home-banner-carousel")
        self.assertContains(response, "data-autoplay-ms=\"4500\"")
        self.assertContains(response, "data-banner-dot=\"1\"")
        self.assertNotContains(response, "خرید سریع، موجودی واقعی و قیمت به‌روز")

    def test_mobile_call_buttons_and_zero_cart_badge_render(self):
        settings = SiteSetting.load()
        settings.phone = "+989121234567"
        settings.save(update_fields=["phone"])
        response = self.client.get("/")
        self.assertContains(response, 'href="tel:+989121234567"')
        self.assertContains(response, "تماس با ما")
        self.assertContains(response, 'class="mnav-count">0</span>')

    def test_login_and_register_pages_render_modern_shell(self):
        login_response = self.client.get(reverse("login"))
        register_response = self.client.get(reverse("register"))
        self.assertEqual(login_response.status_code, 200)
        self.assertEqual(register_response.status_code, 200)
        self.assertContains(login_response, "auth-page-card")
        self.assertContains(register_response, "auth-page-card")

    def test_site_logo_url_fallback_is_renderable(self):
        settings = SiteSetting.load()
        settings.logo_url = "https://example.com/logo.png"
        settings.save(update_fields=["logo_url"])
        self.assertEqual(settings.logo_src, "https://example.com/logo.png")
        response = self.client.get("/")
        self.assertContains(response, "https://example.com/logo.png")

    def test_authenticated_header_has_account_menu_and_logout(self):
        user = User.objects.create_user(email="test@example.com", password="StrongPass123!")
        self.client.force_login(user)
        response = self.client.get("/")
        self.assertContains(response, "مشخصات حساب")
        self.assertContains(response, "سفارش‌ها")
        self.assertContains(response, "خروج از حساب")
        self.assertEqual(self.client.get(reverse("account_profile")).status_code, 200)


class TelegramBotSmokeTests(TestCase):
    def test_management_command_module_imports_and_main_menu_builds(self):
        from .management.commands import telegram_bot, telegram_bot_v3

        markup = telegram_bot.main_menu()
        self.assertIsNotNone(markup)
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("🧰 محصولات عادی", labels)
        self.assertIn("🔗 محصولات خاص", labels)
        self.assertIn("🔎 جستجوی محصول", labels)
        self.assertIn("💾 بکاپ", labels)

        settings_labels = [button.text for row in telegram_bot_v3.settings_menu().inline_keyboard for button in row]
        self.assertIn("☎️ تلفن", settings_labels)
        self.assertEqual(telegram_bot_v3.normalize_phone("tel:+989121234567"), "+989121234567")


class SourceCleanupTests(TestCase):
    def test_source_store_name_is_removed_from_product_copy(self):
        title = _clean_source_name("کابل شارژ B930 - خرید لوازم جانبی از فروشگاه همراه دوم")
        description = _clean_source_description(
            "کابل مقاوم و مناسب شارژ سریع. خرید این محصول از فروشگاه همراه دوم. دارای کانکتور Type-C."
        )
        self.assertNotIn("همراه دوم", title)
        self.assertNotIn("همراه دوم", description)
        self.assertIn("کابل مقاوم", description)

    def test_gallery_ignores_logo_and_keeps_product_image(self):
        soup = BeautifulSoup(
            """
            <div class='product-gallery'>
              <img src='/media/products/b930-main.jpg' width='800' height='800' alt='کابل B930'>
              <img src='/assets/logo-store.png' width='500' height='200' alt='لوگوی همراه دوم'>
            </div>
            """,
            "lxml",
        )
        gallery = _extract_gallery(soup, "https://hamrahedovom.ir/Product/BKP-1/", "")
        self.assertEqual(gallery, ["https://hamrahedovom.ir/media/products/b930-main.jpg"])
