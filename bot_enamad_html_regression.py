#!/usr/bin/env python3
"""Regression: pasted official eNamad HTML works in the unified Delta bot only."""
import asyncio
import delta_bot_native as bot

id_code = "7609266"
token = "EXAMPLECODE"
original = (
    "<a referrerpolicy='origin' target='_blank' "
    "href='https://trustseal.enamad.ir/?id=%s&amp;Code=%s'>"
    "<img referrerpolicy='origin' "
    "src='https://trustseal.enamad.ir/logo.aspx?id=%s&amp;Code=%s'>"
    "</a>" % (id_code, token, id_code, token)
)
parsed = bot._enamad_urls_from_html(original)
assert parsed == {
    "target_url": "https://trustseal.enamad.ir/?id=7609266&Code=EXAMPLECODE",
    "image_url": "https://trustseal.enamad.ir/logo.aspx?id=7609266&Code=EXAMPLECODE",
}, parsed
for snippet in (
    '<a href="javascript:alert(1)"><img src="https://trustseal.enamad.ir/logo.aspx?id=1"></a>',
    '<a href="https://evil.example/"><img src="https://trustseal.enamad.ir/logo.aspx"></a>',
    '<a href="https://trustseal.enamad.ir.evil.example/"><img src="https://trustseal.enamad.ir/logo.aspx"></a>',
    '<a href="https://trustseal.enamad.ir/"><img src="data:image/svg+xml,evil"></a>',
    '<a href="https://trustseal.enamad.ir/"><img src="https://evil.example/logo.aspx"></a>',
    '<a href="https://trustseal.enamad.ir/">incomplete</a>',
):
    try:
        bot._enamad_urls_from_html(snippet)
    except (ValueError, TypeError):
        pass
    else:
        raise AssertionError("Unsafe or incomplete HTML was accepted")

class FakeMsg:
    text = original
    def __init__(self):
        self.replies = []
    async def reply_text(self, text, reply_markup=None, **kw):
        self.replies.append(text)
class FakeUpdate:
    effective_user = type("User", (), {"id": 1})()
    def __init__(self):
        self.message = FakeMsg()
class FakeContext:
    def __init__(self):
        self.user_data = {"flow": "d_badge_image", "badge_type": "enamad", "site_id": 7, "platform": "deltajanebi"}

async def main():
    calls = []
    prev_site, prev_api = bot._site, bot.core.api
    try:
        bot._site = lambda uid, sid: {"id":7,"name":"Delta"} if uid == 1 and sid == 7 else None
        async def fake_api(site, action, payload=None, **kwargs):
            calls.append((action, payload))
            return {"ok": True, "data": {}}
        bot.core.api = fake_api
        update, context = FakeUpdate(), FakeContext()
        assert await bot.message(update, context) is True
        assert calls == [("delta_badge_set", {"badge_type":"enamad", **parsed})], calls
        assert context.user_data == {}
        assert "ثبت شد" in update.message.replies[-1]

        # A plain image URL still uses the legacy 2-step flow.
        update2, context2 = FakeUpdate(), FakeContext()
        update2.message.text = "https://trustseal.enamad.ir/logo.aspx?id=1"
        assert await bot.message(update2, context2) is True
        assert context2.user_data["flow"] == "d_badge_target"
        assert context2.user_data["badge_image_url"] == update2.message.text

        update3, context3 = FakeUpdate(), FakeContext()
        update3.message.text = '<a href="https://evil.example"><img src="https://evil.example/logo.aspx"></a>'
        assert await bot.message(update3, context3) is True
        assert context3.user_data["flow"] == "d_badge_image"
        assert len(calls) == 1
    finally:
        bot._site, bot.core.api = prev_site, prev_api
    print("eNamad bot HTML parse/permissions/saving/legacy URL regression OK")

asyncio.run(main())
