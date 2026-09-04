import json
import os
from unittest.mock import patch

from django.test import Client, TestCase

from enhancements.models import AddonSetting
from shop.models import SiteSetting


class SiteTitleV34Tests(TestCase):
    def setUp(self):
        self.client = Client()
        self.api_key = "test-site-title-api-key-0123456789"
        store = SiteSetting.load()
        store.store_name = "دلتا جانبی"
        store.save(update_fields=["store_name"])

    def api(self, action, payload=None):
        with patch.dict(os.environ, {"DELTAJANEBI_BOT_API_KEY": self.api_key}, clear=False):
            return self.client.post(
                "/api/bot/v1/",
                data=json.dumps({"action": action, "payload": payload or {}}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer " + self.api_key,
            )

    def test_api_sets_verification_code_as_html_title_without_renaming_store(self):
        response = self.api("delta_site_title_set", {"title": "7389548"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["effective_title"], "7389548")
        self.assertEqual(SiteSetting.load().store_name, "دلتا جانبی")
        self.assertEqual(AddonSetting.load().site_title_override, "7389548")

        html = self.client.get("/").content.decode("utf-8")
        self.assertIn("<title>7389548</title>", html)
        self.assertIn("دلتا جانبی", html)

    def test_dash_or_empty_restores_normal_store_title(self):
        self.api("delta_site_title_set", {"title": "7389548"})
        reset = self.api("delta_site_title_set", {"title": "-"})
        self.assertEqual(reset.status_code, 200)
        self.assertFalse(reset.json()["data"]["is_override"])
        self.assertEqual(reset.json()["data"]["effective_title"], "دلتا جانبی")
        self.assertEqual(AddonSetting.load().site_title_override, "")
        html = self.client.get("/").content.decode("utf-8")
        self.assertIn("<title>دلتا جانبی</title>", html)

    def test_title_is_html_escaped(self):
        self.api("delta_site_title_set", {"title": "<b>7389548</b>"})
        html = self.client.get("/").content.decode("utf-8")
        self.assertIn("<title>&lt;b&gt;7389548&lt;/b&gt;</title>", html)
        self.assertNotIn("<title><b>7389548</b></title>", html)
