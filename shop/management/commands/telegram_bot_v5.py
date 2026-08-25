import os

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from shop.management.commands import telegram_bot as old
from shop.management.commands import telegram_bot_v3 as v3
from shop.management.commands import telegram_bot_v4 as v4
from shop.models import Category


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧰 محصولات عادی", callback_data="m:manual"), InlineKeyboardButton("🔗 محصولات خاص", callback_data="m:synced")],
        [InlineKeyboardButton("🔎 جستجوی محصول", callback_data="search"), InlineKeyboardButton("⭐ پیشنهادهای فعال", callback_data="offers")],
        [InlineKeyboardButton("📦 سفارش‌ها", callback_data="orders"), InlineKeyboardButton("📂 دسته‌بندی‌ها", callback_data="categories")],
        [InlineKeyboardButton("🌐 ثبت سایت", callback_data="source:list"), InlineKeyboardButton("🔄 همگام‌سازی همه", callback_data="syncall")],
        [InlineKeyboardButton("⚙️ تنظیمات سایت", callback_data="settings"), InlineKeyboardButton("📝 توضیحات و فوتر", callback_data="set:footer")],
        [InlineKeyboardButton("🛡 نمادها", callback_data="badge:list"), InlineKeyboardButton("☎️ تلفن", callback_data="set:phone")],
        [InlineKeyboardButton("💾 بکاپ", callback_data="backup")],
    ])


def _category_page(page):
    page = max(0, int(page))
    start = page * v4.PAGE_SIZE
    rows = list(Category.objects.order_by("parent_id", "order", "name")[start:start + v4.PAGE_SIZE + 1])
    has_next = len(rows) > v4.PAGE_SIZE
    result = []
    for category in rows[:v4.PAGE_SIZE]:
        result.append((category.id, category.is_active, v4.category_path(category)))
    return result, has_next


async def show_categories(message, page=0):
    page = max(0, int(page))
    rows, has_next = await sync_to_async(_category_page)(page)
    buttons = []
    for category_id, is_active, path in rows:
        prefix = "✅" if is_active else "⛔"
        buttons.append([InlineKeyboardButton(f"{prefix} {path[:46]}", callback_data=f"cat:view:{category_id}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"cat:list:{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"cat:list:{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🏠 منوی اصلی", callback_data="main")])
    await message.reply_text(
        f"📂 مدیریت همه دسته‌بندی‌ها — صفحه {page + 1}\nروی هر دسته بزن تا نام، عکس، نمایش یا حذفش را مدیریت کنی:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


v4.show_categories = show_categories


class Command(BaseCommand):
    def handle(self, *args, **opts):
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            self.stderr.write("TELEGRAM_BOT_TOKEN is empty")
            return

        old.main_menu = main_menu
        old.settings_menu = v3.settings_menu

        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler("start", old.start))
        app.add_handler(CommandHandler("cancel", old.cancel))
        app.add_handler(MessageHandler(filters.Regex(r"^/order_\d+$"), old.order_cmd))
        app.add_handler(CallbackQueryHandler(old.order_callback, pattern=r"^order:\d+:(preparing|shipped|delivered|cancelled)$"))
        app.add_handler(CallbackQueryHandler(v4.on_callback))
        app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, v4.on_message))
        app.run_polling(allowed_updates=Update.ALL_TYPES)
