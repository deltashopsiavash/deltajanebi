import os
import re
import unicodedata
import uuid
from decimal import Decimal, InvalidOperation

from asgiref.sync import sync_to_async
from django.core.files.base import ContentFile
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

from shop.models import Category, Order, Product, SiteSetting
from shop.services.source_sync import scrape_product, sync_category_path, sync_product

(
    SPECIAL_URL,
    SPECIAL_MARKUP,
    MANUAL_NAME,
    MANUAL_PRICE,
    MANUAL_STOCK,
    MANUAL_CATEGORY,
    MANUAL_IMAGE,
    SET_SHIPPING,
    SET_LOGO,
) = range(9)

_DIGIT_TRANS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
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
    text = unicodedata.normalize("NFKC", str(value or "")).translate(_DIGIT_TRANS)
    text = "".join(ch for ch in text if ch not in _BIDI_CHARS)
    text = text.replace("٬", "").replace(",", "").replace("،", "").replace("٫", ".")
    return re.sub(r"\s+", "", text)


def parse_markup_input(value):
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


def parse_category_path(value):
    text = str(value or "").strip()
    if not text or text == "-":
        return []
    return [part.strip()[:120] for part in text.split(">") if part.strip()]


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
            InlineKeyboardButton("📂 دسته‌بندی‌ها", callback_data="categories"),
            InlineKeyboardButton("🚚 هزینه ارسال", callback_data="shipping"),
        ],
        [InlineKeyboardButton("🖼 لوگوی سایت", callback_data="logo")],
    ])


def category_tree_text():
    categories = list(Category.objects.filter(is_active=True).select_related("parent").order_by("parent_id", "order", "name"))
    if not categories:
        return "هنوز دسته‌بندی‌ای ساخته نشده."
    children = {}
    for category in categories:
        children.setdefault(category.parent_id, []).append(category)
    lines = []

    def walk(parent_id=None, level=0):
        for category in children.get(parent_id, []):
            lines.append(f"{'  ' * level}{'↳ ' if level else '• '}{category.name}")
            walk(category.id, level + 1)

    walk()
    return "📂 دسته‌بندی‌های سایت:\n\n" + "\n".join(lines[:80])


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
    if q.data == "categories":
        text = await sync_to_async(category_tree_text)()
        await q.message.reply_text(text, reply_markup=menu())
        return ConversationHandler.END
    if q.data == "orders":
        rows = await sync_to_async(list)(Order.objects.all()[:10])
        text = "\n\n".join(
            [f"#{o.id} | {o.get_status_display()} | {o.total:,} تومان | {o.full_name} | /order_{o.id}" for o in rows]
        ) or "سفارشی نیست."
        await q.message.reply_text(text, reply_markup=menu())
        return ConversationHandler.END
    if q.data == "syncnow":
        rows = await sync_to_async(list)(Product.objects.filter(source_type=Product.SYNCED, is_active=True))
        for product in rows:
            await sync_to_async(sync_product)(product)
        await q.message.reply_text(f"✅ {len(rows)} محصول همگام شد؛ دسته‌بندی، قیمت، موجودی و اطلاعات هم بررسی شدند.", reply_markup=menu())
        return ConversationHandler.END


async def special_url(update, context):
    url = update.message.text.strip()
    await update.message.reply_text("در حال خواندن محصول...")
    try:
        data = await sync_to_async(scrape_product)(url)
    except Exception as exc:
        await update.message.reply_text(f"❌ {exc}\nدوباره لینک را بفرست یا /cancel بزن.")
        return SPECIAL_URL

    context.user_data["special"] = {"url": url, "data": data}
    category_line = " ← ".join(data.get("categories") or []) or "تشخیص داده نشد"
    await update.message.reply_text(
        f"✅ {data['name']}\n"
        f"قیمت منبع: {data['price']:,} تومان\n"
        f"موجودی: {data['stock']}\n"
        f"دسته‌بندی: {category_line}\n"
        f"تصاویر محصول: {len(data.get('gallery') or [])}\n\n"
        "افزایش قیمت را بفرست:\n"
        "20%  |  %20  |  ۲۰٪  |  ٪۲۰  |  20000"
    )
    return SPECIAL_MARKUP


async def special_markup(update, context):
    try:
        typ, val = parse_markup_input(update.message.text)
    except (ValueError, InvalidOperation):
        await update.message.reply_text(
            "❌ فرمت نامعتبر است.\nدرصد: 20% یا %20 یا ۲۰٪\nمبلغ ثابت: 20000 یا ۲۰۰۰۰"
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
        category = sync_category_path(data.get("categories") or [])
        product = Product.objects.create(
            category=category,
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
        product.price = product.apply_markup(data["price"])
        product.save(update_fields=["price"])
        return product

    try:
        product = await sync_to_async(create)()
    except Exception as exc:
        await update.message.reply_text(f"❌ خطا در ثبت محصول: {exc}\n/cancel بزن و دوباره امتحان کن.")
        return SPECIAL_MARKUP

    mode = "درصد" if typ == Product.MARKUP_PERCENT else "تومان ثابت"
    await update.message.reply_text(
        f"✅ محصول خاص ثبت شد.\nافزایش: {val} {mode}\nقیمت فروش: {product.price:,} تومان\n"
        f"دسته: {product.category.name if product.category else 'بدون دسته'}\nهر ۳۰ دقیقه بررسی می‌شود.",
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
    await update.message.reply_text(
        "دسته‌بندی محصول را بفرست.\n"
        "مثال ساده: کابل\n"
        "برای زیر‌دسته: جانبی موبایل > کابل > کابل شارژ موبایل\n"
        "اگر دسته نمی‌خواهی - بفرست."
    )
    return MANUAL_CATEGORY


async def manual_category(update, context):
    context.user_data["manual"]["categories"] = parse_category_path(update.message.text)
    await update.message.reply_text(
        "حالا عکس محصول را مستقیم همینجا ارسال کن (Photo یا فایل تصویر).\n"
        "اگر خواستی لینک عکس بفرستی هم قبول می‌کنم؛ اگر عکس نداری - بفرست."
    )
    return MANUAL_IMAGE


async def manual_image(update, context):
    data = context.user_data.get("manual")
    if not data:
        await update.effective_message.reply_text("اطلاعات محصول منقضی شده؛ دوباره افزودن محصول عادی را بزن.", reply_markup=menu())
        return ConversationHandler.END

    image_bytes = None
    image_filename = None
    image_url = ""

    if update.message.photo:
        photo = update.message.photo[-1]
        tg_file = await photo.get_file()
        image_bytes = bytes(await tg_file.download_as_bytearray())
        image_filename = f"telegram-{uuid.uuid4().hex}.jpg"
    elif update.message.document:
        document = update.message.document
        if not (document.mime_type or "").startswith("image/"):
            await update.message.reply_text("این فایل تصویر نیست. عکس بفرست یا /cancel بزن.")
            return MANUAL_IMAGE
        if document.file_size and document.file_size > 15 * 1024 * 1024:
            await update.message.reply_text("حجم عکس بیشتر از ۱۵ مگابایت است؛ تصویر کوچک‌تر بفرست.")
            return MANUAL_IMAGE
        tg_file = await document.get_file()
        image_bytes = bytes(await tg_file.download_as_bytearray())
        ext = {"image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(document.mime_type, ".jpg")
        image_filename = f"telegram-{uuid.uuid4().hex}{ext}"
    elif update.message.text:
        text = update.message.text.strip()
        if text != "-":
            if not re.match(r"^https?://", text, re.I):
                await update.message.reply_text("لینک عکس معتبر نیست؛ عکس مستقیم بفرست، لینک http/https بفرست یا - بزن.")
                return MANUAL_IMAGE
            image_url = text
    else:
        await update.message.reply_text("عکس، فایل تصویر، لینک عکس یا - بفرست.")
        return MANUAL_IMAGE

    def create():
        category = sync_category_path(data.get("categories") or [])
        product = Product(
            category=category,
            name=data["name"],
            price=data["price"],
            stock=data["stock"],
            image_url=image_url,
            source_type=Product.MANUAL,
        )
        if image_bytes and image_filename:
            product.image.save(image_filename, ContentFile(image_bytes), save=False)
        product.save()
        if product.image:
            product.image_url = product.image.url
            product.gallery = [product.image.url]
            product.save(update_fields=["image_url", "gallery"])
        elif product.image_url:
            product.gallery = [product.image_url]
            product.save(update_fields=["gallery"])
        return product

    try:
        product = await sync_to_async(create)()
    except Exception as exc:
        await update.message.reply_text(f"❌ خطا در ثبت محصول: {exc}")
        return MANUAL_IMAGE

    await update.message.reply_text(
        f"✅ محصول عادی #{product.id} ثبت شد.\n"
        f"دسته: {product.category.name if product.category else 'بدون دسته'}\n"
        f"عکس: {'ثبت شد' if product.primary_image else 'ندارد'}",
        reply_markup=menu(),
    )
    context.user_data.pop("manual", None)
    return ConversationHandler.END


async def set_shipping(update, context):
    try:
        value = parse_nonnegative_int(update.message.text)
    except ValueError:
        await update.message.reply_text("فقط عدد بفرست؛ فارسی یا انگلیسی فرقی ندارد.")
        return SET_SHIPPING
    settings = await sync_to_async(SiteSetting.load)()
    settings.shipping_cost = value
    await sync_to_async(settings.save)(update_fields=["shipping_cost"])
    await update.message.reply_text("✅ هزینه ارسال تغییر کرد.", reply_markup=menu())
    return ConversationHandler.END


async def set_logo(update, context):
    settings = await sync_to_async(SiteSetting.load)()
    settings.logo_url = update.message.text.strip()
    await sync_to_async(settings.save)(update_fields=["logo_url"])
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
        order = await sync_to_async(Order.objects.get)(pk=oid)
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
        f"سفارش #{order.id}\n{order.full_name} | {order.phone}\n{order.total:,} تومان\n"
        f"وضعیت: {order.get_status_display()}\nآدرس: {order.province}، {order.city}، {order.address}",
        reply_markup=kb,
    )


async def status_cb(update, context):
    if not allowed(update):
        return
    q = update.callback_query
    await q.answer()
    _, oid, status = q.data.split(":")
    order = await sync_to_async(Order.objects.get)(pk=int(oid))
    order.status = status
    await sync_to_async(order.save)(update_fields=["status"])
    await q.edit_message_text(f"✅ سفارش #{order.id}: {order.get_status_display()}")


class Command(BaseCommand):
    def handle(self, *args, **opts):
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            self.stderr.write("TELEGRAM_BOT_TOKEN is empty")
            return

        app = Application.builder().token(token).build()
        conv = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(
                    on_menu,
                    pattern="^(special|manual|orders|syncnow|categories|shipping|logo)$",
                )
            ],
            states={
                SPECIAL_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, special_url)],
                SPECIAL_MARKUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, special_markup)],
                MANUAL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_name)],
                MANUAL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_price)],
                MANUAL_STOCK: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_stock)],
                MANUAL_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_category)],
                MANUAL_IMAGE: [
                    MessageHandler(filters.PHOTO, manual_image),
                    MessageHandler(filters.Document.IMAGE, manual_image),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, manual_image),
                ],
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
