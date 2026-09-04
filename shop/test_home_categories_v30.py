from unittest.mock import patch

from django.test import TestCase

from shop.home_category_models import HomeCategoryShowcase, HomeCategoryTile
from shop.models import Category, SourceSite
from shop.services.home_category_clone_v30 import clone_homepage_categories, reset_homepage_categories


class FakeResponse:
    def __init__(self, text, url):
        self.text = text
        self.url = url


class HomeCategoryCloneTests(TestCase):
    def setUp(self):
        self.site = SourceSite.objects.create(
            name="مریوان فون",
            base_url="https://marivanphone.com",
            hostname="marivanphone.com",
            is_active=True,
            bulk_import_enabled=True,
        )
        self.mobile = Category.objects.create(name="گوشی موبایل", slug="mobile")
        self.accessories = Category.objects.create(name="لوازم جانبی", slug="accessories")
        self.menu_only = Category.objects.create(name="دسته منوی مستقل", slug="menu-only")

    def _html(self):
        return """
        <html><body>
          <header>
            <a href="https://marivanphone.com/product-category/header-only/">
              <img src="https://marivanphone.com/header.jpg" alt="دسته هدر">
            </a>
          </header>
          <main>
            <section class="elementor-widget-wd_product_categories promo-before">
              <h2>پیشنهادهای محبوب</h2>
              <div class="wd-categories">
                <div class="category-grid-item"><a href="/product-category/promo-one/"><img src="/promo1.jpg" alt="پرومو یک"><h3 class="wd-entities-title">پرومو یک</h3></a></div>
                <div class="category-grid-item"><a href="/product-category/promo-two/"><img src="/promo2.jpg" alt="پرومو دو"><h3 class="wd-entities-title">پرومو دو</h3></a></div>
              </div>
            </section>

            <section class="elementor-widget-wd_product_categories wanted-section">
              <h2>دسته بندی محصولات مریوان فون</h2>
              <p>این متن تبلیغاتی نباید به Delta منتقل شود</p>
              <div class="wd-categories">
                <div class="category-grid-item">
                  <a href="/product-category/mobile/">
                    <img data-src="/media/mobile.webp" alt="گوشی موبایل">
                    <h3 class="wd-entities-title">گوشی موبایل <mark class="count">(25)</mark></h3>
                  </a>
                </div>
                <div class="category-grid-item">
                  <a href="/product-category/accessories/">
                    <img src="/media/accessories.webp" alt="لوازم جانبی">
                    <h3 class="wd-entities-title">لوازم جانبی</h3>
                  </a>
                </div>
              </div>
            </section>

            <section class="elementor-widget-wd_product_categories promo-after">
              <h2>برندهای منتخب</h2>
              <div class="wd-categories">
                <div class="category-grid-item"><a href="/product-category/brand-one/"><img src="/brand1.jpg" alt="برند یک"><h3 class="wd-entities-title">برند یک</h3></a></div>
                <div class="category-grid-item"><a href="/product-category/brand-two/"><img src="/brand2.jpg" alt="برند دو"><h3 class="wd-entities-title">برند دو</h3></a></div>
              </div>
            </section>
          </main>
          <footer><a href="/product-category/footer-only/">دسته فوتر</a></footer>
        </body></html>
        """

    @patch("shop.services.home_category_clone_v30._safe_get")
    def test_clone_replaces_only_named_product_category_section_and_leaves_menu_tree_alone(self, safe_get):
        safe_get.return_value = FakeResponse(self._html(), "https://marivanphone.com/")
        before = list(Category.objects.order_by("id").values_list("id", "name", "parent_id"))

        result = clone_homepage_categories(self.site.id)

        self.assertEqual(result["count"], 2)
        self.assertEqual(result["matched_categories"], 2)
        self.assertTrue(result["menu_untouched"])
        self.assertTrue(result["section_only"])
        self.assertEqual(
            list(Category.objects.order_by("id").values_list("id", "name", "parent_id")),
            before,
        )
        self.assertEqual(
            list(HomeCategoryTile.objects.order_by("order").values_list("name", flat=True)),
            ["گوشی موبایل", "لوازم جانبی"],
        )
        imported = list(HomeCategoryTile.objects.values_list("name", flat=True))
        for unwanted in ["دسته هدر", "پرومو یک", "پرومو دو", "برند یک", "برند دو"]:
            self.assertNotIn(unwanted, imported)

        showcase = HomeCategoryShowcase.load()
        self.assertTrue(showcase.enabled)
        self.assertEqual(showcase.source_site_id, self.site.id)
        self.assertEqual(showcase.title, "دسته‌بندی محصولات")
        self.assertEqual(showcase.subtitle, "")

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn('data-home-category-source="marivanphone.com"', html)
        self.assertIn("گوشی موبایل", html)
        self.assertIn("لوازم جانبی", html)
        self.assertNotIn("این متن تبلیغاتی نباید به Delta منتقل شود", html)
        self.assertNotIn("پرومو یک", html)
        self.assertIn("/category/mobile/", html)
        self.assertIn("https://marivanphone.com/media/mobile.webp", html)

    @patch("shop.services.home_category_clone_v30._safe_get")
    def test_page_without_exact_product_categories_section_is_rejected(self, safe_get):
        safe_get.return_value = FakeResponse(
            """
            <html><main>
              <section class='elementor-widget-wd_product_categories'>
                <h2>پیشنهادهای محبوب</h2>
                <div class='category-grid-item'><a href='/product-category/a/'><img src='/a.jpg' alt='الف'><h3>الف</h3></a></div>
                <div class='category-grid-item'><a href='/product-category/b/'><img src='/b.jpg' alt='ب'><h3>ب</h3></a></div>
              </section>
            </main></html>
            """,
            "https://marivanphone.com/",
        )
        with self.assertRaisesRegex(ValueError, "دسته‌بندی محصولات"):
            clone_homepage_categories(self.site.id)
        self.assertEqual(HomeCategoryTile.objects.count(), 0)

    @patch("shop.services.home_category_clone_v30._safe_get")
    def test_reset_returns_homepage_to_default_without_deleting_categories(self, safe_get):
        safe_get.return_value = FakeResponse(self._html(), "https://marivanphone.com/")
        clone_homepage_categories(self.site.id)
        reset_homepage_categories()

        showcase = HomeCategoryShowcase.load()
        self.assertFalse(showcase.enabled)
        self.assertEqual(Category.objects.count(), 3)
        response = self.client.get("/")
        html = response.content.decode("utf-8")
        self.assertNotIn('data-home-category-source="marivanphone.com"', html)
        self.assertIn("دسته منوی مستقل", html)

    @patch("shop.services.home_category_clone_v30._safe_get")
    def test_failed_clone_keeps_previous_showcase_intact(self, safe_get):
        safe_get.return_value = FakeResponse(self._html(), "https://marivanphone.com/")
        clone_homepage_categories(self.site.id)
        previous = list(HomeCategoryTile.objects.values_list("name", flat=True))

        safe_get.return_value = FakeResponse("<html><main><h1>بدون دسته بندی</h1></main></html>", "https://marivanphone.com/")
        with self.assertRaises(ValueError):
            clone_homepage_categories(self.site.id)

        self.assertEqual(list(HomeCategoryTile.objects.values_list("name", flat=True)), previous)
        self.assertTrue(HomeCategoryShowcase.load().enabled)
