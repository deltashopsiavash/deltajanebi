#!/usr/bin/env python3
import asyncio

import external_bot_delta_v16  # installs the full Delta patch chain
import delta_bot_native as native
import delta_site_title_v34 as title


class Query:
    def __init__(self, data):
        self.data = data
        self.from_user = type("User", (), {"id": 1})()
        self.answers = []
        self.message = self

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)

    async def edit_message_text(self, text, reply_markup=None, **kwargs):
        return None

    async def reply_text(self, text, reply_markup=None, **kwargs):
        return None


class CallbackUpdate:
    def __init__(self, data):
        self.callback_query = Query(data)


class Message:
    def __init__(self, text):
        self.text = text
        self.replies = []

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.replies.append((text, reply_markup))


class MessageUpdate:
    def __init__(self, text):
        self.message = Message(text)
        self.effective_user = type("User", (), {"id": 1})()


class Context:
    def __init__(self):
        self.user_data = {}


async def test_settings_screen_contains_title_button():
    site = {"id": 7, "name": "Delta", "base_url": "https://delta.example", "platform": "deltajanebi"}
    edits = []
    original_site = native._site
    original_api = native.core.api
    original_edit = native._edit
    try:
        native._site = lambda uid, sid: site if (uid, sid) == (1, 7) else None

        async def api(_site, action, payload=None, timeout=None):
            if action == "settings_get":
                return {"ok": True, "data": {"site_name": "دلتا جانبی", "contact_phone": "", "shipping_fee": 0}}
            if action == "delta_site_title_get":
                return {"ok": True, "data": {"title": "", "effective_title": "دلتا جانبی", "is_override": False}}
            raise AssertionError(action)

        async def edit(_q, text, markup=None):
            edits.append((text, markup))

        native.core.api = api
        native._edit = edit
        handled = await title.callback(CallbackUpdate("d:settings:7"), Context())
    finally:
        native._site = original_site
        native.core.api = original_api
        native._edit = original_edit

    assert handled is True
    assert edits
    callbacks = [button.callback_data for row in edits[-1][1].inline_keyboard for button in row]
    assert "d:sitetitle:7" in callbacks
    assert "عنوان صفحه" in edits[-1][0]


async def test_title_button_starts_flow_and_code_is_saved():
    site = {"id": 7, "name": "Delta", "base_url": "https://delta.example", "platform": "deltajanebi"}
    calls = []
    edits = []
    original_site = native._site
    original_api = native.core.api
    original_edit = native._edit
    try:
        native._site = lambda uid, sid: site if (uid, sid) == (1, 7) else None

        async def api(_site, action, payload=None, timeout=None):
            calls.append((action, payload or {}))
            if action == "delta_site_title_get":
                return {"ok": True, "data": {"title": "", "effective_title": "دلتا جانبی", "is_override": False}}
            if action == "delta_site_title_set":
                value = (payload or {}).get("title") or ""
                return {"ok": True, "data": {"title": value, "effective_title": value or "دلتا جانبی", "is_override": bool(value)}}
            raise AssertionError(action)

        async def edit(_q, text, markup=None):
            edits.append((text, markup))

        native.core.api = api
        native._edit = edit
        context = Context()
        handled = await title.callback(CallbackUpdate("d:sitetitle:7"), context)
        assert handled is True
        assert context.user_data["flow"] == "d_site_title"
        assert "-" in edits[-1][0]

        update = MessageUpdate("7389548")
        handled = await title.message(update, context)
    finally:
        native._site = original_site
        native.core.api = original_api
        native._edit = original_edit

    assert handled is True
    assert ("delta_site_title_set", {"title": "7389548"}) in calls
    assert context.user_data == {}
    assert "7389548" in update.message.replies[-1][0]


async def test_dash_resets_title():
    site = {"id": 7, "name": "Delta", "base_url": "https://delta.example", "platform": "deltajanebi"}
    calls = []
    original_site = native._site
    original_api = native.core.api
    try:
        native._site = lambda uid, sid: site if (uid, sid) == (1, 7) else None

        async def api(_site, action, payload=None, timeout=None):
            calls.append((action, payload or {}))
            return {"ok": True, "data": {"title": "", "effective_title": "دلتا جانبی", "is_override": False}}

        native.core.api = api
        context = Context()
        context.user_data.update(flow="d_site_title", platform="deltajanebi", site_id=7)
        update = MessageUpdate("-")
        handled = await title.message(update, context)
    finally:
        native._site = original_site
        native.core.api = original_api

    assert handled is True
    assert calls == [("delta_site_title_set", {"title": ""})]
    assert "حذف شد" in update.message.replies[-1][0]


def test_install_contract():
    assert getattr(native, "_delta_site_title_v34_installed", False) is True
    markup = native.settings_menu(7)
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "d:sitetitle:7" in callbacks


if __name__ == "__main__":
    asyncio.run(test_settings_screen_contains_title_button())
    asyncio.run(test_title_button_starts_flow_and_code_is_saved())
    asyncio.run(test_dash_resets_title())
    test_install_contract()
    print("Delta site title controls regression: OK")
