#!/usr/bin/env python3
"""Reliable desktop/mobile banner controls for the external Delta bot.

The storefront renders desktop banners at a 6:1 aspect ratio and mobile banners
at 18:7. This layer makes those target sizes explicit in every relevant banner
flow and adds the missing desktop-image replacement action for existing banners.
"""
import base64
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import delta_bot_native as native

_ORIGINAL_CALLBACK = native.callback
_ORIGINAL_MESSAGE = native.message
_ORIGINAL_MEDIA = native.media

DESKTOP_SIZE = "1800×300"
DESKTOP_RATIO = "6:1"
MOBILE_SIZE = "1080×420"
MOBILE_RATIO = "18:7"


def _parts(data):
    return str(data or "").split(":")


def _valid(data, command, min_parts=3):
    parts = _parts(data)
    return len(parts) >= min_parts and parts[0] == "d" and parts[1] == command and parts[2].isdigit()


def _banner_back(sid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 بنرها", callback_data=f"d:banners:{sid}")]
    ])


def _desktop_prompt():
    return (
        "🖥 عکس دسکتاپ بنر را بفرست؛ عکس/فایل یا لینک http/https:\n\n"
        f"📐 سایز پیشنهادی دسکتاپ: {DESKTOP_SIZE} پیکسل\n"
        f"↔️ نسبت تصویر: {DESKTOP_RATIO}\n"
        "برای بهترین نمایش، دقیقاً با همین نسبت طراحی کن."
    )


def _mobile_prompt():
    return (
        "📱 عکس مخصوص موبایل را بفرست؛ عکس/فایل یا لینک http/https:\n\n"
        f"📐 سایز پیشنهادی موبایل: {MOBILE_SIZE} پیکسل\n"
        f"↔️ نسبت تصویر: {MOBILE_RATIO}\n"
        "برای بهترین نمایش، دقیقاً با همین نسبت طراحی کن."
    )


async def _show_banners(q, site, sid):
    rows = (await native.core.api(site, "banners"))["data"]
    keys = []
    for item in rows:
        bid = int(item["id"])
        title = str(item.get("title") or f"بنر #{bid}")[:31]
        keys.append([
            InlineKeyboardButton(
                f"{'✅' if item.get('is_active') else '⛔'} {title}",
                callback_data=f"d:bannertoggle:{sid}:{bid}",
            )
        ])
        keys.append([
            InlineKeyboardButton("🖥 دسکتاپ", callback_data=f"d:bannerdesktop:{sid}:{bid}"),
            InlineKeyboardButton("📱 موبایل", callback_data=f"d:bannermobile:{sid}:{bid}"),
            InlineKeyboardButton("🗑 حذف", callback_data=f"d:bannerdelete:{sid}:{bid}"),
        ])
    keys += [
        [InlineKeyboardButton("➕ افزودن بنر", callback_data=f"d:banneradd:{sid}")],
        [InlineKeyboardButton("⬅️ تنظیمات", callback_data=f"d:settings:{sid}")],
    ]
    await native._edit(
        q,
        "📣 بنرهای تبلیغاتی\n\n"
        f"🖥 دسکتاپ: {DESKTOP_SIZE} px — نسبت {DESKTOP_RATIO}\n"
        f"📱 موبایل: {MOBILE_SIZE} px — نسبت {MOBILE_RATIO}\n\n"
        "روی «دسکتاپ» یا «موبایل» هر بنر بزن تا همان تصویر را جداگانه عوض کنی. "
        "دکمه عنوان/وضعیت، فعال یا غیرفعال بودن بنر را تغییر می‌دهد.",
        InlineKeyboardMarkup(keys),
    )
    return True


async def callback(update, context):
    q = update.callback_query
    data = q.data or ""

    if _valid(data, "banners"):
        sid = int(_parts(data)[2])
        site = native._site(q.from_user.id, sid)
        if not site:
            await q.answer("عدم دسترسی", show_alert=True)
            return True
        await q.answer()
        return await _show_banners(q, site, sid)

    if _valid(data, "bannerdesktop", 4):
        parts = _parts(data)
        if not parts[3].isdigit():
            return await _ORIGINAL_CALLBACK(update, context)
        sid = int(parts[2])
        site = native._site(q.from_user.id, sid)
        if not site:
            await q.answer("عدم دسترسی", show_alert=True)
            return True
        native._flow(context, "d_banner_desktop", sid, banner_id=int(parts[3]))
        await q.answer()
        await native._edit(q, _desktop_prompt(), _banner_back(sid))
        return True

    if _valid(data, "bannermobile", 4):
        parts = _parts(data)
        if not parts[3].isdigit():
            return await _ORIGINAL_CALLBACK(update, context)
        sid = int(parts[2])
        site = native._site(q.from_user.id, sid)
        if not site:
            await q.answer("عدم دسترسی", show_alert=True)
            return True
        native._flow(context, "d_banner_mobile", sid, banner_id=int(parts[3]))
        await q.answer()
        await native._edit(q, _mobile_prompt(), _banner_back(sid))
        return True

    return await _ORIGINAL_CALLBACK(update, context)


async def message(update, context):
    flow = context.user_data.get("flow") or ""
    if context.user_data.get("platform") != "deltajanebi":
        return await _ORIGINAL_MESSAGE(update, context)

    sid = context.user_data.get("site_id")
    site = native._site(update.effective_user.id, sid) if sid else None
    if not site:
        return await _ORIGINAL_MESSAGE(update, context)
    text = str(getattr(update.message, "text", "") or "").strip()

    # Keep the existing add-banner flow, but make the required desktop geometry
    # visible at the exact step where the user must upload the image.
    if flow == "d_banner_target":
        context.user_data.update(
            flow="d_banner_image",
            banner_target="" if text == "-" else text,
        )
        await update.message.reply_text(_desktop_prompt())
        return True

    if flow == "d_banner_desktop":
        if not re.match(r"^https?://", text, re.I):
            await update.message.reply_text(_desktop_prompt())
            return True
        bid = int(context.user_data["banner_id"])
        await native.core.api(
            site,
            "delta_banner_media_set",
            {"id": bid, "mobile": False, "image_url": text},
            timeout=90,
        )
        context.user_data.clear()
        await update.message.reply_text(
            "✅ عکس دسکتاپ بنر تغییر کرد.",
            reply_markup=_banner_back(sid),
        )
        return True

    if flow == "d_banner_mobile":
        if not re.match(r"^https?://", text, re.I):
            await update.message.reply_text(_mobile_prompt())
            return True
        bid = int(context.user_data["banner_id"])
        await native.core.api(
            site,
            "delta_banner_media_set",
            {"id": bid, "mobile": True, "image_url": text},
            timeout=90,
        )
        context.user_data.clear()
        await update.message.reply_text(
            "✅ عکس موبایل بنر تغییر کرد.",
            reply_markup=_banner_back(sid),
        )
        return True

    if flow == "d_banner_image" and text and not re.match(r"^https?://", text, re.I):
        await update.message.reply_text(_desktop_prompt())
        return True

    return await _ORIGINAL_MESSAGE(update, context)


async def media(update, context):
    flow = context.user_data.get("flow") or ""
    if context.user_data.get("platform") != "deltajanebi" or flow != "d_banner_desktop":
        return await _ORIGINAL_MEDIA(update, context)

    sid = context.user_data.get("site_id")
    site = native._site(update.effective_user.id, sid) if sid else None
    if not site:
        return await _ORIGINAL_MEDIA(update, context)
    try:
        raw, name = await native._download_media(update.message)
        bid = int(context.user_data["banner_id"])
        await native.core.api(
            site,
            "delta_banner_media_set",
            {
                "id": bid,
                "mobile": False,
                "image_b64": base64.b64encode(raw).decode(),
                "image_filename": name,
            },
            timeout=90,
        )
        context.user_data.clear()
        await update.message.reply_text(
            "✅ عکس دسکتاپ بنر تغییر کرد.",
            reply_markup=_banner_back(sid),
        )
        return True
    except Exception as exc:
        await update.message.reply_text(
            f"❌ تغییر عکس دسکتاپ ناموفق بود: {str(exc)[:700]}\n\n{_desktop_prompt()}"
        )
        return True


def install():
    if getattr(native, "_delta_banner_controls_v31_installed", False):
        return
    native.callback = callback
    native.message = message
    native.media = media
    native._delta_banner_controls_v31_installed = True
