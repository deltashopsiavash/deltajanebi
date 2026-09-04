from bs4 import BeautifulSoup
from PIL import Image, ImageDraw
from django.test import TestCase

from shop.models import Category, Product, SourceSite
from shop.source_offer_models import ProductSourceOffer
from shop.services import category_v22
from shop.services import source_catalog_v22 as engine
from shop.services import source_identity_v22 as identity
from shop.services import source_sanitizer_v22 as sanitizer


class SourceV22TestCase(TestCase):
    def setUp(self):
        self.first, _ = SourceSite.objects.update_or_create(
            hostname="hamrahedovom.ir",
            defaults={
                "name": "همراه دوم",
                "base_url": "https://hamrahedovom.ir",
                "is_active": True,
                "default_markup_type": SourceSite.MARKUP_PERCENT,
                "default_markup_value": 0,
            },
        )
        self.second, _ = SourceSite.objects.update_or_create(
            hostname="example-source.ir",
            defaults={
                "name": "منبع دوم",
                "base_url": "https://example-source.ir",
                "is_active": True,
                "default_markup_type": SourceSite.MARKUP_PERCENT,
                "default_markup_value": 0,
            },
        )

    @staticmethod
    def flash(url, capacity, stock, price=500000, model="UV150"):
        return {
            "name": f"فلش مموری ADATA مدل {model} ظرفیت {capacity} گیگابایت",
            "description": "فلش مموری USB",
            "price": price,
            "stock": stock,
            "image_url": "",
            "gallery": [],
            "specs": {"مدل": model, "ظرفیت حافظه": f"{capacity} GB"},
            "sku": "",
            "categories": ["فلش مموری", f"فلش {capacity} گیگ"],
            "source_url": url,
        }

    def test_same_model_and_capacity_merges_across_sources_and_sums_stock(self):
        first, created = engine._upsert_with_data(
            self.first,
            "https://hamrahedovom.ir/product/uv150-128-a",
            self.flash("https://hamrahedovom.ir/product/uv150-128-a", 128, 3),
        )
        self.assertTrue(created)
        second, created = engine._upsert_with_data(
            self.second,
            "https://example-source.ir/product/uv150-128-b",
            self.flash("https://example-source.ir/product/uv150-128-b", 128, 5, 480000),
        )
        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)
        first.refresh_from_db()
        self.assertEqual(first.stock, 8)
        self.assertEqual(ProductSourceOffer.objects.filter(product=first).count(), 2)

    def test_same_model_different_capacity_never_merges(self):
        p128, _ = engine._upsert_with_data(
            self.first,
            "https://hamrahedovom.ir/product/uv150-128",
            self.flash("https://hamrahedovom.ir/product/uv150-128", 128, 5),
        )
        p16, created = engine._upsert_with_data(
            self.second,
            "https://example-source.ir/product/uv150-16",
            self.flash("https://example-source.ir/product/uv150-16", 16, 7),
        )
        self.assertTrue(created)
        self.assertNotEqual(p128.pk, p16.pk)
        self.assertEqual(identity.identity_key(self.flash("x", 128, 1)), "model:uv150|storage:128gb")
        self.assertEqual(identity.identity_key(self.flash("x", 16, 1)), "model:uv150|storage:16gb")
        self.assertEqual(p128.category.name, "فلش 128 گیگ")
        self.assertEqual(p16.category.name, "فلش 16 گیگ")

    def test_two_urls_from_same_source_contribute_to_one_product(self):
        first, _ = engine._upsert_with_data(
            self.first,
            "https://hamrahedovom.ir/product/duplicate-a",
            self.flash("https://hamrahedovom.ir/product/duplicate-a", 128, 2),
        )
        second, created = engine._upsert_with_data(
            self.first,
            "https://hamrahedovom.ir/product/duplicate-b",
            self.flash("https://hamrahedovom.ir/product/duplicate-b", 128, 4),
        )
        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)
        first.refresh_from_db()
        self.assertEqual(first.stock, 6)
        self.assertEqual(ProductSourceOffer.objects.filter(product=first).count(), 2)

    def test_historical_16_and_128_overmerge_is_split_again(self):
        product = Product.objects.create(
            name="فلش UV150",
            source_type=Product.SYNCED,
            source_url="https://hamrahedovom.ir/product/legacy",
            price=500000,
            source_price=500000,
            stock=7,
        )
        for capacity, stock in ((16, 2), (128, 5)):
            data = self.flash(f"https://hamrahedovom.ir/product/legacy-{capacity}", capacity, stock)
            ProductSourceOffer.objects.create(
                product=product,
                source_site=self.first,
                source_url=data["source_url"],
                model_key="model:uv150",
                source_price=data["price"],
                sale_price=data["price"],
                stock=stock,
                category_path=data["categories"],
                payload={
                    "name": data["name"],
                    "description": data["description"],
                    "image_url": "",
                    "gallery": [],
                    "specs": data["specs"],
                },
            )
        stats = identity.consolidate_duplicate_products()
        products = list(Product.objects.filter(source_type=Product.SYNCED, is_active=True).order_by("stock"))
        self.assertEqual(stats["products_split"], 1)
        self.assertEqual(len(products), 2)
        self.assertEqual([p.stock for p in products], [2, 5])
        self.assertEqual({p.category.name for p in products}, {"فلش 16 گیگ", "فلش 128 گیگ"})

    def test_duplicate_product_rows_are_really_deleted_after_offer_merge(self):
        model_key = "model:uv150|storage:128gb"
        p1 = Product.objects.create(name="فلش UV150 128", source_type=Product.SYNCED, price=1, stock=2)
        p2 = Product.objects.create(name="ADATA UV150 128GB", source_type=Product.SYNCED, price=1, stock=4)
        for product, site, suffix, stock in ((p1, self.first, "a", 2), (p2, self.second, "b", 4)):
            data = self.flash(f"https://{site.hostname}/product/{suffix}", 128, stock)
            ProductSourceOffer.objects.create(
                product=product,
                source_site=site,
                source_url=data["source_url"],
                model_key=model_key,
                source_price=100,
                sale_price=100,
                stock=stock,
                category_path=data["categories"],
                payload={"name": data["name"], "specs": data["specs"], "gallery": [], "image_url": ""},
            )
        stats = identity.consolidate_duplicate_products()
        self.assertEqual(stats["products_deleted"], 1)
        remaining = Product.objects.filter(pk__in=[p1.pk, p2.pk])
        self.assertEqual(remaining.count(), 1)
        self.assertEqual(remaining.get().stock, 6)
        self.assertEqual(ProductSourceOffer.objects.filter(product=remaining.get()).count(), 2)


class CategoryV22Tests(TestCase):
    def test_existing_global_leaf_is_reused_and_reparented_to_exact_source_path(self):
        wrong_parent = Category.objects.create(name="کابل و تبدیل", slug="")
        existing = Category.objects.create(name="کابل AUX", slug="", parent=wrong_parent)
        before = Category.objects.count()

        resolved = category_v22.sync_category_path(["لوازم صوتی", "کابل AUX"])

        self.assertEqual(resolved.pk, existing.pk)
        self.assertEqual(Category.objects.count(), before + 1)
        existing.refresh_from_db()
        self.assertEqual(existing.parent.name, "لوازم صوتی")
        self.assertEqual(Category.objects.filter(name="کابل AUX").count(), 1)

    def test_woocommerce_breadcrumb_is_copied_in_source_order(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <nav class="woocommerce-breadcrumb">
                <a href="/">خانه</a>
                <a href="/product-category/accessories/">لوازم جانبی</a>
                <a href="/product-category/cables/">کابل و تبدیل</a>
                <a href="/product-category/cables/aux/">کابل AUX</a>
                محصول تست
              </nav>
              <h1 class="product_title">محصول تست</h1>
            </body></html>
            """,
            "lxml",
        )
        self.assertEqual(
            category_v22.enhanced_category_names(soup),
            ["لوازم جانبی", "کابل و تبدیل", "کابل AUX"],
        )

    def test_global_duplicate_cleanup_keeps_one_category_and_product(self):
        a = Category.objects.create(name="شاخه اول", slug="")
        b = Category.objects.create(name="شاخه دوم", slug="")
        first = Category.objects.create(name="کابل AUX", slug="", parent=a)
        duplicate = Category.objects.create(name="کابل  AUX", slug="", parent=b)
        product = Product.objects.create(name="AUX", price=1000, stock=1, category=duplicate)

        stats = category_v22.consolidate_sibling_duplicates()

        product.refresh_from_db()
        remaining = Category.objects.filter(name__contains="AUX")
        self.assertEqual(stats["categories_merged"], 1)
        self.assertEqual(remaining.count(), 1)
        self.assertEqual(product.category_id, remaining.get().id)
        self.assertFalse(Category.objects.filter(pk__in=[first.pk, duplicate.pk]).count() > 1)

    def test_offer_paths_can_repair_old_parent_tree(self):
        site = SourceSite.objects.create(
            name="مریوان فون",
            hostname="marivanphone.com",
            base_url="https://marivanphone.com",
            is_active=True,
        )
        wrong_root = Category.objects.create(name="دسته اشتباه", slug="")
        leaf = Category.objects.create(name="کابل AUX", slug="", parent=wrong_root)
        product = Product.objects.create(name="کابل تست", price=1000, stock=1, category=leaf, source_type=Product.SYNCED)
        ProductSourceOffer.objects.create(
            product=product,
            source_site=site,
            source_url="https://marivanphone.com/product/test/",
            category_path=["لوازم جانبی", "کابل و تبدیل", "کابل AUX"],
            payload={"name": "کابل تست", "specs": {}},
            is_active=True,
        )

        stats = category_v22.rebuild_category_tree_from_offers(site.id)

        leaf.refresh_from_db()
        self.assertEqual(leaf.parent.name, "کابل و تبدیل")
        self.assertEqual(leaf.parent.parent.name, "لوازم جانبی")
        self.assertGreaterEqual(stats["categories_reparented"], 1)


class StrictImageSanitizerV22Tests(TestCase):
    def setUp(self):
        self.site, _ = SourceSite.objects.update_or_create(
            hostname="hamrahedovom.ir",
            defaults={"name": "همراه دوم", "base_url": "https://hamrahedovom.ir", "is_active": True},
        )

    def test_source_brand_or_banner_url_is_rejected_before_download(self):
        self.assertTrue(sanitizer._suspicious_url("https://cdn.example/hamrahedovom-logo-banner.webp", self.site))
        self.assertTrue(sanitizer._suspicious_url("https://cdn.example/product/promo-slider.jpg", self.site))

    def test_wide_ad_card_is_rejected(self):
        image = Image.new("RGB", (1200, 400), "white")
        self.assertIsNone(sanitizer._strict_studio_clean(image))

    def test_centered_studio_product_is_accepted_and_squared(self):
        image = Image.new("RGB", (700, 700), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((230, 150, 470, 570), fill="black")
        draw.rectangle((15, 15, 75, 35), fill="black")
        cleaned = sanitizer._strict_studio_clean(image)
        self.assertIsNotNone(cleaned)
        self.assertEqual(cleaned.width, cleaned.height)
