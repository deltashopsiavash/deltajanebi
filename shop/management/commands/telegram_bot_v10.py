import os

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from shop.management.commands import telegram_bot as old
from shop.management.commands import telegram_bot_v3 as v3
from shop.management.commands import telegram_bot_v4 as v4
from shop.management.commands import telegram_bot_v5 as v5
from shop.management.commands import telegram_bot_v6 as v6
from shop.management.commands import telegram_bot_v7 as v7
from shop.management.commands import telegram_bot_v8 as v8
from shop.management.commands import telegram_bot_v9 as v9
from shop.models import Order, SiteSetting
from shop.services.order_workflow import email_customer, mark_paid, order_report_text
from shop.services.telegram_notify import notify_admins


async def show_commerce(message):
    site = await sync_to_async(SiteSetting.load)()
    free_label = f"{site.free_shipping_threshold:,} تومان" if site.free_shipping_threshold else "غیرفعال"
    text = (
        "💳 تنظیمات پرداخت و سفارش\n\n"
        f"🏦 کارت به کارت: {'✅ روشن' if site.card_payment_enabled else '⛔ خاموش'}\n"
        f"شماره کارت: {site.card_number or '-'}\n"
        f"صاحب حساب: {site.card_owner or '-'}\n\n"
        f"💳 زرین‌پال: {'✅ روشن' if site.zarinpal_payment_enabled else '⛔ خاموش'}\n"
        f"مرچنت: {site.zarinpal_merchant_id or '-'}\n\n"
        f"🚚 هزینه ارسال: {site.shipping_cost:,} تومان\n"
        f"📦 هزینه بسته‌بندی: {site.packaging_cost:,} تومان\n"
        f"🎁 ارسال رایگان از: {free_label}"
    )
    await message.reply_text(text, reply_markup=v9.commerce_menu(site))


async def on_callback(update: Update, context):
    if not old.allowed(update):
        return
    q = update.callback_query
    data = q.data or ""

    if data == "commerce:settings":
        await q.answer()
        old.clear_state(context)
        await show_commerce(q.message)
        return

    if data.startswith("commerce:toggle:"):
        v9._show_commerce = show_commerce
        await v9.on_callback(update, context)
        return

    if data.startswith("receipt:approve:"):
        await q.answer()
        oid = int(data.rsplit(":", 1)[1])
        order = await sync_to_async(Order.objects.select_related("user").prefetch_related("items__product").get)(pk=oid)
        if order.payment_method != Order.PAYMENT_CARD or not order.receipt:
            await q.message.reply_text("این سفارش رسید کارت‌به‌کارت قابل تایید ندارد.")
            return
        if order.payment_status in (Order.PAY_REJECTED, Order.PAY_FAILED) or order.status in ("payment_rejected", "cancelled"):
            await q.message.reply_text("این رسید قبلاً رد/بسته شده و موجودی سفارش آزاد شده است؛ دیگر قابل تایید نیست.")
            return
        if order.payment_status != Order.PAY_PAID:
            await sync_to_async(mark_paid)(order)
            await sync_to_async(email_customer)(order, f"پرداخت سفارش #{order.id} تایید شد", "رسید پرداخت شما تایید شد و سفارش وارد مرحله آماده‌سازی شد.")
        await sync_to_async(notify_admins)(order_report_text(order, "✅ پرداخت کارت‌به‌کارت تایید شد"))
        await q.message.reply_text(f"✅ رسید سفارش #{order.id} تایید شد و سفارش وارد آماده‌سازی شد.")
        return

    if data.startswith("receipt:reject:"):
        await q.answer()
        oid = int(data.rsplit(":", 1)[1])
        order = await sync_to_async(Order.objects.only("id", "payment_status", "status").get)(pk=oid)
        if order.payment_status == Order.PAY_PAID or order.status in ("preparing", "shipped", "delivered"):
            await q.message.reply_text("این پرداخت قبلاً تایید شده و دیگر قابل رد کردن نیست.")
            return
        if order.payment_status in (Order.PAY_REJECTED, Order.PAY_FAILED) or order.status in ("payment_rejected", "cancelled"):
            await q.message.reply_text("این پرداخت قبلاً رد/بسته شده است.")
            return
        old.set_state(context, "receipt_reject_reason", order_id=oid)
        await q.message.reply_text(f"دلیل رد رسید سفارش #{oid} را بنویس:")
        return

    v9._show_commerce = show_commerce
    await v9.on_callback(update, context)


async def on_message(update: Update, context):
    if not old.allowed(update):
        return
    v9._show_commerce = show_commerce
    await v9.on_message(update, context)


class Command(BaseCommand):
    def handle(self, *args, **opts):
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            self.stderr.write("TELEGRAM_BOT_TOKEN is empty")
            return

        old.main_menu = v9.main_menu
        old.settings_menu = v9.settings_menu
        v3.settings_menu = v9.settings_menu
        v8.settings_menu = v9.settings_menu
        v9._show_commerce = show_commerce
        v4.show_categories = v5.show_categories
        v4.source_text = v7.source_text
        v4.source_actions = v7.source_actions
        v6.source_text = v7.source_text
        v6.source_actions = v7.source_actions

        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler("start", old.start))
        app.add_handler(CommandHandler("cancel", old.cancel))
        app.add_handler(MessageHandler(filters.Regex(r"^/order_\d+$"), old.order_cmd))
        app.add_handler(CallbackQueryHandler(old.order_callback, pattern=r"^order:\d+:(preparing|shipped|delivered|cancelled)$"))
        app.add_handler(CallbackQueryHandler(on_callback))
        app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, on_message))
        app.run_polling(allowed_updates=Update.ALL_TYPES)
