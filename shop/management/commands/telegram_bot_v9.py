import os
import re

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from shop.management.commands import telegram_bot as old
from shop.management.commands import telegram_bot_v3 as v3
from shop.management.commands import telegram_bot_v4 as v4
from shop.management.commands import telegram_bot_v5 as v5
from shop.management.commands import telegram_bot_v6 as v6
from shop.management.commands import telegram_bot_v7 as v7
from shop.management.commands import telegram_bot_v8 as v8
from shop.models import DiscountCode, Order, SiteSetting
from shop.services.order_workflow import email_customer, mark_paid, release_order_stock


def main_menu():
    return v8.main_menu()


def settings_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ نام سایت", callback_data="set:name"), InlineKeyboardButton("🖼 لوگوی سایت", callback_data="set:logo")],
        [InlineKeyboardButton("🌐 شبکه‌های اجتماعی", callback_data="social:list"), InlineKeyboardButton("📣 بنرهای تبلیغاتی", callback_data="banner:list")],
        [InlineKeyboardButton("🛡 نمادها", callback_data="badge:list"), InlineKeyboardButton("☎️ تلفن", callback_data="set:phone")],
        [InlineKeyboardButton("📝 توضیحات و فوتر", callback_data="set:footer")],
        [InlineKeyboardButton("💳 پرداخت، تخفیف و ارسال", callback_data="commerce:settings")],
        [InlineKeyboardButton("⬅️ منوی اصلی", callback_data="main")],
    ])


def commerce_menu(site):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🏦 کارت به کارت: {'روشن' if site.card_payment_enabled else 'خاموش'}", callback_data="commerce:toggle:card"), InlineKeyboardButton(f"💳 زرین‌پال: {'روشن' if site.zarinpal_payment_enabled else 'خاموش'}", callback_data="commerce:toggle:zarinpal")],
        [InlineKeyboardButton("💳 شماره کارت", callback_data="commerce:cardnumber"), InlineKeyboardButton("👤 صاحب حساب", callback_data="commerce:cardowner")],
        [InlineKeyboardButton("🔑 مرچنت زرین‌پال", callback_data="commerce:merchant")],
        [InlineKeyboardButton("🚚 هزینه ارسال", callback_data="commerce:shipping"), InlineKeyboardButton("📦 هزینه بسته‌بندی", callback_data="commerce:packaging")],
        [InlineKeyboardButton("🎁 حد ارسال رایگان", callback_data="commerce:freeshipping")],
        [InlineKeyboardButton("🎟 کدهای تخفیف", callback_data="discount:list"), InlineKeyboardButton("📜 قوانین و مقررات", callback_data="commerce:terms")],
        [InlineKeyboardButton("⬅️ تنظیمات سایت", callback_data="settings")],
    ])


def allproducts_keyboard(site):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🙈 غیرفعال کردن ناموجودها: {'روشن' if site.hide_out_of_stock else 'خاموش'}", callback_data="stockhide:toggle")],
        [InlineKeyboardButton("⬅️ منوی اصلی", callback_data="main")],
    ])


def discount_list_menu(rows):
    buttons = []
    for item in rows:
        label = f"{'✅' if item.is_active else '⛔'} {item.code} | " + (f"{item.value}%" if item.discount_type == DiscountCode.PERCENT else f"{item.value:,} تومان")
        buttons.append([InlineKeyboardButton(label, callback_data=f"discount:toggle:{item.id}"), InlineKeyboardButton("🗑", callback_data=f"discount:delete:{item.id}")])
    buttons.append([InlineKeyboardButton("➕ ساخت کد تخفیف", callback_data="discount:add")])
    buttons.append([InlineKeyboardButton("⬅️ پرداخت و ارسال", callback_data="commerce:settings")])
    return InlineKeyboardMarkup(buttons)


async def _show_commerce(message):
    site = await sync_to_async(SiteSetting.load)()
    text = (
        "💳 تنظیمات پرداخت و سفارش\n\n"
        f"کارت به کارت: {'✅' if site.card_payment_enabled else '⛔'}\n"
        f"شماره کارت: {site.card_number or '-'}\n"
        f"صاحب حساب: {site.card_owner or '-'}\n\n"
        f"زرین‌پال: {'✅' if site.zarinpal_payment_enabled else '⛔'}\n"
        f"مرچنت: {site.zarinpal_merchant_id or '-'}\n\n"
        f"ارسال: {site.shipping_cost:,} تومان\n"
        f"بسته‌بندی: {site.packaging_cost:,} تومان\n"
        f"ارسال رایگان از: {site.free_shipping_threshold:,} تومان" if site.free_shipping_threshold else "ارسال رایگان خودکار: غیرفعال"
    )
    await message.reply_text(text, reply_markup=commerce_menu(site))


async def on_callback(update: Update, context):
    if not old.allowed(update):
        return
    q = update.callback_query
    data = q.data or ""

    if data == "settings":
        await q.answer()
        old.clear_state(context)
        await q.message.reply_text("⚙️ تنظیمات سایت", reply_markup=settings_menu())
        return
    if data == "allproducts":
        await q.answer()
        text = await sync_to_async(v8._all_products_report)()
        site = await sync_to_async(SiteSetting.load)()
        await q.message.reply_text(text, reply_markup=allproducts_keyboard(site))
        return
    if data == "stockhide:toggle":
        await q.answer()
        site = await sync_to_async(SiteSetting.load)()
        site.hide_out_of_stock = not site.hide_out_of_stock
        await sync_to_async(site.save)(update_fields=["hide_out_of_stock"])
        text = await sync_to_async(v8._all_products_report)()
        await q.message.reply_text(text + f"\n\nنمایش ناموجودها: {'مخفی' if site.hide_out_of_stock else 'فعال'}", reply_markup=allproducts_keyboard(site))
        return
    if data == "commerce:settings":
        await q.answer()
        old.clear_state(context)
        await _show_commerce(q.message)
        return
    if data == "commerce:toggle:card":
        await q.answer()
        site = await sync_to_async(SiteSetting.load)()
        if not site.card_payment_enabled and (not site.card_number or not site.card_owner):
            await q.message.reply_text("اول شماره کارت و نام صاحب حساب را ثبت کن.", reply_markup=commerce_menu(site))
            return
        site.card_payment_enabled = not site.card_payment_enabled
        await sync_to_async(site.save)(update_fields=["card_payment_enabled"])
        await _show_commerce(q.message)
        return
    if data == "commerce:toggle:zarinpal":
        await q.answer()
        site = await sync_to_async(SiteSetting.load)()
        if not site.zarinpal_payment_enabled and not site.zarinpal_merchant_id:
            await q.message.reply_text("اول مرچنت زرین‌پال را ثبت کن.", reply_markup=commerce_menu(site))
            return
        site.zarinpal_payment_enabled = not site.zarinpal_payment_enabled
        await sync_to_async(site.save)(update_fields=["zarinpal_payment_enabled"])
        await _show_commerce(q.message)
        return
    state_map = {
        "commerce:cardnumber": ("commerce_cardnumber", "شماره کارت را بفرست؛ فاصله و خط تیره مجاز است. برای پاک کردن - بفرست."),
        "commerce:cardowner": ("commerce_cardowner", "نام صاحب حساب را بفرست."),
        "commerce:merchant": ("commerce_merchant", "Merchant ID زرین‌پال را بفرست."),
        "commerce:shipping": ("commerce_shipping", "هزینه ارسال را به تومان بفرست."),
        "commerce:packaging": ("commerce_packaging", "هزینه بسته‌بندی را به تومان بفرست."),
        "commerce:freeshipping": ("commerce_freeshipping", "حد خرید برای ارسال رایگان را به تومان بفرست. برای غیرفعال کردن 0 بفرست."),
        "commerce:terms": ("commerce_terms", "متن کامل قوانین و مقررات فروشگاه را بفرست."),
    }
    if data in state_map:
        await q.answer()
        state, prompt = state_map[data]
        old.set_state(context, state)
        await q.message.reply_text(prompt)
        return

    if data == "discount:list":
        await q.answer()
        rows = await sync_to_async(list)(DiscountCode.objects.all()[:50])
        await q.message.reply_text("🎟 کدهای تخفیف", reply_markup=discount_list_menu(rows))
        return
    if data == "discount:add":
        await q.answer()
        old.set_state(context, "discount_code")
        await q.message.reply_text("کد تخفیف را بفرست؛ مثال DELTA20")
        return
    if data.startswith("discount:type:"):
        await q.answer()
        dtype = data.rsplit(":", 1)[1]
        flow = context.user_data.setdefault("flow", {})
        flow["discount_type"] = dtype
        context.user_data["awaiting"] = "discount_value"
        await q.message.reply_text("درصد یا مبلغ تخفیف را فقط عدد بفرست:")
        return
    if data.startswith("discount:toggle:"):
        await q.answer()
        did = int(data.rsplit(":", 1)[1])
        item = await sync_to_async(DiscountCode.objects.get)(pk=did)
        item.is_active = not item.is_active
        await sync_to_async(item.save)(update_fields=["is_active"])
        rows = await sync_to_async(list)(DiscountCode.objects.all()[:50])
        await q.message.reply_text("وضعیت کد تغییر کرد.", reply_markup=discount_list_menu(rows))
        return
    if data.startswith("discount:delete:"):
        await q.answer()
        did = int(data.rsplit(":", 1)[1])
        await sync_to_async(DiscountCode.objects.filter(pk=did).delete)()
        rows = await sync_to_async(list)(DiscountCode.objects.all()[:50])
        await q.message.reply_text("کد تخفیف حذف شد.", reply_markup=discount_list_menu(rows))
        return

    if data.startswith("receipt:approve:"):
        await q.answer()
        oid = int(data.rsplit(":", 1)[1])
        order = await sync_to_async(Order.objects.select_related("user").get)(pk=oid)
        if order.payment_method != Order.PAYMENT_CARD or not order.receipt:
            await q.message.reply_text("این سفارش رسید کارت‌به‌کارت قابل تایید ندارد.")
            return
        await sync_to_async(mark_paid)(order)
        await sync_to_async(email_customer)(order, f"پرداخت سفارش #{order.id} تایید شد", "رسید پرداخت شما تایید شد و سفارش وارد مرحله آماده‌سازی شد.")
        await q.message.reply_text(f"✅ رسید سفارش #{order.id} تایید شد و سفارش وارد آماده‌سازی شد.")
        return
    if data.startswith("receipt:reject:"):
        await q.answer()
        oid = int(data.rsplit(":", 1)[1])
        old.set_state(context, "receipt_reject_reason", order_id=oid)
        await q.message.reply_text(f"دلیل رد رسید سفارش #{oid} را بنویس:")
        return

    await v8.on_callback(update, context)


async def on_message(update: Update, context):
    if not old.allowed(update):
        return
    state = context.user_data.get("awaiting")
    flow = context.user_data.setdefault("flow", {})
    text = update.effective_message.text.strip() if update.effective_message.text else ""

    if state in {"commerce_shipping", "commerce_packaging", "commerce_freeshipping"}:
        try:
            value = old.parse_nonnegative_int(text)
        except ValueError:
            await update.effective_message.reply_text("فقط عدد معتبر بفرست.")
            return
        site = await sync_to_async(SiteSetting.load)()
        field = {"commerce_shipping": "shipping_cost", "commerce_packaging": "packaging_cost", "commerce_freeshipping": "free_shipping_threshold"}[state]
        setattr(site, field, value)
        await sync_to_async(site.save)(update_fields=[field])
        old.clear_state(context)
        await update.effective_message.reply_text("✅ ذخیره شد.", reply_markup=commerce_menu(site))
        return
    if state == "commerce_cardnumber":
        site = await sync_to_async(SiteSetting.load)()
        if text == "-":
            value = ""
        else:
            value = re.sub(r"[^0-9]", "", old.normalize_number_text(text))
            if len(value) != 16:
                await update.effective_message.reply_text("شماره کارت باید ۱۶ رقم باشد.")
                return
        site.card_number = value
        await sync_to_async(site.save)(update_fields=["card_number"])
        old.clear_state(context)
        await update.effective_message.reply_text("✅ شماره کارت ذخیره شد.", reply_markup=commerce_menu(site))
        return
    if state == "commerce_cardowner":
        if not text:
            await update.effective_message.reply_text("نام صاحب حساب را بفرست.")
            return
        site = await sync_to_async(SiteSetting.load)()
        site.card_owner = text[:120]
        await sync_to_async(site.save)(update_fields=["card_owner"])
        old.clear_state(context)
        await update.effective_message.reply_text("✅ نام صاحب حساب ذخیره شد.", reply_markup=commerce_menu(site))
        return
    if state == "commerce_merchant":
        value = "" if text == "-" else text.strip()
        if value and not re.fullmatch(r"[A-Za-z0-9-]{20,64}", value):
            await update.effective_message.reply_text("Merchant ID معتبر نیست.")
            return
        site = await sync_to_async(SiteSetting.load)()
        site.zarinpal_merchant_id = value
        await sync_to_async(site.save)(update_fields=["zarinpal_merchant_id"])
        old.clear_state(context)
        await update.effective_message.reply_text("✅ مرچنت زرین‌پال ذخیره شد.", reply_markup=commerce_menu(site))
        return
    if state == "commerce_terms":
        if not text:
            await update.effective_message.reply_text("متن قوانین را بفرست.")
            return
        site = await sync_to_async(SiteSetting.load)()
        site.terms_text = text
        await sync_to_async(site.save)(update_fields=["terms_text"])
        old.clear_state(context)
        await update.effective_message.reply_text("✅ قوانین و مقررات ذخیره شد.", reply_markup=commerce_menu(site))
        return
    if state == "discount_code":
        code = re.sub(r"\s+", "", text.upper())[:60]
        if not code:
            await update.effective_message.reply_text("کد معتبر بفرست.")
            return
        exists = await sync_to_async(DiscountCode.objects.filter(code__iexact=code).exists)()
        if exists:
            await update.effective_message.reply_text("این کد قبلاً وجود دارد.")
            return
        flow["discount_code"] = code
        context.user_data["awaiting"] = "discount_type_choice"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("٪ درصدی", callback_data="discount:type:percent"), InlineKeyboardButton("💵 مبلغ ثابت", callback_data="discount:type:fixed")]])
        await update.effective_message.reply_text("نوع تخفیف را انتخاب کن:", reply_markup=kb)
        return
    if state == "discount_type_choice":
        await update.effective_message.reply_text("نوع تخفیف را از دکمه‌ها انتخاب کن.")
        return
    if state == "discount_value":
        try:
            value = old.parse_nonnegative_int(text)
        except ValueError:
            await update.effective_message.reply_text("فقط عدد بفرست.")
            return
        dtype = flow.get("discount_type")
        if dtype == DiscountCode.PERCENT and value > 100:
            await update.effective_message.reply_text("درصد تخفیف نمی‌تواند بیشتر از ۱۰۰ باشد.")
            return
        item = await sync_to_async(DiscountCode.objects.create)(code=flow["discount_code"], discount_type=dtype, value=value)
        old.clear_state(context)
        await update.effective_message.reply_text(f"✅ کد {item.code} ساخته شد.", reply_markup=commerce_menu(await sync_to_async(SiteSetting.load)()))
        return
    if state == "receipt_reject_reason":
        if not text:
            await update.effective_message.reply_text("دلیل رد را بنویس.")
            return
        oid = int(flow["order_id"])
        order = await sync_to_async(Order.objects.select_related("user").get)(pk=oid)
        order.payment_status = Order.PAY_REJECTED
        order.status = "payment_rejected"
        order.receipt_rejection_reason = text[:1000]
        await sync_to_async(order.save)(update_fields=["payment_status", "status", "receipt_rejection_reason", "updated_at"])
        await sync_to_async(release_order_stock)(order)
        await sync_to_async(email_customer)(order, f"رسید سفارش #{order.id} رد شد", f"رسید پرداخت شما رد شد. دلیل: {order.receipt_rejection_reason}")
        old.clear_state(context)
        await update.effective_message.reply_text(f"❌ رسید سفارش #{order.id} رد شد و دلیل برای کاربر ثبت/ایمیل شد.", reply_markup=v8.admin_management_menu())
        return

    await v8.on_message(update, context)


class Command(BaseCommand):
    def handle(self, *args, **opts):
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            self.stderr.write("TELEGRAM_BOT_TOKEN is empty")
            return

        old.main_menu = main_menu
        old.settings_menu = settings_menu
        v3.settings_menu = settings_menu
        v8.settings_menu = settings_menu
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
