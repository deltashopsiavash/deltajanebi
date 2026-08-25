import os
import re
import uuid
from pathlib import Path

from asgiref.sync import sync_to_async
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from shop.management.commands import telegram_bot as old
from shop.management.commands import telegram_bot_v3 as v3
from shop.models import Category, Product, SourceSite
from shop.services.source_sync import scrape_product, sync_category_path
from shop.source_registry import normalize_site_url, registered_source_for_url

PAGE_SIZE = 9


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧰 محصولات عادی", callback_data="m:manual"), InlineKeyboardButton("🔗 محصولات خاص", callback_data="m:synced")],
        [InlineKeyboardButton("🔎 جستجوی محصول", callback_data="search"), InlineKeyboardButton("⭐ پیشنهادهای فعال", callback_data="offers")],
        [InlineKeyboardButton("📦 سفارش‌ها", callback_data="orders"), InlineKeyboardButton("📂 دسته‌بندی‌ها", callback_data="categories")],
        [InlineKeyboardButton("🌐 سایت‌های منبع", callback_data="source:list"), InlineKeyboardButton("🔄 همگام‌سازی همه", callback_data="syncall")],
        [InlineKeyboardButton("⚙️ تنظیمات سایت", callback_data="settings"), InlineKeyboardButton("📝 توضیحات و فوتر", callback_data="set:footer")],
        [InlineKeyboardButton("🛡 نمادها", callback_data="badge:list"), InlineKeyboardButton("☎️ تلفن", callback_data="set:phone")],
        [InlineKeyboardButton("💾 بکاپ", callback_data="backup")],
    ])


def category_path(category):
    return " ← ".join(item.name for item in category.ancestor_chain())


def category_text(category):
    child_count = category.children.count()
    product_count = category.products.count()
    return (
        f"📂 {category.name}\n"
        f"مسیر: {category_path(category)}\n"
        f"وضعیت: {'✅ نمایش' if category.is_active else '⛔ مخفی'}\n"
        f"تصویر: {'✅ دارد' if category.image_url else '⬜ ندارد'}\n"
        f"زیردسته مستقیم: {child_count}\n"
        f"محصول مستقیم: {product_count}"
    )


def category_actions(category):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ تغییر نام", callback_data=f"cat:name:{category.id}"), InlineKeyboardButton("🖼 تغییر عکس", callback_data=f"cat:image:{category.id}")],
        [InlineKeyboardButton("👁 نمایش / مخفی", callback_data=f"cat:toggle:{category.id}"), InlineKeyboardButton("🗑 حذف دسته", callback_data=f"cat:delask:{category.id}")],
        [InlineKeyboardButton("⬅️ همه دسته‌ها", callback_data="cat:list:0"), InlineKeyboardButton("🏠 منوی اصلی", callback_data="main")],
    ])


def source_text(site):
    count = Product.objects.filter(source_type=Product.SYNCED, source_url__icontains=site.hostname).count()
    return (
        f"🌐 {site.name}\n"
        f"دامنه: {site.hostname}\n"
        f"آدرس پایه: {site.base_url}\n"
        f"وضعیت: {'✅ فعال' if site.is_active else '⛔ غیرفعال'}\n"
        f"محصول خاص مرتبط: {count}"
    )


def source_actions(site):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ تغییر نام", callback_data=f"source:name:{site.id}"), InlineKeyboardButton("⏯ فعال / غیرفعال", callback_data=f"source:toggle:{site.id}")],
        [InlineKeyboardButton("🗑 حذف سایت", callback_data=f"source:delask:{site.id}")],
        [InlineKeyboardButton("⬅️ سایت‌های منبع", callback_data="source:list"), InlineKeyboardButton("🏠 منوی اصلی", callback_data="main")],
    ])


def save_category_image(category, raw=None, filename=None, link="", clear=False):
    if clear:
        category.image_url = ""
        category.save(update_fields=["image_url"])
        return category
    if raw and filename:
        suffix = Path(filename).suffix.lower() or ".jpg"
        path = default_storage.save(f"categories/{uuid.uuid4().hex}{suffix}", ContentFile(raw))
        category.image_url = default_storage.url(path)
    else:
        category.image_url = link
    category.save(update_fields=["image_url"])
    return category


async def show_categories(message, page=0):
    page = max(0, page)
    start = page * PAGE_SIZE
    rows = await sync_to_async(list)(Category.objects.select_related("parent").order_by("parent_id", "order", "name")[start:start + PAGE_SIZE + 1])
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    buttons = []
    for category in rows:
        prefix = "✅" if category.is_active else "⛔"
        buttons.append([InlineKeyboardButton(f"{prefix} {category_path(category)[:46]}", callback_data=f"cat:view:{category.id}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"cat:list:{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"cat:list:{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🏠 منوی اصلی", callback_data="main")])
    await message.reply_text(f"📂 مدیریت همه دسته‌بندی‌ها — صفحه {page + 1}\nروی هر دسته بزن تا ویرایشش کنی:", reply_markup=InlineKeyboardMarkup(buttons))


async def show_sources(message):
    rows = await sync_to_async(list)(SourceSite.objects.order_by("name", "id")[:40])
    buttons = [[InlineKeyboardButton(f"{'✅' if site.is_active else '⛔'} {site.name} • {site.hostname}", callback_data=f"source:view:{site.id}")] for site in rows]
    buttons.append([InlineKeyboardButton("➕ ثبت سایت جدید", callback_data="source:add")])
    buttons.append([InlineKeyboardButton("🏠 منوی اصلی", callback_data="main")])
    await message.reply_text("🌐 سایت‌های منبع\nهر سایتی که می‌خواهی از آن محصول خاص وارد کنی، اول اینجا ثبت کن:", reply_markup=InlineKeyboardMarkup(buttons))


async def choose_source_for_product(message):
    rows = await sync_to_async(list)(SourceSite.objects.filter(is_active=True).order_by("name", "id")[:30])
    if not rows:
        await message.reply_text("هنوز سایت منبع فعالی ثبت نشده.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ ثبت سایت منبع", callback_data="source:add")],
            [InlineKeyboardButton("⬅️ منوی اصلی", callback_data="main")],
        ]))
        return
    buttons = [[InlineKeyboardButton(f"🌐 {site.name} • {site.hostname}", callback_data=f"source:pick:{site.id}")] for site in rows]
    buttons.append([InlineKeyboardButton("➕ ثبت سایت جدید", callback_data="source:add")])
    buttons.append([InlineKeyboardButton("⬅️ محصولات خاص", callback_data="m:synced")])
    await message.reply_text("اول سایت منبع محصول را انتخاب کن:", reply_markup=InlineKeyboardMarkup(buttons))


async def on_callback(update: Update, context):
    if not old.allowed(update):
        return
    q = update.callback_query
    data = q.data or ""

    if data == "categories":
        await q.answer()
        old.clear_state(context)
        await show_categories(q.message, 0)
        return
    if data.startswith("cat:list:"):
        await q.answer()
        await show_categories(q.message, int(data.rsplit(":", 1)[1]))
        return
    if data.startswith("cat:view:"):
        await q.answer()
        category = await sync_to_async(Category.objects.get)(pk=int(data.rsplit(":", 1)[1]))
        await q.message.reply_text(await sync_to_async(category_text)(category), reply_markup=category_actions(category))
        return
    if data.startswith("cat:name:"):
        await q.answer()
        category_id = int(data.rsplit(":", 1)[1])
        category = await sync_to_async(Category.objects.get)(pk=category_id)
        old.set_state(context, "cat_edit_name", category_id=category_id)
        await q.message.reply_text(f"نام فعلی: {category.name}\nنام جدید دسته را بفرست:")
        return
    if data.startswith("cat:image:"):
        await q.answer()
        category_id = int(data.rsplit(":", 1)[1])
        old.set_state(context, "cat_edit_image", category_id=category_id)
        await q.message.reply_text("عکس اختصاصی دسته را مستقیم بفرست، فایل تصویر یا لینک عکس ارسال کن. برای پاک‌کردن عکس - بفرست.")
        return
    if data.startswith("cat:toggle:"):
        await q.answer()
        category = await sync_to_async(Category.objects.get)(pk=int(data.rsplit(":", 1)[1]))
        category.is_active = not category.is_active
        await sync_to_async(category.save)(update_fields=["is_active"])
        await q.message.reply_text(await sync_to_async(category_text)(category), reply_markup=category_actions(category))
        return
    if data.startswith("cat:delask:"):
        await q.answer()
        category = await sync_to_async(Category.objects.get)(pk=int(data.rsplit(":", 1)[1]))
        child_count = await sync_to_async(category.children.count)()
        product_count = await sync_to_async(category.products.count)()
        await q.message.reply_text(
            f"⚠️ حذف «{category.name}»؟\nزیردسته مستقیم: {child_count}\nمحصول مستقیم: {product_count}\n\nبا حذف، زیردسته‌های این شاخه هم حذف می‌شوند و محصولات بدون دسته می‌مانند.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ حذف قطعی", callback_data=f"cat:delete:{category.id}"), InlineKeyboardButton("❌ انصراف", callback_data=f"cat:view:{category.id}")]])
        )
        return
    if data.startswith("cat:delete:"):
        await q.answer()
        category = await sync_to_async(Category.objects.get)(pk=int(data.rsplit(":", 1)[1]))
        name = category.name
        await sync_to_async(category.delete)()
        await q.message.reply_text(f"✅ دسته «{name}» حذف شد.")
        await show_categories(q.message, 0)
        return

    if data == "source:list":
        await q.answer()
        old.clear_state(context)
        await show_sources(q.message)
        return
    if data == "source:add":
        await q.answer()
        old.set_state(context, "source_add_url")
        await q.message.reply_text("🌐 آدرس سایت منبع را بفرست؛ مثال:\nhttps://example.com\n\nبعد از ثبت، می‌توانی از لینک محصولات همین دامنه محصول خاص بسازی.")
        return
    if data.startswith("source:view:"):
        await q.answer()
        site = await sync_to_async(SourceSite.objects.get)(pk=int(data.rsplit(":", 1)[1]))
        await q.message.reply_text(await sync_to_async(source_text)(site), reply_markup=source_actions(site))
        return
    if data.startswith("source:name:"):
        await q.answer()
        site_id = int(data.rsplit(":", 1)[1])
        site = await sync_to_async(SourceSite.objects.get)(pk=site_id)
        old.set_state(context, "source_edit_name", source_id=site_id)
        await q.message.reply_text(f"نام فعلی: {site.name}\nنام نمایشی جدید سایت منبع را بفرست:")
        return
    if data.startswith("source:toggle:"):
        await q.answer()
        site = await sync_to_async(SourceSite.objects.get)(pk=int(data.rsplit(":", 1)[1]))
        site.is_active = not site.is_active
        await sync_to_async(site.save)(update_fields=["is_active"])
        await q.message.reply_text(await sync_to_async(source_text)(site), reply_markup=source_actions(site))
        return
    if data.startswith("source:delask:"):
        await q.answer()
        site = await sync_to_async(SourceSite.objects.get)(pk=int(data.rsplit(":", 1)[1]))
        count = await sync_to_async(Product.objects.filter(source_type=Product.SYNCED, source_url__icontains=site.hostname).count)()
        await q.message.reply_text(
            f"⚠️ سایت «{site.name}» حذف شود؟\n{count} محصول خاص از این دامنه ثبت شده. بعد از حذف، Sync آن‌ها متوقف می‌شود تا دوباره دامنه را ثبت کنی.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ حذف قطعی", callback_data=f"source:delete:{site.id}"), InlineKeyboardButton("❌ انصراف", callback_data=f"source:view:{site.id}")]])
        )
        return
    if data.startswith("source:delete:"):
        await q.answer()
        site = await sync_to_async(SourceSite.objects.get)(pk=int(data.rsplit(":", 1)[1]))
        name = site.name
        await sync_to_async(site.delete)()
        await q.message.reply_text(f"✅ سایت منبع «{name}» حذف شد.")
        await show_sources(q.message)
        return

    if data == "add:synced":
        await q.answer()
        old.clear_state(context)
        await choose_source_for_product(q.message)
        return
    if data.startswith("source:pick:"):
        await q.answer()
        site = await sync_to_async(SourceSite.objects.get)(pk=int(data.rsplit(":", 1)[1]), is_active=True)
        old.set_state(context, "special_source_url_v4", source_id=site.id)
        await q.message.reply_text(f"منبع انتخاب شد: {site.name}\nحالا لینک محصول از دامنه {site.hostname} را بفرست:")
        return

    await v3.on_callback(update, context)


async def on_message(update: Update, context):
    if not old.allowed(update):
        return
    message = update.effective_message
    state = context.user_data.get("awaiting")
    flow = context.user_data.setdefault("flow", {})
    text = message.text.strip() if message.text else ""

    if state == "cat_edit_name":
        if not text:
            await message.reply_text("نام دسته را متنی بفرست.")
            return
        category = await sync_to_async(Category.objects.get)(pk=int(flow["category_id"]))
        category.name = text[:120]
        await sync_to_async(category.save)(update_fields=["name"])
        old.clear_state(context)
        await message.reply_text("✅ نام دسته تغییر کرد.\n\n" + await sync_to_async(category_text)(category), reply_markup=category_actions(category))
        return

    if state == "cat_edit_image":
        category = await sync_to_async(Category.objects.get)(pk=int(flow["category_id"]))
        if text == "-":
            await sync_to_async(save_category_image)(category, clear=True)
        else:
            try:
                raw, filename = await old.telegram_image_bytes(message, "category")
            except ValueError as exc:
                await message.reply_text(f"❌ {exc}")
                return
            link = ""
            if not raw:
                if not re.match(r"^https?://", text, re.I):
                    await message.reply_text("عکس مستقیم، فایل تصویر، لینک معتبر یا - بفرست.")
                    return
                link = text
            await sync_to_async(save_category_image)(category, raw, filename, link)
        old.clear_state(context)
        await message.reply_text("✅ عکس دسته به‌روزرسانی شد.", reply_markup=category_actions(category))
        return

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
                brand_terms=hostname.split(".")[0],
                is_active=True,
            )
            created = True
        old.clear_state(context)
        await message.reply_text(("✅ سایت ثبت شد.\n\n" if created else "✅ سایت از قبل وجود داشت و دوباره فعال شد.\n\n") + await sync_to_async(source_text)(site), reply_markup=source_actions(site))
        return

    if state == "source_edit_name":
        if not text:
            await message.reply_text("نام را متنی بفرست.")
            return
        site = await sync_to_async(SourceSite.objects.get)(pk=int(flow["source_id"]))
        site.name = text[:120]
        site.brand_terms = ",".join(dict.fromkeys([x for x in [site.brand_terms, text[:120], site.hostname.split(".")[0]] if x]))[:500]
        await sync_to_async(site.save)(update_fields=["name", "brand_terms"])
        old.clear_state(context)
        await message.reply_text("✅ نام سایت منبع تغییر کرد.\n\n" + await sync_to_async(source_text)(site), reply_markup=source_actions(site))
        return

    if state == "special_source_url_v4":
        site = await sync_to_async(SourceSite.objects.get)(pk=int(flow["source_id"]), is_active=True)
        selected = await sync_to_async(registered_source_for_url)(text)
        if not selected or selected.id != site.id:
            await message.reply_text(f"❌ این لینک مربوط به {site.hostname} نیست. لینک محصول از همین سایت را بفرست.")
            return
        status = await message.reply_text("در حال خواندن محصول از سایت منبع...")
        try:
            data = await sync_to_async(scrape_product)(text)
        except Exception as exc:
            await status.edit_text(f"❌ خواندن محصول ناموفق بود:\n{exc}\n\nاگر این سایت ساختار اختصاصی دارد، parser مخصوص آن لازم می‌شود.")
            return
        flow["url"] = data.get("source_url") or text
        flow["data"] = data
        context.user_data["awaiting"] = "special_markup_v4"
        cats = " ← ".join(data.get("categories") or []) or "تشخیص داده نشد"
        await status.edit_text(
            f"✅ {data['name']}\n"
            f"منبع: {site.name}\n"
            f"قیمت منبع: {data['price']:,} تومان\n"
            f"موجودی: {data['stock']}\n"
            f"دسته: {cats}\n\n"
            "افزایش قیمت را بفرست؛ مثال 20% یا 20000"
        )
        return

    if state == "special_markup_v4":
        try:
            markup_type, markup_value = old.parse_markup_input(text)
        except ValueError:
            await message.reply_text("فرمت نامعتبر است؛ مثال 20% یا 20000")
            return
        data = flow["data"]

        def create_special():
            sku = data.get("sku") or None
            if sku and Product.objects.filter(sku=sku).exists():
                sku = None
            category = sync_category_path(data.get("categories") or [])
            product = Product.objects.create(
                category=category,
                name=data["name"],
                description=data.get("description", ""),
                source_type=Product.SYNCED,
                source_url=flow["url"],
                source_product_code=data.get("sku", "") or "",
                source_price=data["price"],
                stock=data["stock"],
                image_url=data.get("image_url", ""),
                gallery=data.get("gallery") or [],
                specs=data.get("specs") or {},
                sku=sku,
                markup_type=markup_type,
                markup_value=markup_value,
            )
            product.price = product.apply_markup(data["price"])
            product.save()
            return product

        product = await sync_to_async(create_special)()
        old.clear_state(context)
        await message.reply_text(
            f"✅ محصول خاص ثبت شد.\nکد اختصاصی: {product.public_code}\nقیمت فروش: {product.price:,} تومان",
            reply_markup=old.product_actions(product),
        )
        return

    await v3.on_message(update, context)


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
        app.add_handler(CallbackQueryHandler(on_callback))
        app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, on_message))
        app.run_polling(allowed_updates=Update.ALL_TYPES)
