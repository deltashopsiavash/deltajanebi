import os
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from shop.models import Order, Product, SiteSetting
from shop.services.source_sync import scrape_product, sync_product

SPECIAL_URL, SPECIAL_MARKUP, MANUAL_NAME, MANUAL_PRICE, MANUAL_STOCK, MANUAL_IMAGE, SET_SHIPPING, SET_LOGO = range(8)

_DIGIT_TRANS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)
_BIDI_CHARS = {
    "\u200e", "\u200f", "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
    "\u2066", "\u2067", "\u2068", "\u2069", "\ufeff",
}
_PERCENT_SIGNS = ("%", "٪", "％")


def admins():
    return {int(x) for x in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",") if x.strip().isdigit()}


def allowed(update):
    return bool(update.effective_user and update.effective_user.id in admins())


def normalize_number_text(value):
    """Normalize Persian/Arabic digits, RTL markers, separators and common labels."""
    text = unicodedata.normalize("NFKC", str(value or "")).translate(_DIGIT_TRANS)
    text = "".join(ch for ch in text if ch not in _BIDI_CHARS)
    text = text.replace("٬", "").replace(",", "").replace("،", "").replace("٫", ".")
    text = re.sub(r"\s+", "", text)
    return text


def parse_markup_input(value):
    """Return (markup_type, Decimal value) for 20%, %20, ۲۰٪, ٪۲۰ or fixed amounts."""
    text = normalize_number_text(value)
    is_percent = any(sign in text for sign in _PERCENT_SIGNS) or "درصد" in text

    for sign in _PERCENT_SIGNS:
        text = text.replace(sign, "")
    text = text.replace("درصد", "")
    text = re.sub(r"(?:تومان|تومن|ریال)$", "", text, flags=re.IGNORECASE)

    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        raise ValueError("invalid markup")

    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("invalid markup") from exc

    if number < 0:
        raise ValueError("negative markup")

    return (Product.MARKUP_PERCENT if is_percent else Product.MARKUP_FIXED), number


def parse_nonnegative_int(value):
    text = normalize_number_text(value)
    text = re.sub(r"(?:تومان|تومن|ریال|عدد)$", "", text, flags=re.IGNORECASE)
    if not re.fullmatch(r"\d+", text):
        raise ValueError("invalid integer")
    return int(text)


def menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔗 افزودن محصول خاص", callback_data="special"),
            InlineKeyboardButton("➕ افزودن محصول عادی", callback_data="manual"),
        ],
        [
            InlineKeyboardButton("📦 سفارش‌های اخیر", callback_data="orders"),
            InlineKeyboardButton("🔄 همگام‌سازی الان", callback_data="syncnow"),
        ],
        [
            InlineKeyboardButton("🚚 هزینه ارسال", callback_data="shipping"),
            InlineKeyboardButton("🖼 لوگوی سایت", callback_data="logo"),
        ],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if allowed(update):
        await update.effective_message.reply_text("مدیریت دلتا جانبی", reply_markup=menu())


async def on_menu(update, context):
    if not allowed(update):
        return ConversationHandler.END

    q = update.callback_query
    await q.answer()

    if q.data == "special":
        await q.message.reply_text("لینک محصول سایت منبع را بفرست:")
        return SPECIAL_URL
    if q.data == "manual":
        await q.message.reply_text("نام محصول عادی را بفرست:")
        return MANUAL_NAME
    if q.data == "shipping":
        await q.message.reply_text("هزینه ارسال جدید به تومان:")
        return SET_SHIPPING
    if q.data == "logo":
        await q.message.reply_text("لینک مستقیم لوگوی سایت را بفرست:")
        return SET_LOGO
    if q.data == "orders":
        rows = await sync_to_async(list)(Order.objects.all()[:10])
        text = "\n\n".join(
            [f"#{o.id} | {o.get_status_display()} | {o.total:,} تومان | {o.full_name} | /order_{o.id}" for o in rows]
        ) or "سفارشی نیست."
        await q.message.reply_text(text, reply_markup=menu())
        return ConversationHandler.END
    if q.data == "syncnow":
        rows = await sync_to_async(list)(Product.objects.filter(source_type=Product.SYNCED, is_active=True))
        for p in rows:
            await sync_to_async(sync_product)(p)
        await q.message.reply_text(f"✅ {len(rows)} محصول همگام شد.", reply_markup=menu())
        return ConversationHandler.END


async def special_url(update, context):
    url = update.message.text.strip()
    await update.message.reply_text("در حال خواندن محصول...")
    try:
        data = await sync_to_async(scrape_product)(url)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}\nدوباره لینک را بفرست یا /cancel بزن.")
        return SPECIAL_URL

    context.user_data["special"] = {"url": url, "data": data}
    await update.message.reply_text(
        f"✅ {data['name']}\n"
        f"قیمت منبع: {data['price']:,} تومان\n"
        f"موجودی: {data['stock']}\n\n"
        "افزایش قیمت را بفرست. همه این حالت‌ها معتبرند:\n"
        "20%  |  %20  |  ۲۰٪  |  ٪۲۰  |  20000"
    )
    return SPECIAL_MARKUP


async def special_markup(update, context):
    try:
        typ, val = parse_markup_input(update.message.text)
    except (ValueError, InvalidOperation):
        await update.message.reply_text(
            "❌ فرمت نامعتبر است.\n"
            "درصد: 20% یا %20 یا ۲۰٪\n"
            "مبلغ ثابت: 20000 یا ۲۰۰۰۰"
        )
        return SPECIAL_MARKUP

    info = context.user_data.get("special")
    if not info:
        await update.message.reply_text("اطلاعات محصول منقضی شده. دوباره «افزودن محصول خاص» را بزن.", reply_markup=menu())
        return ConversationHandler.END

    data = info["data"]

    def create():
        sku = data.get("sku") or None
        if sku and Product.objects.filter(sku=sku).exists():
            sku = None
        p = Product.objects.create(
            name=data["name"],
            description=data["description"],
            source_type=Product.SYNCED,
            source_url=info["url"],
            source_product_code=data.get("sku", "") or "",
            source_price=data["price"],
            stock=data["stock"],
            image_url=data["image_url"],
            gallery=data["gallery"],
            specs=data["specs"],
            sku=sku,
            markup_type=typ,
            markup_value=val,
        )
        p.price = p.apply_markup(data["price"])
        p.save(update_fields=["price"])
        return p

    try:
        p = await sync_to_async(create)()
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در ثبت محصول: {e}\n/cancel بزن و دوباره امتحان کن.")
        return SPECIAL_MARKUP

    mode = "درصد" if typ == Product.MARKUP_PERCENT else "تومان ثابت"
    await update.message.reply_text(
        f"✅ محصول خاص ثبت شد.\n"
        f"افزایش: {val} {mode}\n"
        f"قیمت فروش: {p.price:,} تومان\n"
        "هر ۳۰ دقیقه بررسی می‌شود.",
        reply_markup=menu(),
    )
    context.user_data.pop("special", None)
    return ConversationHandler.END


async def manual_name(update, context):
    context.user_data["manual"] = {"name": update.message.text.strip()}
    await update.message.reply_text("قیمت به تومان:")
    return MANUAL_PRICE


async def manual_price(update, context):
    try:
        context.user_data["manual"]["price"] = parse_nonnegative_int(update.message.text)
    except ValueError:
        await update.message.reply_text("فقط عدد بفرست؛ فارسی یا انگلیسی فرقی ندارد.")
        return MANUAL_PRICE
    await update.message.reply_text("موجودی:")
    return MANUAL_STOCK


async def manual_stock(update, context):
    try:
        context.user_data["manual"]["stock"] = parse_nonnegative_int(update.message.text)
    except ValueError:
        await update.message.reply_text("فقط عدد بفرست؛ فارسی یا انگلیسی فرقی ندارد.")
        return MANUAL_STOCK
    await update.message.reply_text("لینک عکس را بفرست؛ اگر نداری - بفرست:")
    return MANUAL_IMAGE


async def manual_image(update, context):
    d = context.user_data["manual"]
    image = "" if update.message.text.strip() == "-" else update.message.text.strip()
    p = await sync_to_async(Product.objects.create)(
        name=d["name"],
        price=d["price"],
        stock=d["stock"],
        image_url=image,
        source_type=Product.MANUAL,
    )
    await update.message.reply_text(f"✅ محصول عادی #{p.id} ثبت شد.", reply_markup=menu())
    context.user_data.pop("manual", None)
    return ConversationHandler.END


async def set_shipping(update, context):
    try:
        value = parse_nonnegative_int(update.message.text)
    except ValueError:
        await update.message.reply_text("فقط عدد بفرست؛ فارسی یا انگلیسی فرقی ندارد.")
        return SET_SHIPPING
    s = await sync_to_async(SiteSetting.load)()
    s.shipping_cost = value
    await sync_to_async(s.save)(update_fields=["shipping_cost"])
    await update.message.reply_text("✅ هزینه ارسال تغییر کرد.", reply_markup=menu())
    return ConversationHandler.END


async def set_logo(update, context):
    s = await sync_to_async(SiteSetting.load)()
    s.logo_url = update.message.text.strip()
    await sync_to_async(s.save)(update_fields=["logo_url"])
    await update.message.reply_text("✅ لوگو تغییر کرد.", reply_markup=menu())
    return ConversationHandler.END


async def cancel(update, context):
    context.user_data.pop("special", None)
    context.user_data.pop("manual", None)
    await update.effective_message.reply_text("لغو شد.", reply_markup=menu())
    return ConversationHandler.END


async def order_cmd(update, context):
    if not allowed(update):
        return
    oid = int(update.message.text.split("_", 1)[1])
    try:
        o = await sync_to_async(Order.objects.get)(pk=oid)
    except Order.DoesNotExist:
        await update.message.reply_text("سفارش پیدا نشد.")
        return

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ آماده‌سازی", callback_data=f"st:{oid}:preparing"),
            InlineKeyboardButton("🚚 ارسال شد", callback_data=f"st:{oid}:shipped"),
        ],
        [InlineKeyboardButton("❌ لغو", callback_data=f"st:{oid}:cancelled")],
    ])
    await update.message.reply_text(
        f"سفارش #{o.id}\n{o.full_name} | {o.phone}\n{o.total:,} تومان\n"
        f"وضعیت: {o.get_status_display()}\nآدرس: {o.province}، {o.city}، {o.address}",
        reply_markup=kb,
    )


async def status_cb(update, context):
    if not allowed(update):
        return
    q = update.callback_query
    await q.answer()
    _, oid, status = q.data.split(":")
    o = await sync_to_async(Order.objects.get)(pk=int(oid))
    o.status = status
    await sync_to_async(o.save)(update_fields=["status"])
    await q.edit_message_text(f"✅ سفارش #{o.id}: {o.get_status_display()}")


class Command(BaseCommand):
    def handle(self, *args, **opts):
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            self.stderr.write("TELEGRAM_BOT_TOKEN is empty")
            return

        app = Application.builder().token(token).build()
        conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(on_menu, pattern="^(special|manual|orders|syncnow|shipping|logo)$")],
            states={
                SPECIAL_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, special_url)],
                SPECIAL_MARKUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, special_markup)],
                MANUAL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_name)],
                MANUAL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_price)],
                MANUAL_STOCK: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_stock)],
                MANUAL_IMAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_image)],
                SET_SHIPPING: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_shipping)],
                SET_LOGO: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_logo)],
            },
            fallbacks=[CommandHandler("cancel", cancel)],
        )
        app.add_handler(CommandHandler("start", start))
        app.add_handler(conv)
        app.add_handler(MessageHandler(filters.Regex(r"^/order_\d+$"), order_cmd))
        app.add_handler(CallbackQueryHandler(status_cb, pattern=r"^st:\d+:(preparing|shipped|cancelled)$"))
        app.run_polling(allowed_updates=Update.ALL_TYPES)
