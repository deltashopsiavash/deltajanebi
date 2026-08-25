import os

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from shop.management.commands import telegram_bot as old
from shop.management.commands import telegram_bot_v3 as v3
from shop.management.commands import telegram_bot_v4 as v4
from shop.management.commands import telegram_bot_v5 as v5
from shop.models import Product, SourceSite
from shop.services.source_sanitizer import normalize_brand_terms
from shop.source_registry import normalize_site_url


def source_text(site):
    count = Product.objects.filter(source_type=Product.SYNCED, source_url__icontains=site.hostname).count()
    terms = site.brand_terms or "تنظیم نشده"
    return (
        f"🌐 {site.name}\n"
        f"دامنه: {site.hostname}\n"
        f"آدرس پایه: {site.base_url}\n"
        f"وضعیت: {'✅ فعال' if site.is_active else '⛔ غیرفعال'}\n"
        f"🧹 عبارت‌های حذف‌شونده: {terms}\n"
        f"🖼 پاکسازی تبلیغات عکس: ✅ خودکار\n"
        f"محصول خاص مرتبط: {count}"
    )


def source_actions(site):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ تغییر نام", callback_data=f"source:name:{site.id}"), InlineKeyboardButton("🧹 عبارات پاکسازی", callback_data=f"source:terms:{site.id}")],
        [InlineKeyboardButton("⏯ فعال / غیرفعال", callback_data=f"source:toggle:{site.id}"), InlineKeyboardButton("🗑 حذف سایت", callback_data=f"source:delask:{site.id}")],
        [InlineKeyboardButton("⬅️ سایت‌های منبع", callback_data="source:list"), InlineKeyboardButton("🏠 منوی اصلی", callback_data="main")],
    ])


async def on_callback(update: Update, context):
    if not old.allowed(update):
        return
    q = update.callback_query
    data = q.data or ""

    if data.startswith("source:terms:"):
        await q.answer()
        site_id = int(data.rsplit(":", 1)[1])
        site = await sync_to_async(SourceSite.objects.get)(pk=site_id)
        old.set_state(context, "source_edit_terms_v6", source_id=site_id)
        await q.message.reply_text(
            f"🧹 عبارت‌های فعلی:\n{site.brand_terms or '—'}\n\n"
            "هر نوشته‌ای که نباید از سایت منبع وارد محصول تو شود را بفرست.\n"
            "مثال: همراه دوم\n"
            "اگر چند عبارت داری با کاما بفرست: همراه دوم, فروشگاه همراه دوم, HAMRAHEDOVOM\n"
            "برای خالی‌کردن این لیست - بفرست."
        )
        return

    await v4.on_callback(update, context)


async def on_message(update: Update, context):
    if not old.allowed(update):
        return
    message = update.effective_message
    state = context.user_data.get("awaiting")
    flow = context.user_data.setdefault("flow", {})
    text = message.text.strip() if message.text else ""

    if state == "source_add_url":
        try:
            base_url, hostname = await sync_to_async(normalize_site_url)(text)
        except ValueError as exc:
            await message.reply_text(f"❌ {exc}\nدوباره آدرس سایت را بفرست.")
            return

        site = await sync_to_async(SourceSite.objects.filter(hostname=hostname).first)()
        if site:
            site.base_url = base_url
            site.is_active = True
            await sync_to_async(site.save)(update_fields=["base_url", "is_active"])
            created = False
        else:
            site = await sync_to_async(SourceSite.objects.create)(
                name=hostname,
                base_url=base_url,
                hostname=hostname,
                brand_terms="",
                is_active=True,
            )
            created = True

        old.set_state(context, "source_add_terms_v6", source_id=site.id, created=created)
        await message.reply_text(
            f"✅ دامنه {hostname} {'ثبت شد' if created else 'از قبل وجود داشت و فعال شد'}.\n\n"
            "حالا بگو چه اسم/نوشته تبلیغاتی مربوط به همین سایت باید از محصولات پاک شود.\n"
            "مثلاً برای همراه دوم بفرست:\nهمراه دوم\n\n"
            "برای سایتی مثل مریوان فون بفرست:\nمریوان فون\n\n"
            "اگر چند عبارت مختلف دارد با کاما جدا کن."
        )
        return

    if state == "source_add_terms_v6":
        site = await sync_to_async(SourceSite.objects.get)(pk=int(flow["source_id"]))
        site.brand_terms = "" if text == "-" else normalize_brand_terms(text)
        await sync_to_async(site.save)(update_fields=["brand_terms"])
        old.clear_state(context)
        await message.reply_text(
            "✅ سایت منبع آماده شد. از این به بعد هنگام واردکردن محصول از این سایت، "
            "این عبارت‌ها از نام، توضیحات، مشخصات و دسته‌ها حذف می‌شوند و تصاویر محصول هم برای تبلیغات حاشیه‌ای پاکسازی می‌شوند.\n\n"
            + await sync_to_async(source_text)(site),
            reply_markup=source_actions(site),
        )
        return

    if state == "source_edit_terms_v6":
        site = await sync_to_async(SourceSite.objects.get)(pk=int(flow["source_id"]))
        site.brand_terms = "" if text == "-" else normalize_brand_terms(text)
        await sync_to_async(site.save)(update_fields=["brand_terms"])
        old.clear_state(context)
        await message.reply_text("✅ عبارت‌های پاکسازی تغییر کرد.\n\n" + await sync_to_async(source_text)(site), reply_markup=source_actions(site))
        return

    await v4.on_message(update, context)


class Command(BaseCommand):
    def handle(self, *args, **opts):
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            self.stderr.write("TELEGRAM_BOT_TOKEN is empty")
            return

        old.main_menu = v5.main_menu
        old.settings_menu = v3.settings_menu
        v4.show_categories = v5.show_categories
        v4.source_text = source_text
        v4.source_actions = source_actions

        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler("start", old.start))
        app.add_handler(CommandHandler("cancel", old.cancel))
        app.add_handler(MessageHandler(filters.Regex(r"^/order_\d+$"), old.order_cmd))
        app.add_handler(CallbackQueryHandler(old.order_callback, pattern=r"^order:\d+:(preparing|shipped|delivered|cancelled)$"))
        app.add_handler(CallbackQueryHandler(on_callback))
        app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, on_message))
        app.run_polling(allowed_updates=Update.ALL_TYPES)
