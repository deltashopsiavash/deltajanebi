import json
import os
from unittest.mock import patch

from django.test import Client, TestCase

from shop.models import Category, Product, SiteSetting, User
from shop.services.wallet import adjust_wallet


class DeltaSiteApiV15Tests(TestCase):
    API_KEY = "test-delta-v15-api-key-0123456789abcdef"

    def setUp(self):
        self.env = patch.dict(os.environ, {"DELTAJANEBI_BOT_API_KEY": self.API_KEY, "DOMAIN": "delta.example"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = Client()

    def api(self, action, payload=None):
        return self.client.post(
            "/api/bot/v1/",
            data=json.dumps({"action": action, "payload": payload or {}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.API_KEY}",
        )

    def test_ping_identifies_native_delta_platform(self):
        SiteSetting.load()
        response = self.api("ping")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["site"]["platform"], "deltajanebi")
        self.assertEqual(data["site"]["version"], 15)
        self.assertIn("delta_native_panel", data["site"]["capabilities"])
        self.assertIn("wallet", data["site"]["capabilities"])
        self.assertIn("source_products", data["site"]["capabilities"])

    def test_native_category_delete_preserves_products(self):
        parent = Category.objects.create(name="لوازم", slug="")
        child = Category.objects.create(name="کابل", slug="", parent=parent)
        p1 = Product.objects.create(name="محصول والد", price=200000, stock=2, category=parent)
        p2 = Product.objects.create(name="محصول فرزند", price=100000, stock=3, category=child)

        preview = self.api("delta_category_delete_preview", {"id": parent.id})
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["data"]["descendant_count"], 1)
        self.assertEqual(preview.json()["data"]["all_products"], 2)
        self.assertEqual(preview.json()["data"]["product_behavior"], "keep_without_category")

        deleted = self.api("delta_category_delete", {"id": parent.id})
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(Category.objects.filter(pk__in=[parent.id, child.id]).exists())
        p1.refresh_from_db(); p2.refresh_from_db()
        self.assertIsNone(p1.category_id)
        self.assertIsNone(p2.category_id)

    def test_commerce_update_keeps_native_delta_fields(self):
        response = self.api("delta_commerce_update", {
            "card_payment_enabled": False,
            "zarinpal_payment_enabled": True,
            "card_number": "6037990000000000",
            "card_owner": "Delta Owner",
            "zarinpal_merchant_id": "merchant-test",
            "shipping_cost": 75000,
            "packaging_cost": 15000,
            "free_shipping_threshold": 2000000,
            "hide_out_of_stock": True,
            "terms_text": "قوانین تست",
        })
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertFalse(data["card_payment_enabled"])
        self.assertTrue(data["zarinpal_payment_enabled"])
        self.assertEqual(data["packaging_cost"], 15000)
        self.assertTrue(data["hide_out_of_stock"])
        store = SiteSetting.load()
        self.assertEqual(store.zarinpal_merchant_id, "merchant-test")
        self.assertEqual(store.terms_text, "قوانین تست")

    def test_delta_user_detail_reports_real_wallet_balance(self):
        user = User.objects.create_user(email="wallet@example.com", password="pass12345", phone="09120000000")
        adjust_wallet(user.id, 275000, "test topup", "test")
        response = self.api("delta_user_detail", {"id": user.id})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["wallet_balance"], 275000)

    def test_manual_product_and_timed_offer_are_native_delta_flows(self):
        created = self.api("delta_manual_product_create", {
            "name": "کابل تست",
            "price": 200000,
            "stock": 5,
            "category_path": ["کابل", "Type-C"],
        })
        self.assertEqual(created.status_code, 200, created.content)
        item = created.json()["data"]
        self.assertEqual(item["source_type"], "manual")
        self.assertEqual(item["price"], 200000)
        self.assertEqual(item["category"], "Type-C")

        timed = self.api("delta_timed_offer_set", {"id": item["id"], "price": 150000, "minutes": 120})
        self.assertEqual(timed.status_code, 200, timed.content)
        self.assertEqual(timed.json()["data"]["effective_price"], 150000)
        self.assertTrue(timed.json()["data"]["discount_active"])

        cleared = self.api("delta_timed_offer_clear", {"id": item["id"]})
        self.assertEqual(cleared.status_code, 200, cleared.content)
        self.assertEqual(cleared.json()["data"]["effective_price"], 200000)
        self.assertFalse(cleared.json()["data"]["discount_active"])
