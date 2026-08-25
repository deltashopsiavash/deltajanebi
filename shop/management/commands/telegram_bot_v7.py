import os
import shutil
from pathlib import Path

from asgiref.sync import sync_to_async
from django.conf import settings as django_settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from shop.management.commands import telegram_bot as old
from shop.management.commands import telegram_bot_v3 as v3
from shop.management.commands import telegram_bot_v4 as v4
from shop.management.commands import telegram_bot_v5 as v5
from shop.management.commands import telegram_bot_v6 as v6
from shop.models import Category, Product, SourceSite
from shop.services.source_catalog import (
    apply_site_markup_to_existing,
    discover_product_urls,
    source_products,
    upsert_source_product,
)
from shop.source_registry import registered_source_for_url


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧰 محصولات عادی", callback_data="m:manual"), InlineKeyboardButton("🔗 محصولات خاص", callback_data="m:synced")],
        [InlineKeyboardButton("🔎 جستجوی محصول", callback_data="search"), InlineKeyboardButton("⭐ پیشنهادهای فعال", callback_data="offers")],
        [InlineKeyboardButton("📦 سفارش‌ها", callback_data="orders"), InlineKeyboardButton("📂 دسته‌بندی‌ها", callback_data="categories")],
        [InlineKeyboardButton("🌐 ثبت سایت", callback_data="source:list"), InlineKeyboardButton("🔄 همگام‌سازی همه", callback_data="syncall")],
        [InlineKeyboardButton("⚙️ تنظیمات سایت", callback_data="settings"), InlineKeyboardButton("📝 توضیحات و فوتر", callback_data="set:footer")],
        [InlineKeyboardButton("🛡 نمادها", callback_data="badge:list"), InlineKeyboardButton("☎️ تلفن", callback_data="set:phone")],
        [InlineKeyboardButton("💾 بکاپ", callback_data="backup"), InlineKeyboardButton("🧹 پاکسازی محصولات", callback_data="catalog:purge")],
    ])


def source_text(site):
    count = Product.objects.filter(source_type=Product.SYNCED, source_url__icontains=site.hostname).count()
    terms = site.brand_terms or "تنظیم نشده"
    last_sync = timezone.localtime(site.last_bulk_sync_at).strftime("%Y/%m/%d %H:%M") if site.last_bulk_sync_at else "هنوز انجام نشده"
    return (
        f"🌐 {site.name}\n"
        f"دامنه: {site.hostname}\n"
        f"آدرس پایه: {site.base_url}\n"
        f"وضعیت: {'✅ فعال' if site.is_active else '⛔ غیرفعال'}\n"
        f"📥 آپلود همه: {'✅ روشن' if site.bulk_import_enabled else '⛔ خاموش'}\n"
        f"💵 قیمت پیش‌فرض: {site.markup_label()}\n"
        f"🧹 عبارت‌های حذف‌شونده: {terms}\n"
        f"🖼 پاکسازی تبلیغات عکس: ✅ خودکار\n"
        f"🔎 آخرین تعداد کشف‌شده: {site.last_discovered_count}\n"
        f"🕒 آخرین Bulk Sync: {last_sync}\n"
        f"محصول خاص مرتبط: {count}"
    )


def source_actions(site):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"📥 آپلود همه: {'روشن' if site.bulk_import_enabled else 'خاموش'}", callback_data=f"source:bulk:{site.id}"),
            InlineKeyboardButton("💵 قیمت", callback_data=f"source:price:{site.id}"),
        ],
        [InlineKeyboardButton("✏️ تغییر نام", callback_data=f"source:name:{site.id}"), InlineKeyboardButton("🧹 عبارات پاکسازی", callback_data=f"source:terms:{site.id}")],
        [InlineKeyboardButton("⏯ فعال / غیرفعال", callback_data=f"source:toggle:{site.id}"), InlineKeyboardButton("🗑 حذف سایت", callback_data=f"source:delask:{site.id}")],
        [InlineKeyboardButton("⬅️ سایت‌های منبع", callback_data="source:list"), InlineKeyboardButton("🏠 منوی اصلی", callback_data="main")],
    ])


def _progress_bar(done, total, width=18):
    if total <= 0:
        return "░" * width
    filled = min(width, int(width * done / total))
    return "█" * filled + "░" * (width - filled)


def _catalog_urls_for_existing(site):
    return list(source_products(site).exclude(source_url="").values_list("source_url", flat=True))


def _save_discovery(site, count):
    site.last_discovered_count = count
    site.save(update_fields=["last_discovered_count"])


def _finish_bulk_sync(site, count):
    site.last_discovered_count = count
    site.last_bulk_sync_at = timezone.now()
    site.save(update_fields=["last_discovered_count", "last_bulk_sync_at"])


def _purge_product_catalog():
    count = Product.objects.count()
    for product in Product.objects.exclude(image="").iterator(chunk_size=200):
        try:
            product.image.delete(save=False)
        except Exception:
            pass
    Product.objects.all().delete()
    Category.objects.filter(image_url__startswith="/media/products/").update(image_url="")
    products_dir = Path(django_settings.MEDIA_ROOT) / "products"
    if products_dir.exists():
        shutil.rmtree(products_dir, ignore_errors=True)
    return count


async def _safe_edit(message, text, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        pass


async def run_full_sync(message):
    sites = await sync_to_async(list)(SourceSite.objects.filter(is_active=True).order_by("id"))
    if not sites:
        await message.reply_text("هیچ سایت منبع فعالی ثبت نشده.", reply_markup=main_menu())
        return

    status = await message.reply_text("🔄 شروع همگام‌سازی...\nدر حال پیدا کردن کاتالوگ سایت‌ها")
    plans = []
    discovery_errors = []

    for index, site in enumerate(sites, 1):
        await _safe_edit(status, f"🔎 در حال بررسی سایت {index}/{len(sites)}\n🌐 {site.name}\n{'█' * (index - 1)}{'░' * (len(sites) - index + 1)}")
        try:
            if site.bulk_import_enabled:
                urls = await sync_to_async(discover_product_urls)(site)
                await sync_to_async(_save_discovery)(site, len(urls))
                if not urls:
                    urls = await sync_to_async(_catalog_urls_for_existing)(site)
                    if urls:
                        discovery_errors.append(f"{site.name}: محصول جدیدی از sitemap پیدا نشد؛ محصولات قبلی Sync شدند.")
            else:
                urls = await sync_to_async(_catalog_urls_for_existing)(site)
            plans.append((site, list(dict.fromkeys(urls))))
        except Exception as exc:
            plans.append((site, await sync_to_async(_catalog_urls_for_existing)(site)))
            discovery_errors.append(f"{site.name}: {str(exc)[:120]}")

    total = sum(len(urls) for _, urls in plans)
    if total == 0:
        text = "هیچ محصولی برای همگام‌سازی پیدا نشد."
        if discovery_errors:
            text += "\n\n" + "\n".join(f"⚠️ {x}" for x in discovery_errors[:5])
        await _safe_edit(status, text, reply_markup=main_menu())
        return

    done = created = updated = errors = 0
    first_errors = []
    update_step = max(1, total // 100)

    for site, urls in plans:
        for url in urls:
            try:
                _, was_created = await sync_to_async(upsert_source_product)(site, url)
                if was_created:
                    created += 1
                else:
                    updated += 1
            except Exception as exc:
                errors += 1
                if len(first_errors) < 5:
                    first_errors.append(f"{site.name}: {str(exc)[:110]}")
            done += 1
            if done == total or done % update_step == 0:
                percent = int(done * 100 / total)
                await _safe_edit(
                    status,
                    "🔄 همگام‌سازی کاتالوگ\n\n"
                    f"{_progress_bar(done, total)}  {percent}%\n"
                    f"📦 {done:,} / {total:,}\n"
                    f"➕ جدید: {created:,}\n"
                    f"♻️ به‌روزشده: {updated:,}\n"
                    f"⚠️ خطا: {errors:,}\n\n"
                    f"🌐 در حال پردازش: {site.name}",
                )
        if site.bulk_import_enabled:
            await sync_to_async(_finish_bulk_sync)(site, len(urls))

    final = (
        "✅ همگام‌سازی کامل شد.\n\n"
        f"{_progress_bar(total, total)}  100%\n"
        f"📦 بررسی‌شده: {total:,}\n"
        f"➕ محصول جدید: {created:,}\n"
        f"♻️ به‌روزشده: {updated:,}\n"
        f"⚠️ خطا: {errors:,}"
    )
    notes = discovery_errors + first_errors
    if notes:
        final += "\n\nنمونه هشدارها:\n" + "\n".join(f"• {x}" for x in notes[:7])
    await _safe_edit(status, final, reply_markup=main_menu())


async def on_callback(update: Update, context):
    if not old.allowed(update):
        return
    q = update.callback_query
    data = q.data or ""

    if data.startswith("source:bulk:"):
        await q.answer()
        site = await sync_to_async(SourceSite.objects.get)(pk=int(data.rsplit(":", 1)[1]))
        site.bulk_import_enabled = not site.bulk_import_enabled
        await sync_to_async(site.save)(update_fields=["bulk_import_enabled"])
        await q.message.reply_text(
            ("✅ آپلود همه فعال شد. با زدن «همگام‌سازی همه»، کاتالوگ قابل کشف این سایت کامل وارد می‌شود.\n\n" if site.bulk_import_enabled else "⛔ آپلود همه خاموش شد؛ فقط محصولات قبلاً ثبت‌شده Sync می‌شوند.\n\n")
            + await sync_to_async(source_text)(site),
            reply_markup=source_actions(site),
        )
        return

    if data.startswith("source:price:"):
        await q.answer()
        site_id = int(data.rsplit(":", 1)[1])
        site = await sync_to_async(SourceSite.objects.get)(pk=site_id)
        old.set_state(context, "source_default_markup_v7", source_id=site_id)
        await q.message.reply_text(
            f"💵 قیمت پیش‌فرض فعلی: {site.markup_label()}\n\n"
            "افزایش قیمت همه محصولات این سایت را بفرست:\n"
            "• 20% یعنی ۲۰ درصد روی قیمت منبع\n"
            "• 20000 یعنی ۲۰ هزار تومان روی قیمت منبع\n"
            "• 0% یعنی دقیقاً قیمت منبع"
        )
        return

    if data == "syncall":
        await q.answer("همگام‌سازی شروع شد")
        old.clear_state(context)
        await run_full_sync(q.message)
        return

    if data == "catalog:purge":
        await q.answer()
        count = await sync_to_async(Product.objects.count)()
        await q.message.reply_text(
            f"⚠️ پاکسازی کاتالوگ\nالان {count:,} محصول روی سایت وجود دارد.\n\nهمه محصولات حذف شوند؟ کاربران، سفارش‌های قبلی، تنظیمات و بنرها باقی می‌مانند.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ آره", callback_data="catalog:purge:yes"), InlineKeyboardButton("❌ نه", callback_data="main")],
            ]),
        )
        return

    if data == "catalog:purge:yes":
        await q.answer()
        await q.message.reply_text(
            "🚨 تأیید نهایی\nاین عملیات قابل برگشت نیست مگر از بکاپ. مطمئنی کل محصولات حذف شوند؟",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔥 حذف قطعی همه محصولات", callback_data="catalog:purge:confirm")],
                [InlineKeyboardButton("❌ انصراف", callback_data="main")],
            ]),
        )
        return

    if data == "catalog:purge:confirm":
        await q.answer()
        status = await q.message.reply_text("🧹 در حال پاکسازی کامل محصولات...")
        count = await sync_to_async(_purge_product_catalog)()
        await _safe_edit(status, f"✅ پاکسازی انجام شد.\n{count:,} محصول حذف شد.\nکاربران، سفارش‌ها و تنظیمات سایت دست‌نخورده ماندند.", reply_markup=main_menu())
        return

    await v6.on_callback(update, context)


async def on_message(update: Update, context):
    if not old.allowed(update):
        return
    message = update.effective_message
    state = context.user_data.get("awaiting")
    flow = context.user_data.setdefault("flow", {})
    text = message.text.strip() if message.text else ""

    if state == "source_default_markup_v7":
        try:
            markup_type, markup_value = old.parse_markup_input(text)
        except ValueError:
            await message.reply_text("فرمت نامعتبر است؛ مثال 20% یا 20000 یا 0%")
            return
        site = await sync_to_async(SourceSite.objects.get)(pk=int(flow["source_id"]))
        site.default_markup_type = markup_type
        site.default_markup_value = markup_value
        await sync_to_async(site.save)(update_fields=["default_markup_type", "default_markup_value"])
        changed = await sync_to_async(apply_site_markup_to_existing)(site)
        old.clear_state(context)
        await message.reply_text(
            f"✅ قیمت پیش‌فرض سایت ذخیره شد: {site.markup_label()}\n"
            f"{changed:,} محصول موجود همین منبع هم با این قانون به‌روزرسانی شد.\n"
            "اگر یک محصول قیمت دستی داشته باشد، قیمت دستی خودش همچنان اولویت دارد.",
            reply_markup=source_actions(site),
        )
        return

    if state == "special_source_url_v4":
        site = await sync_to_async(SourceSite.objects.get)(pk=int(flow["source_id"]), is_active=True)
        selected = await sync_to_async(registered_source_for_url)(text)
        if not selected or selected.id != site.id:
            await message.reply_text(f"❌ این لینک مربوط به {site.hostname} نیست. لینک محصول از همین سایت را بفرست.")
            return
        status = await message.reply_text(f"در حال ثبت محصول با قیمت پیش‌فرض {site.markup_label()}...")
        try:
            product, created = await sync_to_async(upsert_source_product)(site, text)
        except Exception as exc:
            await _safe_edit(status, f"❌ ثبت محصول ناموفق بود:\n{str(exc)[:700]}")
            return
        old.clear_state(context)
        await _safe_edit(
            status,
            f"✅ محصول {'ثبت' if created else 'به‌روزرسانی'} شد.\n"
            f"کد اختصاصی: {product.public_code}\n"
            f"قانون قیمت سایت: {site.markup_label()}\n"
            f"قیمت فروش: {product.price:,} تومان",
            reply_markup=old.product_actions(product),
        )
        return

    await v6.on_message(update, context)


class Command(BaseCommand):
    def handle(self, *args, **opts):
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            self.stderr.write("TELEGRAM_BOT_TOKEN is empty")
            return

        old.main_menu = main_menu
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
