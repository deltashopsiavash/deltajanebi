import os

from asgiref.sync import sync_to_async
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
from shop.management.commands import telegram_bot_v11 as v11
from shop.models import User
from shop.services.wallet import adjust_wallet, wallet_balance, wallet_history


def main_menu():
    return v11.main_menu()


def settings_menu():
    return v11.settings_menu()


def _wallet_history_text(uid):
    rows = wallet_history(uid, 20)
    if not rows:
        return "📜 هنوز تراکنشی برای این کیف پول ثبت نشده است."
    lines = ["📜 ۲۰ تراکنش اخیر کیف پول\n"]
    for row in rows:
        sign = "+" if row["amount"] > 0 else ""
        lines.append(
            f"• {sign}{row['amount']:,} تومان | موجودی: {row['balance_after']:,}\n"
            f"  دلیل: {row['reason'] or '-'}\n"
            f"  مدیر: {row['admin_id'] or '-'}\n"
            f"  زمان: {row['created_at'].replace('T', ' ')}"
        )
    return "\n\n".join(lines)


def _user_detail(uid):
    base = v11._user_detail(uid)
    balance = wallet_balance(uid)
    return base + f"\n\n👛 کیف پول\nموجودی فعلی: {balance:,} تومان"


def _user_keyboard(uid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ افزایش موجودی", callback_data=f"wallet:add:{uid}"), InlineKeyboardButton("➖ کاهش موجودی", callback_data=f"wallet:sub:{uid}")],
        [InlineKeyboardButton("📜 تراکنش‌های کیف پول", callback_data=f"wallet:history:{uid}")],
        [InlineKeyboardButton("⬅️ کاربران", callback_data="users:list:0"), InlineKeyboardButton("🏠 منوی اصلی", callback_data="main")],
    ])


def _announcement_text_all():
    from shop.models import Announcement
    total = Announcement.objects.count()
    active = Announcement.objects.filter(is_active=True).count()
    return (
        f"🔔 اطلاع‌رسانی سایت\n\nکل اطلاعیه‌ها: {total:,}\nفعال: {active:,}\n\n"
        "هر اطلاعیه فعال در زنگوله برای همه نمایش داده می‌شود؛ چه کاربر وارد حساب شده باشد چه مهمان سایت باشد."
    )


async def _show_user(message, uid):
    try:
        text = await sync_to_async(_user_detail)(uid)
    except User.DoesNotExist:
        await message.reply_text("کاربر پیدا نشد.")
        return
    kb = _user_keyboard(uid)
    for start in range(0, len(text), 3800):
        await message.reply_text(text[start:start + 3800], reply_markup=kb if start + 3800 >= len(text) else None)


async def on_callback(update: Update, context):
    if not old.allowed(update):
        return
    q = update.callback_query
    data = q.data or ""

    if data == "users:search":
        await q.answer()
        old.set_state(context, "user_search_wallet")
        await q.message.reply_text("🔎 شماره مشتری یا ایمیل کاربر را بفرست.\nمثال: #1001 یا email@example.com")
        return

    if data.startswith("user:view:"):
        await q.answer()
        uid = int(data.rsplit(":", 1)[1])
        old.clear_state(context)
        await _show_user(q.message, uid)
        return

    if data.startswith("wallet:add:") or data.startswith("wallet:sub:"):
        await q.answer()
        action, uid = data.split(":")[1:]
        uid = int(uid)
        old.set_state(context, "wallet_amount", user_id=uid, sign=1 if action == "add" else -1)
        label = "افزایش" if action == "add" else "کاهش"
        await q.message.reply_text(f"💰 مبلغ {label} موجودی را به تومان بفرست.\nمثال: 250000")
        return

    if data.startswith("wallet:history:"):
        await q.answer()
        uid = int(data.rsplit(":", 1)[1])
        text = await sync_to_async(_wallet_history_text)(uid)
        await q.message.reply_text(text, reply_markup=_user_keyboard(uid))
        return

    if data == "announcement:add":
        await q.answer()
        old.set_state(context, "announcement_text")
        await q.message.reply_text("متن اطلاعیه را کامل بفرست. بعد از ثبت، زنگوله برای همه بازدیدکننده‌ها (عضو و مهمان) آن را نمایش می‌دهد.")
        return

    await v11.on_callback(update, context)


async def on_message(update: Update, context):
    if not old.allowed(update):
        return
    state = context.user_data.get("awaiting")
    message = update.effective_message
    text = message.text.strip() if message.text else ""

    if state == "user_search_wallet":
        uid = await sync_to_async(v11._find_user)(text)
        if not uid:
            await message.reply_text("کاربری با این شماره مشتری یا ایمیل پیدا نشد. دوباره بفرست یا /cancel بزن.")
            return
        old.clear_state(context)
        await _show_user(message, uid)
        return

    if state == "wallet_amount":
        try:
            amount = old.parse_nonnegative_int(text)
        except ValueError:
            await message.reply_text("مبلغ معتبر بفرست؛ فقط عدد به تومان. مثال: 250000")
            return
        if amount <= 0:
            await message.reply_text("مبلغ باید بیشتر از صفر باشد.")
            return
        flow = context.user_data.get("flow", {})
        old.set_state(context, "wallet_reason", user_id=int(flow["user_id"]), amount=int(amount) * int(flow["sign"]))
        await message.reply_text("📝 دلیل این تراکنش را بنویس.\nمثال: برگشت وجه سفارش #123\nاگر توضیح نمی‌خواهی، - بفرست.")
        return

    if state == "wallet_reason":
        flow = context.user_data.get("flow", {})
        uid = int(flow["user_id"])
        amount = int(flow["amount"])
        reason = "" if text == "-" else text[:1000]
        try:
            new_balance = await sync_to_async(adjust_wallet)(
                uid,
                amount,
                reason,
                str(update.effective_user.id if update.effective_user else ""),
            )
        except (ValueError, User.DoesNotExist) as exc:
            await message.reply_text(f"⛔ {exc}")
            return
        old.clear_state(context)
        sign = "+" if amount > 0 else ""
        await message.reply_text(
            f"✅ تراکنش کیف پول ثبت شد.\n"
            f"مبلغ: {sign}{amount:,} تومان\n"
            f"موجودی جدید: {new_balance:,} تومان\n"
            f"دلیل: {reason or '-'}"
        )
        await _show_user(message, uid)
        return

    await v11.on_message(update, context)


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
        v11._announcement_text = _announcement_text_all

        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler("start", old.start))
        app.add_handler(CommandHandler("cancel", old.cancel))
        app.add_handler(MessageHandler(filters.Regex(r"^/order_\d+$"), old.order_cmd))
        app.add_handler(CallbackQueryHandler(v11.order_status_callback, pattern=r"^order:\d+:(preparing|shipped|delivered|cancelled)$"))
        app.add_handler(CallbackQueryHandler(on_callback))
        app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, on_message))
        app.run_polling(allowed_updates=Update.ALL_TYPES)
