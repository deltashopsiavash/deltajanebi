import os
import re

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from shop.models import SiteSetting
from shop.management.commands import telegram_bot as old
from shop.management.commands import telegram_bot_v2 as v2


def settings_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ نام سایت", callback_data="set:name"), InlineKeyboardButton("🖼 لوگوی سایت", callback_data="set:logo")],
        [InlineKeyboardButton("☎️ تلفن", callback_data="set:phone"), InlineKeyboardButton("🚚 هزینه ارسال", callback_data="set:shipping")],
        [InlineKeyboardButton("🌐 شبکه‌های اجتماعی", callback_data="social:list"), InlineKeyboardButton("📣 بنرهای تبلیغاتی", callback_data="banner:list")],
        [InlineKeyboardButton("⬅️ منوی اصلی", callback_data="main")],
    ])


def normalize_phone(value):
    text = str(value or "").strip()
    text = re.sub(r"^tel:\s*", "", text, flags=re.I)
    text = text.replace(" ", "").replace("-", "")
    if not re.fullmatch(r"\+?[0-9]{5,20}", text):
        raise ValueError("invalid phone")
    return text


async def on_callback(update, context):
    if not old.allowed(update):
        return
    q = update.callback_query
    data = q.data or ""

    if data == "set:phone":
        await q.answer()
        old.set_state(context, "set_phone")
        site = await sync_to_async(SiteSetting.load)()
        current = site.phone or "ثبت نشده"
        await q.message.reply_text(
            f"☎️ تلفن فعلی: {current}\n\n"
            "شماره جدید را بفرست؛ مثال:\n"
            "+989121234567\n"
            "یا:\n"
            "tel:+989121234567\n\n"
            "کاربر با لمس آیکن تلفن مستقیم وارد تماس می‌شود."
        )
        return

    await v2.on_callback(update, context)


async def on_message(update, context):
    if not old.allowed(update):
        return

    state = context.user_data.get("awaiting")
    message = update.effective_message
    text = message.text.strip() if message.text else ""

    if state == "set_phone":
        try:
            phone = normalize_phone(text)
        except ValueError:
            await message.reply_text("❌ شماره معتبر نیست. مثال: +989121234567 یا tel:+989121234567")
            return
        site = await sync_to_async(SiteSetting.load)()
        site.phone = phone
        await sync_to_async(site.save)(update_fields=["phone"])
        old.clear_state(context)
        await message.reply_text(
            f"✅ تلفن سایت ذخیره شد:\n{phone}\n\nآیکن تلفن بالای موبایل و «تماس با ما» پایین سایت از همین شماره استفاده می‌کنند.",
            reply_markup=settings_menu(),
        )
        return

    await v2.on_message(update, context)


class Command(BaseCommand):
    def handle(self, *args, **opts):
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            self.stderr.write("TELEGRAM_BOT_TOKEN is empty")
            return

        # Patch the original menu so every delegated settings response also includes Phone.
        old.settings_menu = settings_menu

        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler("start", old.start))
        app.add_handler(CommandHandler("cancel", old.cancel))
        app.add_handler(MessageHandler(filters.Regex(r"^/order_\d+$"), old.order_cmd))
        app.add_handler(CallbackQueryHandler(old.order_callback, pattern=r"^order:\d+:(preparing|shipped|delivered|cancelled)$"))
        app.add_handler(CallbackQueryHandler(on_callback))
        app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, on_message))
        app.run_polling(allowed_updates=Update.ALL_TYPES)
