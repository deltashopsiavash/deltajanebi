#!/usr/bin/env python3
"""Restore DeltaJanebi's original multi-step footer management flow.

This module is intentionally additive: it intercepts only the native Delta
"توضیحات و فوتر" callback/state machine and delegates every other callback
and message to delta_bot_native unchanged.
"""
import re

import delta_bot_native as native

_ORIGINAL_CALLBACK = native.callback
_ORIGINAL_MESSAGE = native.message
_FOOTER_FLOWS = {
    "d_footer_address",
    "d_footer_phone",
    "d_footer_email",
    "d_footer_description",
    "d_footer_support",
}


def normalize_phone(value):
    """Match the original Delta footer phone normalization from telegram_bot_v3."""
    text = str(value or "").strip()
    text = re.sub(r"^tel:\s*", "", text, flags=re.I)
    text = text.replace(" ", "").replace("-", "")
    if not re.fullmatch(r"\+?[0-9]{5,20}", text):
        raise ValueError("invalid phone")
    return text


def _is_footer_callback(data):
    parts = str(data or "").split(":")
    return len(parts) == 4 and parts[0] == "d" and parts[1] == "set" and parts[2].isdigit() and parts[3] == "footer"


async def callback(update, context):
    q = update.callback_query
    data = q.data or ""
    if not _is_footer_callback(data):
        return await _ORIGINAL_CALLBACK(update, context)

    uid = q.from_user.id
    sid = int(data.split(":")[2])
    site = native._site(uid, sid)
    if not site:
        await q.answer("عدم دسترسی", show_alert=True)
        return True

    try:
        await q.answer()
        current = (await native.core.api(site, "settings_get", timeout=35)).get("data") or {}
        context.user_data.clear()
        context.user_data.update(
            flow="d_footer_address",
            platform="deltajanebi",
            site_id=sid,
        )
        await native._edit(
            q,
            "📝 تنظیم توضیحات و فوتر سایت\n\n"
            f"آدرس فعلی:\n{current.get('address') or 'ثبت نشده'}\n\n"
            "آدرس جدید را بفرست. برای خالی‌کردن - بفرست.",
        )
    except Exception as exc:
        try:
            await q.answer("عملیات ناموفق بود؛ اتصال سایت حفظ شده است.")
        except Exception:
            pass
        await native._edit(
            q,
            f"⚠️ دریافت تنظیمات فوتر انجام نشد، ولی اتصال سایت حذف نشده است.\n\n{str(exc)[:900]}",
            native.settings_menu(sid),
        )
    return True


async def message(update, context):
    flow = context.user_data.get("flow") or ""
    if flow not in _FOOTER_FLOWS:
        return await _ORIGINAL_MESSAGE(update, context)

    uid = update.effective_user.id
    sid = context.user_data.get("site_id")
    site = native._site(uid, sid) if sid else None
    message = update.effective_message
    if not site:
        context.user_data.clear()
        await message.reply_text("دسترسی سایت پیدا نشد.")
        return True

    text = (message.text or "").strip()

    if flow == "d_footer_address":
        context.user_data["footer_address"] = "" if text == "-" else text
        context.user_data["flow"] = "d_footer_phone"
        await message.reply_text("☎️ شماره تماسی که داخل فوتر نمایش داده شود را بفرست. برای خالی‌کردن - بفرست.")
        return True

    if flow == "d_footer_phone":
        if text == "-":
            phone = ""
        else:
            try:
                phone = normalize_phone(text)
            except ValueError:
                await message.reply_text("❌ شماره معتبر نیست. مثال: +989121234567 یا -")
                return True
        context.user_data["footer_phone"] = phone
        context.user_data["flow"] = "d_footer_email"
        await message.reply_text("📧 ایمیل تماس فروشگاه را بفرست. برای خالی‌کردن - بفرست.")
        return True

    if flow == "d_footer_email":
        if text != "-" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", text):
            await message.reply_text("❌ ایمیل معتبر نیست. مثال: info@example.com یا -")
            return True
        context.user_data["footer_email"] = "" if text == "-" else text
        context.user_data["flow"] = "d_footer_description"
        await message.reply_text(
            "✍️ متن معرفی فروشگاه را بفرست؛ همون متنی که پایین سایت زیر نام فروشگاه نمایش داده می‌شود. برای خالی‌کردن - بفرست."
        )
        return True

    if flow == "d_footer_description":
        context.user_data["footer_description"] = "" if text == "-" else text
        context.user_data["flow"] = "d_footer_support"
        await message.reply_text(
            "🕘 متن پشتیبانی/ساعات پاسخگویی را بفرست؛ مثال: شنبه تا پنج‌شنبه در ساعات اداری پاسخگوی شما هستیم. برای خالی‌کردن - بفرست."
        )
        return True

    payload = {
        "address": context.user_data.get("footer_address", ""),
        "phone": context.user_data.get("footer_phone", ""),
        "contact_email": context.user_data.get("footer_email", ""),
        "footer_description": context.user_data.get("footer_description", ""),
        "support_text": "" if text == "-" else text[:240],
    }
    try:
        await native.core.api(site, "settings_update", payload, timeout=45)
    except Exception as exc:
        await message.reply_text(
            f"⚠️ ذخیره فوتر انجام نشد. اطلاعاتی که وارد کردی پاک نشده؛ دوباره همین مرحله را بفرست.\n\n{str(exc)[:700]}"
        )
        return True

    context.user_data.clear()
    await message.reply_text("✅ اطلاعات فوتر ذخیره شد.", reply_markup=native.settings_menu(sid))
    return True


def install():
    """Patch only the two native dispatch functions, once."""
    if getattr(native, "_delta_footer_restore_v17_installed", False):
        return
    native.callback = callback
    native.message = message
    native._delta_footer_restore_v17_installed = True
