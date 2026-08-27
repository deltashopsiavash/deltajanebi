#!/usr/bin/env python3
import asyncio
import os
from pathlib import Path

Path(os.environ.get("BOT_DB_PATH", "/tmp/deltajanebi-router-test.sqlite3")).unlink(missing_ok=True)

import external_bot_delta_v16 as entry
import external_bot_delta_v15 as router
import delta_bot_native as delta

core = router.core


def labels(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def seed():
    conn = router.ensure_schema()
    conn.execute("DELETE FROM site_admins")
    conn.execute("DELETE FROM admins")
    conn.execute("DELETE FROM sites")
    conn.execute("INSERT INTO sites(id,name,base_url,api_key,platform,api_version,capabilities_json) VALUES(1,'Sana CI','https://sana.example','ci-sana-key','sanashop',16,'[]')")
    conn.execute("INSERT INTO sites(id,name,base_url,api_key,platform,api_version,capabilities_json) VALUES(2,'Delta CI','https://delta.example','ci-delta-key','deltajanebi',15,'[]')")
    conn.commit()
    return core.get_site(1), core.get_site(2)


class Message:
    def __init__(self): self.replies = []
    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.replies.append((text, reply_markup)); return self


class Query:
    def __init__(self, data):
        self.data = data
        self.from_user = type("U", (), {"id": 1})()
        self.message = Message()
        self.edits = []
    async def answer(self, *args, **kwargs): return None
    async def edit_message_text(self, text, reply_markup=None, **kwargs):
        self.edits.append((text, reply_markup)); return self.message


class Update:
    def __init__(self, data):
        self.callback_query = Query(data)
        self.effective_user = self.callback_query.from_user
        self.effective_chat = type("C", (), {"id": 1})()


class Context:
    def __init__(self): self.user_data = {}


async def check_routing(sana_site, delta_site):
    original = router.sana.callback
    leaked = []
    async def forbidden(update, context):
        leaked.append(update.callback_query.data)
        raise AssertionError("Delta leaked into SanaShop")
    router.sana.callback = forbidden
    try:
        u = Update(f"products:{delta_site['id']}")
        await router.callback(u, Context())
        assert not leaked
        assert u.callback_query.message.replies
        assert "👥 کاربران و کیف پول" in labels(u.callback_query.message.replies[-1][1])
        u = Update(f"d:home:{delta_site['id']}")
        await router.callback(u, Context())
        assert not leaked
    finally:
        router.sana.callback = original

    called = []
    async def sana_handler(update, context):
        called.append(update.callback_query.data)
        return "ok"
    router.sana.callback = sana_handler
    try:
        result = await router.callback(Update(f"products:{sana_site['id']}"), Context())
        assert result == "ok"
        assert called == [f"products:{sana_site['id']}"]
    finally:
        router.sana.callback = original


def check_native_contract():
    source = Path("delta_bot_native.py").read_text(encoding="utf-8")
    required = [
        "مدیریت محصولات", "تنظیمات مدیریتی", "محصولات عادی", "محصولات خاص",
        "افزودن محصول خاص", "سایت‌های منبع", "همگام‌سازی همه", "پاکسازی کل کاتالوگ",
        "کاربران و کیف پول", "کیف پول", "تراکنش", "اطلاع‌رسانی", "پرداخت، تخفیف و ارسال",
        "بنرهای تبلیغاتی", "نمادها", "معرفی محصولات", "بکاپ کامل Delta", "شگفت‌انگیز",
        "پیشنهاد زمان‌دار", "Sync همین محصول", "حذف تغییرات دستی",
        "d:wallet:", "d:wallethistory:", "d_wallet_amount", "d_wallet_reason",
    ]
    missing = [x for x in required if x not in source]
    assert not missing, missing
    assert "core.site_panel =" not in source


def main():
    sana_site, delta_site = seed()
    assert router.platform_of(sana_site) == "sanashop"
    assert router.platform_of(delta_site) == "deltajanebi"
    assert core.site_panel is router.routed_site_panel
    sana_labels = labels(router.routed_site_panel(sana_site, 1))
    delta_labels = labels(router.routed_site_panel(delta_site, 1))
    assert "👥 کاربران و کیف پول" in delta_labels
    assert "👥 کاربران و کیف پول" not in sana_labels
    assert sana_labels != delta_labels
    check_native_contract()
    asyncio.run(check_routing(sana_site, delta_site))
    print("Mixed DB router isolation: OK")
    print("Native Delta flow contract: OK")


if __name__ == "__main__":
    main()
