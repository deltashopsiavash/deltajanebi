import json
import os
from unittest.mock import patch

from django.test import Client, TestCase

from enhancements.help_pages import ensure_default_help_pages
from enhancements.models import HelpPage
from shop.models import SiteSetting


class HelpPagesV33Tests(TestCase):
    def setUp(self):
        self.client = Client()
        self.api_key = "test-help-pages-api-key-0123456789"

    def api(self, action, payload=None):
        with patch.dict(os.environ, {"DELTAJANEBI_BOT_API_KEY": self.api_key}, clear=False):
            return self.client.post(
                "/api/bot/v1/",
                data=json.dumps({"action": action, "payload": payload or {}}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer " + self.api_key,
            )

    def test_register_terms_is_clickable(self):
        response = self.client.get("/register/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn('href="/terms/"', html)
        self.assertIn("قوانین و شرایط استفاده از فروشگاه", html)

    def test_default_help_pages_appear_in_footer(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("راهنما", html)
        self.assertIn("قوانین و مقررات", html)
        self.assertIn("رویه بازگشت کالا", html)
        self.assertIn("راهنمای خرید", html)
        self.assertEqual(HelpPage.objects.filter(is_builtin=True).count(), 3)

    def test_old_terms_text_is_preserved_in_rules_page(self):
        store = SiteSetting.load()
        store.terms_text = "قوانین قدیمی فروشگاه برای انتقال"
        store.save(update_fields=["terms_text"])
        ensure_default_help_pages()
        rules = HelpPage.objects.get(slug="rules")
        self.assertEqual(rules.content, store.terms_text)
        response = self.client.get("/terms/")
        self.assertContains(response, store.terms_text)

    def test_hidden_page_disappears_from_footer_but_terms_route_stays_accessible(self):
        ensure_default_help_pages()
        returns = HelpPage.objects.get(slug="returns")
        returns.is_visible = False
        returns.save(update_fields=["is_visible", "updated_at"])
        home = self.client.get("/").content.decode("utf-8")
        self.assertNotIn("رویه بازگشت کالا", home)
        self.assertEqual(self.client.get("/help/returns/").status_code, 404)

        rules = HelpPage.objects.get(slug="rules")
        rules.is_visible = False
        rules.content = "قوانین ثبت نام"
        rules.save(update_fields=["is_visible", "content", "updated_at"])
        self.assertEqual(self.client.get("/terms/").status_code, 200)

    def test_api_can_create_edit_toggle_and_delete_custom_page(self):
        listed = self.api("delta_help_pages")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()["data"]), 3)

        created = self.api("delta_help_page_create", {"title": "سوالات متداول", "content": "پاسخ سوالات", "is_visible": True})
        self.assertEqual(created.status_code, 200, created.content)
        item = created.json()["data"]
        self.assertFalse(item["is_builtin"])
        self.assertTrue(item["is_visible"])

        updated = self.api("delta_help_page_update", {"id": item["id"], "title": "پرسش‌های متداول", "content": "متن جدید", "is_visible": False, "sort_order": 5})
        self.assertEqual(updated.status_code, 200, updated.content)
        data = updated.json()["data"]
        self.assertEqual(data["title"], "پرسش‌های متداول")
        self.assertFalse(data["is_visible"])
        self.assertEqual(data["sort_order"], 5)

        deleted = self.api("delta_help_page_delete", {"id": item["id"]})
        self.assertEqual(deleted.status_code, 200, deleted.content)
        self.assertFalse(HelpPage.objects.filter(pk=item["id"]).exists())

    def test_builtin_page_cannot_be_deleted_and_rules_update_syncs_legacy_terms(self):
        rows = self.api("delta_help_pages").json()["data"]
        rules = next(x for x in rows if x["slug"] == "rules")
        response = self.api("delta_help_page_update", {"id": rules["id"], "content": "قوانین جدید دلتا جانبی"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SiteSetting.load().terms_text, "قوانین جدید دلتا جانبی")
        protected = self.api("delta_help_page_delete", {"id": rules["id"]})
        self.assertEqual(protected.status_code, 409)
        self.assertTrue(HelpPage.objects.filter(pk=rules["id"]).exists())

    def test_legacy_commerce_terms_edit_also_updates_rules_page(self):
        self.api("delta_help_pages")
        response = self.api("delta_commerce_update", {"terms_text": "قوانین از پیام قدیمی ربات"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(SiteSetting.load().terms_text, "قوانین از پیام قدیمی ربات")
        self.assertEqual(HelpPage.objects.get(slug="rules").content, "قوانین از پیام قدیمی ربات")
