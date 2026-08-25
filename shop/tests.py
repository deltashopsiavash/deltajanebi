from datetime import timedelta

from bs4 import BeautifulSoup
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image, ImageDraw

from .models import Banner, Category, Product, SiteSetting, SocialLink, SourceSite, User
from .services.source_sanitizer import _clean_studio_image, sanitize_scraped_text
from .services.source_sync import (
    _clean_source_description,
    _clean_source_name,
    _extract_gallery,
    sync_category_path,
)
from .source_registry import source_context


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
        self.product = Product.objects.create(name="کابل B930", category=self.leaf, price=260000, stock=2)

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
        self.assertNotContains(response, "story-cats")
        self.assertEqual(response.content.decode().count("دسته‌بندی محصولات"), 1)

    def test_mobile_call_buttons_and_zero_cart_badge_render(self):
        settings = SiteSetting.load()
        settings.phone = "+989121234567"
        settings.save(update_fields=["phone"])
        response = self.client.get("/")
        self.assertContains(response, 'href="tel:+989121234567"')
        self.assertContains(response, "تماس با ما")
        self.assertContains(response, 'class="mnav-count">0</span>')

    def test_global_footer_uses_dedicated_footer_phone_on_product_page(self):
        settings = SiteSetting.load()
        settings.address = "تهران، خیابان نمونه"
        settings.footer_phone = "+982112345678"
        settings.contact_email = "info@example.com"
        settings.footer_description = "متن معرفی فروشگاه"
        settings.save(update_fields=["address", "footer_phone", "contact_email", "footer_description"])
        response = self.client.get(self.product.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "rich-footer")
        self.assertContains(response, "تهران، خیابان نمونه")
        self.assertContains(response, 'href="tel:+982112345678"')
        self.assertContains(response, "متن معرفی فروشگاه")

    def test_mobile_drawer_has_icons_and_nested_category_panel(self):
        response = self.client.get("/")
        self.assertContains(response, 'data-drawer-target="categories"')
        self.assertContains(response, f'data-drawer-panel="root-{self.leaf.parent.parent_id}"')
        self.assertContains(response, "drawer-row-main")
        self.assertNotContains(response, "♙")

    def test_rubika_and_eitaa_footer_icons_render(self):
        SocialLink.objects.create(platform="rubika", label="روبیکا", url="https://rubika.ir/example")
        SocialLink.objects.create(platform="eitaa", label="ایتا", url="https://eitaa.com/example")
        response = self.client.get("/")
        self.assertContains(response, 'aria-label="روبیکا"')
        self.assertContains(response, 'aria-label="ایتا"')

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
        from .management.commands import telegram_bot, telegram_bot_v3, telegram_bot_v4, telegram_bot_v5, telegram_bot_v6

        markup = telegram_bot.main_menu()
        self.assertIsNotNone(markup)
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("🧰 محصولات عادی", labels)
        self.assertIn("🔗 محصولات خاص", labels)
        self.assertIn("🔎 جستجوی محصول", labels)
        self.assertIn("💾 بکاپ", labels)

        settings_labels = [button.text for row in telegram_bot_v3.settings_menu().inline_keyboard for button in row]
        self.assertIn("☎️ تلفن", settings_labels)
        self.assertIn("📝 توضیحات و فوتر", settings_labels)
        self.assertEqual(telegram_bot_v3.normalize_phone("tel:+989121234567"), "+989121234567")

        v4_labels = [button.text for row in telegram_bot_v4.main_menu().inline_keyboard for button in row]
        self.assertIn("🌐 سایت‌های منبع", v4_labels)
        self.assertIn("📂 دسته‌بندی‌ها", v4_labels)
        v5_labels = [button.text for row in telegram_bot_v5.main_menu().inline_keyboard for button in row]
        self.assertIn("🌐 ثبت سایت", v5_labels)
        self.assertTrue(callable(telegram_bot_v6.source_actions))

    def test_social_choices_include_rubika_and_eitaa(self):
        values = dict(SocialLink.PLATFORM_CHOICES)
        self.assertEqual(values["rubika"], "روبیکا")
        self.assertEqual(values["eitaa"], "ایتا")

    def test_source_site_model_can_hold_multiple_domains(self):
        one = SourceSite.objects.create(name="منبع یک", base_url="https://one.example", hostname="one.example")
        two = SourceSite.objects.create(name="منبع دو", base_url="https://two.example", hostname="two.example")
        self.assertNotEqual(one.hostname, two.hostname)
        self.assertTrue(one.is_active)


class SourceCleanupTests(TestCase):
    def setUp(self):
        self.hamrah, _ = SourceSite.objects.update_or_create(
            hostname="hamrahedovom.ir",
            defaults={
                "name": "همراه دوم",
                "base_url": "https://hamrahedovom.ir",
                "brand_terms": "همراه دوم,HAMRAHEDOVOM",
                "is_active": True,
            },
        )

    def test_source_store_name_is_removed_from_product_copy(self):
        with source_context("https://hamrahedovom.ir/Product/BKP-1/"):
            title = _clean_source_name("کابل شارژ B930 - خرید لوازم جانبی از فروشگاه همراه دوم")
            description = _clean_source_description(
                "کابل مقاوم و مناسب شارژ سریع. خرید این محصول از فروشگاه همراه دوم. دارای کانکتور Type-C."
            )
        self.assertNotIn("همراه دوم", title)
        self.assertNotIn("همراه دوم", description)
        self.assertIn("کابل مقاوم", description)

    def test_cleanup_terms_are_scoped_to_each_source(self):
        SourceSite.objects.create(
            name="سایت دوم",
            base_url="https://second.example",
            hostname="second.example",
            brand_terms="مریوان فون",
        )
        with source_context("https://hamrahedovom.ir/Product/1/"):
            self.assertIn("مریوان فون", _clean_source_name("کابل مریوان فون"))
        with source_context("https://second.example/product/1"):
            self.assertNotIn("مریوان فون", _clean_source_name("کابل مریوان فون"))

    def test_sanitizer_removes_source_phrase_from_all_product_text_fields(self):
        data = {
            "name": "کابل همراه دوم مدل X",
            "description": "خرید کابل همراه دوم با کیفیت عالی",
            "specs": {"فروشنده": "همراه دوم", "رنگ": "مشکی همراه دوم"},
            "categories": ["جانبی همراه دوم", "کابل"],
        }
        cleaned = sanitize_scraped_text(data, "https://hamrahedovom.ir/Product/1/")
        self.assertNotIn("همراه دوم", cleaned["name"])
        self.assertNotIn("همراه دوم", cleaned["description"])
        self.assertNotIn("همراه دوم", " ".join(cleaned["specs"].keys()))
        self.assertNotIn("همراه دوم", " ".join(cleaned["specs"].values()))
        self.assertNotIn("همراه دوم", " ".join(cleaned["categories"]))

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
        with source_context("https://hamrahedovom.ir/Product/BKP-1/"):
            gallery = _extract_gallery(soup, "https://hamrahedovom.ir/Product/BKP-1/", "")
        self.assertEqual(gallery, ["https://hamrahedovom.ir/media/products/b930-main.jpg"])

    def test_studio_image_cleaner_removes_outer_ad_frame_conservatively(self):
        image = Image.new("RGB", (700, 700), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((5, 5, 695, 695), outline=(0, 170, 235), width=9)
        draw.rectangle((80, 25, 280, 70), fill=(20, 80, 140))
        draw.rectangle((355, 135, 585, 590), fill=(215, 220, 225))
        draw.rectangle((105, 280, 300, 585), fill=(25, 25, 28))
        cleaned = _clean_studio_image(image)
        self.assertIsNotNone(cleaned)
        self.assertLess(cleaned.width, 700)
        self.assertEqual(cleaned.width, cleaned.height)
