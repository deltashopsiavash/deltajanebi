#!/usr/bin/env python3
import asyncio

import delta_bot_native as native
import delta_footer_restore as restore


class DummyMessage:
    def __init__(self, text=""):
        self.text = text
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


class DummyCallbackUpdate:
    def __init__(self, data):
        self.callback_query = DummyQuery(data)
        self.effective_user = self.callback_query.from_user
        self.effective_chat = type("Chat", (), {"id": 1})()


class DummyMessageUpdate:
    def __init__(self, text):
        self.effective_user = type("User", (), {"id": 1})()
        self.message = DummyMessage(text)
        self.effective_message = self.message
        self.effective_chat = type("Chat", (), {"id": 1})()


class DummyContext:
    def __init__(self):
        self.user_data = {}


def fake_site(uid, sid):
    if int(uid) == 1 and int(sid) == 7:
        return {"id": 7, "name": "Delta Footer CI", "base_url": "https://delta.example", "platform": "deltajanebi"}
    return None


async def run_checks():
    calls = []
    original_site = native._site
    original_api = native.core.api
    original_delegate = restore._ORIGINAL_CALLBACK

    async def fake_api(site, action, payload=None, timeout=None):
        calls.append((action, payload or {}))
        if action == "settings_get":
            return {"ok": True, "data": {
                "address": "آدرس قبلی",
                "phone": "+982100000000",
                "contact_email": "old@example.com",
                "footer_description": "معرفی قبلی",
                "support_text": "پشتیبانی قبلی",
            }}
        if action == "settings_update":
            return {"ok": True}
        raise AssertionError(f"Unexpected API action: {action}")

    native._site = fake_site
    native.core.api = fake_api
    try:
        # The footer button must start the original five-step Delta flow.
        context = DummyContext()
        start = DummyCallbackUpdate("d:set:7:footer")
        handled = await restore.callback(start, context)
        assert handled is True
        assert context.user_data["flow"] == "d_footer_address"
        assert context.user_data["platform"] == "deltajanebi"
        assert context.user_data["site_id"] == 7
        assert start.callback_query.edits
        assert "تنظیم توضیحات و فوتر سایت" in start.callback_query.edits[-1][0]
        assert "آدرس قبلی" in start.callback_query.edits[-1][0]

        sequence = [
            ("تهران، خیابان نمونه", "d_footer_phone"),
            ("tel:+98 912-123-4567", "d_footer_email"),
            ("info@example.com", "d_footer_description"),
            ("متن معرفی فروشگاه", "d_footer_support"),
        ]
        for text, next_flow in sequence:
            msg = DummyMessageUpdate(text)
            assert await restore.message(msg, context) is True
            assert context.user_data["flow"] == next_flow

        final = DummyMessageUpdate("شنبه تا پنج‌شنبه در ساعات اداری پاسخگوی شما هستیم.")
        assert await restore.message(final, context) is True
        assert context.user_data == {}
        assert final.message.replies[-1][0] == "✅ اطلاعات فوتر ذخیره شد."

        updates = [payload for action, payload in calls if action == "settings_update"]
        assert len(updates) == 1, updates
        assert updates[0] == {
            "address": "تهران، خیابان نمونه",
            "phone": "+989121234567",
            "contact_email": "info@example.com",
            "footer_description": "متن معرفی فروشگاه",
            "support_text": "شنبه تا پنج‌شنبه در ساعات اداری پاسخگوی شما هستیم.",
        }, updates[0]

        # Invalid phone must keep the user on the same step and never save partial footer data.
        bad_context = DummyContext()
        bad_context.user_data.update(flow="d_footer_phone", platform="deltajanebi", site_id=7, footer_address="تهران")
        before_updates = len([1 for action, _ in calls if action == "settings_update"])
        bad = DummyMessageUpdate("not-a-phone")
        assert await restore.message(bad, bad_context) is True
        assert bad_context.user_data["flow"] == "d_footer_phone"
        assert "شماره معتبر نیست" in bad.message.replies[-1][0]
        after_updates = len([1 for action, _ in calls if action == "settings_update"])
        assert before_updates == after_updates

        # '-' keeps the original clear-field semantics.
        clear_context = DummyContext()
        clear_context.user_data.update(flow="d_footer_address", platform="deltajanebi", site_id=7)
        for text in ["-", "-", "-", "-", "-"]:
            assert await restore.message(DummyMessageUpdate(text), clear_context) is True
        clear_payload = [payload for action, payload in calls if action == "settings_update"][-1]
        assert clear_payload == {
            "address": "",
            "phone": "",
            "contact_email": "",
            "footer_description": "",
            "support_text": "",
        }

        # Every non-footer callback must remain untouched and delegate to the existing native Delta handler.
        delegated = []

        async def fake_delegate(update, context):
            delegated.append(update.callback_query.data)
            return "delegated-ok"

        restore._ORIGINAL_CALLBACK = fake_delegate
        other = DummyCallbackUpdate("d:settings:7")
        result = await restore.callback(other, DummyContext())
        assert result == "delegated-ok"
        assert delegated == ["d:settings:7"]
    finally:
        native._site = original_site
        native.core.api = original_api
        restore._ORIGINAL_CALLBACK = original_delegate

    print("Original Delta five-stage footer flow: OK")
    print("Footer clear/validation/delegation regression: OK")


if __name__ == "__main__":
    asyncio.run(run_checks())
