import os

import requests


def _admins():
    raw = os.getenv("TELEGRAM_STORE_CHAT_ID") or os.getenv("TELEGRAM_ADMIN_IDS", "")
    return [x.strip() for x in raw.split(",") if x.strip()]


def _send(method, payload):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return
    for chat_id in _admins():
        data = dict(payload)
        data["chat_id"] = chat_id
        try:
            requests.post(f"https://api.telegram.org/bot{token}/{method}", json=data, timeout=12)
        except Exception:
            pass


def _chunks(text, limit=3900):
    text = str(text or "")
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        yield text[:cut]
        text = text[cut:].lstrip("\n")
    if text:
        yield text


def notify_admins(text, reply_markup=None):
    parts = list(_chunks(text)) or [""]
    for index, part in enumerate(parts):
        payload = {"text": part}
        if reply_markup and index == len(parts) - 1:
            payload["reply_markup"] = reply_markup
        _send("sendMessage", payload)


def notify_admins_photo(photo_url, caption="", reply_markup=None):
    if not photo_url:
        notify_admins(caption, reply_markup=reply_markup)
        return
    payload = {"photo": photo_url, "caption": str(caption or "")[:1024]}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    _send("sendPhoto", payload)
