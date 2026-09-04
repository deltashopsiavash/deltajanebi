#!/usr/bin/env python3
import asyncio

import external_bot_delta_v16  # installs the full Delta patch chain
import delta_banner_controls_v31 as banner
import delta_bot_native as native


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


async def test_banner_list_has_independent_desktop_mobile_buttons_and_sizes():
    site = {"id": 7, "name": "Delta", "base_url": "https://delta.example", "platform": "deltajanebi"}
    edits = []
    original_site = native._site
    original_api = native.core.api
    original_edit = native._edit
    try:
        native._site = lambda uid, sid: site if (uid, sid) == (1, 7) else None

        async def api(_site, action, payload=None, timeout=None):
            assert action == "banners"
            return {"ok": True, "data": [{"id": 11, "title": "اسلایدر اول", "is_active": True}]}

        async def edit(_q, text, markup=None):
            edits.append((text, markup))

        native.core.api = api
        native._edit = edit
        handled = await banner.callback(CallbackUpdate("d:banners:7"), Context())
    finally:
        native._site = original_site
        native.core.api = original_api
        native._edit = original_edit

    assert handled is True
    assert edits
    text, markup = edits[-1]
    assert banner.DESKTOP_SIZE in text
    assert banner.MOBILE_SIZE in text
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "d:bannerdesktop:7:11" in callbacks
    assert "d:bannermobile:7:11" in callbacks


async def test_desktop_button_starts_desktop_flow_with_size_hint():
    site = {"id": 7, "name": "Delta", "base_url": "https://delta.example", "platform": "deltajanebi"}
    edits = []
    original_site = native._site
    original_edit = native._edit
    try:
        native._site = lambda uid, sid: site if (uid, sid) == (1, 7) else None

        async def edit(_q, text, markup=None):
            edits.append((text, markup))

        native._edit = edit
        context = Context()
        handled = await banner.callback(CallbackUpdate("d:bannerdesktop:7:11"), context)
    finally:
        native._site = original_site
        native._edit = original_edit

    assert handled is True
    assert context.user_data["flow"] == "d_banner_desktop"
    assert context.user_data["banner_id"] == 11
    assert banner.DESKTOP_SIZE in edits[-1][0]


async def test_desktop_url_replacement_calls_media_set_mobile_false():
    site = {"id": 7, "name": "Delta", "base_url": "https://delta.example", "platform": "deltajanebi"}
    calls = []
    original_site = native._site
    original_api = native.core.api
    try:
        native._site = lambda uid, sid: site if (uid, sid) == (1, 7) else None

        async def api(_site, action, payload=None, timeout=None):
            calls.append((action, payload or {}))
            return {"ok": True, "data": {"id": 11}}

        native.core.api = api
        context = Context()
        context.user_data.update(
            flow="d_banner_desktop",
            platform="deltajanebi",
            site_id=7,
            banner_id=11,
        )
        update = MessageUpdate("https://cdn.example/new-desktop.webp")
        handled = await banner.message(update, context)
    finally:
        native._site = original_site
        native.core.api = original_api

    assert handled is True
    assert calls == [("delta_banner_media_set", {"id": 11, "mobile": False, "image_url": "https://cdn.example/new-desktop.webp"})]
    assert context.user_data == {}
    assert "دسکتاپ" in update.message.replies[-1][0]


def test_install_contract_and_dimensions():
    assert getattr(native, "_delta_banner_controls_v31_installed", False) is True
    assert banner.DESKTOP_SIZE == "1800×300"
    assert banner.MOBILE_SIZE == "1080×420"


if __name__ == "__main__":
    asyncio.run(test_banner_list_has_independent_desktop_mobile_buttons_and_sizes())
    asyncio.run(test_desktop_button_starts_desktop_flow_with_size_hint())
    asyncio.run(test_desktop_url_replacement_calls_media_set_mobile_false())
    test_install_contract_and_dimensions()
    print("Delta banner desktop/mobile controls regression: OK")
