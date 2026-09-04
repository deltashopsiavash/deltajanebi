#!/usr/bin/env python3
"""Temporary browser/page title controls for verification workflows such as Enamad."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import delta_bot_native as native

_ORIGINAL_CALLBACK = native.callback
_ORIGINAL_MESSAGE = native.message
_ORIGINAL_SETTINGS_MENU = native.settings_menu


def settings_menu(sid):
    original = _ORIGINAL_SETTINGS_MENU(sid)
    rows = [list(row) for row in original.inline_keyboard]
    title_row = [InlineKeyboardButton("🔤 تغییر عنوان سایت", callback_data=f"d:sitetitle:{sid}")]
    insert_at = 2 if len(rows) >= 2 else len(rows)
    rows.insert(insert_at, title_row)
    return InlineKeyboardMarkup(rows)


def _settings_back(sid):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ تنظیمات اختصاصی", callback_data=f"d:settings:{sid}")]])


async def _show_settings(q, site, sid):
    settings = (await native.core.api(site, "settings_get", timeout=35))["data"]
    title = (await native.core.api(site, "delta_site_title_get", timeout=35))["data"]
    current_title = title.get("effective_title") or settings.get("site_name") or "-"
    override_note = " (اختصاصی/موقت)" if title.get("is_override") else " (همان نام فروشگاه)"
    await native._edit(
        q,
        f"⚙️ تنظیمات اختصاصی Delta\n\n"
        f"نام فروشگاه: {settings.get('site_name')}\n"
        f"🔤 عنوان صفحه: {current_title}{override_note}\n"
        f"☎️ تلفن: {settings.get('contact_phone') or '-'}\n"
        f"🚚 ارسال: {native.money(settings.get('shipping_fee'))} تومان\n"
        f"📦 بسته‌بندی و روش‌های پرداخت از بخش پرداخت مدیریت می‌شوند.",
        settings_menu(sid),
    )
    return True


async def callback(update, context):
    q = update.callback_query
    data = str(q.data or "")
    parts = data.split(":")
    if len(parts) < 3 or parts[0] != "d" or parts[1] not in {"settings", "sitetitle"} or not parts[2].isdigit():
        return await _ORIGINAL_CALLBACK(update, context)

    sid = int(parts[2])
    site = native._site(q.from_user.id, sid)
    if not site:
        await q.answer("عدم دسترسی", show_alert=True)
        return True

    try:
        await q.answer()
        if parts[1] == "settings":
            return await _show_settings(q, site, sid)

        current = (await native.core.api(site, "delta_site_title_get", timeout=35))["data"]
        native._flow(context, "d_site_title", sid)
        await native._edit(
            q,
            "🔤 تغییر عنوان صفحه سایت\n\n"
            f"عنوان فعلی: {current.get('effective_title') or '-'}\n\n"
            "عنوان جدید را بفرست. این مقدار فقط تگ <title> صفحه را تغییر می‌دهد و نام فروشگاه، لوگو و متن‌های سایت دست نمی‌خورند.\n\n"
            "برای تایید اینماد، کد اعلام‌شده توسط اینماد را دقیقاً بفرست.\n"
            "بعد از تایید، برای بازگشت عنوان به نام فروشگاه فقط - بفرست.",
            _settings_back(sid),
        )
        return True
    except Exception as exc:
        try:
            await q.answer("عملیات انجام نشد", show_alert=False)
        except Exception:
            pass
        await native._edit(q, f"⚠️ تغییر عنوان انجام نشد ولی اتصال سایت حفظ شده است.\n{str(exc)[:800]}", _settings_back(sid))
        return True


async def message(update, context):
    if context.user_data.get("platform") != "deltajanebi" or context.user_data.get("flow") != "d_site_title":
        return await _ORIGINAL_MESSAGE(update, context)

    sid = context.user_data.get("site_id")
    site = native._site(update.effective_user.id, sid) if sid else None
    if not site:
        return await _ORIGINAL_MESSAGE(update, context)

    text = str(update.message.text or "").strip()
    if not text:
        await update.message.reply_text("عنوان نمی‌تواند خالی باشد؛ برای بازگشت به عنوان پیش‌فرض - بفرست.")
        return True
    if len(text) > 240:
        await update.message.reply_text("عنوان خیلی طولانی است؛ حداکثر ۲۴۰ کاراکتر بفرست.")
        return True

    try:
        payload_title = "" if text == "-" else text
        result = (await native.core.api(site, "delta_site_title_set", {"title": payload_title}, timeout=35))["data"]
        context.user_data.clear()
        if result.get("is_override"):
            msg = f"✅ عنوان سایت تغییر کرد.\n\n🔤 عنوان فعال: {result.get('effective_title') or payload_title}"
        else:
            msg = f"✅ عنوان اختصاصی حذف شد.\n\n🔤 عنوان سایت دوباره از نام فروشگاه استفاده می‌کند: {result.get('effective_title') or '-'}"
        await update.message.reply_text(msg, reply_markup=settings_menu(sid))
        return True
    except Exception as exc:
        await update.message.reply_text(f"⚠️ عنوان ذخیره نشد ولی اتصال سایت حفظ شده است.\n{str(exc)[:800]}", reply_markup=_settings_back(sid))
        return True


def install():
    if getattr(native, "_delta_site_title_v34_installed", False):
        return
    native.settings_menu = settings_menu
    native.callback = callback
    native.message = message
    native._delta_site_title_v34_installed = True
