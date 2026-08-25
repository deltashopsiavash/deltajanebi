import os
import re
import uuid

from asgiref.sync import sync_to_async
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from shop.models import SiteSetting, TrustBadge
from shop.management.commands import telegram_bot as old
from shop.management.commands import telegram_bot_v2 as v2


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧰 محصولات عادی", callback_data="m:manual"), InlineKeyboardButton("🔗 محصولات خاص", callback_data="m:synced")],
        [InlineKeyboardButton("🔎 جستجوی محصول", callback_data="search"), InlineKeyboardButton("⭐ پیشنهادهای فعال", callback_data="offers")],
        [InlineKeyboardButton("📦 سفارش‌ها", callback_data="orders"), InlineKeyboardButton("📂 دسته‌بندی‌ها", callback_data="categories")],
        [InlineKeyboardButton("🔄 همگام‌سازی همه", callback_data="syncall"), InlineKeyboardButton("⚙️ تنظیمات سایت", callback_data="settings")],
        [InlineKeyboardButton("📝 توضیحات و فوتر", callback_data="set:footer"), InlineKeyboardButton("🛡 نمادها", callback_data="badge:list")],
        [InlineKeyboardButton("☎️ تلفن", callback_data="set:phone"), InlineKeyboardButton("💾 بکاپ", callback_data="backup")],
    ])


def settings_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ نام سایت", callback_data="set:name"), InlineKeyboardButton("🖼 لوگوی سایت", callback_data="set:logo")],
        [InlineKeyboardButton("☎️ تلفن", callback_data="set:phone"), InlineKeyboardButton("🚚 هزینه ارسال", callback_data="set:shipping")],
        [InlineKeyboardButton("📝 توضیحات و فوتر", callback_data="set:footer"), InlineKeyboardButton("🛡 نمادها", callback_data="badge:list")],
        [InlineKeyboardButton("🌐 شبکه‌های اجتماعی", callback_data="social:list"), InlineKeyboardButton("📣 بنرهای تبلیغاتی", callback_data="banner:list")],
        [InlineKeyboardButton("⬅️ منوی اصلی", callback_data="main")],
    ])


def badge_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟦 اینماد", callback_data="badge:set:enamad"), InlineKeyboardButton("🟨 زرین‌پال", callback_data="badge:set:zarinpal")],
        [InlineKeyboardButton("⬅️ تنظیمات", callback_data="settings")],
    ])


def normalize_phone(value):
    text = str(value or "").strip()
    text = re.sub(r"^tel:\s*", "", text, flags=re.I)
    text = text.replace(" ", "").replace("-", "")
    if not re.fullmatch(r"\+?[0-9]{5,20}", text):
        raise ValueError("invalid phone")
    return text


def save_badge(badge_type, target_url, raw=None, filename=None, image_url="", clear=False):
    badge, _ = TrustBadge.objects.get_or_create(badge_type=badge_type)
    if badge.image:
        try:
            badge.image.delete(save=False)
        except Exception:
            pass
    badge.target_url = target_url
    badge.is_active = True
    if clear:
        badge.image = ""
        badge.image_url = ""
    elif raw and filename:
        badge.image.save(filename, ContentFile(raw), save=False)
        badge.image_url = ""
    else:
        badge.image = ""
        badge.image_url = image_url
    badge.save()
    return badge


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not old.allowed(update):
        return
    q = update.callback_query
    data = q.data or ""

    if data == "set:phone":
        await q.answer()
        old.set_state(context, "set_phone")
        site = await sync_to_async(SiteSetting.load)()
        await q.message.reply_text(
            f"☎️ شماره تماس دکمه‌های سایت: {site.phone or 'ثبت نشده'}\n\n"
            "این شماره فقط برای دکمه‌های تماس بالای موبایل و نوار پایین استفاده می‌شود.\n"
            "شماره جدید را بفرست؛ مثال +989121234567 یا tel:+989121234567"
        )
        return

    if data == "set:footer":
        await q.answer()
        site = await sync_to_async(SiteSetting.load)()
        old.set_state(context, "footer_address")
        await q.message.reply_text(
            "📝 تنظیم توضیحات و فوتر سایت\n\n"
            f"آدرس فعلی:\n{site.address or 'ثبت نشده'}\n\n"
            "آدرس جدید را بفرست. برای خالی‌کردن - بفرست."
        )
        return

    if data == "badge:list":
        await q.answer()
        enamad = await sync_to_async(TrustBadge.objects.filter(badge_type=TrustBadge.ENAMAD).first)()
        zarin = await sync_to_async(TrustBadge.objects.filter(badge_type=TrustBadge.ZARINPAL).first)()
        text = (
            "🛡 نمادهای اعتماد\n\n"
            f"اینماد: {'✅ ثبت شده' if enamad and enamad.image_src else '⬜ خالی'}\n"
            f"زرین‌پال: {'✅ ثبت شده' if zarin and zarin.image_src else '⬜ خالی'}\n\n"
            "روی هر مورد بزن تا لینک و تصویر واقعی نماد را ثبت کنی."
        )
        await q.message.reply_text(text, reply_markup=badge_menu())
        return

    if data.startswith("badge:set:"):
        await q.answer()
        badge_type = data.rsplit(":", 1)[1]
        label = "اینماد" if badge_type == TrustBadge.ENAMAD else "زرین‌پال"
        old.set_state(context, "badge_target", badge_type=badge_type)
        await q.message.reply_text(
            f"🛡 ثبت {label}\n\n"
            "اول لینک مقصد/اعتبارسنجی نماد را بفرست (https://...).\n"
            "اگر فعلاً لینک نداری - بفرست."
        )
        return

    await v2.on_callback(update, context)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not old.allowed(update):
        return

    state = context.user_data.get("awaiting")
    flow = context.user_data.setdefault("flow", {})
    message = update.effective_message
    text = message.text.strip() if message.text else ""

    if state == "set_phone":
        try:
            phone = normalize_phone(text)
        except ValueError:
            await message.reply_text("❌ شماره معتبر نیست. مثال: +989121234567")
            return
        site = await sync_to_async(SiteSetting.load)()
        site.phone = phone
        await sync_to_async(site.save)(update_fields=["phone"])
        old.clear_state(context)
        await message.reply_text(f"✅ شماره دکمه تماس ذخیره شد: {phone}", reply_markup=settings_menu())
        return

    if state == "footer_address":
        flow["address"] = "" if text == "-" else text
        context.user_data["awaiting"] = "footer_phone"
        await message.reply_text("☎️ شماره تماسی که داخل فوتر نمایش داده شود را بفرست. برای خالی‌کردن - بفرست.")
        return

    if state == "footer_phone":
        if text == "-":
            flow["footer_phone"] = ""
        else:
            try:
                flow["footer_phone"] = normalize_phone(text)
            except ValueError:
                await message.reply_text("❌ شماره معتبر نیست. مثال: +989121234567 یا -")
                return
        context.user_data["awaiting"] = "footer_email"
        await message.reply_text("📧 ایمیل تماس فروشگاه را بفرست. برای خالی‌کردن - بفرست.")
        return

    if state == "footer_email":
        if text != "-" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", text):
            await message.reply_text("❌ ایمیل معتبر نیست. مثال: info@example.com یا -")
            return
        flow["contact_email"] = "" if text == "-" else text
        context.user_data["awaiting"] = "footer_description"
        await message.reply_text("✍️ متن معرفی فروشگاه را بفرست؛ همون متنی که پایین سایت زیر نام فروشگاه نمایش داده می‌شود. برای خالی‌کردن - بفرست.")
        return

    if state == "footer_description":
        flow["footer_description"] = "" if text == "-" else text
        context.user_data["awaiting"] = "footer_support"
        await message.reply_text("🕘 متن پشتیبانی/ساعات پاسخگویی را بفرست؛ مثال: شنبه تا پنج‌شنبه در ساعات اداری پاسخگوی شما هستیم. برای خالی‌کردن - بفرست.")
        return

    if state == "footer_support":
        site = await sync_to_async(SiteSetting.load)()
        site.address = flow.get("address", "")
        site.footer_phone = flow.get("footer_phone", "")
        site.contact_email = flow.get("contact_email", "")
        site.footer_description = flow.get("footer_description", "")
        site.support_text = "" if text == "-" else text[:240]
        await sync_to_async(site.save)(update_fields=["address", "footer_phone", "contact_email", "footer_description", "support_text"])
        old.clear_state(context)
        await message.reply_text("✅ اطلاعات فوتر ذخیره شد.", reply_markup=settings_menu())
        return

    if state == "badge_target":
        if text != "-" and not re.match(r"^https?://", text, re.I):
            await message.reply_text("❌ لینک باید با http:// یا https:// شروع شود؛ یا - بفرست.")
            return
        flow["target_url"] = "" if text == "-" else text
        context.user_data["awaiting"] = "badge_image"
        label = "اینماد" if flow["badge_type"] == TrustBadge.ENAMAD else "زرین‌پال"
        await message.reply_text(f"حالا تصویر واقعی {label} را مستقیم به ربات بفرست، فایل تصویر یا لینک مستقیم عکس بفرست. برای پاک‌کردن نماد فعلی - بفرست.")
        return

    if state == "badge_image":
        badge_type = flow["badge_type"]
        if text == "-":
            await sync_to_async(save_badge)(badge_type, flow.get("target_url", ""), clear=True)
            old.clear_state(context)
            await message.reply_text("✅ جای نماد خالی شد.", reply_markup=badge_menu())
            return
        try:
            raw, filename = await old.telegram_image_bytes(message, f"trust-{badge_type}")
        except ValueError as exc:
            await message.reply_text(f"❌ {exc}")
            return
        image_url = ""
        if not raw:
            if not re.match(r"^https?://", text, re.I):
                await message.reply_text("❌ عکس مستقیم، فایل تصویر یا لینک مستقیم عکس بفرست.")
                return
            image_url = text
        filename = filename or f"trust-{badge_type}-{uuid.uuid4().hex}.jpg"
        await sync_to_async(save_badge)(badge_type, flow.get("target_url", ""), raw, filename, image_url)
        old.clear_state(context)
        await message.reply_text("✅ نماد روی سایت ثبت شد.", reply_markup=badge_menu())
        return

    await v2.on_message(update, context)


class Command(BaseCommand):
    def handle(self, *args, **opts):
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            self.stderr.write("TELEGRAM_BOT_TOKEN is empty")
            return

        old.main_menu = main_menu
        old.settings_menu = settings_menu

        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler("start", old.start))
        app.add_handler(CommandHandler("cancel", old.cancel))
        app.add_handler(MessageHandler(filters.Regex(r"^/order_\d+$"), old.order_cmd))
        app.add_handler(CallbackQueryHandler(old.order_callback, pattern=r"^order:\d+:(preparing|shipped|delivered|cancelled)$"))
        app.add_handler(CallbackQueryHandler(on_callback))
        app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, on_message))
        app.run_polling(allowed_updates=Update.ALL_TYPES)
