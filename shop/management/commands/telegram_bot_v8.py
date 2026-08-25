import os

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand
from django.db.models import Max, Sum
from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from shop.management.commands import telegram_bot as old
from shop.management.commands import telegram_bot_v3 as v3
from shop.management.commands import telegram_bot_v4 as v4
from shop.management.commands import telegram_bot_v5 as v5
from shop.management.commands import telegram_bot_v6 as v6
from shop.management.commands import telegram_bot_v7 as v7
from shop.models import OrderItem, Product, SourceSite
from shop.services.source_catalog import CatalogSkip, discover_product_urls, source_products, upsert_source_product_with_changes
from shop.services.source_sync import SourceNotProductError


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍 مدیریت محصولات", callback_data="menu:products"), InlineKeyboardButton("🧭 تنظیمات مدیریتی", callback_data="menu:admin")],
        [InlineKeyboardButton("⚙️ تنظیمات سایت", callback_data="settings"), InlineKeyboardButton("📊 تمامی محصولات", callback_data="allproducts")],
        [InlineKeyboardButton("💾 بکاپ", callback_data="backup")],
    ])


def product_management_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 محصولات خاص", callback_data="m:synced"), InlineKeyboardButton("🧰 محصولات عادی", callback_data="m:manual")],
        [InlineKeyboardButton("🔎 جستجوی محصول", callback_data="search"), InlineKeyboardButton("⭐ پیشنهادهای فعال", callback_data="offers")],
        [InlineKeyboardButton("📂 دسته‌بندی‌ها", callback_data="categories")],
        [InlineKeyboardButton("⬅️ منوی اصلی", callback_data="main")],
    ])


def admin_management_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 سفارش‌ها", callback_data="orders"), InlineKeyboardButton("📂 دسته‌بندی‌ها", callback_data="categories")],
        [InlineKeyboardButton("🔄 همگام‌سازی همه", callback_data="syncall"), InlineKeyboardButton("🌐 ثبت سایت", callback_data="source:list")],
        [InlineKeyboardButton("🧹 پاکسازی محصولات", callback_data="catalog:purge")],
        [InlineKeyboardButton("⬅️ منوی اصلی", callback_data="main")],
    ])


def settings_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ نام سایت", callback_data="set:name"), InlineKeyboardButton("🖼 لوگوی سایت", callback_data="set:logo")],
        [InlineKeyboardButton("🚚 هزینه ارسال", callback_data="set:shipping"), InlineKeyboardButton("🌐 شبکه‌های اجتماعی", callback_data="social:list")],
        [InlineKeyboardButton("📣 بنرهای تبلیغاتی", callback_data="banner:list"), InlineKeyboardButton("🛡 نمادها", callback_data="badge:list")],
        [InlineKeyboardButton("☎️ تلفن", callback_data="set:phone"), InlineKeyboardButton("📝 توضیحات و فوتر", callback_data="set:footer")],
        [InlineKeyboardButton("⬅️ منوی اصلی", callback_data="main")],
    ])


def _iran_time(value):
    if not value:
        return "هنوز بروزرسانی نشده"
    local = timezone.localtime(value)
    return local.strftime("%Y/%m/%d - %H:%M") + " به وقت ایران"


def _all_products_report():
    blocks = ["📊 تمامی محصولات\n"]
    sites = list(SourceSite.objects.order_by("id"))
    for site in sites:
        qs = Product.objects.filter(source_type=Product.SYNCED, source_url__icontains=site.hostname)
        total = qs.count()
        available = qs.filter(stock__gt=0).count()
        unavailable = total - available
        sold = OrderItem.objects.filter(product__source_type=Product.SYNCED, product__source_url__icontains=site.hostname).aggregate(total=Sum("quantity"))["total"] or 0
        latest = qs.aggregate(last=Max("last_synced_at"))["last"] or site.last_bulk_sync_at
        blocks.append(
            f"🌐 از سایت {site.base_url}\n"
            f"تعداد محصول: {total:,}\n"
            f"محصولات موجود: {available:,}\n"
            f"ناموجود: {unavailable:,}\n"
            f"فروش: {sold:,}\n"
            f"آخرین بروزرسانی: {_iran_time(latest)}"
        )

    manual = Product.objects.filter(source_type=Product.MANUAL)
    manual_total = manual.count()
    if manual_total:
        sold = OrderItem.objects.filter(product__source_type=Product.MANUAL).aggregate(total=Sum("quantity"))["total"] or 0
        blocks.append(
            "🧰 محصولات عادی / بدون سایت منبع\n"
            f"تعداد محصول: {manual_total:,}\n"
            f"محصولات موجود: {manual.filter(stock__gt=0).count():,}\n"
            f"ناموجود: {manual.filter(stock=0).count():,}\n"
            f"فروش: {sold:,}"
        )
    if len(blocks) == 1:
        blocks.append("هنوز محصولی ثبت نشده است.")
    return "\n\n".join(blocks)


def _catalog_urls(site):
    if site.bulk_import_enabled:
        urls = discover_product_urls(site)
        site.last_discovered_count = len(urls)
        site.save(update_fields=["last_discovered_count"])
        if urls:
            return list(dict.fromkeys(urls))
    return list(dict.fromkeys(source_products(site).exclude(source_url="").values_list("source_url", flat=True)))


def _change_lines(site, product, created, changes):
    if not created and not changes:
        return []
    title = f"{product.public_code or '-'} | {product.name}"
    if created:
        return [
            f"➕ محصول جدید — {title}\n"
            f"منبع: {site.name}\n"
            f"قیمت اصلی سایت: {product.source_price:,}\n"
            f"قیمت سایت من: {product.price:,}\n"
            f"موجودی: {product.stock:,}"
        ]

    lines = []
    if "source_price" in changes or "price" in changes:
        old_source, new_source = changes.get("source_price", (product.source_price, product.source_price))
        old_price, new_price = changes.get("price", (product.price, product.price))
        lines.append(
            f"💰 تغییر قیمت — {title}\n"
            f"قیمت قبلی منبع: {int(old_source):,}\n"
            f"قیمت جدید منبع: {int(new_source):,}\n"
            f"قیمت قدیمی سایت من: {int(old_price):,}\n"
            f"قیمت جدید سایت من: {int(new_price):,}"
        )
    if "stock" in changes:
        old_stock, new_stock = changes["stock"]
        lines.append(f"📦 تغییر موجودی — {title}\nموجودی قبلی: {old_stock:,}\nموجودی جدید: {new_stock:,}")
    if "name" in changes:
        before, after = changes["name"]
        lines.append(f"✏️ تغییر نام — {product.public_code or '-'}\nقبل: {before}\nجدید: {after}")
    misc = []
    if "image_url" in changes or "gallery" in changes:
        misc.append("تصاویر")
    if "specs" in changes:
        misc.append("مشخصات")
    if misc:
        lines.append(f"📝 بروزرسانی اطلاعات — {title}\nتغییر کرده: {'، '.join(misc)}")
    return lines


def _chunk_changes(lines, limit=3600):
    chunks, current = [], ""
    for line in lines:
        candidate = f"{current}\n\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


async def run_detailed_sync(message):
    sites = await sync_to_async(list)(SourceSite.objects.filter(is_active=True).order_by("id"))
    if not sites:
        await message.reply_text("هیچ سایت منبع فعالی ثبت نشده.", reply_markup=admin_management_menu())
        return

    status = await message.reply_text("🔄 شروع همگام‌سازی همه سایت‌ها...")
    plans, warnings = [], []
    for i, site in enumerate(sites, 1):
        await v7._safe_edit(status, f"🔎 کشف محصولات سایت {i}/{len(sites)}\n🌐 {site.name}")
        try:
            urls = await sync_to_async(_catalog_urls)(site)
        except Exception as exc:
            urls = await sync_to_async(lambda: list(source_products(site).exclude(source_url="").values_list("source_url", flat=True)))()
            warnings.append(f"{site.name}: {str(exc)[:100]}")
        plans.append((site, urls))

    total = sum(len(urls) for _, urls in plans)
    if not total:
        await v7._safe_edit(status, "محصولی برای همگام‌سازی پیدا نشد.", reply_markup=admin_management_menu())
        return

    done = created_count = changed_count = errors = skipped = 0
    change_lines = []
    error_lines = []
    update_step = max(1, total // 100)

    for site, urls in plans:
        for url in urls:
            try:
                product, created, changes = await sync_to_async(upsert_source_product_with_changes)(site, url)
                if created:
                    created_count += 1
                if created or changes:
                    changed_count += 1
                    change_lines.extend(_change_lines(site, product, created, changes))
            except (SourceNotProductError, CatalogSkip):
                skipped += 1
            except Exception as exc:
                errors += 1
                if len(error_lines) < 8:
                    error_lines.append(f"{site.name}: {str(exc)[:120]}\n🔗 {url}")
            done += 1
            if done == total or done % update_step == 0:
                percent = int(done * 100 / total)
                await v7._safe_edit(
                    status,
                    "🔄 همگام‌سازی همه سایت‌ها\n\n"
                    f"{v7._progress_bar(done, total)} {percent}%\n"
                    f"📦 {done:,}/{total:,}\n"
                    f"➕ جدید: {created_count:,}\n"
                    f"🔔 دارای تغییر: {changed_count:,}\n"
                    f"⏭ رد خودکار: {skipped:,}\n"
                    f"⚠️ خطا: {errors:,}\n\n"
                    f"🌐 {site.name}",
                )
        if site.bulk_import_enabled:
            site.last_bulk_sync_at = timezone.now()
            await sync_to_async(site.save)(update_fields=["last_bulk_sync_at"])

    await v7._safe_edit(
        status,
        "✅ همگام‌سازی تمام شد.\n\n"
        f"{v7._progress_bar(total, total)} 100%\n"
        f"📦 بررسی‌شده: {total:,}\n"
        f"➕ جدید: {created_count:,}\n"
        f"🔔 محصولات دارای تغییر: {changed_count:,}\n"
        f"⏭ رد خودکار: {skipped:,}\n"
        f"⚠️ خطا: {errors:,}\n\n"
        "گزارش پایین فقط تغییرات را نمایش می‌دهد.",
        reply_markup=admin_management_menu(),
    )

    if change_lines:
        for idx, chunk in enumerate(_chunk_changes(change_lines), 1):
            await message.reply_text(f"📋 گزارش تغییرات {idx}\n\n{chunk}")
    else:
        await message.reply_text("✅ هیچ تغییر جدیدی در محصولات پیدا نشد.")
    notes = warnings + error_lines
    if notes:
        await message.reply_text("⚠️ خطاهای همگام‌سازی:\n" + "\n\n".join(f"• {x}" for x in notes[:10]))


async def on_callback(update: Update, context):
    if not old.allowed(update):
        return
    q = update.callback_query
    data = q.data or ""

    if data == "menu:products":
        await q.answer()
        old.clear_state(context)
        await q.message.reply_text("🛍 مدیریت محصولات", reply_markup=product_management_menu())
        return
    if data == "menu:admin":
        await q.answer()
        old.clear_state(context)
        await q.message.reply_text("🧭 تنظیمات مدیریتی", reply_markup=admin_management_menu())
        return
    if data == "allproducts":
        await q.answer()
        text = await sync_to_async(_all_products_report)()
        await q.message.reply_text(text, reply_markup=main_menu())
        return
    if data == "settings":
        await q.answer()
        old.clear_state(context)
        await q.message.reply_text("⚙️ تنظیمات سایت", reply_markup=settings_menu())
        return
    if data == "syncall":
        await q.answer("همگام‌سازی شروع شد")
        old.clear_state(context)
        await run_detailed_sync(q.message)
        return

    await v7.on_callback(update, context)


async def on_message(update: Update, context):
    if not old.allowed(update):
        return
    await v7.on_message(update, context)


class Command(BaseCommand):
    def handle(self, *args, **opts):
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            self.stderr.write("TELEGRAM_BOT_TOKEN is empty")
            return

        old.main_menu = main_menu
        old.settings_menu = settings_menu
        v3.settings_menu = settings_menu
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
