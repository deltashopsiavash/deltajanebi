#!/usr/bin/env python3
import asyncio
import base64
import io
import json
import logging
from urllib.parse import urlsplit

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

import external_bot as core
import external_bot_plus as plus
import external_bot_v12 as sana_v12
import external_bot_v16 as sana
from bot_resilience import resilient_notification_loop
from bot_single_instance import acquire_single_instance_lock

import delta_bot_native as delta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("deltajanebi.multisite.v15")

# Importing SanaShop v16 establishes SanaShop's latest native panel/transport. Keep it
# as a platform-specific renderer, then install our router as the only global panel.
SANA_PANEL = core.site_panel


def ensure_schema():
    conn = core.db()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sites)").fetchall()}
    if "platform" not in cols:
        conn.execute("ALTER TABLE sites ADD COLUMN platform TEXT NOT NULL DEFAULT 'unknown'")
    if "api_version" not in cols:
        conn.execute("ALTER TABLE sites ADD COLUMN api_version INTEGER NOT NULL DEFAULT 0")
    if "capabilities_json" not in cols:
        conn.execute("ALTER TABLE sites ADD COLUMN capabilities_json TEXT NOT NULL DEFAULT '{}'")
    conn.commit()
    return conn


def platform_of(site):
    if not site:
        return "unknown"
    try:
        value = str(site["platform"] or "unknown").strip().lower()
    except Exception:
        value = "unknown"
    return value if value in {"sanashop", "deltajanebi"} else "unknown"


def routed_site_panel(site, uid):
    return delta.panel(site, uid) if platform_of(site) == "deltajanebi" else SANA_PANEL(site, uid)


core.site_panel = routed_site_panel


def _normalize_url(value):
    value = str(value or "").strip()
    if "://" not in value:
        value = "https://" + value
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("آدرس سایت معتبر نیست.")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}".rstrip("/")


def _save_metadata(site_id, platform, version=0, capabilities=None):
    with ensure_schema() as conn:
        conn.execute(
            "UPDATE sites SET platform=?, api_version=?, capabilities_json=? WHERE id=?",
            (platform, int(version or 0), json.dumps(capabilities or [], ensure_ascii=False), int(site_id)),
        )
        conn.commit()


async def detect_platform(site, persist=True):
    info = await core.api(site, "ping", timeout=30)
    meta = info.get("site") or {}
    platform = str(meta.get("platform") or "sanashop").strip().lower()
    if platform not in {"sanashop", "deltajanebi"}:
        raise RuntimeError(f"نوع سایت پشتیبانی نمی‌شود: {platform}")
    if persist:
        try:
            _save_metadata(site["id"], platform, meta.get("version") or 0, meta.get("capabilities") or [])
        except Exception:
            pass
    return platform, meta


async def ensure_platform(site):
    current = platform_of(site)
    if current != "unknown":
        return current
    platform, _ = await detect_platform(site, persist=True)
    return platform


def _site_from_callback(uid, data):
    parts = str(data or "").split(":")
    # Both platforms place site_id before object ids in site-specific callbacks.
    for token in parts[1:]:
        if not token.isdigit():
            continue
        sid = int(token)
        if core.can_access(uid, sid):
            return core.get_site(sid)
    return None


async def show_site(target, site, uid, edit=True):
    platform = await ensure_platform(site)
    label = "DeltaJanebi" if platform == "deltajanebi" else "SanaShop"
    text = f"🏪 {site['name']}\n🌐 {site['base_url']}\n🧩 مدیریت اختصاصی: {label}"
    fresh = core.get_site(site["id"])
    if edit:
        try:
            return await target.edit_message_text(text, reply_markup=routed_site_panel(fresh, uid))
        except Exception:
            return await target.message.reply_text(text, reply_markup=routed_site_panel(fresh, uid))
    return await target.reply_text(text, reply_markup=routed_site_panel(fresh, uid))


async def start(update: Update, context):
    ensure_schema()
    uid = update.effective_user.id
    context.user_data.clear()
    if core.is_owner(uid):
        return await update.message.reply_text(
            "👑 پنل مالک چندسایتی\nهر سایت پنل و قابلیت‌های اختصاصی خودش را دارد.",
            reply_markup=core.owner_home(),
        )
    site = core.assigned_site(uid)
    if not site:
        return await update.message.reply_text("⛔️ شما مجاز به استفاده از ربات نمی‌باشید.")
    try:
        await ensure_platform(site)
        site = core.get_site(site["id"])
        return await show_site(update.message, site, uid, edit=False)
    except Exception as exc:
        return await update.message.reply_text(f"⚠️ سایت ذخیره‌شده پیدا شد ولی API پاسخ نداد. اتصال حذف نشده است.\n{str(exc)[:500]}")


async def _owner_sites(q, uid):
    with ensure_schema() as conn:
        sites = conn.execute("SELECT * FROM sites ORDER BY id").fetchall()
    if not sites:
        return await q.edit_message_text(
            "هنوز سایتی متصل نشده است.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 اتصال سایت", callback_data="connect")],
                [InlineKeyboardButton("⬅️ بازگشت", callback_data="owner_home")],
            ]),
        )
    keys = []
    for s in sites:
        p = platform_of(s)
        badge = "🟣 Delta" if p == "deltajanebi" else ("💎 Sana" if p == "sanashop" else "❔")
        keys.append([InlineKeyboardButton(f"{badge} | {s['name']}", callback_data=f"open_site:{s['id']}")])
    keys += [
        [InlineKeyboardButton("🔗 اتصال سایت جدید", callback_data="connect")],
        [InlineKeyboardButton("⬅️ پنل مالک", callback_data="owner_home")],
    ]
    return await q.edit_message_text(
        "🏪 سایت‌های متصل‌شده\n\nهر ردیف با Router مخصوص همان پلتفرم باز می‌شود.",
        reply_markup=InlineKeyboardMarkup(keys),
    )


async def callback(update: Update, context):
    ensure_schema()
    q = update.callback_query
    uid = q.from_user.id
    data = q.data or ""
    if not core.is_authorized(uid):
        return await q.answer("شما مجاز به استفاده از ربات نمی‌باشید.", show_alert=True)

    if data == "connect":
        if not core.is_owner(uid):
            return await q.answer("فقط مالک می‌تواند سایت اضافه کند.", show_alert=True)
        await q.answer()
        context.user_data.clear()
        context.user_data.update(flow="router_connect_url", platform="router")
        return await q.edit_message_text(
            "🔗 اتصال سایت\n\nآدرس سایت SanaShop یا DeltaJanebi را بفرست.\nمثال: https://shop.example.com"
        )
    if data == "owner_sites" and core.is_owner(uid):
        await q.answer()
        context.user_data.clear()
        return await _owner_sites(q, uid)
    if data.startswith("open_site:") and core.is_owner(uid):
        await q.answer()
        site = core.get_site(int(data.split(":")[1]))
        if not site:
            return await q.edit_message_text("سایت پیدا نشد.", reply_markup=core.owner_home())
        try:
            await detect_platform(site, persist=True)
            return await show_site(q, core.get_site(site["id"]), uid, edit=True)
        except Exception as exc:
            return await q.edit_message_text(
                f"⚠️ سایت در دیتابیس ربات باقی ماند ولی اتصال API ناموفق بود:\n{str(exc)[:700]}",
                reply_markup=core.owner_home(),
            )
    if data.startswith("site_info:"):
        site = _site_from_callback(uid, data)
        if not site:
            return await q.answer("عدم دسترسی", show_alert=True)
        await q.answer()
        try:
            await detect_platform(site, persist=True)
            return await show_site(q, core.get_site(site["id"]), uid, edit=True)
        except Exception as exc:
            return await q.edit_message_text(
                f"⚠️ API سایت پاسخ نداد؛ اتصال ذخیره‌شده حذف نشده است.\n{str(exc)[:700]}",
                reply_markup=routed_site_panel(site, uid),
            )

    if data.startswith("d:"):
        return await delta.callback(update, context)

    site = _site_from_callback(uid, data)
    if site:
        try:
            p = await ensure_platform(site)
        except Exception as exc:
            try:
                await q.answer("API سایت پاسخ نداد؛ اتصال حفظ شد.", show_alert=True)
            except Exception:
                pass
            return await q.message.reply_text(f"⚠️ {str(exc)[:700]}", reply_markup=routed_site_panel(site, uid))
        if p == "deltajanebi":
            # Hard isolation: a Delta site is never allowed to fall through into SanaShop handlers.
            await q.answer("این دکمه متعلق به پنل Delta نیست؛ پنل اختصاصی سایت باز شد.", show_alert=True)
            return await q.message.reply_text(
                "🟣 مدیریت DeltaJanebi از پنل اختصاصی خودش انجام می‌شود.",
                reply_markup=delta.panel(site, uid),
            )
        return await sana.callback(update, context)

    # Owner/admin-global callbacks have no site id and remain shared plumbing only.
    return await sana.callback(update, context)


async def message(update: Update, context):
    ensure_schema()
    uid = update.effective_user.id
    flow = context.user_data.get("flow")
    text = (update.message.text or "").strip()
    if flow == "router_connect_url" and core.is_owner(uid):
        try:
            url = _normalize_url(text)
        except ValueError as exc:
            return await update.message.reply_text(f"❌ {exc}\nدوباره آدرس را بفرست.")
        context.user_data.clear()
        context.user_data.update(flow="router_connect_key", platform="router", connect_url=url)
        return await update.message.reply_text(
            "🔑 حالا API Key مخصوص همین سایت را بفرست. Router خودش تشخیص می‌دهد SanaShop است یا DeltaJanebi."
        )
    if flow == "router_connect_key" and core.is_owner(uid):
        url = context.user_data.get("connect_url")
        key = text
        if not url:
            context.user_data.clear()
            return await update.message.reply_text("مرحله اتصال منقضی شده؛ /start را بزن.")
        if len(key) < 32:
            return await update.message.reply_text("❌ کلید کوتاه/نامعتبر است؛ دوباره بفرست.")
        await update.message.reply_text("⏳ در حال تست API و تشخیص نوع سایت...")
        candidate = {"base_url": url, "api_key": key}
        platform, meta = await detect_platform(candidate, persist=False)
        name = str(meta.get("name") or urlsplit(url).hostname or platform)[:120]
        site_id = sana_v12._upsert_connected_site(url, key, name)
        _save_metadata(site_id, platform, meta.get("version") or 0, meta.get("capabilities") or [])
        saved = core.get_site(site_id)
        context.user_data.clear()
        label = "DeltaJanebi" if platform == "deltajanebi" else "SanaShop"
        return await update.message.reply_text(
            f"✅ سایت متصل شد.\n🏪 {saved['name']}\n🧩 نوع مدیریت: {label}\n🌐 {saved['base_url']}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏪 باز کردن پنل اختصاصی", callback_data=f"open_site:{site_id}")],
                [InlineKeyboardButton("⬅️ پنل مالک", callback_data="owner_home")],
            ]),
        )

    if context.user_data.get("platform") == "deltajanebi":
        handled = await delta.message(update, context)
        if handled:
            return
    return await sana.message(update, context)


async def media(update: Update, context):
    if context.user_data.get("platform") == "deltajanebi":
        handled = await delta.media(update, context)
        if handled:
            return
    return await sana.media(update, context)


async def backup_loop(application):
    await asyncio.sleep(20)
    while True:
        try:
            with ensure_schema() as conn:
                sites = conn.execute("SELECT * FROM sites ORDER BY id").fetchall()
            for site in sites:
                try:
                    p = await ensure_platform(site)
                    status = (await core.api(site, "backup_status", timeout=30))["data"]
                    if not status.get("due"):
                        continue
                    data = (await core.api(site, "backup_create", {"label": "auto"}, timeout=300))["data"]
                    raw = base64.b64decode(data["backup_b64"])
                    filename = data.get("filename") or ("delta.deltabackup" if p == "deltajanebi" else "sanashop.sanabackup")
                    delivered = False
                    for chat_id in plus.recipients_for(site["id"]):
                        try:
                            bio = io.BytesIO(raw)
                            bio.name = filename
                            caption = "💾 بکاپ زمان‌بندی‌شده DeltaJanebi" if p == "deltajanebi" else "🔐 بکاپ زمان‌بندی‌شده SanaShop"
                            await application.bot.send_document(
                                chat_id=chat_id,
                                document=bio,
                                filename=filename,
                                caption=caption,
                                read_timeout=180,
                                write_timeout=180,
                            )
                            delivered = True
                        except Exception:
                            logger.exception("backup delivery failed site=%s chat=%s", site["id"], chat_id)
                    if delivered:
                        await core.api(site, "backup_touch", timeout=30)
                except Exception:
                    logger.exception("scheduled backup failed site=%s", site["id"])
        except Exception:
            logger.exception("backup loop failed")
        await asyncio.sleep(60)


async def post_init(application):
    application.create_task(resilient_notification_loop(application, core, plus), name="multisite-events-v15")
    application.create_task(backup_loop(application), name="multisite-backups-v15")


def run():
    try:
        acquire_single_instance_lock()
    except RuntimeError as exc:
        logger.error("Duplicate bot startup refused: %s", exc)
        raise SystemExit(73) from exc
    ensure_schema()
    core.site_panel = routed_site_panel
    app = (
        Application.builder()
        .token(core.TOKEN)
        .concurrent_updates(16)
        .connection_pool_size(32)
        .pool_timeout(10.0)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run()
