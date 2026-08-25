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


def notify_admins(text, reply_markup=None):
    payload = {"text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    _send("sendMessage", payload)


def notify_admins_photo(photo_url, caption="", reply_markup=None):
    if not photo_url:
        notify_admins(caption, reply_markup=reply_markup)
        return
    payload = {"photo": photo_url, "caption": caption[:1024]}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    _send("sendPhoto", payload)
