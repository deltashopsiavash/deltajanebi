import io
import os
import re
import unicodedata
import uuid
import zipfile
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from asgiref.sync import sync_to_async
from django.conf import settings as django_settings
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from shop.models import Banner, Category, Order, Product, SiteSetting, SocialLink
from shop.services.source_sync import scrape_product, sync_category_path, sync_product


_DIGIT_TRANS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_BIDI_CHARS = {
    "\u200e", "\u200f", "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
    "\u2066", "\u2067", "\u2068", "\u2069", "\ufeff",
}
_PERCENT_SIGNS = ("%", "٪", "％")
MAX_IMAGE_BYTES = 15 * 1024 * 1024
PAGE_SIZE = 8


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
    text = re.sub(r"(?:تومان|تومن|ریال)$", "", text, flags=re.I)
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
    text = re.sub(r"(?:تومان|تومن|ریال|عدد)$", "", text, flags=re.I)
    if not re.fullmatch(r"\d+", text):
        raise ValueError("invalid integer")
    return int(text)


def parse_category_path(value):
    text = str(value or "").strip()
    if not text or text == "-":
        return []
    return [part.strip()[:120] for part in text.split(">") if part.strip()]


def parse_duration(value):
    text = normalize_number_text(value).lower()
    text = text.replace("دقیقه", "m").replace("ساعت", "h").replace("روز", "d")
    match = re.fullmatch(r"(\d+)(m|h|d)?", text)
    if not match:
        raise ValueError("invalid duration")
    amount = int(match.group(1))
    unit = match.group(2) or "m"
    if amount <= 0:
        raise ValueError("invalid duration")
    return {"m": timedelta(minutes=amount), "h": timedelta(hours=amount), "d": timedelta(days=amount)}[unit]


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧰 محصولات عادی", callback_data="m:manual"), InlineKeyboardButton("🔗 محصولات خاص", callback_data="m:synced")],
        [InlineKeyboardButton("🔎 جستجوی محصول", callback_data="search"), InlineKeyboardButton("⭐ پیشنهادهای فعال", callback_data="offers")],
        [InlineKeyboardButton("📦 سفارش‌ها", callback_data="orders"), InlineKeyboardButton("📂 دسته‌بندی‌ها", callback_data="categories")],
        [InlineKeyboardButton("🔄 همگام‌سازی همه", callback_data="syncall"), InlineKeyboardButton("⚙️ تنظیمات سایت", callback_data="settings")],
        [InlineKeyboardButton("💾 بکاپ", callback_data="backup")],
    ])


def product_menu(source_type):
    label = "عادی" if source_type == Product.MANUAL else "خاص"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"➕ افزودن محصول {label}", callback_data=f"add:{source_type}"), InlineKeyboardButton("📋 محصولات", callback_data=f"plist:{source_type}:0")],
        [InlineKeyboardButton("⬅️ منوی اصلی", callback_data="main")],
    ])


def settings_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ نام سایت", callback_data="set:name"), InlineKeyboardButton("🖼 لوگوی سایت", callback_data="set:logo")],
        [InlineKeyboardButton("🚚 هزینه ارسال", callback_data="set:shipping"), InlineKeyboardButton("🌐 شبکه‌های اجتماعی", callback_data="social:list")],
        [InlineKeyboardButton("📣 بنرهای تبلیغاتی", callback_data="banner:list")],
        [InlineKeyboardButton("⬅️ منوی اصلی", callback_data="main")],
    ])


def backup_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ ساخت بکاپ جدید", callback_data="backup:create"), InlineKeyboardButton("📂 بکاپ‌ها", callback_data="backup:list")],
        [InlineKeyboardButton("⬅️ منوی اصلی", callback_data="main")],
    ])


def set_state(context, state, **data):
    context.user_data["awaiting"] = state
    context.user_data["flow"] = data


def clear_state(context):
    context.user_data.pop("awaiting", None)
    context.user_data.pop("flow", None)


async def telegram_image_bytes(message, prefix):
    if message.photo:
        tg_file = await message.photo[-1].get_file()
        raw = bytes(await tg_file.download_as_bytearray())
        if len(raw) > MAX_IMAGE_BYTES:
            raise ValueError("حجم عکس بیشتر از ۱۵ مگابایت است.")
        return raw, f"{prefix}-{uuid.uuid4().hex}.jpg"
    if message.document:
        document = message.document
        if not (document.mime_type or "").startswith("image/"):
            raise ValueError("این فایل تصویر نیست.")
        if document.file_size and document.file_size > MAX_IMAGE_BYTES:
            raise ValueError("حجم عکس بیشتر از ۱۵ مگابایت است.")
        tg_file = await document.get_file()
        raw = bytes(await tg_file.download_as_bytearray())
        ext = {"image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(document.mime_type, ".jpg")
        return raw, f"{prefix}-{uuid.uuid4().hex}{ext}"
    return None, None


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
    return "📂 دسته‌بندی‌های سایت:\n\n" + "\n".join(lines[:100])


def product_text(product):
    source = "عادی" if product.source_type == Product.MANUAL else "خاص/همگام"
    sale = ""
    if product.is_sale_active:
        sale = f"\n⭐ پیشنهاد: {product.effective_price:,} تومان تا {timezone.localtime(product.sale_ends_at).strftime('%Y/%m/%d %H:%M')}"
    overrides = []
    if product.manual_name_override:
        overrides.append("نام")
    if product.manual_price_override is not None:
        overrides.append("قیمت")
    if product.manual_stock_override is not None:
        overrides.append("موجودی")
    if product.image or product.manual_image_url_override:
        overrides.append("عکس")
    override_text = f"\n🛠 دستی: {', '.join(overrides)}" if overrides else ""
    return (
        f"📦 {product.name}\n"
        f"🔑 کد: {product.public_code or '-'}\n"
        f"نوع: {source}\n"
        f"💰 قیمت پایه: {product.price:,} تومان\n"
        f"📊 موجودی: {product.stock}\n"
        f"وضعیت: {'✅ فعال' if product.is_active else '⛔ غیرفعال'}"
        f"{sale}{override_text}"
    )


def product_actions(product):
    rows = [
        [InlineKeyboardButton("✏️ نام", callback_data=f"p:name:{product.id}"), InlineKeyboardButton("💰 قیمت", callback_data=f"p:price:{product.id}")],
        [InlineKeyboardButton("📊 موجودی", callback_data=f"p:stock:{product.id}"), InlineKeyboardButton("🖼 عکس", callback_data=f"p:image:{product.id}")],
        [InlineKeyboardButton("⭐ پیشنهاد زمان‌دار", callback_data=f"p:sale:{product.id}"), InlineKeyboardButton("⏹ لغو پیشنهاد", callback_data=f"p:saleclear:{product.id}")],
        [InlineKeyboardButton("⏯ فعال/غیرفعال", callback_data=f"p:toggle:{product.id}"), InlineKeyboardButton("🗑 حذف", callback_data=f"p:delask:{product.id}")],
    ]
    if product.source_type == Product.SYNCED:
        rows.append([InlineKeyboardButton("🔄 Sync همین محصول", callback_data=f"p:sync:{product.id}"), InlineKeyboardButton("♻️ حذف تغییرات دستی", callback_data=f"p:resetsync:{product.id}")])
    rows.append([InlineKeyboardButton("⬅️ لیست", callback_data=f"plist:{product.source_type}:0"), InlineKeyboardButton("🏠 منوی اصلی", callback_data="main")])
    return InlineKeyboardMarkup(rows)


def backup_dir():
    path = Path(os.getenv("BACKUP_DIR", "/app/backups"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_backup_archive():
    root = backup_dir()
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    path = root / f"deltajanebi-{stamp}.zip"
    db_buffer = io.StringIO()
    call_command("dumpdata", indent=2, stdout=db_buffer)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("database.json", db_buffer.getvalue())
        archive.writestr("README.txt", "DeltaJanebi backup: database.json + media + environment snapshot.\n")
        media_root = Path(django_settings.MEDIA_ROOT)
        if media_root.exists():
            for file_path in media_root.rglob("*"):
                if file_path.is_file():
                    archive.write(file_path, f"media/{file_path.relative_to(media_root)}")
        env_path = Path("/app/.env.host")
        if env_path.exists():
            archive.write(env_path, "config/.env")
    backups = sorted(root.glob("deltajanebi-*.zip"), reverse=True)
    for old in backups[10:]:
        try:
            old.unlink()
        except OSError:
            pass
    return path


def save_product_image(product, image_bytes=None, filename=None, link=""):
    if image_bytes and filename:
        if product.image:
            try:
                product.image.delete(save=False)
            except Exception:
                pass
        product.image.save(filename, ContentFile(image_bytes), save=False)
        product.manual_image_url_override = ""
        product.save(update_fields=["image", "manual_image_url_override"])
    elif link:
        if product.image:
            try:
                product.image.delete(save=False)
            except Exception:
                pass
        product.image = ""
        product.manual_image_url_override = link
        product.save(update_fields=["image", "manual_image_url_override"])
    return product


def save_site_logo(image_bytes=None, filename=None, link="", clear=False):
    site = SiteSetting.load()
    if site.logo:
        try:
            site.logo.delete(save=False)
        except Exception:
            pass
    if clear:
        site.logo = ""
        site.logo_url = ""
    elif image_bytes and filename:
        site.logo.save(filename, ContentFile(image_bytes), save=False)
        site.logo_url = ""
    else:
        site.logo = ""
        site.logo_url = link
    site.save(update_fields=["logo", "logo_url"])
    return site


def save_banner_image(flow, image_bytes=None, filename=None, link=""):
    banner = Banner(title=flow.get("title", ""), target_url=flow.get("target_url", ""))
    if image_bytes and filename:
        banner.image.save(filename, ContentFile(image_bytes), save=False)
    else:
        banner.image_url = link
    banner.save()
    return banner


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    context.user_data.clear()
    site = await sync_to_async(SiteSetting.load)()
    await update.effective_message.reply_text(f"مدیریت {site.store_name}", reply_markup=main_menu())


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    clear_state(context)
    await update.effective_message.reply_text("عملیات لغو شد.", reply_markup=main_menu())


async def show_product_list(message, source_type, page=0):
    page = max(0, page)
    start_idx = page * PAGE_SIZE
    rows = await sync_to_async(list)(Product.objects.filter(source_type=source_type).order_by("-id")[start_idx:start_idx + PAGE_SIZE + 1])
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    title = "محصولات عادی" if source_type == Product.MANUAL else "محصولات خاص"
    buttons = [[InlineKeyboardButton(f"{p.public_code or '-'} | {p.name[:34]}", callback_data=f"p:view:{p.id}")] for p in rows]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"plist:{source_type}:{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"plist:{source_type}:{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("➕ افزودن", callback_data=f"add:{source_type}"), InlineKeyboardButton("⬅️ برگشت", callback_data=f"m:{source_type}")])
    await message.reply_text(f"📋 {title} — صفحه {page+1}\nروی نام محصول بزن:", reply_markup=InlineKeyboardMarkup(buttons))


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data == "main":
        clear_state(context)
        await q.message.reply_text("منوی اصلی:", reply_markup=main_menu())
        return
    if data in ("m:manual", "m:synced"):
        clear_state(context)
        source_type = Product.MANUAL if data.endswith("manual") else Product.SYNCED
        await q.message.reply_text("مدیریت محصولات:", reply_markup=product_menu(source_type))
        return
    if data.startswith("plist:"):
        _, source_type, page = data.split(":", 2)
        await show_product_list(q.message, source_type, int(page))
        return
    if data.startswith("add:"):
        source_type = data.split(":", 1)[1]
        if source_type == Product.MANUAL:
            set_state(context, "manual_name")
            await q.message.reply_text("نام محصول عادی را بفرست:")
        else:
            set_state(context, "special_url")
            await q.message.reply_text("لینک محصول سایت منبع را بفرست:")
        return
    if data == "search":
        set_state(context, "product_search")
        await q.message.reply_text("کد اختصاصی محصول را بفرست؛ مثال: DJ-000001")
        return
    if data == "offers":
        rows = await sync_to_async(list)(Product.objects.filter(sale_price__isnull=False).order_by("sale_ends_at")[:20])
        active = [p for p in rows if p.is_sale_active]
        buttons = [[InlineKeyboardButton(f"⭐ {p.public_code} | {p.name[:30]}", callback_data=f"p:view:{p.id}")] for p in active]
        buttons.append([InlineKeyboardButton("⬅️ منوی اصلی", callback_data="main")])
        await q.message.reply_text(f"⭐ پیشنهادهای فعال: {len(active)}", reply_markup=InlineKeyboardMarkup(buttons))
        return
    if data.startswith("p:view:"):
        pid = int(data.rsplit(":", 1)[1])
        try:
            product = await sync_to_async(Product.objects.select_related("category").get)(pk=pid)
        except Product.DoesNotExist:
            await q.message.reply_text("محصول پیدا نشد.", reply_markup=main_menu())
            return
        await q.message.reply_text(product_text(product), reply_markup=product_actions(product))
        return
    if data.startswith("p:toggle:"):
        pid = int(data.rsplit(":", 1)[1])
        product = await sync_to_async(Product.objects.get)(pk=pid)
        product.is_active = not product.is_active
        await sync_to_async(product.save)(update_fields=["is_active"])
        await q.message.reply_text(product_text(product), reply_markup=product_actions(product))
        return
    if data.startswith("p:delask:"):
        pid = int(data.rsplit(":", 1)[1])
        product = await sync_to_async(Product.objects.get)(pk=pid)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ بله، حذف شود", callback_data=f"p:delete:{pid}"), InlineKeyboardButton("❌ انصراف", callback_data=f"p:view:{pid}")]])
        await q.message.reply_text(f"محصول «{product.name}» برای همیشه حذف شود؟", reply_markup=kb)
        return
    if data.startswith("p:delete:"):
        pid = int(data.rsplit(":", 1)[1])
        product = await sync_to_async(Product.objects.get)(pk=pid)
        source_type = product.source_type
        await sync_to_async(product.delete)()
        await q.message.reply_text("✅ محصول حذف شد.", reply_markup=product_menu(source_type))
        return
    if any(data.startswith(prefix) for prefix in ("p:name:", "p:price:", "p:stock:", "p:image:")):
        action, pid = data.split(":")[1:]
        set_state(context, f"edit_{action}", product_id=int(pid))
        prompts = {"name": "نام جدید محصول را بفرست:", "price": "قیمت جدید به تومان را بفرست:", "stock": "موجودی جدید را بفرست:", "image": "عکس جدید را مستقیم بفرست، فایل تصویر بفرست یا لینک http/https ارسال کن:"}
        await q.message.reply_text(prompts[action])
        return
    if data.startswith("p:sale:"):
        pid = int(data.rsplit(":", 1)[1])
        set_state(context, "sale_price", product_id=pid)
        await q.message.reply_text("قیمت جدید پیشنهاد ویژه را به تومان بفرست:")
        return
    if data.startswith("p:saleclear:"):
        pid = int(data.rsplit(":", 1)[1])
        product = await sync_to_async(Product.objects.get)(pk=pid)
        await sync_to_async(product.clear_sale)()
        await q.message.reply_text("✅ پیشنهاد ویژه حذف شد.", reply_markup=product_actions(product))
        return
    if data.startswith("p:sync:"):
        pid = int(data.rsplit(":", 1)[1])
        product = await sync_to_async(Product.objects.get)(pk=pid)
        await sync_to_async(sync_product)(product)
        product = await sync_to_async(Product.objects.get)(pk=pid)
        await q.message.reply_text("✅ محصول از منبع بررسی شد.\n\n" + product_text(product), reply_markup=product_actions(product))
        return
    if data.startswith("p:resetsync:"):
        pid = int(data.rsplit(":", 1)[1])
        product = await sync_to_async(Product.objects.get)(pk=pid)
        def reset_sync():
            product.manual_name_override = ""
            product.manual_price_override = None
            product.manual_stock_override = None
            product.manual_image_url_override = ""
            if product.image:
                try:
                    product.image.delete(save=False)
                except Exception:
                    pass
            product.image = ""
            product.save()
            sync_product(product)
            return Product.objects.get(pk=pid)
        product = await sync_to_async(reset_sync)()
        await q.message.reply_text("✅ تغییرات دستی پاک شد و محصول دوباره Sync شد.\n\n" + product_text(product), reply_markup=product_actions(product))
        return
    if data == "orders":
        rows = await sync_to_async(list)(Order.objects.all()[:12])
        text = "\n\n".join([f"#{o.id} | {o.get_status_display()} | {o.total:,} تومان | {o.full_name} | /order_{o.id}" for o in rows]) or "سفارشی نیست."
        await q.message.reply_text(text, reply_markup=main_menu())
        return
    if data == "categories":
        text = await sync_to_async(category_tree_text)()
        await q.message.reply_text(text, reply_markup=main_menu())
        return
    if data == "syncall":
        rows = await sync_to_async(list)(Product.objects.filter(source_type=Product.SYNCED, is_active=True))
        for product in rows:
            await sync_to_async(sync_product)(product)
        await q.message.reply_text(f"✅ {len(rows)} محصول خاص بررسی و همگام شد.", reply_markup=main_menu())
        return
    if data == "settings":
        clear_state(context)
        site = await sync_to_async(SiteSetting.load)()
        await q.message.reply_text(f"⚙️ تنظیمات سایت\nنام فعلی: {site.store_name}\nهزینه ارسال: {site.shipping_cost:,} تومان", reply_markup=settings_menu())
        return
    if data == "set:name":
        set_state(context, "set_site_name")
        await q.message.reply_text("نام جدید سایت را بفرست:")
        return
    if data == "set:shipping":
        set_state(context, "set_shipping")
        await q.message.reply_text("هزینه ارسال جدید به تومان:")
        return
    if data == "set:logo":
        set_state(context, "set_logo")
        await q.message.reply_text("لوگوی جدید را به صورت عکس/فایل تصویر/لینک بفرست. برای حذف لوگو - بفرست.")
        return
    if data == "social:list":
        rows = await sync_to_async(list)(SocialLink.objects.all()[:30])
        buttons = [[InlineKeyboardButton(f"{'✅' if x.is_active else '⛔'} {x.label}", callback_data=f"social:toggle:{x.id}"), InlineKeyboardButton("🗑", callback_data=f"social:delete:{x.id}")] for x in rows]
        buttons.append([InlineKeyboardButton("➕ افزودن شبکه اجتماعی", callback_data="social:add")])
        buttons.append([InlineKeyboardButton("⬅️ تنظیمات", callback_data="settings")])
        await q.message.reply_text("🌐 شبکه‌های اجتماعی", reply_markup=InlineKeyboardMarkup(buttons))
        return
    if data == "social:add":
        buttons = [[InlineKeyboardButton(label, callback_data=f"social:platform:{value}")] for value, label in SocialLink.PLATFORM_CHOICES]
        buttons.append([InlineKeyboardButton("⬅️ برگشت", callback_data="social:list")])
        await q.message.reply_text("نوع شبکه را انتخاب کن:", reply_markup=InlineKeyboardMarkup(buttons))
        return
    if data.startswith("social:platform:"):
        platform = data.rsplit(":", 1)[1]
        set_state(context, "social_label", platform=platform)
        await q.message.reply_text("عنوانی که روی سایت نمایش داده شود را بفرست؛ مثلاً Instagram یا پشتیبانی تلگرام:")
        return
    if data.startswith("social:toggle:"):
        sid = int(data.rsplit(":", 1)[1])
        item = await sync_to_async(SocialLink.objects.get)(pk=sid)
        item.is_active = not item.is_active
        await sync_to_async(item.save)(update_fields=["is_active"])
        await q.message.reply_text("✅ وضعیت تغییر کرد.", reply_markup=settings_menu())
        return
    if data.startswith("social:delete:"):
        sid = int(data.rsplit(":", 1)[1])
        await sync_to_async(SocialLink.objects.filter(pk=sid).delete)()
        await q.message.reply_text("✅ شبکه اجتماعی حذف شد.", reply_markup=settings_menu())
        return
    if data == "banner:list":
        rows = await sync_to_async(list)(Banner.objects.all()[:20])
        buttons = [[InlineKeyboardButton(f"{'✅' if b.is_active else '⛔'} {b.title or ('بنر #' + str(b.id))}", callback_data=f"banner:toggle:{b.id}"), InlineKeyboardButton("🗑", callback_data=f"banner:delete:{b.id}")] for b in rows]
        buttons.append([InlineKeyboardButton("➕ افزودن بنر", callback_data="banner:add")])
        buttons.append([InlineKeyboardButton("⬅️ تنظیمات", callback_data="settings")])
        await q.message.reply_text("📣 بنرهای صفحه اول", reply_markup=InlineKeyboardMarkup(buttons))
        return
    if data == "banner:add":
        set_state(context, "banner_title")
        await q.message.reply_text("عنوان بنر را بفرست. اگر عنوان نمی‌خواهی - بفرست:")
        return
    if data.startswith("banner:toggle:"):
        bid = int(data.rsplit(":", 1)[1])
        banner = await sync_to_async(Banner.objects.get)(pk=bid)
        banner.is_active = not banner.is_active
        await sync_to_async(banner.save)(update_fields=["is_active"])
        await q.message.reply_text("✅ وضعیت بنر تغییر کرد.", reply_markup=settings_menu())
        return
    if data.startswith("banner:delete:"):
        bid = int(data.rsplit(":", 1)[1])
        await sync_to_async(Banner.objects.filter(pk=bid).delete)()
        await q.message.reply_text("✅ بنر حذف شد.", reply_markup=settings_menu())
        return
    if data == "backup":
        clear_state(context)
        await q.message.reply_text("💾 مدیریت بکاپ", reply_markup=backup_menu())
        return
    if data == "backup:create":
        status = await q.message.reply_text("در حال ساخت بکاپ کامل...")
        try:
            path = await sync_to_async(create_backup_archive)()
            size = path.stat().st_size
            await status.edit_text(f"✅ بکاپ ساخته شد: {path.name}\nحجم: {size/1024/1024:.1f} MB")
            if size <= 48 * 1024 * 1024:
                with path.open("rb") as fh:
                    await q.message.reply_document(document=fh, filename=path.name, caption="بکاپ دلتا جانبی")
            else:
                await q.message.reply_text("حجم بکاپ برای ارسال مستقیم تلگرام بزرگ است و روی سرور ذخیره شد.", reply_markup=backup_menu())
        except Exception as exc:
            await status.edit_text(f"❌ خطا در بکاپ: {exc}")
        return
    if data == "backup:list":
        files = sorted(backup_dir().glob("deltajanebi-*.zip"), reverse=True)[:10]
        context.user_data["backup_files"] = [str(x) for x in files]
        buttons = [[InlineKeyboardButton(f"💾 {p.name} ({p.stat().st_size/1024/1024:.1f}MB)", callback_data=f"backup:get:{i}")] for i, p in enumerate(files)]
        buttons.append([InlineKeyboardButton("⬅️ بکاپ", callback_data="backup")])
        await q.message.reply_text("📂 آخرین بکاپ‌ها", reply_markup=InlineKeyboardMarkup(buttons))
        return
    if data.startswith("backup:get:"):
        idx = int(data.rsplit(":", 1)[1])
        files = context.user_data.get("backup_files") or []
        if idx >= len(files):
            await q.message.reply_text("لیست بکاپ منقضی شده؛ دوباره بخش بکاپ‌ها را باز کن.")
            return
        path = Path(files[idx])
        if not path.exists():
            await q.message.reply_text("فایل بکاپ پیدا نشد.")
            return
        if path.stat().st_size > 48 * 1024 * 1024:
            await q.message.reply_text("این بکاپ برای ارسال مستقیم تلگرام بزرگ است و روی سرور باقی می‌ماند.")
            return
        with path.open("rb") as fh:
            await q.message.reply_document(document=fh, filename=path.name)
        return


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    message = update.effective_message
    state = context.user_data.get("awaiting")
    flow = context.user_data.setdefault("flow", {})
    if not state:
        await message.reply_text("از منوی مدیریت یک گزینه را انتخاب کن.", reply_markup=main_menu())
        return

    text = message.text.strip() if message.text else ""

    if state == "product_search":
        code = text.upper().replace(" ", "")
        product = await sync_to_async(Product.objects.filter(public_code__iexact=code).first)()
        if not product:
            product = await sync_to_async(Product.objects.filter(source_product_code__iexact=text).first)()
        if not product:
            await message.reply_text("❌ محصولی با این کد پیدا نشد. دوباره کد را بفرست یا /cancel بزن.")
            return
        clear_state(context)
        await message.reply_text(product_text(product), reply_markup=product_actions(product))
        return

    if state == "manual_name":
        if not text:
            await message.reply_text("نام محصول را متنی بفرست.")
            return
        flow["name"] = text[:300]
        context.user_data["awaiting"] = "manual_price"
        await message.reply_text("قیمت محصول به تومان:")
        return
    if state == "manual_price":
        try:
            flow["price"] = parse_nonnegative_int(text)
        except ValueError:
            await message.reply_text("فقط عدد بفرست.")
            return
        context.user_data["awaiting"] = "manual_stock"
        await message.reply_text("موجودی محصول:")
        return
    if state == "manual_stock":
        try:
            flow["stock"] = parse_nonnegative_int(text)
        except ValueError:
            await message.reply_text("فقط عدد بفرست.")
            return
        context.user_data["awaiting"] = "manual_category"
        await message.reply_text("دسته‌بندی را بفرست؛ مثال: جانبی موبایل > کابل > کابل شارژ موبایل\nاگر نمی‌خواهی - بفرست.")
        return
    if state == "manual_category":
        flow["categories"] = parse_category_path(text)
        context.user_data["awaiting"] = "manual_image"
        await message.reply_text("عکس محصول را مستقیم بفرست، فایل تصویر/لینک بفرست یا - برای بدون عکس.")
        return
    if state == "manual_image":
        try:
            raw, filename = await telegram_image_bytes(message, "product")
        except ValueError as exc:
            await message.reply_text(f"❌ {exc}")
            return
        link = ""
        if not raw and text != "-":
            if not re.match(r"^https?://", text, re.I):
                await message.reply_text("عکس، لینک معتبر یا - بفرست.")
                return
            link = text
        def create_manual():
            category = sync_category_path(flow.get("categories") or [])
            p = Product(category=category, name=flow["name"], price=flow["price"], stock=flow["stock"], source_type=Product.MANUAL)
            if raw and filename:
                p.image.save(filename, ContentFile(raw), save=False)
            elif link:
                p.image_url = link
            p.save()
            if p.primary_image and not p.gallery:
                p.gallery = [p.primary_image]
                p.save(update_fields=["gallery"])
            return p
        product = await sync_to_async(create_manual)()
        clear_state(context)
        await message.reply_text(f"✅ محصول عادی ثبت شد.\nکد اختصاصی: {product.public_code}\nقیمت: {product.price:,} تومان", reply_markup=product_actions(product))
        return

    if state == "special_url":
        if not text:
            await message.reply_text("لینک محصول را متنی بفرست.")
            return
        status = await message.reply_text("در حال خواندن محصول...")
        try:
            data = await sync_to_async(scrape_product)(text)
        except Exception as exc:
            await status.edit_text(f"❌ {exc}\nدوباره لینک را بفرست.")
            return
        flow["url"] = text
        flow["data"] = data
        context.user_data["awaiting"] = "special_markup"
        cats = " ← ".join(data.get("categories") or []) or "تشخیص داده نشد"
        await status.edit_text(f"✅ {data['name']}\nقیمت منبع: {data['price']:,}\nموجودی: {data['stock']}\nدسته: {cats}\n\nافزایش را بفرست: 20% یا 20000")
        return
    if state == "special_markup":
        try:
            typ, val = parse_markup_input(text)
        except ValueError:
            await message.reply_text("فرمت نامعتبر است؛ مثال 20% یا 20000")
            return
        data = flow["data"]
        def create_special():
            sku = data.get("sku") or None
            if sku and Product.objects.filter(sku=sku).exists():
                sku = None
            category = sync_category_path(data.get("categories") or [])
            p = Product.objects.create(category=category, name=data["name"], description=data["description"], source_type=Product.SYNCED, source_url=flow["url"], source_product_code=data.get("sku", "") or "", source_price=data["price"], stock=data["stock"], image_url=data["image_url"], gallery=data["gallery"], specs=data["specs"], sku=sku, markup_type=typ, markup_value=val)
            p.price = p.apply_markup(data["price"])
            p.save()
            return p
        product = await sync_to_async(create_special)()
        clear_state(context)
        await message.reply_text(f"✅ محصول خاص ثبت شد.\nکد اختصاصی: {product.public_code}\nقیمت فروش: {product.price:,} تومان", reply_markup=product_actions(product))
        return

    if state.startswith("edit_"):
        pid = int(flow["product_id"])
        product = await sync_to_async(Product.objects.get)(pk=pid)
        action = state[5:]
        if action == "name":
            if not text:
                await message.reply_text("نام جدید را متنی بفرست.")
                return
            if product.source_type == Product.SYNCED:
                product.manual_name_override = text[:300]
            product.name = text[:300]
            await sync_to_async(product.save)()
        elif action == "price":
            try:
                value = parse_nonnegative_int(text)
            except ValueError:
                await message.reply_text("قیمت را عددی بفرست.")
                return
            if product.source_type == Product.SYNCED:
                product.manual_price_override = value
            product.price = value
            await sync_to_async(product.save)()
        elif action == "stock":
            try:
                value = parse_nonnegative_int(text)
            except ValueError:
                await message.reply_text("موجودی را عددی بفرست.")
                return
            if product.source_type == Product.SYNCED:
                product.manual_stock_override = value
            product.stock = value
            await sync_to_async(product.save)()
        elif action == "image":
            try:
                raw, filename = await telegram_image_bytes(message, "product-edit")
            except ValueError as exc:
                await message.reply_text(f"❌ {exc}")
                return
            link = ""
            if not raw:
                if not re.match(r"^https?://", text, re.I):
                    await message.reply_text("عکس مستقیم یا لینک معتبر بفرست.")
                    return
                link = text
            product = await sync_to_async(save_product_image)(product, raw, filename, link)
        clear_state(context)
        product = await sync_to_async(Product.objects.get)(pk=pid)
        await message.reply_text("✅ محصول ویرایش شد.\n\n" + product_text(product), reply_markup=product_actions(product))
        return

    if state == "sale_price":
        try:
            value = parse_nonnegative_int(text)
        except ValueError:
            await message.reply_text("قیمت پیشنهاد را عددی بفرست.")
            return
        product = await sync_to_async(Product.objects.get)(pk=int(flow["product_id"]))
        if value >= product.price:
            await message.reply_text(f"قیمت پیشنهاد باید کمتر از قیمت پایه {product.price:,} تومان باشد.")
            return
        flow["sale_price"] = value
        context.user_data["awaiting"] = "sale_duration"
        await message.reply_text("مدت پیشنهاد را بفرست؛ مثال: 30m ، 2h ، 1d یا فقط 60 برای ۶۰ دقیقه:")
        return
    if state == "sale_duration":
        try:
            duration = parse_duration(text)
        except ValueError:
            await message.reply_text("مدت نامعتبر است؛ مثال 30m یا 2h یا 1d")
            return
        product = await sync_to_async(Product.objects.get)(pk=int(flow["product_id"]))
        product.sale_price = flow["sale_price"]
        product.sale_starts_at = timezone.now()
        product.sale_ends_at = timezone.now() + duration
        await sync_to_async(product.save)(update_fields=["sale_price", "sale_starts_at", "sale_ends_at"])
        clear_state(context)
        await message.reply_text("✅ پیشنهاد زمان‌دار فعال شد.\n\n" + product_text(product), reply_markup=product_actions(product))
        return

    if state == "set_site_name":
        if not text:
            await message.reply_text("نام جدید را بفرست.")
            return
        site = await sync_to_async(SiteSetting.load)()
        site.store_name = text[:120]
        await sync_to_async(site.save)(update_fields=["store_name"])
        clear_state(context)
        await message.reply_text(f"✅ نام سایت شد: {site.store_name}", reply_markup=settings_menu())
        return
    if state == "set_shipping":
        try:
            value = parse_nonnegative_int(text)
        except ValueError:
            await message.reply_text("فقط عدد بفرست.")
            return
        site = await sync_to_async(SiteSetting.load)()
        site.shipping_cost = value
        await sync_to_async(site.save)(update_fields=["shipping_cost"])
        clear_state(context)
        await message.reply_text("✅ هزینه ارسال تغییر کرد.", reply_markup=settings_menu())
        return
    if state == "set_logo":
        try:
            raw, filename = await telegram_image_bytes(message, "logo")
        except ValueError as exc:
            await message.reply_text(f"❌ {exc}")
            return
        clear = text == "-"
        link = ""
        if not raw and not clear:
            if not re.match(r"^https?://", text, re.I):
                await message.reply_text("عکس مستقیم، لینک معتبر یا - بفرست.")
                return
            link = text
        await sync_to_async(save_site_logo)(raw, filename, link, clear)
        clear_state(context)
        await message.reply_text("✅ لوگوی سایت به‌روزرسانی شد.", reply_markup=settings_menu())
        return

    if state == "social_label":
        if not text:
            await message.reply_text("عنوان را بفرست.")
            return
        flow["label"] = text[:80]
        context.user_data["awaiting"] = "social_url"
        await message.reply_text("لینک کامل شبکه اجتماعی را بفرست؛ با https://")
        return
    if state == "social_url":
        if not re.match(r"^https?://", text, re.I):
            await message.reply_text("لینک معتبر http/https بفرست.")
            return
        await sync_to_async(SocialLink.objects.create)(platform=flow["platform"], label=flow["label"], url=text)
        clear_state(context)
        await message.reply_text("✅ شبکه اجتماعی اضافه شد.", reply_markup=settings_menu())
        return

    if state == "banner_title":
        flow["title"] = "" if text == "-" else text[:160]
        context.user_data["awaiting"] = "banner_target"
        await message.reply_text("لینک مقصد بنر را بفرست؛ اگر بدون لینک است - بفرست:")
        return
    if state == "banner_target":
        if text != "-" and not re.match(r"^https?://", text, re.I):
            await message.reply_text("لینک معتبر یا - بفرست.")
            return
        flow["target_url"] = "" if text == "-" else text
        context.user_data["awaiting"] = "banner_image"
        await message.reply_text("حالا عکس بنر را مستقیم بفرست، فایل تصویر یا لینک عکس بفرست:")
        return
    if state == "banner_image":
        try:
            raw, filename = await telegram_image_bytes(message, "banner")
        except ValueError as exc:
            await message.reply_text(f"❌ {exc}")
            return
        link = ""
        if not raw:
            if not re.match(r"^https?://", text, re.I):
                await message.reply_text("عکس مستقیم یا لینک معتبر بفرست.")
                return
            link = text
        banner = await sync_to_async(save_banner_image)(flow, raw, filename, link)
        clear_state(context)
        await message.reply_text(f"✅ بنر #{banner.id} اضافه شد.", reply_markup=settings_menu())
        return


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
        [InlineKeyboardButton("✅ آماده‌سازی", callback_data=f"order:{oid}:preparing"), InlineKeyboardButton("🚚 ارسال شد", callback_data=f"order:{oid}:shipped")],
        [InlineKeyboardButton("✅ تحویل شد", callback_data=f"order:{oid}:delivered"), InlineKeyboardButton("❌ لغو", callback_data=f"order:{oid}:cancelled")],
    ])
    await update.message.reply_text(f"سفارش #{order.id}\n{order.full_name} | {order.phone}\n{order.total:,} تومان\nوضعیت: {order.get_status_display()}\nآدرس: {order.province}، {order.city}، {order.address}", reply_markup=kb)


async def order_callback(update, context):
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
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("cancel", cancel))
        app.add_handler(MessageHandler(filters.Regex(r"^/order_\d+$"), order_cmd))
        app.add_handler(CallbackQueryHandler(order_callback, pattern=r"^order:\d+:(preparing|shipped|delivered|cancelled)$"))
        app.add_handler(CallbackQueryHandler(on_callback))
        app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, on_message))
        app.run_polling(allowed_updates=Update.ALL_TYPES)
