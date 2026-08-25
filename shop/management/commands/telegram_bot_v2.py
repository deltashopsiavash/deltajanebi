import os
import re

from asgiref.sync import sync_to_async
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from shop.models import Banner
from shop.management.commands import telegram_bot as old


def _save_desktop_banner(flow, raw=None, filename=None, link=""):
    banner = Banner(title=flow.get("title", ""), target_url=flow.get("target_url", ""))
    if raw and filename:
        banner.image.save(filename, ContentFile(raw), save=False)
    else:
        banner.image_url = link
    banner.save()
    return banner


def _save_mobile_banner(banner, raw=None, filename=None, link="", clear=False):
    if banner.mobile_image:
        try:
            banner.mobile_image.delete(save=False)
        except Exception:
            pass
    if clear:
        banner.mobile_image = ""
        banner.mobile_image_url = ""
    elif raw and filename:
        banner.mobile_image.save(filename, ContentFile(raw), save=False)
        banner.mobile_image_url = ""
    else:
        banner.mobile_image = ""
        banner.mobile_image_url = link
    banner.save(update_fields=["mobile_image", "mobile_image_url"])
    return banner


async def on_callback(update, context):
    if not old.allowed(update):
        return
    q = update.callback_query
    data = q.data or ""

    if data == "banner:list":
        await q.answer()
        rows = await sync_to_async(list)(Banner.objects.all()[:20])
        buttons = []
        for banner in rows:
            title = banner.title or f"بنر #{banner.id}"
            buttons.append([
                InlineKeyboardButton(f"{'✅' if banner.is_active else '⛔'} {title[:22]}", callback_data=f"banner:toggle:{banner.id}"),
                InlineKeyboardButton("📱 عکس موبایل", callback_data=f"banner:mobile:{banner.id}"),
                InlineKeyboardButton("🗑", callback_data=f"banner:delete:{banner.id}"),
            ])
        buttons.append([InlineKeyboardButton("➕ افزودن بنر", callback_data="banner:add")])
        buttons.append([InlineKeyboardButton("⬅️ تنظیمات", callback_data="settings")])
        await q.message.reply_text("📣 بنرهای صفحه اول\nبرای هر بنر می‌توانی عکس موبایل جدا بگذاری.", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("banner:mobile:"):
        await q.answer()
        banner_id = int(data.rsplit(":", 1)[1])
        banner = await sync_to_async(Banner.objects.get)(pk=banner_id)
        old.set_state(context, "banner_existing_mobile", banner_id=banner.id)
        await q.message.reply_text(
            f"📱 عکس موبایل برای «{banner.title or ('بنر #' + str(banner.id))}» را بفرست.\n"
            "اندازه پیشنهادی: 1080×420\n"
            "می‌توانی عکس مستقیم، فایل تصویر یا لینک بفرستی.\n"
            "برای حذف عکس موبایل و استفاده از نسخه دسکتاپ، - بفرست."
        )
        return

    await old.on_callback(update, context)


async def on_message(update, context):
    if not old.allowed(update):
        return

    message = update.effective_message
    state = context.user_data.get("awaiting")
    flow = context.user_data.setdefault("flow", {})
    text = message.text.strip() if message.text else ""

    if state == "banner_image":
        try:
            raw, filename = await old.telegram_image_bytes(message, "banner-desktop")
        except ValueError as exc:
            await message.reply_text(f"❌ {exc}")
            return

        link = ""
        if not raw:
            if not re.match(r"^https?://", text, re.I):
                await message.reply_text("عکس دسکتاپ را مستقیم بفرست یا لینک معتبر عکس ارسال کن.")
                return
            link = text

        banner = await sync_to_async(_save_desktop_banner)(flow, raw, filename, link)
        old.set_state(context, "banner_mobile_image", banner_id=banner.id)
        await message.reply_text(
            "✅ عکس دسکتاپ ذخیره شد.\n\n"
            "حالا عکس مخصوص موبایل را بفرست.\n"
            "اندازه پیشنهادی موبایل: 1080×420\n"
            "اگر عکس جدا برای موبایل نمی‌خواهی، - بفرست تا همان عکس دسکتاپ بدون برش استفاده شود."
        )
        return

    if state in ("banner_mobile_image", "banner_existing_mobile"):
        banner_id = int(flow["banner_id"])
        banner = await sync_to_async(Banner.objects.get)(pk=banner_id)
        if text == "-":
            await sync_to_async(_save_mobile_banner)(banner, clear=True)
            old.clear_state(context)
            await message.reply_text(
                f"✅ بنر #{banner.id} از عکس دسکتاپ در موبایل استفاده می‌کند و تصویر crop نمی‌شود.",
                reply_markup=old.settings_menu(),
            )
            return

        try:
            raw, filename = await old.telegram_image_bytes(message, "banner-mobile")
        except ValueError as exc:
            await message.reply_text(f"❌ {exc}")
            return

        link = ""
        if not raw:
            if not re.match(r"^https?://", text, re.I):
                await message.reply_text("عکس موبایل، لینک معتبر عکس یا - بفرست.")
                return
            link = text

        await sync_to_async(_save_mobile_banner)(banner, raw, filename, link)
        old.clear_state(context)
        await message.reply_text(
            f"✅ عکس موبایل بنر #{banner.id} ذخیره شد.",
            reply_markup=old.settings_menu(),
        )
        return

    await old.on_message(update, context)


class Command(BaseCommand):
    def handle(self, *args, **opts):
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            self.stderr.write("TELEGRAM_BOT_TOKEN is empty")
            return

        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler("start", old.start))
        app.add_handler(CommandHandler("cancel", old.cancel))
        app.add_handler(MessageHandler(filters.Regex(r"^/order_\d+$"), old.order_cmd))
        app.add_handler(CallbackQueryHandler(old.order_callback, pattern=r"^order:\d+:(preparing|shipped|delivered|cancelled)$"))
        app.add_handler(CallbackQueryHandler(on_callback))
        app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, on_message))
        app.run_polling(allowed_updates=Update.ALL_TYPES)
