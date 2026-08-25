import os,requests
def _admins():
    raw=os.getenv("TELEGRAM_STORE_CHAT_ID") or os.getenv("TELEGRAM_ADMIN_IDS",""); return [x.strip() for x in raw.split(",") if x.strip()]
def notify_admins(text):
    token=os.getenv("TELEGRAM_BOT_TOKEN","")
    if not token: return
    for chat_id in _admins():
        try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage",json={"chat_id":chat_id,"text":text},timeout=5)
        except Exception: pass
