import os
import re
import uuid

from asgiref.sync import sync_to_async
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from telegram import Update
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


def _save_mobile_banner(banner, raw=None, filename=None, link=""):
    if raw and filename:
        banner.mobile_image.save(filename, ContentFile(raw), save=False)
        banner.mobile_image_url = ""
    else:
        banner.mobile_image = ""
        banner.mobile_image_url = link
    banner.save(update_fields=["mobile_image", "mobile_image_url"])
    return banner


async def on_callback(update, context):
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

    if state == "banner_mobile_image":
        banner_id = int(flow["banner_id"])
        banner = await sync_to_async(Banner.objects.get)(pk=banner_id)
        if text == "-":
            old.clear_state(context)
            await message.reply_text(
                f"✅ بنر #{banner.id} ثبت شد.\n"
                "نسخه موبایل از عکس دسکتاپ استفاده می‌کند و تصویر بریده نمی‌شود.",
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
            f"✅ بنر #{banner.id} با عکس جدا برای دسکتاپ و موبایل ثبت شد.",
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
