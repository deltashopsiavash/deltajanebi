import os

from asgiref.sync import sync_to_async
from django.db.models import Sum
from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from shop.management.commands import telegram_bot as old
from shop.management.commands import telegram_bot_v3 as v3
from shop.management.commands import telegram_bot_v4 as v4
from shop.management.commands import telegram_bot_v5 as v5
from shop.management.commands import telegram_bot_v6 as v6
from shop.management.commands import telegram_bot_v7 as v7
from shop.management.commands import telegram_bot_v8 as v8
from shop.management.commands import telegram_bot_v9 as v9
from shop.management.commands import telegram_bot_v10 as v10
from shop.models import Announcement, Order, SiteSetting, User
from shop.services.order_workflow import email_customer, mark_paid, order_report_text, release_order_stock
from shop.services.telegram_notify import notify_admins

PAGE_SIZE = 15


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 کاربران", callback_data="users:list:0"), InlineKeyboardButton("🔔 اطلاع‌رسانی", callback_data="announcement:list")],
        [InlineKeyboardButton("🛍 مدیریت محصولات", callback_data="menu:products"), InlineKeyboardButton("🧭 تنظیمات مدیریتی", callback_data="menu:admin")],
        [InlineKeyboardButton("⚙️ تنظیمات سایت", callback_data="settings"), InlineKeyboardButton("📊 تمامی محصولات", callback_data="allproducts")],
        [InlineKeyboardButton("💾 بکاپ", callback_data="backup")],
    ])


def settings_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ نام سایت", callback_data="set:name"), InlineKeyboardButton("🖼 لوگوی سایت", callback_data="set:logo")],
        [InlineKeyboardButton("🌐 شبکه‌های اجتماعی", callback_data="social:list"), InlineKeyboardButton("📣 بنرهای تبلیغاتی", callback_data="banner:list")],
        [InlineKeyboardButton("🛡 نمادها", callback_data="badge:list"), InlineKeyboardButton("☎️ تلفن", callback_data="set:phone")],
        [InlineKeyboardButton("📝 توضیحات و فوتر", callback_data="set:footer"), InlineKeyboardButton("✨ متن بالا", callback_data="set:topbar")],
        [InlineKeyboardButton("💳 پرداخت، تخفیف و ارسال", callback_data="commerce:settings")],
        [InlineKeyboardButton("⬅️ منوی اصلی", callback_data="main")],
    ])


def _iran(value):
    if not value:
        return "-"
    return timezone.localtime(value).strftime("%Y/%m/%d - %H:%M:%S")


def _user_page(page):
    qs = User.objects.filter(is_staff=False).order_by("-date_joined", "-id")
    total = qs.count()
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(0, int(page)), pages - 1)
    rows = list(qs[page * PAGE_SIZE:(page + 1) * PAGE_SIZE].values("id", "customer_code", "email", "first_name", "last_name"))
    return total, pages, page, rows


def _users_keyboard(page):
    total, pages, page, rows = _user_page(page)
    buttons = []
    for row in rows:
        name = (f"{row['first_name']} {row['last_name']}").strip()
        identity = name or row["email"]
        label = f"{row['customer_code'] or '-'} | {identity}"[:58]
        buttons.append([InlineKeyboardButton(label, callback_data=f"user:view:{row['id']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"users:list:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="users:noop"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"users:list:{page+1}"))
    buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔎 جستجوی کاربر", callback_data="users:search")])
    buttons.append([InlineKeyboardButton("⬅️ منوی اصلی", callback_data="main")])
    return total, InlineKeyboardMarkup(buttons)


def _find_user(query):
    q = (query or "").strip()
    if not q:
        return None
    user = User.objects.filter(is_staff=False, customer_code__iexact=q).first()
    if user:
        return user.id
    user = User.objects.filter(is_staff=False, email__iexact=q.lower()).first()
    if user:
        return user.id
    user = User.objects.filter(is_staff=False, email__icontains=q.lower()).order_by("id").first()
    return user.id if user else None


def _user_detail(uid):
    user = User.objects.get(pk=uid)
    orders = user.orders.all().order_by("-created_at")
    order_count = orders.count()
    paid = orders.filter(payment_status=Order.PAY_PAID)
    paid_count = paid.count()
    paid_total = paid.aggregate(total=Sum("total"))["total"] or 0
    last_phone = user.phone or orders.exclude(phone="").values_list("phone", flat=True).first() or "-"

    addresses = []
    seen = set()
    for province, city, address, postal in orders.exclude(address="").values_list("province", "city", "address", "postal_code")[:40]:
        key = (province, city, address, postal)
        if key not in seen:
            seen.add(key)
            addresses.append(f"• {province}، {city} — {address}" + (f" | کدپستی {postal}" if postal else ""))
        if len(addresses) >= 5:
            break

    recent = []
    for order in orders[:10]:
        recent.append(
            f"• #{order.id} | {order.get_status_display()} | {order.get_payment_method_display()} | {order.total:,} تومان | {_iran(order.created_at)}"
        )

    full_name = user.get_full_name().strip() or "-"
    return (
        "👤 مشخصات کامل کاربر\n\n"
        f"شماره مشتری: {user.customer_code or '-'}\n"
        f"نام و نام خانوادگی: {full_name}\n"
        f"ایمیل: {user.email}\n"
        f"شماره تلفن: {last_phone}\n"
        f"تاریخ عضویت: {_iran(user.date_joined)}\n"
        f"آخرین ورود: {_iran(user.last_login)}\n"
        f"وضعیت حساب: {'✅ فعال' if user.is_active else '⛔ غیرفعال'}\n\n"
        f"📦 تعداد کل سفارش‌ها: {order_count:,}\n"
        f"✅ خریدهای پرداخت‌شده: {paid_count:,}\n"
        f"💰 مجموع خرید موفق: {paid_total:,} تومان\n\n"
        "📍 آدرس‌های ثبت‌شده/استفاده‌شده:\n"
        + ("\n".join(addresses) if addresses else "آدرسی ثبت نشده")
        + "\n\n🧾 ۱۰ سفارش اخیر:\n"
        + ("\n".join(recent) if recent else "سفارشی ثبت نشده")
    )


def _announcement_menu():
    rows = list(Announcement.objects.order_by("-created_at")[:10])
    buttons = [[InlineKeyboardButton("➕ اطلاعیه جدید", callback_data="announcement:add")]]
    for item in rows:
        label = ("✅ " if item.is_active else "⛔ ") + item.text.replace("\n", " ")[:36]
        buttons.append([
            InlineKeyboardButton(label, callback_data=f"announcement:toggle:{item.id}"),
            InlineKeyboardButton("🗑", callback_data=f"announcement:delete:{item.id}"),
        ])
    buttons.append([InlineKeyboardButton("⬅️ منوی اصلی", callback_data="main")])
    return InlineKeyboardMarkup(buttons)


def _announcement_text():
    total = Announcement.objects.count()
    active = Announcement.objects.filter(is_active=True).count()
    return f"🔔 اطلاع‌رسانی سایت\n\nکل اطلاعیه‌ها: {total:,}\nفعال: {active:,}\n\nهر اطلاعیه فعال برای کاربران ثبت‌نام‌شده به‌صورت زنگوله و Badge خوانده‌نشده نمایش داده می‌شود."


async def order_status_callback(update: Update, context):
    if not old.allowed(update):
        return
    q = update.callback_query
    await q.answer()
    _, oid, status = q.data.split(":")
    order = await sync_to_async(Order.objects.select_related("user").get)(pk=int(oid))

    if status != "cancelled" and (order.payment_status != Order.PAY_PAID or not order.stock_committed):
        await q.edit_message_text(
            f"⛔ سفارش #{order.id} هنوز پرداخت نهایی و کسر موجودی نشده و نمی‌تواند وارد مرحله {dict(Order.STATUS).get(status, status)} شود."
        )
        return

    if status == "cancelled" and not order.stock_committed and not order.reservation_released:
        await sync_to_async(release_order_stock)(order)
        order.reservation_released = True

    order.status = status
    fields = ["status", "updated_at"]
    if order.reservation_released:
        fields.append("reservation_released")
    await sync_to_async(order.save)(update_fields=fields)
    await q.edit_message_text(f"✅ سفارش #{order.id}: {order.get_status_display()}")


async def on_callback(update: Update, context):
    if not old.allowed(update):
        return
    q = update.callback_query
    data = q.data or ""

    if data.startswith("users:list:"):
        await q.answer()
        old.clear_state(context)
        page = int(data.rsplit(":", 1)[1])
        total, kb = await sync_to_async(_users_keyboard)(page)
        await q.message.reply_text(f"👥 کاربران\n\nآمار ربات: {total:,} نفر\nهر صفحه حداکثر {PAGE_SIZE} کاربر", reply_markup=kb)
        return
    if data == "users:noop":
        await q.answer()
        return
    if data == "users:search":
        await q.answer()
        old.set_state(context, "user_search")
        await q.message.reply_text("🔎 شماره مشتری یا ایمیل کاربر را بفرست.\nمثال: CU-0000123 یا email@example.com")
        return
    if data.startswith("user:view:"):
        await q.answer()
        uid = int(data.rsplit(":", 1)[1])
        try:
            text = await sync_to_async(_user_detail)(uid)
        except User.DoesNotExist:
            await q.message.reply_text("کاربر پیدا نشد.")
            return
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ کاربران", callback_data="users:list:0")]])
        for chunk_start in range(0, len(text), 3800):
            await q.message.reply_text(text[chunk_start:chunk_start+3800], reply_markup=kb if chunk_start + 3800 >= len(text) else None)
        return

    if data == "announcement:list":
        await q.answer()
        old.clear_state(context)
        text = await sync_to_async(_announcement_text)()
        kb = await sync_to_async(_announcement_menu)()
        await q.message.reply_text(text, reply_markup=kb)
        return
    if data == "announcement:add":
        await q.answer()
        old.set_state(context, "announcement_text")
        await q.message.reply_text("متن اطلاعیه را کامل بفرست. به محض ثبت، زنگوله کاربران دارای حساب Badge جدید می‌گیرد.")
        return
    if data.startswith("announcement:toggle:"):
        await q.answer()
        aid = int(data.rsplit(":", 1)[1])
        item = await sync_to_async(Announcement.objects.get)(pk=aid)
        item.is_active = not item.is_active
        await sync_to_async(item.save)(update_fields=["is_active"])
        kb = await sync_to_async(_announcement_menu)()
        await q.message.reply_text("✅ وضعیت اطلاعیه تغییر کرد.", reply_markup=kb)
        return
    if data.startswith("announcement:delete:"):
        await q.answer()
        aid = int(data.rsplit(":", 1)[1])
        await sync_to_async(Announcement.objects.filter(pk=aid).delete)()
        kb = await sync_to_async(_announcement_menu)()
        await q.message.reply_text("🗑 اطلاعیه حذف شد.", reply_markup=kb)
        return

    if data == "set:topbar":
        await q.answer()
        old.set_state(context, "site_topbar_text")
        site = await sync_to_async(SiteSetting.load)()
        await q.message.reply_text(f"✨ متن فعلی بالای سایت:\n{site.top_bar_text}\n\nمتن جدید را بفرست (حداکثر ۲۴۰ کاراکتر).")
        return

    if data.startswith("receipt:approve:"):
        await q.answer()
        oid = int(data.rsplit(":", 1)[1])
        order = await sync_to_async(Order.objects.select_related("user").prefetch_related("items__product").get)(pk=oid)
        if order.payment_method != Order.PAYMENT_CARD or not order.receipt:
            await q.message.reply_text("این سفارش رسید کارت‌به‌کارت قابل تایید ندارد.")
            return
        if order.payment_status == Order.PAY_PAID:
            await q.message.reply_text("این پرداخت قبلاً تایید شده است.")
            return
        try:
            await sync_to_async(mark_paid)(order)
        except ValueError as exc:
            await q.message.reply_text(f"⛔ امکان تایید نیست: {exc}")
            return
        await sync_to_async(email_customer)(order, f"پرداخت سفارش #{order.id} تایید شد", "رسید پرداخت شما تایید شد و سفارش وارد مرحله آماده‌سازی شد.")
        report = await sync_to_async(order_report_text)(order, "✅ پرداخت کارت‌به‌کارت تایید شد")
        await sync_to_async(notify_admins)(report)
        await q.message.reply_text(f"✅ رسید سفارش #{order.id} تایید شد؛ موجودی واقعی همین الان کسر شد و سفارش وارد آماده‌سازی شد.")
        return

    await v10.on_callback(update, context)


async def on_message(update: Update, context):
    if not old.allowed(update):
        return
    state = context.user_data.get("awaiting")
    text = update.effective_message.text.strip() if update.effective_message.text else ""

    if state == "user_search":
        uid = await sync_to_async(_find_user)(text)
        if not uid:
            await update.effective_message.reply_text("کاربری با این شماره مشتری یا ایمیل پیدا نشد. دوباره بفرست یا /cancel بزن.")
            return
        old.clear_state(context)
        detail = await sync_to_async(_user_detail)(uid)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ کاربران", callback_data="users:list:0")]])
        for start in range(0, len(detail), 3800):
            await update.effective_message.reply_text(detail[start:start+3800], reply_markup=kb if start + 3800 >= len(detail) else None)
        return

    if state == "announcement_text":
        if not text:
            await update.effective_message.reply_text("متن اطلاعیه خالی است؛ متن را بفرست.")
            return
        item = await sync_to_async(Announcement.objects.create)(text=text[:4000], is_active=True)
        old.clear_state(context)
        kb = await sync_to_async(_announcement_menu)()
        await update.effective_message.reply_text(f"✅ اطلاعیه #{item.id} ثبت شد. زنگوله کاربران به‌صورت خوانده‌نشده نمایش داده می‌شود.", reply_markup=kb)
        return

    if state == "site_topbar_text":
        if not text:
            await update.effective_message.reply_text("متن نمی‌تواند خالی باشد.")
            return
        site = await sync_to_async(SiteSetting.load)()
        site.top_bar_text = text[:240]
        await sync_to_async(site.save)(update_fields=["top_bar_text"])
        old.clear_state(context)
        await update.effective_message.reply_text("✅ متن بالای سایت ذخیره شد و با افکت تایپ نمایش داده می‌شود.", reply_markup=settings_menu())
        return

    await v10.on_message(update, context)


class Command(v10.Command):
    def handle(self, *args, **opts):
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            self.stderr.write("TELEGRAM_BOT_TOKEN is empty")
            return

        old.main_menu = main_menu
        old.settings_menu = settings_menu
        v3.settings_menu = settings_menu
        v8.main_menu = main_menu
        v8.settings_menu = settings_menu
        v9.settings_menu = settings_menu
        v4.show_categories = v5.show_categories
        v4.source_text = v7.source_text
        v4.source_actions = v7.source_actions
        v6.source_text = v7.source_text
        v6.source_actions = v7.source_actions
        v9._show_commerce = v10.show_commerce

        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler("start", old.start))
        app.add_handler(CommandHandler("cancel", old.cancel))
        app.add_handler(MessageHandler(filters.Regex(r"^/order_\d+$"), old.order_cmd))
        app.add_handler(CallbackQueryHandler(order_status_callback, pattern=r"^order:\d+:(preparing|shipped|delivered|cancelled)$"))
        app.add_handler(CallbackQueryHandler(on_callback))
        app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, on_message))
        app.run_polling(allowed_updates=Update.ALL_TYPES)