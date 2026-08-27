#!/usr/bin/env python3
import logging
import os
import sys
from urllib.parse import urlsplit

RUNTIME_DIR = os.environ.get("DELTAJANEBI_RUNTIME_DIR", "/opt/deltajanebi-bot/runtime")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from bot_single_instance import acquire_single_instance_lock
import external_bot as core
import external_bot_plus as plus
import external_bot_v8 as v8
import external_bot_v10 as v10
import external_bot_v12 as v12
import external_bot_v15 as v15

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("deltajanebi.external_bot")


def _site(uid, sid):
    try:
        sid = int(sid)
    except (TypeError, ValueError):
        return None
    if not core.can_access(uid, sid):
        return None
    return core.get_site(sid)


def _money(value):
    return plus.money(value) if value is not None else "-"


def site_panel(site, uid):
    """Delta panel keeps every old management area and adds SanaShop parity."""
    sid = site["id"]
    rows = [
        [InlineKeyboardButton("📊 داشبورد", callback_data=f"dash:{sid}")],
        [InlineKeyboardButton("🛍 مدیریت محصولات", callback_data=f"delta_products_menu:{sid}"), InlineKeyboardButton("🧭 تنظیمات مدیریتی", callback_data=f"delta_admin_menu:{sid}")],
        [InlineKeyboardButton("👥 کاربران", callback_data=f"users:{sid}"), InlineKeyboardButton("🔔 اطلاع‌رسانی", callback_data=f"delta_announcements:{sid}")],
        [InlineKeyboardButton("🛒 سفارش‌ها", callback_data=f"orders:{sid}"), InlineKeyboardButton("🧾 رسیدها", callback_data=f"receipts:{sid}")],
        [InlineKeyboardButton("🎞 بنرهای تبلیغاتی", callback_data=f"banners:{sid}"), InlineKeyboardButton("🔴 معرفی محصولات", callback_data=f"stories:{sid}")],
        [InlineKeyboardButton("🎟 کدهای تخفیف", callback_data=f"discounts:{sid}"), InlineKeyboardButton("💾 بکاپ", callback_data=f"backups_v10:{sid}")],
        [InlineKeyboardButton("⚙️ تنظیمات سایت", callback_data=f"settings:{sid}"), InlineKeyboardButton("📊 تمامی محصولات", callback_data=f"delta_all_products:{sid}")],
    ]
    if core.is_owner(uid):
        rows.append([InlineKeyboardButton("⬅️ سایت‌های متصل", callback_data="owner_sites")])
    return InlineKeyboardMarkup(rows)


core.site_panel = site_panel


def _products_menu(sid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 محصولات خاص", callback_data=f"delta_products:synced:{sid}"), InlineKeyboardButton("🧰 محصولات عادی", callback_data=f"delta_products:manual:{sid}")],
        [InlineKeyboardButton("🔎 جستجوی محصول", callback_data=f"delta_product_search:{sid}"), InlineKeyboardButton("⭐ پیشنهادهای فعال", callback_data=f"delta_products:offers:{sid}")],
        [InlineKeyboardButton("📂 دسته‌بندی‌ها", callback_data=f"categories:{sid}"), InlineKeyboardButton("➕ محصول جدید", callback_data=f"product_add:{sid}")],
        [InlineKeyboardButton("⬅️ پنل سایت", callback_data=f"site_info:{sid}")],
    ])


def _admin_menu(sid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 سفارش‌ها", callback_data=f"orders:{sid}"), InlineKeyboardButton("📂 دسته‌بندی‌ها", callback_data=f"categories:{sid}")],
        [InlineKeyboardButton("🔄 همگام‌سازی همه", callback_data=f"delta_sync_all:{sid}"), InlineKeyboardButton("🌐 ثبت سایت", callback_data=f"delta_sources:{sid}")],
        [InlineKeyboardButton("🧹 پاکسازی محصولات", callback_data=f"delta_purge:{sid}")],
        [InlineKeyboardButton("⬅️ پنل سایت", callback_data=f"site_info:{sid}")],
    ])


async def _show_products(q, site, sid, mode="all", query=""):
    result = await core.api(site, "delta_products", {"mode": mode, "query": query}, timeout=45)
    rows = result.get("data") or []
    labels = {"manual": "🧰 محصولات عادی", "synced": "🔗 محصولات خاص", "offers": "⭐ پیشنهادهای فعال", "all": "📊 تمامی محصولات"}
    keys = []
    for p in rows[:45]:
        badge = "🔥" if p.get("amazing_active") else ("🏷" if p.get("discount_active") else ("✅" if p.get("is_active") else "⛔"))
        keys.append([InlineKeyboardButton(f"{badge} {p['name'][:40]} | {_money(p.get('effective_price'))}", callback_data=f"product:{sid}:{p['id']}")])
    keys.append([InlineKeyboardButton("⬅️ مدیریت محصولات", callback_data=f"delta_products_menu:{sid}")])
    title = labels.get(mode, labels["all"])
    suffix = f"\nجستجو: {query}" if query else ""
    if not rows:
        return await q.edit_message_text(f"{title}{suffix}\n\nمحصولی پیدا نشد.", reply_markup=InlineKeyboardMarkup(keys))
    return await q.edit_message_text(f"{title}{suffix}\n\n{len(rows)} مورد نمایش داده شد.", reply_markup=InlineKeyboardMarkup(keys))


async def _show_sources(q, site, sid):
    rows = (await core.api(site, "source_sites", timeout=35))["data"]
    keys = []
    for item in rows:
        keys.append([InlineKeyboardButton(f"{'✅' if item['is_active'] else '⛔'} {item['name']} | {item['product_count']} محصول", callback_data=f"delta_source:{sid}:{item['id']}")])
    keys += [
        [InlineKeyboardButton("➕ ثبت سایت منبع", callback_data=f"delta_source_add:{sid}")],
        [InlineKeyboardButton("⬅️ تنظیمات مدیریتی", callback_data=f"delta_admin_menu:{sid}")],
    ]
    return await q.edit_message_text("🌐 سایت‌های منبع\n\nثبت، قیمت‌گذاری، پاکسازی متن، Bulk Import و فعال/غیرفعال‌سازی از این بخش انجام می‌شود.", reply_markup=InlineKeyboardMarkup(keys))


async def _show_source(q, site, sid, source_id):
    item = (await core.api(site, "source_site_detail", {"id": int(source_id)}, timeout=35))["data"]
    text = (
        f"🌐 {item['name']}\n"
        f"دامنه: {item['hostname']}\n"
        f"آدرس: {item['base_url']}\n"
        f"وضعیت: {'✅ فعال' if item['is_active'] else '⛔ غیرفعال'}\n"
        f"📥 آپلود همه: {'✅ روشن' if item['bulk_import_enabled'] else '⛔ خاموش'}\n"
        f"💵 قیمت پیش‌فرض: {item['markup_label']}\n"
        f"🧹 عبارات پاکسازی: {item['brand_terms'] or 'تنظیم نشده'}\n"
        f"🔎 آخرین کشف: {item['last_discovered_count']}\n"
        f"📦 محصولات مرتبط: {item['product_count']}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📥 آپلود همه: {'روشن' if item['bulk_import_enabled'] else 'خاموش'}", callback_data=f"delta_source_bulk:{sid}:{source_id}"), InlineKeyboardButton("💵 قیمت", callback_data=f"delta_source_price:{sid}:{source_id}")],
        [InlineKeyboardButton("✏️ تغییر نام", callback_data=f"delta_source_name:{sid}:{source_id}"), InlineKeyboardButton("🧹 عبارات پاکسازی", callback_data=f"delta_source_terms:{sid}:{source_id}")],
        [InlineKeyboardButton("⏯ فعال / غیرفعال", callback_data=f"delta_source_toggle:{sid}:{source_id}"), InlineKeyboardButton("🗑 حذف سایت", callback_data=f"delta_source_delete:{sid}:{source_id}")],
        [InlineKeyboardButton("⬅️ سایت‌های منبع", callback_data=f"delta_sources:{sid}")],
    ])
    return await q.edit_message_text(text, reply_markup=kb)


async def _show_announcements(q, site, sid):
    rows = (await core.api(site, "announcements", timeout=35))["data"]
    keys = [[InlineKeyboardButton("➕ اطلاعیه جدید", callback_data=f"delta_announcement_add:{sid}")]]
    for item in rows[:20]:
        label = ("✅ " if item["is_active"] else "⛔ ") + item["text"].replace("\n", " ")[:38]
        keys.append([
            InlineKeyboardButton(label, callback_data=f"delta_announcement_toggle:{sid}:{item['id']}"),
            InlineKeyboardButton("🗑", callback_data=f"delta_announcement_delete:{sid}:{item['id']}"),
        ])
    keys.append([InlineKeyboardButton("⬅️ پنل سایت", callback_data=f"site_info:{sid}")])
    return await q.edit_message_text(f"🔔 اطلاع‌رسانی سایت\n\nکل اطلاعیه‌ها: {len(rows)}\nبرای فعال/غیرفعال‌کردن روی خود اطلاعیه بزنید.", reply_markup=InlineKeyboardMarkup(keys))


async def _show_user(q, site, sid, user_id):
    u = (await core.api(site, "user_detail", {"id": int(user_id)}, timeout=35))["data"]
    text = v8._user_text(u) + f"\n\n👛 موجودی کیف پول: {_money(u.get('wallet_balance'))} تومان"
    keys = []
    for order in (u.get("orders") or [])[:4]:
        keys.append([InlineKeyboardButton(f"🧾 {order['code']} — {order['status_label']}", callback_data=f"order:{sid}:{order['id']}")])
    keys += [
        [InlineKeyboardButton("➕ افزایش کیف پول", callback_data=f"delta_wallet_add:{sid}:{user_id}"), InlineKeyboardButton("➖ کاهش کیف پول", callback_data=f"delta_wallet_sub:{sid}:{user_id}")],
        [InlineKeyboardButton("📜 تراکنش‌های کیف پول", callback_data=f"delta_wallet_history:{sid}:{user_id}")],
        [InlineKeyboardButton("📱 تغییر تلفن", callback_data=f"user_phone_v8:{sid}:{user_id}"), InlineKeyboardButton("✉️ تغییر ایمیل", callback_data=f"user_email_v8:{sid}:{user_id}")],
        [InlineKeyboardButton("🔐 ارسال لینک بازیابی رمز", callback_data=f"user_reset_v8:{sid}:{user_id}")],
        [InlineKeyboardButton("🔄 فعال/غیرفعال کردن حساب", callback_data=f"user_toggle:{sid}:{user_id}")],
        [InlineKeyboardButton("⬅️ کاربران", callback_data=f"users:{sid}")],
    ]
    return await q.edit_message_text(text[:3900], reply_markup=InlineKeyboardMarkup(keys))


def _normalize_url(value):
    value = str(value or "").strip()
    if "://" not in value:
        value = "https://" + value
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("آدرس سایت معتبر نیست.")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}".rstrip("/")


async def callback(update: Update, context):
    q = update.callback_query
    uid = q.from_user.id
    data = q.data or ""

    try:
        if data == "connect":
            if not core.is_owner(uid):
                return await q.answer("فقط مالک اصلی می‌تواند سایت اضافه کند.", show_alert=True)
            await q.answer()
            context.user_data.clear()
            context.user_data["flow"] = "delta_connect_url"
            return await q.edit_message_text("🔗 اتصال سایت DeltaJanebi\n\nآدرس کامل سایت را بفرستید.\nمثال:\nhttps://shop.example.com")

        if data.startswith("delta_products_menu:"):
            sid = int(data.split(":")[1]); site = _site(uid, sid)
            if not site: return await q.answer("عدم دسترسی", show_alert=True)
            await q.answer(); context.user_data.clear()
            return await q.edit_message_text("🛍 مدیریت محصولات", reply_markup=_products_menu(sid))

        if data.startswith("delta_admin_menu:"):
            sid = int(data.split(":")[1]); site = _site(uid, sid)
            if not site: return await q.answer("عدم دسترسی", show_alert=True)
            await q.answer(); context.user_data.clear()
            return await q.edit_message_text("🧭 تنظیمات مدیریتی", reply_markup=_admin_menu(sid))

        if data.startswith("delta_products:"):
            _, mode, sid = data.split(":"); site = _site(uid, sid)
            if not site: return await q.answer("عدم دسترسی", show_alert=True)
            await q.answer(); return await _show_products(q, site, int(sid), mode)

        if data.startswith("delta_product_search:"):
            sid = int(data.split(":")[1]); site = _site(uid, sid)
            if not site: return await q.answer("عدم دسترسی", show_alert=True)
            await q.answer(); context.user_data.clear(); context.user_data.update(flow="delta_product_search", site_id=sid)
            return await q.edit_message_text("🔎 نام، کد محصول یا SKU را بفرستید:")

        if data.startswith("delta_all_products:"):
            sid = int(data.split(":")[1]); site = _site(uid, sid)
            if not site: return await q.answer("عدم دسترسی", show_alert=True)
            await q.answer()
            all_rows = (await core.api(site, "delta_products", {"mode": "all"}, timeout=45))["data"]
            manual = sum(1 for x in all_rows if x.get("source_type") == "manual")
            synced = sum(1 for x in all_rows if x.get("source_type") == "synced")
            available = sum(1 for x in all_rows if int(x.get("available_stock") or 0) > 0)
            text = f"📊 تمامی محصولات\n\nکل: {len(all_rows)}\n🔗 خاص/همگام: {synced}\n🧰 عادی: {manual}\n✅ موجود: {available}\n⛔ ناموجود: {len(all_rows)-available}"
            return await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 نمایش لیست", callback_data=f"delta_products:all:{sid}")],[InlineKeyboardButton("⬅️ پنل سایت", callback_data=f"site_info:{sid}")]]))

        if data.startswith("delta_sources:"):
            sid = int(data.split(":")[1]); site = _site(uid, sid)
            if not site: return await q.answer("عدم دسترسی", show_alert=True)
            await q.answer(); context.user_data.clear(); return await _show_sources(q, site, sid)

        if data.startswith("delta_source:") and len(data.split(":")) == 3:
            _, sid, source_id = data.split(":"); site = _site(uid, sid)
            if not site: return await q.answer("عدم دسترسی", show_alert=True)
            await q.answer(); return await _show_source(q, site, int(sid), int(source_id))

        if data.startswith("delta_source_add:"):
            sid = int(data.split(":")[1]); site = _site(uid, sid)
            if not site: return await q.answer("عدم دسترسی", show_alert=True)
            await q.answer(); context.user_data.clear(); context.user_data.update(flow="delta_source_add_name", site_id=sid)
            return await q.edit_message_text("🌐 نام سایت منبع را بفرستید:")

        for prefix, flow, prompt in [
            ("delta_source_name:", "delta_source_name", "✏️ نام جدید سایت منبع را بفرستید:"),
            ("delta_source_terms:", "delta_source_terms", "🧹 عبارت‌های حذف‌شونده را با کاما بفرستید. برای پاک‌کردن - بفرستید:"),
            ("delta_source_price:", "delta_source_price", "💵 افزایش قیمت را بفرستید؛ مثال 20% یا 25000 یا 0%:"),
        ]:
            if data.startswith(prefix):
                _, sid, source_id = data.split(":"); site = _site(uid, sid)
                if not site: return await q.answer("عدم دسترسی", show_alert=True)
                await q.answer(); context.user_data.clear(); context.user_data.update(flow=flow, site_id=int(sid), source_id=int(source_id))
                return await q.edit_message_text(prompt)

        if data.startswith("delta_source_toggle:") or data.startswith("delta_source_bulk:"):
            action, sid, source_id = data.split(":"); site = _site(uid, sid)
            if not site: return await q.answer("عدم دسترسی", show_alert=True)
            info = (await core.api(site, "source_site_detail", {"id": int(source_id)}))["data"]
            field = "is_active" if action == "delta_source_toggle" else "bulk_import_enabled"
            await core.api(site, "source_site_update", {"id": int(source_id), field: not bool(info[field])})
            await q.answer("ذخیره شد"); return await _show_source(q, site, int(sid), int(source_id))

        if data.startswith("delta_source_delete:"):
            _, sid, source_id = data.split(":"); site = _site(uid, sid)
            if not site: return await q.answer("عدم دسترسی", show_alert=True)
            await q.answer()
            return await q.edit_message_text("🗑 سایت منبع حذف شود؟ محصولات فعلی حذف نمی‌شوند.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ حذف سایت", callback_data=f"delta_source_delete_confirm:{sid}:{source_id}")],[InlineKeyboardButton("❌ انصراف", callback_data=f"delta_source:{sid}:{source_id}")]]))

        if data.startswith("delta_source_delete_confirm:"):
            _, sid, source_id = data.split(":"); site = _site(uid, sid)
            if not site: return await q.answer("عدم دسترسی", show_alert=True)
            await core.api(site, "source_site_delete", {"id": int(source_id)}); await q.answer("حذف شد")
            return await _show_sources(q, site, int(sid))

        if data.startswith("delta_sync_all:"):
            sid = int(data.split(":")[1]); site = _site(uid, sid)
            if not site: return await q.answer("عدم دسترسی", show_alert=True)
            await q.answer("همگام‌سازی شروع شد")
            await q.edit_message_text("🔄 همگام‌سازی همه سایت‌های منبع شروع شد. بسته به تعداد محصولات ممکن است چند دقیقه طول بکشد...")
            result = (await core.api(site, "source_sync_all", timeout=1800))["data"]
            text = f"✅ همگام‌سازی تمام شد.\n\n🌐 سایت‌ها: {result['sites']}\n📦 بررسی‌شده: {result['checked']}\n➕ جدید: {result['created']}\n🔔 تغییرکرده: {result['changed']}\n⏭ ردشده: {result['skipped']}\n⚠️ خطا: {result['errors']}"
            if result.get("warnings"): text += "\n\nنمونه هشدارها:\n" + "\n".join(f"• {x}" for x in result["warnings"][:5])
            return await q.edit_message_text(text[:3900], reply_markup=_admin_menu(sid))

        if data.startswith("delta_purge:") and not data.startswith("delta_purge_yes:") and not data.startswith("delta_purge_confirm:"):
            sid = int(data.split(":")[1]); site = _site(uid, sid)
            if not site: return await q.answer("عدم دسترسی", show_alert=True)
            count = len((await core.api(site, "delta_products", {"mode": "all"}))["data"]); await q.answer()
            return await q.edit_message_text(f"⚠️ پاکسازی کاتالوگ\nالان {count} محصول وجود دارد.\n\nهمه محصولات حذف شوند؟ کاربران، سفارش‌ها، تنظیمات و سایت‌های منبع باقی می‌مانند.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ آره", callback_data=f"delta_purge_yes:{sid}"),InlineKeyboardButton("❌ نه", callback_data=f"delta_admin_menu:{sid}")]]))

        if data.startswith("delta_purge_yes:"):
            sid = int(data.split(":")[1]); await q.answer()
            return await q.edit_message_text("🚨 تأیید نهایی\nاین عملیات قابل برگشت نیست مگر از بکاپ.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔥 حذف قطعی همه محصولات", callback_data=f"delta_purge_confirm:{sid}")],[InlineKeyboardButton("❌ انصراف", callback_data=f"delta_admin_menu:{sid}")]]))

        if data.startswith("delta_purge_confirm:"):
            sid = int(data.split(":")[1]); site = _site(uid, sid)
            if not site: return await q.answer("عدم دسترسی", show_alert=True)
            await q.answer("در حال پاکسازی..."); result=(await core.api(site,"catalog_purge",{"confirm":"PURGE_ALL_PRODUCTS"},timeout=180))["data"]
            return await q.edit_message_text(f"✅ پاکسازی انجام شد.\n{result['deleted']} محصول حذف شد.\nکاربران، سفارش‌ها و تنظیمات دست‌نخورده ماندند.", reply_markup=_admin_menu(sid))

        if data.startswith("delta_announcements:"):
            sid=int(data.split(":")[1]); site=_site(uid,sid)
            if not site:return await q.answer("عدم دسترسی",show_alert=True)
            await q.answer(); context.user_data.clear(); return await _show_announcements(q,site,sid)

        if data.startswith("delta_announcement_add:"):
            sid=int(data.split(":")[1]); site=_site(uid,sid)
            if not site:return await q.answer("عدم دسترسی",show_alert=True)
            await q.answer(); context.user_data.clear(); context.user_data.update(flow="delta_announcement_add",site_id=sid)
            return await q.edit_message_text("🔔 متن کامل اطلاعیه را بفرستید:")

        if data.startswith("delta_announcement_toggle:"):
            _,sid,aid=data.split(":"); site=_site(uid,sid)
            if not site:return await q.answer("عدم دسترسی",show_alert=True)
            rows=(await core.api(site,"announcements"))["data"]; item=next((x for x in rows if int(x["id"])==int(aid)),None)
            if not item:return await q.answer("اطلاعیه پیدا نشد",show_alert=True)
            await core.api(site,"announcement_update",{"id":int(aid),"is_active":not item["is_active"]}); await q.answer("تغییر کرد")
            return await _show_announcements(q,site,int(sid))

        if data.startswith("delta_announcement_delete:"):
            _,sid,aid=data.split(":"); site=_site(uid,sid)
            if not site:return await q.answer("عدم دسترسی",show_alert=True)
            await core.api(site,"announcement_delete",{"id":int(aid)}); await q.answer("حذف شد")
            return await _show_announcements(q,site,int(sid))

        if data.startswith("user:") and len(data.split(":")) == 3:
            _, sid, user_id = data.split(":"); site = _site(uid, sid)
            if not site: return await q.answer("عدم دسترسی", show_alert=True)
            await q.answer(); return await _show_user(q, site, int(sid), int(user_id))

        if data.startswith("delta_wallet_history:"):
            _,sid,user_id=data.split(":"); site=_site(uid,sid)
            if not site:return await q.answer("عدم دسترسی",show_alert=True)
            info=(await core.api(site,"wallet_history",{"id":int(user_id),"limit":20}))["data"]; await q.answer()
            lines=[f"👛 موجودی: {_money(info['balance'])} تومان","","📜 ۲۰ تراکنش اخیر:"]
            for x in info.get("transactions") or []:
                sign="+" if int(x["amount"])>0 else ""; lines.append(f"• {sign}{_money(x['amount'])} | موجودی {_money(x['balance_after'])}\n  {x.get('reason') or '-'}")
            return await q.edit_message_text("\n".join(lines)[:3900],reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ مشتری",callback_data=f"user:{sid}:{user_id}")]]))

        if data.startswith("delta_wallet_add:") or data.startswith("delta_wallet_sub:"):
            action,sid,user_id=data.split(":"); site=_site(uid,sid)
            if not site:return await q.answer("عدم دسترسی",show_alert=True)
            await q.answer(); context.user_data.clear(); context.user_data.update(flow="delta_wallet_amount",site_id=int(sid),user_id=int(user_id),wallet_sign=1 if action=="delta_wallet_add" else -1)
            return await q.edit_message_text("💰 مبلغ را فقط به تومان بفرستید؛ مثال 250000")

        return await v15.callback(update, context)
    except Exception as exc:
        logger.exception("Delta callback failed: %s", data)
        site = None
        for part in str(data).split(":")[1:]:
            if part.isdigit() and core.can_access(uid, int(part)):
                site = core.get_site(int(part)); break
        try: await q.answer("عملیات ناموفق بود؛ اتصال سایت حفظ شده است.")
        except Exception: pass
        text = str(exc).strip() or exc.__class__.__name__
        try:
            return await q.edit_message_text(f"⚠️ عملیات انجام نشد، ولی اتصال سایت حذف نشده است.\n\nخطا: {text[:700]}", reply_markup=site_panel(site,uid) if site else core.owner_home())
        except Exception:
            return await q.message.reply_text(f"⚠️ عملیات انجام نشد، ولی اتصال سایت حفظ شد.\n{text[:700]}", reply_markup=site_panel(site,uid) if site else core.owner_home())


async def message(update: Update, context):
    uid=update.effective_user.id; flow=context.user_data.get("flow"); text=(update.message.text or "").strip()
    try:
        if flow == "delta_connect_url" and core.is_owner(uid):
            try: url=_normalize_url(text)
            except ValueError as exc:return await update.message.reply_text(f"❌ {exc}\nدوباره آدرس را بفرستید.")
            context.user_data.clear(); context.user_data.update(flow="delta_connect_key",connect_url=url)
            return await update.message.reply_text(f"🌐 {url}\n\n🔑 حالا DELTAJANEBI_BOT_API_KEY همین سایت را بفرستید.")
        if flow == "delta_connect_key" and core.is_owner(uid):
            url=context.user_data.get("connect_url"); key=text
            if not url:return await update.message.reply_text("مرحله اتصال منقضی شده؛ /start را بزنید.")
            if len(key)<32:return await update.message.reply_text("❌ کلید اتصال کوتاه/نامعتبر است؛ کلید DeltaJanebi را دوباره بفرستید.")
            await update.message.reply_text("⏳ در حال تست API و ثبت سایت...")
            candidate={"base_url":url,"api_key":key}
            info=await core.api(candidate,"ping",timeout=30); meta=info.get("site") or {}
            if meta.get("platform")!="deltajanebi":raise RuntimeError("این API مربوط به DeltaJanebi نیست.")
            name=str(meta.get("name") or urlsplit(url).hostname or "DeltaJanebi")[:120]
            site_id=v12._upsert_connected_site(url,key,name); saved=core.get_site(site_id); context.user_data.clear()
            return await update.message.reply_text(f"✅ سایت با موفقیت متصل شد.\n🏪 {saved['name']}\n🌐 {saved['base_url']}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏪 باز کردن پنل سایت",callback_data=f"open_site:{site_id}")],[InlineKeyboardButton("⬅️ پنل مالک",callback_data="owner_home")]]))

        if flow == "delta_product_search":
            sid=int(context.user_data["site_id"]); site=_site(uid,sid)
            if not site: context.user_data.clear(); return await update.message.reply_text("عدم دسترسی")
            context.user_data.clear(); result=(await core.api(site,"delta_products",{"mode":"all","query":text}))["data"]
            keys=[[InlineKeyboardButton(f"{x['name'][:42]} | {_money(x.get('effective_price'))}",callback_data=f"product:{sid}:{x['id']}")] for x in result[:45]]
            keys.append([InlineKeyboardButton("⬅️ مدیریت محصولات",callback_data=f"delta_products_menu:{sid}")])
            return await update.message.reply_text(f"🔎 نتیجه جستجو: {len(result)} مورد",reply_markup=InlineKeyboardMarkup(keys))

        if flow == "delta_source_add_name":
            if not text:return await update.message.reply_text("نام سایت را بفرستید.")
            context.user_data["source_name"]=text[:120]; context.user_data["flow"]="delta_source_add_url"
            return await update.message.reply_text("🌐 آدرس سایت منبع را بفرستید؛ مثال https://example.com")
        if flow == "delta_source_add_url":
            sid=int(context.user_data["site_id"]); site=_site(uid,sid)
            if not site: context.user_data.clear(); return await update.message.reply_text("عدم دسترسی")
            try:
                result=await core.api(site,"source_site_create",{"name":context.user_data["source_name"],"base_url":text},timeout=45)
            except Exception as exc:return await update.message.reply_text(f"❌ ثبت نشد: {exc}\nآدرس را اصلاح و دوباره بفرستید.")
            source_id=result["data"]["id"]; context.user_data.clear()
            return await update.message.reply_text("✅ سایت منبع ثبت شد.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🌐 تنظیمات سایت منبع",callback_data=f"delta_source:{sid}:{source_id}")]]))

        if flow in {"delta_source_name","delta_source_terms","delta_source_price"}:
            sid=int(context.user_data["site_id"]); source_id=int(context.user_data["source_id"]); site=_site(uid,sid)
            if not site: context.user_data.clear(); return await update.message.reply_text("عدم دسترسی")
            if flow=="delta_source_name": payload={"id":source_id,"name":text}
            elif flow=="delta_source_terms": payload={"id":source_id,"brand_terms":"" if text=="-" else text}
            else:
                raw=text.replace(",","").replace("٬","").strip(); mt="percent" if raw.endswith("%") else "fixed"; number=raw[:-1] if mt=="percent" else raw
                try:value=float(number)
                except ValueError:return await update.message.reply_text("فرمت نامعتبر است؛ مثال 20% یا 25000")
                if value<0:return await update.message.reply_text("مقدار منفی مجاز نیست.")
                payload={"id":source_id,"default_markup_type":mt,"default_markup_value":value}
            await core.api(site,"source_site_update",payload); context.user_data.clear()
            return await update.message.reply_text("✅ ذخیره شد.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ سایت منبع",callback_data=f"delta_source:{sid}:{source_id}")]]))

        if flow == "delta_announcement_add":
            sid=int(context.user_data["site_id"]); site=_site(uid,sid)
            if not site:context.user_data.clear();return await update.message.reply_text("عدم دسترسی")
            if not text:return await update.message.reply_text("متن اطلاعیه خالی است.")
            await core.api(site,"announcement_create",{"text":text}); context.user_data.clear()
            return await update.message.reply_text("✅ اطلاعیه ثبت شد.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔔 اطلاع‌رسانی",callback_data=f"delta_announcements:{sid}")]]))

        if flow == "delta_wallet_amount":
            raw=text.replace(",","").replace("٬","").strip()
            if not raw.isdigit() or int(raw)<=0:return await update.message.reply_text("مبلغ معتبر و بیشتر از صفر بفرستید.")
            context.user_data["wallet_amount"]=int(raw)*int(context.user_data["wallet_sign"]); context.user_data["flow"]="delta_wallet_reason"
            return await update.message.reply_text("📝 دلیل تراکنش را بنویسید؛ اگر توضیح نمی‌خواهید - بفرستید.")
        if flow == "delta_wallet_reason":
            sid=int(context.user_data["site_id"]); user_id=int(context.user_data["user_id"]); amount=int(context.user_data["wallet_amount"]); site=_site(uid,sid)
            if not site:context.user_data.clear();return await update.message.reply_text("عدم دسترسی")
            reason="" if text=="-" else text
            result=(await core.api(site,"wallet_adjust",{"id":user_id,"amount":amount,"reason":reason,"admin_id":str(uid)}))["data"]; context.user_data.clear()
            sign="+" if amount>0 else ""
            return await update.message.reply_text(f"✅ تراکنش ثبت شد.\nمبلغ: {sign}{_money(amount)} تومان\nموجودی جدید: {_money(result['balance'])} تومان",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ مشتری",callback_data=f"user:{sid}:{user_id}")]]))

        return await v15.message(update,context)
    except Exception as exc:
        logger.exception("Delta text flow failed")
        return await update.message.reply_text(f"⚠️ عملیات انجام نشد ولی اتصال سایت حفظ شده است.\n{str(exc)[:700]}")


async def media(update: Update, context):
    return await v15.media(update, context)


def run():
    try:
        acquire_single_instance_lock()
    except RuntimeError as exc:
        logger.error("DeltaJanebi bot refused duplicate startup: %s", exc)
        raise SystemExit(73) from exc
    core.db()
    app=(Application.builder().token(core.TOKEN).concurrent_updates(16).connection_pool_size(32).pool_timeout(10.0).post_init(v10.post_init).build())
    app.add_handler(CommandHandler("start",core.start))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.PHOTO|filters.VIDEO|filters.Document.ALL,media))
    app.add_handler(MessageHandler(filters.TEXT&~filters.COMMAND,message))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run()
