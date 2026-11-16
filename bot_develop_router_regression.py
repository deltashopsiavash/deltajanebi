#!/usr/bin/env python3
"""Platform 3 integration: DeltaDevelop must not fall through into SanaShop."""
import asyncio
import os
from pathlib import Path

os.environ["BOT_DB_PATH"] = "/tmp/deltadevelop-bot-router-ci.sqlite3"
Path(os.environ["BOT_DB_PATH"]).unlink(missing_ok=True)
import external_bot_delta_v16 as entry
import external_bot_delta_v15 as router

core = router.core


def labels(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


class Message:
    def __init__(self):
        self.replies = []
    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.replies.append((text, reply_markup))
        return self


class Query:
    def __init__(self, data):
        self.data = data
        self.from_user = type("U", (), {"id": 1})()
        self.message = Message()
        self.edits = []
        self.answers = []
    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))
    async def edit_message_text(self, text, reply_markup=None, **kwargs):
        self.edits.append((text, reply_markup))
        return self.message


class Update:
    def __init__(self, data):
        self.callback_query = Query(data)
        self.effective_user = self.callback_query.from_user
        self.effective_chat = type("C", (), {"id": 1})()


class Context:
    def __init__(self):
        self.user_data = {}


def seed():
    with router.ensure_schema() as con:
        con.execute("DELETE FROM site_admins")
        con.execute("DELETE FROM admins")
        con.execute("DELETE FROM sites")
        for sid, kind in ((1, "sanashop"), (2, "deltajanebi"), (3, "unknown")):
            con.execute(
                "INSERT INTO sites(id,name,base_url,api_key,platform,api_version,capabilities_json) "
                "VALUES(?,?,?,?,?,?,?)",
                (sid, kind, f"https://{kind}.example", f"test-{kind}-key", kind, 1, "[]"),
            )
        con.commit()


async def test_develop_routing():
    seed()
    calls = []
    async def api(site, action, payload=None, timeout=30):
        calls.append((site["id"], action, payload))
        if action == "ping":
            return {"ok": True, "site": {
                "name": "DeltaDevelop", "platform": "deltadevelop",
                "version": 1, "capabilities": ["studio_dashboard"],
            }}
        if site["id"] != 3:
            raise AssertionError("Cross-site API access")
        if action == "dashboard":
            return {"ok": True, "data": {
                "service_count": 2, "new_requests": 1, "total_requests": 3,
            }}
        if action == "settings_get":
            return {"ok": True, "data": {
                "name":"DeltaDevelop", "slogan":"توسعه نرم‌افزار",
                "email":"contact@example.org", "phone":"", "address":"",
            }}
        if action == "services_list":
            return {"ok": True, "data": [{
                "id": 7, "title": "طراحی سایت", "is_active": True, "order": 1,
            }]}
        if action == "service_toggle":
            return {"ok": True, "data": {"id": payload["id"], "is_active": False}}
        if action == "requests_list":
            return {"ok": True, "data": [{
                "id": 5, "name":"کاربر آزمون", "status":"new", "service":"وب",
            }]}
        if action == "request_detail":
            return {"ok": True, "data": {
                "id": 5, "name":"کاربر آزمون", "email":"contact@example.org",
                "phone":"", "description":"یک سامانه لازم داریم.",
                "status":"new", "service":"وب",
            }}
        if action == "request_status":
            return {"ok": True, "data": {"id": 5, "status": "reviewing"}}
        raise AssertionError(action)

    original_api = core.api
    original_sana = router.sana.callback
    original_delta = router.delta.callback
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("The request reached SanaShop or DeltaJanebi handler")
    core.api = api
    router.sana.callback = forbidden
    router.delta.callback = forbidden
    try:
        open_site = Update("open_site:3")
        await router.callback(open_site, Context())
        assert router.platform_of(core.get_site(3)) == "deltadevelop"
        assert open_site.callback_query.edits
        panel = open_site.callback_query.edits[-1][1]
        assert "🧭 داشبورد DeltaDevelop" in labels(panel)
        assert not any("گالری تصاویر" in name for name in labels(panel))
        assert not any("👥 کاربران و کیف پول" in name for name in labels(panel))
        assert "👥 کاربران و کیف پول" in labels(router.routed_site_panel(core.get_site(2), 1))

        for data, expected_action in (
            ("dev:home:3", "dashboard"),
            ("dev:profile:3", "settings_get"),
            ("dev:services:3", "services_list"),
            ("dev:toggle:3:7", "service_toggle"),
            ("dev:requests:3", "requests_list"),
            ("dev:request:3:5", "request_detail"),
            ("dev:status:3:5:reviewing", "request_status"),
        ):
            update = Update(data)
            before = len(calls)
            await router.callback(update, Context())
            assert any(c[1] == expected_action for c in calls[before:]), (data, calls[before:])
            assert update.callback_query.edits, data
            assert all(c[0] == 3 for c in calls[before:]), data

        forged = Update("dev:home:1")
        await router.callback(forged, Context())
        assert not forged.callback_query.edits
        assert forged.callback_query.answers
        forged2 = Update("dev:home:2")
        await router.callback(forged2, Context())
        assert not forged2.callback_query.edits

        old_button = Update("settings:3")
        await router.callback(old_button, Context())
        assert old_button.callback_query.message.replies
        assert "DeltaDevelop" in old_button.callback_query.message.replies[-1][0]
        assert not old_button.callback_query.edits
        print("DeltaDevelop platform detected and all admin callbacks isolated: OK")
    finally:
        core.api = original_api
        router.sana.callback = original_sana
        router.delta.callback = original_delta


if __name__ == "__main__":
    asyncio.run(test_develop_routing())
