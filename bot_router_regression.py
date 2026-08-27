#!/usr/bin/env python3
import asyncio
import os
from pathlib import Path

DB_PATH = Path(os.environ.get("BOT_DB_PATH", "/tmp/deltajanebi-router-test.sqlite3"))
DB_PATH.unlink(missing_ok=True)

import external_bot_delta_v16 as entry
import external_bot_delta_v15 as router
import delta_bot_native as delta

core = router.core


def labels(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def seed_sites():
    conn = router.ensure_schema()
    conn.execute("DELETE FROM site_admins")
    conn.execute("DELETE FROM admins")
    conn.execute("DELETE FROM sites")
    conn.execute(
        "INSERT INTO sites(id,name,base_url,api_key,platform,api_version,capabilities_json) VALUES(1,?,?,?,?,?,?)",
        ("Sana CI", "https://sana.example", "s" * 64, "sanashop", 16, "[]"),
    )
    conn.execute(
        "INSERT INTO sites(id,name,base_url,api_key,platform,api_version,capabilities_json) VALUES(2,?,?,?,?,?,?)",
        ("Delta CI", "https://delta.example", "d" * 64, "deltajanebi", 15, "[]"),
    )
    conn.commit()
    return core.get_site(1), core.get_site(2)


class DummyMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.replies.append((text, reply_markup))
        return self


class DummyQuery:
    def __init__(self, data):
        self.data = data
        self.from_user = type("User", (), {"id": 1})()
        self.message = DummyMessage()
        self.answers = []
        self.edits = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text, reply_markup=None, **kwargs):
        self.edits.append((text, reply_markup))
        return self.message


class DummyUpdate:
    def __init__(self, data):
        self.callback_query = DummyQuery(data)
        self.effective_user = self.callback_query.from_user
        self.effective_chat = type("Chat", (), {"id": 1})()


class DummyContext:
    def __init__(self):
        self.user_data = {}


async def routing_checks(sana_site, delta_site):
    # Delta legacy/Sana-style callback must be blocked from falling into Sana handlers.
    sana_calls = []
    original_sana = router.sana.callback

    async def forbidden_sana(update, context):
        sana_calls.append(update.callback_query.data)
        raise AssertionError("Delta callback leaked into SanaShop handler")

    router.sana.callback = forbidden_sana
    try:
        update = DummyUpdate(f"products:{delta_site['id']}")
        await router.callback(update, DummyContext())
        assert not sana_calls
        assert update.callback_query.message.replies
        _, markup = update.callback_query.message.replies[-1]
        assert "👥 کاربران و کیف پول" in labels(markup)

        # Native d:* callback stays inside Delta and does not need Sana fallback.
        update = DummyUpdate(f"d:home:{delta_site['id']}")
        await router.callback(update, DummyContext())
        assert not sana_calls
        assert update.callback_query.edits or update.callback_query.message.replies
    finally:
        router.sana.callback = original_sana

    # Sana callbacks must still go to Sana's own handler.
    seen = []

    async def tracked_sana(update, context):
        seen.append(update.callback_query.data)
        return "sana-ok"

    router.sana.callback = tracked_sana
    try:
        update = DummyUpdate(f"products:{sana_site['id']}")
        result = await router.callback(update, DummyContext())
        assert result == "sana-ok"
        assert seen == [f"products:{sana_site['id']}"]
    finally:
        router.sana.callback = original_sana


def native_contract_checks():
    source = Path("delta_bot_native.py").read_text(encoding="utf-8")
    required = [
        "مدیریت محصولات",
        "تنظیمات مدیریتی",
        "محصولات عادی",
        "محصولات خاص",
        "افزودن محصول خاص",
        "سایت‌های منبع",
        "همگام‌سازی همه",
        "پاکسازی کل کاتالوگ",
        "کاربران و کیف پول",
        "افزایش موجودی",
        "کاهش موجودی",
        "تراکنش‌های کیف پول",
        "اطلاع‌رسانی",
        "پرداخت، تخفیف و ارسال",
        "بنرهای تبلیغاتی",
        "نمادها",
        "معرفی محصولات",
        "بکاپ کامل Delta",
        "شگفت‌انگیز",
        "پیشنهاد زمان‌دار",
        "Sync همین محصول",
        "حذف تغییرات دستی",
    ]
    missing = [item for item in required if item not in source]
    assert not missing, f"Native Delta capabilities missing: {missing}"
    assert "core.site_panel =" not in source, "Delta module must not globally replace the shared panel"


def main():
    sana_site, delta_site = seed_sites()
    assert router.platform_of(sana_site) == "sanashop"
    assert router.platform_of(delta_site) == "deltajanebi"
    assert core.site_panel is router.routed_site_panel
    assert router.SANA_PANEL is not router.routed_site_panel

    sana_labels = labels(router.routed_site_panel(sana_site, 1))
    delta_labels = labels(router.routed_site_panel(delta_site, 1))
    assert "👥 کاربران و کیف پول" in delta_labels
    assert "🧭 تنظیمات مدیریتی" in delta_labels
    assert "👥 کاربران و کیف پول" not in sana_labels
    assert sana_labels != delta_labels

    native_contract_checks()
    asyncio.run(routing_checks(sana_site, delta_site))
    print("Mixed SanaShop + DeltaJanebi bot DB isolation: OK")
    print("Native Delta capability contract: OK")


if __name__ == "__main__":
    main()
