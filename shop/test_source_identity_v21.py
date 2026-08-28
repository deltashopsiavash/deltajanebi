from django.test import TestCase

from shop.models import Category, Product, SourceSite
from shop.source_offer_models import ProductSourceOffer
from shop.services.category_v21 import (
    canonical_path,
    consolidate_sibling_duplicates,
    sync_category_path,
)
from shop.services.source_catalog_v21 import _upsert_with_data
from shop.services.source_identity_v21 import (
    backfill_existing_offers,
    consolidate_duplicate_products,
    identity_key,
)


class SourceIdentityV21Tests(TestCase):
    def setUp(self):
        self.hamrah = SourceSite.objects.create(
            name="همراه دوم",
            base_url="https://hamrahedovom.ir",
            hostname="hamrahedovom.ir",
            is_active=True,
            default_markup_type=SourceSite.MARKUP_PERCENT,
            default_markup_value=10,
        )
        self.marivan = SourceSite.objects.create(
            name="مریوان فون",
            base_url="https://marivanphone.com",
            hostname="marivanphone.com",
            is_active=True,
            default_markup_type=SourceSite.MARKUP_PERCENT,
            default_markup_value=5,
        )

    def _charger(self, url, name, stock, price, model="EP-T2510"):
        return {
            "name": name,
            "description": "شارژر دیواری سریع",
            "price": price,
            "stock": stock,
            "image_url": "https://cdn.example.com/charger.webp",
            "gallery": [],
            "specs": {"مدل": model, "توان": "25W"},
            "sku": "",
            "categories": ["لوازم جانبی", "شارژر"],
            "source_url": url,
        }

    def test_model_parser_ignores_capacity_and_normalizes_model(self):
        data = {
            "name": "شارژر سامسونگ مدل EP-T2510 25W",
            "specs": {"Model": "EP T2510 25W"},
        }
        self.assertEqual(identity_key(data), "model:ept2510")

    def test_same_model_across_sources_is_one_product_and_stock_is_summed(self):
        first, created = _upsert_with_data(
            self.hamrah,
            "https://hamrahedovom.ir/product/a",
            self._charger(
                "https://hamrahedovom.ir/product/a",
                "شارژر سامسونگ مدل EP-T2510 25W",
                3,
                500_000,
            ),
        )
        self.assertTrue(created)

        second, created = _upsert_with_data(
            self.marivan,
            "https://marivanphone.com/product/b",
            self._charger(
                "https://marivanphone.com/product/b",
                "کلگی شارژ Samsung مدل EP T2510",
                5,
                480_000,
            ),
        )
        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)

        product = Product.objects.get(pk=first.pk)
        self.assertEqual(Product.objects.filter(source_type=Product.SYNCED, is_active=True).count(), 1)
        self.assertEqual(ProductSourceOffer.objects.filter(product=product, is_active=True).count(), 2)
        self.assertEqual(product.stock, 8)
        # Cheapest final sale price: 480,000 + 5% = 504,000.
        self.assertEqual(product.price, 504_000)
        self.assertEqual(product.category.name, "شارژر")
        self.assertEqual(product.category.parent.name, "شارژر و آداپتور")

    def test_source_stock_update_reaggregates_without_double_counting(self):
        product, _ = _upsert_with_data(
            self.hamrah,
            "https://hamrahedovom.ir/product/a",
            self._charger("https://hamrahedovom.ir/product/a", "شارژر EP-T2510", 3, 500_000),
        )
        _upsert_with_data(
            self.marivan,
            "https://marivanphone.com/product/b",
            self._charger("https://marivanphone.com/product/b", "شارژر EP-T2510", 5, 480_000),
        )
        _upsert_with_data(
            self.marivan,
            "https://marivanphone.com/product/b",
            self._charger("https://marivanphone.com/product/b", "شارژر EP-T2510", 2, 480_000),
        )
        product.refresh_from_db()
        self.assertEqual(product.stock, 5)
        self.assertEqual(ProductSourceOffer.objects.filter(product=product).count(), 2)

    def test_manual_stock_override_is_preserved(self):
        product, _ = _upsert_with_data(
            self.hamrah,
            "https://hamrahedovom.ir/product/a",
            self._charger("https://hamrahedovom.ir/product/a", "شارژر EP-T2510", 3, 500_000),
        )
        Product.objects.filter(pk=product.pk).update(manual_stock_override=99, stock=99)
        product.refresh_from_db()
        _upsert_with_data(
            self.marivan,
            "https://marivanphone.com/product/b",
            self._charger("https://marivanphone.com/product/b", "شارژر EP-T2510", 5, 480_000),
        )
        product.refresh_from_db()
        self.assertEqual(product.stock, 99)
        self.assertEqual(product.manual_stock_override, 99)

    def test_strict_exact_name_fallback_can_merge_when_no_model_exists(self):
        base = {
            "name": "هندزفری سیمی حرفه ای صدای استریو مخصوص موبایل",
            "description": "",
            "price": 200_000,
            "stock": 2,
            "image_url": "",
            "gallery": [],
            "specs": {},
            "sku": "",
            "categories": ["هندزفری"],
        }
        first_data = dict(base, source_url="https://hamrahedovom.ir/product/wired")
        second_data = dict(base, source_url="https://marivanphone.com/product/wired")
        first, _ = _upsert_with_data(self.hamrah, first_data["source_url"], first_data)
        second, created = _upsert_with_data(self.marivan, second_data["source_url"], second_data)
        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)
        first.refresh_from_db()
        self.assertEqual(first.stock, 4)

    def test_different_models_never_merge(self):
        first, _ = _upsert_with_data(
            self.hamrah,
            "https://hamrahedovom.ir/product/a",
            self._charger("https://hamrahedovom.ir/product/a", "شارژر مدل EP-T2510", 3, 500_000, "EP-T2510"),
        )
        second, created = _upsert_with_data(
            self.marivan,
            "https://marivanphone.com/product/c",
            self._charger("https://marivanphone.com/product/c", "شارژر مدل EP-T4510", 4, 600_000, "EP-T4510"),
        )
        self.assertTrue(created)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(Product.objects.filter(source_type=Product.SYNCED, is_active=True).count(), 2)

    def test_legacy_duplicates_are_consolidated_without_deleting_rows(self):
        p1 = Product.objects.create(
            name="شارژر EP-T2510",
            source_type=Product.SYNCED,
            source_url="https://hamrahedovom.ir/product/legacy-a",
            source_product_code="A",
            source_price=500_000,
            price=550_000,
            stock=2,
            specs={"مدل": "EP-T2510"},
        )
        p2 = Product.objects.create(
            name="کلگی سامسونگ EP T2510",
            source_type=Product.SYNCED,
            source_url="https://marivanphone.com/product/legacy-b",
            source_product_code="B",
            source_price=480_000,
            price=504_000,
            stock=4,
            specs={"Model": "EP T2510"},
        )
        self.assertEqual(backfill_existing_offers(), 2)
        stats = consolidate_duplicate_products()
        self.assertEqual(stats["products_merged"], 1)
        active = Product.objects.filter(pk__in=[p1.pk, p2.pk], is_active=True).get()
        inactive = Product.objects.filter(pk__in=[p1.pk, p2.pk], is_active=False).get()
        self.assertEqual(active.stock, 6)
        self.assertEqual(ProductSourceOffer.objects.filter(product=active).count(), 2)
        self.assertEqual(inactive.stock, 0)
        self.assertTrue(inactive.sync_error.startswith("merged_into:"))


class CategoryV21Tests(TestCase):
    def test_same_leaf_under_different_parents_is_not_globally_merged(self):
        mobile_leaf = sync_category_path(["موبایل", "کابل"])
        computer_leaf = sync_category_path(["کامپیوتر", "کابل"])
        self.assertNotEqual(mobile_leaf.pk, computer_leaf.pk)
        self.assertEqual(mobile_leaf.parent.name, "موبایل")
        self.assertEqual(computer_leaf.parent.name, "کامپیوتر")
        stats = consolidate_sibling_duplicates()
        self.assertEqual(stats["categories_merged"], 0)
        self.assertEqual(Category.objects.filter(name="کابل").count(), 2)

    def test_classifier_replaces_generic_source_root_with_delta_category(self):
        path = canonical_path(
            ["فروشگاه", "لوازم جانبی"],
            "شارژر دیواری سامسونگ مدل EP-T2510 25W",
            {"توان": "25W"},
        )
        self.assertEqual(path[0], "شارژر و آداپتور")
        self.assertNotIn("فروشگاه", path)
