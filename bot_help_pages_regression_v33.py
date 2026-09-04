#!/usr/bin/env python3
import asyncio

import external_bot_delta_v16  # installs the full Delta patch chain
import delta_bot_native as native
import delta_help_pages_v33 as helpui


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


def callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_settings_menu_contains_help_pages():
    items = callbacks(native.settings_menu(7))
    assert "d:helppages:7" in items
    assert "d:commerce:7" in items


async def test_commerce_menu_contains_help_pages():
    site = {"id": 7, "name": "Delta", "base_url": "https://delta.example", "platform": "deltajanebi"}
    edits = []
    original_site = native._site
    original_api = native.core.api
    original_edit = native._edit
    try:
        native._site = lambda uid, sid: site if (uid, sid) == (1, 7) else None

        async def api(_site, action, payload=None, timeout=None):
            assert action == "delta_commerce_get"
            return {"ok": True, "data": {
                "card_payment_enabled": True,
                "zarinpal_payment_enabled": False,
                "card_number": "",
                "card_owner": "",
                "shipping_cost": 0,
                "packaging_cost": 0,
                "free_shipping_threshold": 0,
                "hide_out_of_stock": False,
            }}

        async def edit(_q, text, markup=None):
            edits.append((text, markup))

        native.core.api = api
        native._edit = edit
        handled = await helpui.callback(CallbackUpdate("d:commerce:7"), Context())
    finally:
        native._site = original_site
        native.core.api = original_api
        native._edit = original_edit

    assert handled is True
    assert "d:helppages:7" in callbacks(edits[-1][1])


async def test_help_list_and_edit_flow():
    site = {"id": 7, "name": "Delta", "base_url": "https://delta.example", "platform": "deltajanebi"}
    edits = []
    calls = []
    original_site = native._site
    original_api = native.core.api
    original_edit = native._edit
    try:
        native._site = lambda uid, sid: site if (uid, sid) == (1, 7) else None

        async def api(_site, action, payload=None, timeout=None):
            calls.append((action, payload or {}))
            if action == "delta_help_pages":
                return {"ok": True, "data": [
                    {"id": 1, "title": "قوانین و مقررات", "is_visible": True, "has_content": True},
                    {"id": 2, "title": "رویه بازگشت کالا", "is_visible": False, "has_content": False},
                ]}
            if action == "delta_help_page_update":
                return {"ok": True, "data": {"id": payload["id"]}}
            raise AssertionError(action)

        async def edit(_q, text, markup=None):
            edits.append((text, markup))

        native.core.api = api
        native._edit = edit
        handled = await helpui.callback(CallbackUpdate("d:helppages:7"), Context())
        assert handled is True
        assert "d:helppage:7:1" in callbacks(edits[-1][1])
        assert "d:helpadd:7" in callbacks(edits[-1][1])

        context = Context()
        context.user_data.update(flow="d_help_edit_content", platform="deltajanebi", site_id=7, help_page_id=9)
        update = MessageUpdate("متن جدید قوانین")
        handled = await helpui.message(update, context)
        assert handled is True
        assert ("delta_help_page_update", {"id": 9, "content": "متن جدید قوانین"}) in calls
        assert context.user_data == {}
    finally:
        native._site = original_site
        native.core.api = original_api
        native._edit = original_edit


def test_install_contract():
    assert getattr(native, "_delta_help_pages_v33_installed", False) is True
    assert callable(native.settings_menu)
    items = callbacks(native.settings_menu(7))
    # Later feature packs may wrap the settings menu; the v33 help entry must remain.
    assert "d:helppages:7" in items


if __name__ == "__main__":
    test_settings_menu_contains_help_pages()
    asyncio.run(test_commerce_menu_contains_help_pages())
    asyncio.run(test_help_list_and_edit_flow())
    test_install_contract()
    print("Delta help-page controls regression: OK")
