#!/usr/bin/env python3
import base64
import io
import re
import tempfile
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import external_bot as core
import external_bot_plus as plus

MAX_FILE = 48 * 1024 * 1024
PLATFORMS = [("instagram", "اینستاگرام"), ("telegram", "تلگرام"), ("whatsapp", "واتساپ"), ("rubika", "روبیکا"), ("eitaa", "ایتا"), ("youtube", "یوتیوب"), ("aparat", "آپارات"), ("x", "ایکس"), ("facebook", "فیسبوک"), ("other", "سایر")]


def money(value):
    try:
        return f"{int(value or 0):,}"
    except Exception:
        return str(value or "0")


def back(sid, label="⬅️ پنل Delta"):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"d:home:{sid}")]])


def panel(site, uid):
    sid = site["id"]
    rows = [
        [InlineKeyboardButton("📊 داشبورد Delta", callback_data=f"d:dash:{sid}")],
        [InlineKeyboardButton("🛍 مدیریت محصولات", callback_data=f"d:pmenu:{sid}"), InlineKeyboardButton("🧭 تنظیمات مدیریتی", callback_data=f"d:admin:{sid}")],
        [InlineKeyboardButton("🛒 سفارش‌ها", callback_data=f"d:orders:{sid}"), InlineKeyboardButton("🧾 رسیدها", callback_data=f"d:receipts:{sid}")],
        [InlineKeyboardButton("👥 کاربران و کیف پول", callback_data=f"d:users:{sid}"), InlineKeyboardButton("🔔 اطلاع‌رسانی", callback_data=f"d:ann:{sid}")],
        [InlineKeyboardButton("⚙️ تنظیمات اختصاصی Delta", callback_data=f"d:settings:{sid}"), InlineKeyboardButton("📊 تمامی محصولات", callback_data=f"d:products:{sid}:all")],
        [InlineKeyboardButton("🔴 معرفی محصولات", callback_data=f"d:stories:{sid}"), InlineKeyboardButton("💾 بکاپ کامل Delta", callback_data=f"d:backup:{sid}")],
    ]
    if core.is_owner(uid):
        rows.append([InlineKeyboardButton("⬅️ سایت‌های متصل", callback_data="owner_sites")])
    return InlineKeyboardMarkup(rows)


def products_menu(sid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧰 محصولات عادی", callback_data=f"d:products:{sid}:manual"), InlineKeyboardButton("🔗 محصولات خاص", callback_data=f"d:products:{sid}:synced")],
        [InlineKeyboardButton("🔎 جستجوی محصول", callback_data=f"d:psearch:{sid}"), InlineKeyboardButton("⭐ پیشنهادهای فعال", callback_data=f"d:products:{sid}:offers")],
        [InlineKeyboardButton("➕ محصول عادی", callback_data=f"d:padd:{sid}:manual"), InlineKeyboardButton("🔗 افزودن محصول خاص", callback_data=f"d:padd:{sid}:synced")],
        [InlineKeyboardButton("📂 دسته‌بندی‌ها", callback_data=f"d:cats:{sid}"), InlineKeyboardButton("🔴 معرفی محصولات", callback_data=f"d:stories:{sid}")],
        [InlineKeyboardButton("⬅️ پنل Delta", callback_data=f"d:home:{sid}")],
    ])


def admin_menu(sid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 سایت‌های منبع", callback_data=f"d:sources:{sid}"), InlineKeyboardButton("🔄 همگام‌سازی همه", callback_data=f"d:syncall:{sid}")],
        [InlineKeyboardButton("📂 دسته‌بندی‌ها", callback_data=f"d:cats:{sid}"), InlineKeyboardButton("📊 تمامی محصولات", callback_data=f"d:products:{sid}:all")],
        [InlineKeyboardButton("🧹 پاکسازی کل کاتالوگ", callback_data=f"d:purgeask:{sid}")],
        [InlineKeyboardButton("⬅️ پنل Delta", callback_data=f"d:home:{sid}")],
    ])


def settings_menu(sid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ نام سایت", callback_data=f"d:set:{sid}:name"), InlineKeyboardButton("🖼 لوگوی سایت", callback_data=f"d:set:{sid}:logo")],
        [InlineKeyboardButton("☎️ تلفن", callback_data=f"d:set:{sid}:phone"), InlineKeyboardButton("✨ متن بالای سایت", callback_data=f"d:set:{sid}:topbar")],
        [InlineKeyboardButton("📝 توضیحات و فوتر", callback_data=f"d:set:{sid}:footer"), InlineKeyboardButton("🌐 شبکه‌های اجتماعی", callback_data=f"d:socials:{sid}")],
        [InlineKeyboardButton("📣 بنرهای تبلیغاتی", callback_data=f"d:banners:{sid}"), InlineKeyboardButton("🛡 نمادها", callback_data=f"d:badges:{sid}")],
        [InlineKeyboardButton("💳 پرداخت، تخفیف و ارسال", callback_data=f"d:commerce:{sid}")],
        [InlineKeyboardButton("📨 ایمیل همگانی", callback_data=f"d:broadcast:{sid}"), InlineKeyboardButton("💾 بکاپ", callback_data=f"d:backup:{sid}")],
        [InlineKeyboardButton("⬅️ پنل Delta", callback_data=f"d:home:{sid}")],
    ])


def _flow(context, name, sid, **extra):
    context.user_data.clear()
    context.user_data.update(flow=name, platform="deltajanebi", site_id=int(sid), **extra)


def _site(uid, sid):
    try:
        sid = int(sid)
    except Exception:
        return None
    if not core.can_access(uid, sid):
        return None
    site = core.get_site(sid)
    if site and "platform" in site.keys() and site["platform"] not in ("deltajanebi", "unknown", ""):
        return None
    return site


async def _edit(q, text, markup=None):
    try:
        return await q.edit_message_text(text[:4000], reply_markup=markup)
    except Exception:
        return await q.message.reply_text(text[:4000], reply_markup=markup)


def _product_text(p):
    ov = p.get("manual_overrides") or {}
    flags = [name for key, name in (("name", "نام"), ("price", "قیمت"), ("stock", "موجودی"), ("image", "عکس")) if ov.get(key)]
    lines = [
        f"📦 {p.get('name')}",
        f"🔑 کد: {p.get('sku') or '-'}",
        f"نوع: {'🔗 خاص/همگام' if p.get('source_type') == 'synced' else '🧰 عادی'}",
        f"💰 قیمت اصلی: {money(p.get('base_price') or p.get('price'))} تومان",
        f"🏷 تخفیف: {money(p.get('discount_price')) + ' تومان' if p.get('discount_price') else '-'}",
        f"🔥 شگفت‌انگیز: {money(p.get('amazing_price')) + ' تومان' if p.get('amazing_price') else '-'}",
        f"✅ قیمت فعلی: {money(p.get('effective_price'))} تومان",
        f"📊 موجودی: {p.get('stock', 0)} | قابل فروش: {p.get('available_stock', 0)}",
        f"وضعیت: {'✅ فعال' if p.get('is_active') else '⛔ غیرفعال'}",
        f"دسته: {p.get('category') or '-'}",
    ]
    if p.get("source_type") == "synced":
        lines += [
            f"🌐 منبع: {p.get('source_url') or '-'}",
            f"💵 قیمت منبع: {money(p.get('source_price'))} تومان",
            f"🛠 تغییرات دستی: {', '.join(flags) if flags else '-'}",
        ]
    if p.get("discount_ends_at"):
        lines.append(f"⏱ پایان پیشنهاد زمان‌دار: {str(p['discount_ends_at']).replace('T', ' ')[:16]}")
    if p.get("sync_error"):
        lines.append(f"⚠️ خطای Sync: {p['sync_error'][:300]}")
    return "\n".join(lines)


def _product_kb(sid, p):
    pid = p["id"]
    rows = [
        [InlineKeyboardButton("✏️ نام", callback_data=f"d:pedit:{sid}:{pid}:name"), InlineKeyboardButton("💰 قیمت اصلی", callback_data=f"d:pedit:{sid}:{pid}:price")],
        [InlineKeyboardButton("📊 موجودی", callback_data=f"d:pedit:{sid}:{pid}:stock"), InlineKeyboardButton("🖼 عکس", callback_data=f"d:pedit:{sid}:{pid}:image")],
        [InlineKeyboardButton("🏷 تخفیف", callback_data=f"d:pedit:{sid}:{pid}:discount"), InlineKeyboardButton("🔥 شگفت‌انگیز", callback_data=f"d:pedit:{sid}:{pid}:amazing")],
        [InlineKeyboardButton("⭐ پیشنهاد زمان‌دار", callback_data=f"d:pedit:{sid}:{pid}:timed"), InlineKeyboardButton("⏹ لغو پیشنهاد زمان‌دار", callback_data=f"d:pclear:{sid}:{pid}:timed")],
        [InlineKeyboardButton("🗑 حذف تخفیف", callback_data=f"d:pclear:{sid}:{pid}:discount"), InlineKeyboardButton("🗑 حذف شگفت‌انگیز", callback_data=f"d:pclear:{sid}:{pid}:amazing")],
        [InlineKeyboardButton("⏯ فعال/غیرفعال", callback_data=f"d:ptoggle:{sid}:{pid}"), InlineKeyboardButton("🗑 حذف محصول", callback_data=f"d:pdelask:{sid}:{pid}")],
    ]
    if p.get("source_type") == "synced":
        rows.append([InlineKeyboardButton("🔄 Sync همین محصول", callback_data=f"d:psync:{sid}:{pid}"), InlineKeyboardButton("♻️ حذف تغییرات دستی", callback_data=f"d:preset:{sid}:{pid}")])
    rows.append([InlineKeyboardButton("⬅️ مدیریت محصولات", callback_data=f"d:pmenu:{sid}")])
    return InlineKeyboardMarkup(rows)


async def show_product(q, site, sid, pid):
    p = (await core.api(site, "delta_product_detail", {"id": int(pid)}, timeout=35))["data"]
    return await _edit(q, _product_text(p), _product_kb(sid, p))


async def show_products(q, site, sid, mode="all", query=""):
    rows = (await core.api(site, "delta_products", {"mode": mode, "query": query}, timeout=45))["data"]
    title = {"manual": "🧰 محصولات عادی", "synced": "🔗 محصولات خاص", "offers": "⭐ پیشنهادهای فعال", "all": "📊 تمامی محصولات"}.get(mode, "📦 محصولات")
    keys = []
    for p in rows[:45]:
        badge = "🔥" if p.get("amazing_active") else ("🏷" if p.get("discount_active") else ("✅" if p.get("is_active") else "⛔"))
        keys.append([InlineKeyboardButton(f"{badge} {p['name'][:36]} | {money(p.get('effective_price'))}", callback_data=f"d:product:{sid}:{p['id']}")])
    keys.append([InlineKeyboardButton("⬅️ مدیریت محصولات", callback_data=f"d:pmenu:{sid}")])
    return await _edit(q, f"{title}\n\n{len(rows)} مورد" + (f"\nجستجو: {query}" if query else ""), InlineKeyboardMarkup(keys))


async def callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    data = q.data or ""
    parts = data.split(":")
    if not data.startswith("d:") or len(parts) < 3:
        return False
    try:
        sid = int(parts[2])
    except Exception:
        return False
    site = _site(uid, sid)
    if not site:
        await q.answer("عدم دسترسی", show_alert=True)
        return True
    try:
        await q.answer()
        cmd = parts[1]
        if cmd == "home":
            await _edit(q, f"🟣 پنل اختصاصی DeltaJanebi\n🏪 {site['name']}\n🌐 {site['base_url']}", panel(site, uid)); return True
        if cmd == "dash":
            x = (await core.api(site, "dashboard"))["data"]
            await _edit(q, f"📊 داشبورد Delta\n\n🛍 محصولات فعال: {x['products']}\n🛒 سفارش‌ها: {x['orders']}\n🧾 در انتظار بررسی: {x['pending_orders']}\n👥 کاربران: {x['users']}", back(sid)); return True
        if cmd == "pmenu":
            await _edit(q, "🛍 مدیریت محصولات اختصاصی Delta", products_menu(sid)); return True
        if cmd == "admin":
            await _edit(q, "🧭 تنظیمات مدیریتی Delta", admin_menu(sid)); return True
        if cmd == "products":
            await show_products(q, site, sid, parts[3] if len(parts) > 3 else "all"); return True
        if cmd == "psearch":
            _flow(context, "d_psearch", sid); await _edit(q, "🔎 کد اختصاصی، SKU یا نام محصول را بفرست:"); return True
        if cmd == "product":
            await show_product(q, site, sid, int(parts[3])); return True
        if cmd == "padd":
            typ = parts[3]
            if typ == "manual":
                _flow(context, "d_manual_name", sid); await _edit(q, "نام محصول عادی را بفرست:")
            else:
                rows = (await core.api(site, "source_sites"))["data"]
                keys = [[InlineKeyboardButton(f"🌐 {x['name']} • {x['hostname']}", callback_data=f"d:sourcepick:{sid}:{x['id']}")] for x in rows if x.get("is_active")]
                keys.append([InlineKeyboardButton("⬅️ محصولات", callback_data=f"d:pmenu:{sid}")])
                await _edit(q, "منبع محصول خاص را انتخاب کن:", InlineKeyboardMarkup(keys))
            return True
        if cmd == "sourcepick":
            _flow(context, "d_special_url", sid, source_id=int(parts[3])); await _edit(q, "لینک محصول از همین سایت منبع را بفرست:"); return True
        if cmd == "pedit":
            pid = int(parts[3]); field = parts[4]
            prompts = {"name": "نام جدید محصول:", "price": "قیمت اصلی جدید به تومان:", "stock": "موجودی جدید:", "image": "عکس جدید را بفرست؛ عکس/فایل یا لینک http/https. برای حذف -", "discount": "قیمت تخفیف عادی را بفرست:", "amazing": "قیمت مخصوص شگفت‌انگیز را بفرست:", "timed": "قیمت پیشنهاد زمان‌دار را بفرست:"}
            _flow(context, f"d_pedit_{field}", sid, product_id=pid); await _edit(q, prompts[field]); return True
        if cmd == "pclear":
            pid = int(parts[3]); kind = parts[4]
            if kind == "timed":
                await core.api(site, "delta_timed_offer_clear", {"id": pid})
            else:
                await core.api(site, "product_update", {"id": pid, "discount_price": 0} if kind == "discount" else {"id": pid, "amazing_price": 0})
            await show_product(q, site, sid, pid); return True
        if cmd == "ptoggle":
            pid = int(parts[3]); p = (await core.api(site, "delta_product_detail", {"id": pid}))["data"]
            await core.api(site, "product_update", {"id": pid, "is_active": not p["is_active"]}); await show_product(q, site, sid, pid); return True
        if cmd == "pdelask":
            pid = int(parts[3]); p = (await core.api(site, "delta_product_detail", {"id": pid}))["data"]
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ بله، حذف شود", callback_data=f"d:pdelete:{sid}:{pid}"), InlineKeyboardButton("❌ انصراف", callback_data=f"d:product:{sid}:{pid}")]])
            await _edit(q, f"⚠️ محصول «{p['name']}» برای همیشه حذف شود؟", kb); return True
        if cmd == "pdelete":
            await core.api(site, "delta_product_delete", {"id": int(parts[3])}); await _edit(q, "✅ محصول حذف شد.", products_menu(sid)); return True
        if cmd == "psync":
            await _edit(q, "⏳ در حال Sync محصول...")
            r = (await core.api(site, "delta_product_sync", {"id": int(parts[3])}, timeout=180))["data"]
            await _edit(q, "✅ محصول از منبع Sync شد.\n\n" + _product_text(r["product"]), _product_kb(sid, r["product"])); return True
        if cmd == "preset":
            r = (await core.api(site, "delta_product_reset_sync", {"id": int(parts[3])}, timeout=180))["data"]
            await _edit(q, "✅ تغییرات دستی پاک شد و محصول دوباره از منبع خوانده شد.\n\n" + _product_text(r), _product_kb(sid, r)); return True
        if cmd == "cats":
            rows = (await core.api(site, "categories"))["data"]
            keys = [[InlineKeyboardButton(f"{'✅' if x['is_active'] else '⛔'} {x['name']} ({x['product_count']})", callback_data=f"d:cat:{sid}:{x['id']}")] for x in rows[:60]]
            keys += [[InlineKeyboardButton("➕ دسته جدید", callback_data=f"d:catadd:{sid}")], [InlineKeyboardButton("⬅️ محصولات", callback_data=f"d:pmenu:{sid}")]]
            await _edit(q, "📂 دسته‌بندی‌های Delta", InlineKeyboardMarkup(keys)); return True
        if cmd == "catadd":
            _flow(context, "d_catadd", sid); await _edit(q, "نام دسته جدید را بفرست:"); return True
        if cmd == "cat":
            cid = int(parts[3]); x = (await core.api(site, "category_detail", {"id": cid}))["data"]
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✏️ نام", callback_data=f"d:catedit:{sid}:{cid}:name"), InlineKeyboardButton("🖼 عکس", callback_data=f"d:catedit:{sid}:{cid}:image")], [InlineKeyboardButton("⏯ فعال/غیرفعال", callback_data=f"d:cattoggle:{sid}:{cid}"), InlineKeyboardButton("🗑 حذف دسته", callback_data=f"d:catdelask:{sid}:{cid}")], [InlineKeyboardButton("⬅️ دسته‌ها", callback_data=f"d:cats:{sid}")]])
            await _edit(q, f"📂 {x['name']}\nمحصول مستقیم: {x['product_count']}\nعکس: {'✅' if x.get('has_image') else '❌'}\nوضعیت: {'✅ فعال' if x['is_active'] else '⛔ غیرفعال'}", kb); return True
        if cmd == "catedit":
            _flow(context, "d_catedit_" + parts[4], sid, category_id=int(parts[3])); await _edit(q, "نام جدید دسته را بفرست:" if parts[4] == "name" else "عکس دسته را بفرست؛ عکس/فایل، لینک http/https یا - برای حذف:"); return True
        if cmd == "cattoggle":
            cid = int(parts[3]); x = (await core.api(site, "category_detail", {"id": cid}))["data"]
            await core.api(site, "category_update", {"id": cid, "is_active": not x["is_active"]}); update.callback_query.data = f"d:cat:{sid}:{cid}"; return await callback(update, context)
        if cmd == "catdelask":
            cid = int(parts[3]); x = (await core.api(site, "delta_category_delete_preview", {"id": cid}))["data"]
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ حذف قطعی", callback_data=f"d:catdelete:{sid}:{cid}"), InlineKeyboardButton("❌ انصراف", callback_data=f"d:cat:{sid}:{cid}")]])
            await _edit(q, f"⚠️ حذف «{x['name']}»؟\nزیردسته‌ها: {x['descendant_count']}\nمحصولات داخل این شاخه: {x['all_products']}\n\nمحصولات حذف نمی‌شوند؛ بدون دسته باقی می‌مانند.", kb); return True
        if cmd == "catdelete":
            await core.api(site, "delta_category_delete", {"id": int(parts[3])}); await _edit(q, "✅ دسته و زیردسته‌هایش حذف شدند؛ محصولات حفظ شدند.", InlineKeyboardMarkup([[InlineKeyboardButton("📂 دسته‌ها", callback_data=f"d:cats:{sid}")]])); return True
        if cmd == "sources":
            rows = (await core.api(site, "source_sites"))["data"]
            keys = [[InlineKeyboardButton(f"{'✅' if x['is_active'] else '⛔'} {x['name']} | {x['product_count']} محصول", callback_data=f"d:source:{sid}:{x['id']}")] for x in rows]
            keys += [[InlineKeyboardButton("➕ ثبت سایت منبع", callback_data=f"d:sourceadd:{sid}")], [InlineKeyboardButton("⬅️ تنظیمات مدیریتی", callback_data=f"d:admin:{sid}")]]
            await _edit(q, "🌐 سایت‌های منبع Delta", InlineKeyboardMarkup(keys)); return True
        if cmd == "sourceadd":
            _flow(context, "d_source_add_url", sid); await _edit(q, "🌐 آدرس سایت منبع را بفرست؛ مثال https://example.com"); return True
        if cmd == "source":
            x = (await core.api(site, "source_site_detail", {"id": int(parts[3])}))["data"]; xid = x["id"]
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"📥 آپلود همه: {'روشن' if x['bulk_import_enabled'] else 'خاموش'}", callback_data=f"d:sourcebulk:{sid}:{xid}"), InlineKeyboardButton("💵 قیمت", callback_data=f"d:sourceprice:{sid}:{xid}")], [InlineKeyboardButton("✏️ نام", callback_data=f"d:sourcename:{sid}:{xid}"), InlineKeyboardButton("🧹 عبارات پاکسازی", callback_data=f"d:sourceterms:{sid}:{xid}")], [InlineKeyboardButton("⏯ فعال/غیرفعال", callback_data=f"d:sourcetoggle:{sid}:{xid}"), InlineKeyboardButton("🗑 حذف سایت", callback_data=f"d:sourcedelask:{sid}:{xid}")], [InlineKeyboardButton("⬅️ سایت‌های منبع", callback_data=f"d:sources:{sid}")]])
            await _edit(q, f"🌐 {x['name']}\nدامنه: {x['hostname']}\nوضعیت: {'✅' if x['is_active'] else '⛔'}\n📥 آپلود همه: {'روشن' if x['bulk_import_enabled'] else 'خاموش'}\n💵 قیمت پیش‌فرض: {x['markup_label']}\n🧹 پاکسازی: {x['brand_terms'] or '-'}\n📦 محصولات: {x['product_count']}", kb); return True
        if cmd in {"sourcebulk", "sourcetoggle"}:
            xid = int(parts[3]); x = (await core.api(site, "source_site_detail", {"id": xid}))["data"]
            field = "bulk_import_enabled" if cmd == "sourcebulk" else "is_active"
            await core.api(site, "source_site_update", {"id": xid, field: not x[field]}); update.callback_query.data = f"d:source:{sid}:{xid}"; return await callback(update, context)
        if cmd in {"sourceprice", "sourcename", "sourceterms"}:
            _flow(context, "d_" + cmd, sid, source_id=int(parts[3]))
            await _edit(q, {"sourceprice": "افزایش قیمت را بفرست: 20% یا 20000 یا 0%", "sourcename": "نام جدید سایت منبع را بفرست:", "sourceterms": "عبارت‌های پاکسازی را با کاما بفرست؛ - برای خالی کردن"}[cmd]); return True
        if cmd == "sourcedelask":
            await _edit(q, "⚠️ سایت منبع حذف شود؟ محصولات فعلی حذف نمی‌شوند و فقط Sync آن‌ها متوقف می‌شود.", InlineKeyboardMarkup([[InlineKeyboardButton("✅ حذف", callback_data=f"d:sourcedelete:{sid}:{parts[3]}"), InlineKeyboardButton("❌ انصراف", callback_data=f"d:source:{sid}:{parts[3]}")]])); return True
        if cmd == "sourcedelete":
            await core.api(site, "source_site_delete", {"id": int(parts[3])}); await _edit(q, "✅ سایت منبع حذف شد.", InlineKeyboardMarkup([[InlineKeyboardButton("🌐 سایت‌های منبع", callback_data=f"d:sources:{sid}")]])); return True
        if cmd == "syncall":
            await _edit(q, "⏳ همگام‌سازی همه سایت‌های منبع شروع شد...")
            x = (await core.api(site, "source_sync_all", timeout=1800))["data"]
            await _edit(q, f"✅ همگام‌سازی کامل شد.\nسایت‌ها: {x['sites']}\nبررسی: {x['checked']}\nجدید: {x['created']}\nتغییرکرده: {x['changed']}\nردشده: {x['skipped']}\nخطا: {x['errors']}", admin_menu(sid)); return True
        if cmd == "purgeask":
            await _edit(q, "🚨 همه محصولات کاتالوگ Delta حذف شوند؟ کاربران، سفارش‌ها، تنظیمات و سایت‌های منبع حفظ می‌شوند.", InlineKeyboardMarkup([[InlineKeyboardButton("🔥 حذف قطعی همه محصولات", callback_data=f"d:purge:{sid}"), InlineKeyboardButton("❌ انصراف", callback_data=f"d:admin:{sid}")]])); return True
        if cmd == "purge":
            x = (await core.api(site, "catalog_purge", {"confirm": "PURGE_ALL_PRODUCTS"}, timeout=180))["data"]
            await _edit(q, f"✅ {x['deleted']} محصول حذف شد؛ کاربران، سفارش‌ها و تنظیمات دست‌نخورده ماندند.", admin_menu(sid)); return True
        if cmd == "users":
            rows = (await core.api(site, "users"))["data"]
            keys = [[InlineKeyboardButton("🔎 جستجو", callback_data=f"d:usersearch:{sid}"), InlineKeyboardButton("📨 ایمیل همگانی", callback_data=f"d:broadcast:{sid}")]]
            keys += [[InlineKeyboardButton(f"{u.get('customer_code') or '-'} | {(u.get('full_name') or u.get('email') or '')[:35]}", callback_data=f"d:user:{sid}:{u['id']}")] for u in rows[:30]]
            keys.append([InlineKeyboardButton("⬅️ پنل Delta", callback_data=f"d:home:{sid}")])
            await _edit(q, "👥 کاربران و کیف پول Delta", InlineKeyboardMarkup(keys)); return True
        if cmd == "usersearch":
            _flow(context, "d_usersearch", sid); await _edit(q, "ایمیل، کد مشتری، شماره موبایل یا نام را بفرست:"); return True
        if cmd == "user":
            u = (await core.api(site, "delta_user_detail", {"id": int(parts[3])}))["data"]; user_id = u["id"]
            lines = ["👤 مشخصات کامل کاربر", f"کد مشتری: {u.get('customer_code') or '-'}", f"نام: {u.get('full_name') or '-'}", f"ایمیل: {u.get('email')}", f"موبایل: {u.get('phone') or '-'}", f"وضعیت: {'✅ فعال' if u.get('is_active') else '⛔ غیرفعال'}", f"👛 کیف پول: {money(u.get('wallet_balance'))} تومان", f"🛒 سفارش‌ها: {u.get('order_count', 0)}", f"💰 خرید موفق: {money(u.get('total_spent'))} تومان"]
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ کیف پول", callback_data=f"d:wallet:{sid}:{user_id}:add"), InlineKeyboardButton("➖ کیف پول", callback_data=f"d:wallet:{sid}:{user_id}:sub")], [InlineKeyboardButton("📜 تراکنش‌ها", callback_data=f"d:wallethistory:{sid}:{user_id}")], [InlineKeyboardButton("📱 تغییر موبایل", callback_data=f"d:useredit:{sid}:{user_id}:phone"), InlineKeyboardButton("✉️ تغییر ایمیل", callback_data=f"d:useredit:{sid}:{user_id}:email")], [InlineKeyboardButton("🔐 بازیابی رمز", callback_data=f"d:userreset:{sid}:{user_id}"), InlineKeyboardButton("⏯ فعال/غیرفعال", callback_data=f"d:usertoggle:{sid}:{user_id}")], [InlineKeyboardButton("⬅️ کاربران", callback_data=f"d:users:{sid}")]])
            await _edit(q, "\n".join(lines), kb); return True
        if cmd == "wallet":
            _flow(context, "d_wallet_amount", sid, user_id=int(parts[3]), sign=1 if parts[4] == "add" else -1); await _edit(q, "مبلغ را به تومان بفرست:"); return True
        if cmd == "wallethistory":
            uid2 = int(parts[3]); x = (await core.api(site, "wallet_history", {"id": uid2, "limit": 20}))["data"]
            lines = [f"👛 موجودی: {money(x['balance'])} تومان", "", "📜 تراکنش‌های اخیر:"]
            lines += [f"• {'+' if int(t['amount']) > 0 else ''}{money(t['amount'])} | موجودی {money(t['balance_after'])}\n  {t.get('reason') or '-'}" for t in x.get("transactions", [])]
            await _edit(q, "\n".join(lines), InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ کاربر", callback_data=f"d:user:{sid}:{uid2}")]])); return True
        if cmd == "useredit":
            _flow(context, "d_useredit_" + parts[4], sid, user_id=int(parts[3])); await _edit(q, "مقدار جدید را بفرست:"); return True
        if cmd == "userreset":
            await core.api(site, "user_password_reset", {"id": int(parts[3])}, timeout=45); await q.answer("لینک بازیابی ارسال شد", show_alert=True); return True
        if cmd == "usertoggle":
            uid2 = int(parts[3]); u = (await core.api(site, "delta_user_detail", {"id": uid2}))["data"]
            await core.api(site, "user_update", {"id": uid2, "is_active": not u["is_active"]}); update.callback_query.data = f"d:user:{sid}:{uid2}"; return await callback(update, context)
        if cmd == "broadcast":
            _flow(context, "d_broadcast_subject", sid); await _edit(q, "📨 عنوان ایمیل همگانی را بفرست:"); return True
        if cmd == "broadcastsend":
            subject = context.user_data.get("broadcast_subject"); body = context.user_data.get("broadcast_body")
            x = (await core.api(site, "broadcast_email", {"subject": subject, "body": body}, timeout=180))["data"]
            context.user_data.clear(); await _edit(q, f"✅ ایمیل همگانی ارسال شد.\nگیرندگان: {x.get('recipients', 0)}\nارسال‌شده: {x.get('sent', 0)}", settings_menu(sid)); return True
        if cmd == "ann":
            rows = (await core.api(site, "announcements"))["data"]
            keys = [[InlineKeyboardButton("➕ اطلاعیه جدید", callback_data=f"d:annadd:{sid}")]]
            keys += [[InlineKeyboardButton(("✅ " if x["is_active"] else "⛔ ") + x["text"].replace("\n", " ")[:34], callback_data=f"d:anntoggle:{sid}:{x['id']}"), InlineKeyboardButton("🗑", callback_data=f"d:anndel:{sid}:{x['id']}")] for x in rows[:20]]
            keys.append([InlineKeyboardButton("⬅️ پنل Delta", callback_data=f"d:home:{sid}")])
            await _edit(q, "🔔 اطلاع‌رسانی سایت", InlineKeyboardMarkup(keys)); return True
        if cmd == "annadd":
            _flow(context, "d_annadd", sid); await _edit(q, "متن کامل اطلاعیه را بفرست:"); return True
        if cmd in {"anntoggle", "anndel"}:
            aid = int(parts[3]); rows = (await core.api(site, "announcements"))["data"]
            if cmd == "anntoggle":
                x = next((x for x in rows if int(x["id"]) == aid), None); await core.api(site, "announcement_update", {"id": aid, "is_active": not x["is_active"]})
            else:
                await core.api(site, "announcement_delete", {"id": aid})
            update.callback_query.data = f"d:ann:{sid}"; return await callback(update, context)
        if cmd == "settings":
            s = (await core.api(site, "settings_get"))["data"]
            await _edit(q, f"⚙️ تنظیمات اختصاصی Delta\n\nنام: {s.get('site_name')}\n☎️ تلفن: {s.get('contact_phone') or '-'}\n🚚 ارسال: {money(s.get('shipping_fee'))} تومان\n📦 بسته‌بندی و روش‌های پرداخت از بخش پرداخت مدیریت می‌شوند.", settings_menu(sid)); return True
        if cmd == "set":
            _flow(context, "d_set_" + parts[3], sid)
            prompts = {"name": "نام جدید سایت:", "logo": "لوگو را بفرست؛ عکس/فایل، لینک یا - برای حذف:", "phone": "شماره تلفن جدید:", "topbar": "متن جدید بالای سایت:", "footer": "توضیحات فوتر را بفرست:"}
            await _edit(q, prompts[parts[3]]); return True
        if cmd == "commerce":
            x = (await core.api(site, "delta_commerce_get"))["data"]
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"🏦 کارت‌به‌کارت: {'روشن' if x['card_payment_enabled'] else 'خاموش'}", callback_data=f"d:comtoggle:{sid}:card"), InlineKeyboardButton(f"💳 زرین‌پال: {'روشن' if x['zarinpal_payment_enabled'] else 'خاموش'}", callback_data=f"d:comtoggle:{sid}:zarinpal")], [InlineKeyboardButton("💳 شماره کارت", callback_data=f"d:comedit:{sid}:card_number"), InlineKeyboardButton("👤 صاحب حساب", callback_data=f"d:comedit:{sid}:card_owner")], [InlineKeyboardButton("🔑 مرچنت زرین‌پال", callback_data=f"d:comedit:{sid}:zarinpal_merchant_id")], [InlineKeyboardButton("🚚 هزینه ارسال", callback_data=f"d:comedit:{sid}:shipping_cost"), InlineKeyboardButton("📦 بسته‌بندی", callback_data=f"d:comedit:{sid}:packaging_cost")], [InlineKeyboardButton("🎁 حد ارسال رایگان", callback_data=f"d:comedit:{sid}:free_shipping_threshold"), InlineKeyboardButton(f"📦 مخفی ناموجود: {'روشن' if x['hide_out_of_stock'] else 'خاموش'}", callback_data=f"d:comtoggle:{sid}:hide")], [InlineKeyboardButton("🎟 کدهای تخفیف", callback_data=f"d:discounts:{sid}"), InlineKeyboardButton("📜 قوانین", callback_data=f"d:comedit:{sid}:terms_text")], [InlineKeyboardButton("⬅️ تنظیمات", callback_data=f"d:settings:{sid}")]])
            await _edit(q, f"💳 پرداخت، تخفیف و ارسال\n\nکارت: {x.get('card_number') or '-'}\nصاحب حساب: {x.get('card_owner') or '-'}\nارسال: {money(x['shipping_cost'])}\nبسته‌بندی: {money(x['packaging_cost'])}\nارسال رایگان از: {money(x['free_shipping_threshold'])}", kb); return True
        if cmd == "comtoggle":
            x = (await core.api(site, "delta_commerce_get"))["data"]
            key = {"card": "card_payment_enabled", "zarinpal": "zarinpal_payment_enabled", "hide": "hide_out_of_stock"}[parts[3]]
            await core.api(site, "delta_commerce_update", {key: not x[key]}); update.callback_query.data = f"d:commerce:{sid}"; return await callback(update, context)
        if cmd == "comedit":
            _flow(context, "d_comedit", sid, field=parts[3]); await _edit(q, "مقدار جدید را بفرست؛ برای خالی کردن - بفرست:"); return True
        if cmd == "discounts":
            rows = (await core.api(site, "discounts"))["data"]
            keys = [[InlineKeyboardButton("➕ کد تخفیف", callback_data=f"d:discountadd:{sid}")]]
            keys += [[InlineKeyboardButton(f"{'✅' if x['is_active'] else '⛔'} {x['code']}", callback_data=f"d:discounttoggle:{sid}:{x['id']}"), InlineKeyboardButton("🗑", callback_data=f"d:discountdel:{sid}:{x['id']}")] for x in rows]
            keys.append([InlineKeyboardButton("⬅️ پرداخت", callback_data=f"d:commerce:{sid}")]); await _edit(q, "🎟 کدهای تخفیف", InlineKeyboardMarkup(keys)); return True
        if cmd == "discountadd":
            _flow(context, "d_discount_code", sid); await _edit(q, "کد تخفیف جدید را بفرست:"); return True
        if cmd in {"discounttoggle", "discountdel"}:
            did = int(parts[3]); rows = (await core.api(site, "discounts"))["data"]
            if cmd == "discounttoggle":
                x = next(x for x in rows if int(x["id"]) == did); await core.api(site, "discount_update", {"id": did, "is_active": not x["is_active"]})
            else:
                await core.api(site, "discount_delete", {"id": did})
            update.callback_query.data = f"d:discounts:{sid}"; return await callback(update, context)
        if cmd == "socials":
            rows = (await core.api(site, "socials"))["data"]
            keys = [[InlineKeyboardButton(f"{'✅' if x['is_active'] else '⛔'} {x['title']}", callback_data=f"d:socialtoggle:{sid}:{x['id']}"), InlineKeyboardButton("🗑", callback_data=f"d:socialdel:{sid}:{x['id']}")] for x in rows]
            keys += [[InlineKeyboardButton("➕ افزودن شبکه", callback_data=f"d:socialadd:{sid}")], [InlineKeyboardButton("⬅️ تنظیمات", callback_data=f"d:settings:{sid}")]]
            await _edit(q, "🌐 شبکه‌های اجتماعی", InlineKeyboardMarkup(keys)); return True
        if cmd == "socialadd":
            keys = [[InlineKeyboardButton(label, callback_data=f"d:socialpick:{sid}:{val}")] for val, label in PLATFORMS]
            await _edit(q, "نوع شبکه را انتخاب کن:", InlineKeyboardMarkup(keys)); return True
        if cmd == "socialpick":
            _flow(context, "d_social_label", sid, social_platform=parts[3]); await _edit(q, "عنوان نمایش شبکه را بفرست:"); return True
        if cmd in {"socialtoggle", "socialdel"}:
            xid = int(parts[3]); rows = (await core.api(site, "socials"))["data"]
            if cmd == "socialtoggle":
                x = next(x for x in rows if int(x["id"]) == xid); await core.api(site, "social_update", {"id": xid, "is_active": not x["is_active"]})
            else:
                await core.api(site, "social_delete", {"id": xid})
            update.callback_query.data = f"d:socials:{sid}"; return await callback(update, context)
        if cmd == "badges":
            rows = (await core.api(site, "delta_badges"))["data"]; by = {x["badge_type"]: x for x in rows}
            keys = [[InlineKeyboardButton(f"🛡 اینماد {'✅' if 'enamad' in by else '❌'}", callback_data=f"d:badge:{sid}:enamad"), InlineKeyboardButton(f"💳 زرین‌پال {'✅' if 'zarinpal' in by else '❌'}", callback_data=f"d:badge:{sid}:zarinpal")], [InlineKeyboardButton("⬅️ تنظیمات", callback_data=f"d:settings:{sid}")]]
            await _edit(q, "🛡 نمادهای اعتماد", InlineKeyboardMarkup(keys)); return True
        if cmd == "badge":
            _flow(context, "d_badge_image", sid, badge_type=parts[3]); await _edit(q, "عکس نماد را بفرست؛ عکس/فایل، لینک http/https یا - برای حذف:"); return True
        if cmd == "banners":
            rows = (await core.api(site, "banners"))["data"]; keys = []
            for x in rows:
                keys.append([InlineKeyboardButton(f"{'✅' if x['is_active'] else '⛔'} {x.get('title') or 'بنر #' + str(x['id'])}", callback_data=f"d:bannertoggle:{sid}:{x['id']}"), InlineKeyboardButton("📱 عکس موبایل", callback_data=f"d:bannermobile:{sid}:{x['id']}"), InlineKeyboardButton("🗑", callback_data=f"d:bannerdelete:{sid}:{x['id']}")])
            keys += [[InlineKeyboardButton("➕ افزودن بنر", callback_data=f"d:banneradd:{sid}")], [InlineKeyboardButton("⬅️ تنظیمات", callback_data=f"d:settings:{sid}")]]
            await _edit(q, "📣 بنرهای تبلیغاتی", InlineKeyboardMarkup(keys)); return True
        if cmd == "banneradd":
            _flow(context, "d_banner_title", sid); await _edit(q, "عنوان بنر را بفرست؛ - برای بدون عنوان:"); return True
        if cmd == "bannermobile":
            _flow(context, "d_banner_mobile", sid, banner_id=int(parts[3])); await _edit(q, "عکس مخصوص موبایل را بفرست؛ عکس/فایل یا لینک http/https:"); return True
        if cmd in {"bannertoggle", "bannerdelete"}:
            bid = int(parts[3])
            if cmd == "bannertoggle":
                x = (await core.api(site, "banner_detail", {"id": bid}))["data"]; await core.api(site, "banner_update", {"id": bid, "is_active": not x["is_active"]})
            else:
                await core.api(site, "banner_delete", {"id": bid})
            update.callback_query.data = f"d:banners:{sid}"; return await callback(update, context)
        if cmd == "orders":
            rows = (await core.api(site, "orders"))["data"]
            keys = [[InlineKeyboardButton(f"#{x['code']} | {x['status_label']} | {money(x['total'])}", callback_data=f"d:order:{sid}:{x['id']}")] for x in rows[:40]]
            keys.append([InlineKeyboardButton("⬅️ پنل Delta", callback_data=f"d:home:{sid}")]); await _edit(q, "🛒 سفارش‌های Delta", InlineKeyboardMarkup(keys)); return True
        if cmd == "order":
            oid = int(parts[3]); o = (await core.api(site, "order_detail", {"id": oid}))["data"]; w = (await core.api(site, "delta_order_wallet", {"id": oid}))["data"]
            lines = [f"🛒 سفارش #{o['code']}", f"مشتری: {o.get('full_name')}", f"موبایل: {o.get('mobile')}", f"مبلغ کل: {money(o['total'])} تومان", f"👛 سهم کیف پول: {money(w['wallet_amount'])} تومان", f"💳 قابل پرداخت خارجی: {money(w['external_payable'])} تومان", f"روش: {o.get('payment_method_label')}", f"وضعیت: {o.get('status_label')}", f"کد رهگیری: {o.get('tracking_code') or '-'}", "", "📦 اقلام:"]
            lines += [f"• {i['title']} × {i['quantity']} — {money(i['total'])}" for i in o.get("items", [])]
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ آماده‌سازی", callback_data=f"d:orderstatus:{sid}:{oid}:processing"), InlineKeyboardButton("🚚 ارسال شد", callback_data=f"d:orderstatus:{sid}:{oid}:shipped")], [InlineKeyboardButton("✅ تحویل شد", callback_data=f"d:orderstatus:{sid}:{oid}:delivered"), InlineKeyboardButton("❌ لغو", callback_data=f"d:orderstatus:{sid}:{oid}:cancelled")], [InlineKeyboardButton("📦 کد رهگیری", callback_data=f"d:track:{sid}:{oid}")], [InlineKeyboardButton("⬅️ سفارش‌ها", callback_data=f"d:orders:{sid}")]])
            await _edit(q, "\n".join(lines), kb); return True
        if cmd == "orderstatus":
            await core.api(site, "order_update", {"id": int(parts[3]), "status": parts[4]}, timeout=45); update.callback_query.data = f"d:order:{sid}:{parts[3]}"; return await callback(update, context)
        if cmd == "track":
            _flow(context, "d_track", sid, order_id=int(parts[3])); await _edit(q, "کد رهگیری مرسوله را بفرست:"); return True
        if cmd == "receipts":
            rows = (await core.api(site, "receipts"))["data"]
            keys = [[InlineKeyboardButton(f"#{x['order_code']} | {x['status']} | {money(x['total'])}", callback_data=f"d:receipt:{sid}:{x['id']}")] for x in rows[:40]]
            keys.append([InlineKeyboardButton("⬅️ پنل Delta", callback_data=f"d:home:{sid}")]); await _edit(q, "🧾 رسیدهای کارت‌به‌کارت", InlineKeyboardMarkup(keys)); return True
        if cmd == "receipt":
            rid = int(parts[3]); x = (await core.api(site, "receipt_detail", {"id": rid}))["data"]
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ تایید", callback_data=f"d:receiptset:{sid}:{rid}:approved"), InlineKeyboardButton("❌ رد با دلیل", callback_data=f"d:receiptreject:{sid}:{rid}")], [InlineKeyboardButton("🖼 دریافت تصویر رسید", callback_data=f"d:receiptimage:{sid}:{rid}")], [InlineKeyboardButton("⬅️ رسیدها", callback_data=f"d:receipts:{sid}")]])
            await _edit(q, f"🧾 رسید سفارش #{x['order_code']}\nمشتری: {x.get('full_name')}\nمبلغ: {money(x['total'])}\nوضعیت: {x['status']}\nدلیل رد: {x.get('rejection_reason') or '-'}", kb); return True
        if cmd == "receiptset":
            await core.api(site, "receipt_update", {"id": int(parts[3]), "status": parts[4]}, timeout=45); update.callback_query.data = f"d:receipt:{sid}:{parts[3]}"; return await callback(update, context)
        if cmd == "receiptreject":
            _flow(context, "d_receipt_reject", sid, receipt_id=int(parts[3])); await _edit(q, "دلیل رد رسید را بنویس:"); return True
        if cmd == "receiptimage":
            x = (await core.api(site, "receipt_image", {"id": int(parts[3])}, timeout=45))["data"]
            raw = base64.b64decode(x["image_b64"]); bio = io.BytesIO(raw); bio.name = x.get("filename") or "receipt.jpg"
            await q.message.reply_photo(photo=bio, caption="🧾 تصویر رسید"); return True
        if cmd == "backup":
            x = (await core.api(site, "backup_status", timeout=35))["data"]; mins = int(x.get("interval_minutes") or 0)
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("📤 بکاپ کامل همین حالا", callback_data=f"d:backupnow:{sid}"), InlineKeyboardButton("📂 بکاپ‌های سرور", callback_data=f"d:backuplist:{sid}")], [InlineKeyboardButton("⏱ زمان‌بندی", callback_data=f"d:backupinterval:{sid}"), InlineKeyboardButton("♻️ بازگردانی بکاپ", callback_data=f"d:backuprestore:{sid}")], [InlineKeyboardButton("⬅️ پنل Delta", callback_data=f"d:home:{sid}")]])
            await _edit(q, f"💾 بکاپ کامل DeltaJanebi\n\nزمان‌بندی: {'هر ' + str(mins) + ' دقیقه' if mins else 'غیرفعال'}\nفایل .deltabackup شامل دیتابیس کامل و media سایت است.", kb); return True
        if cmd == "backupnow":
            await _edit(q, "⏳ در حال ساخت بکاپ کامل Delta...")
            x = (await core.api(site, "backup_create", {"label": "manual"}, timeout=300))["data"]
            raw = base64.b64decode(x["backup_b64"]); bio = io.BytesIO(raw); bio.name = x.get("filename") or "delta.deltabackup"
            await context.bot.send_document(chat_id=update.effective_chat.id, document=bio, filename=bio.name, caption="💾 بکاپ کامل DeltaJanebi")
            await q.message.reply_text("✅ بکاپ ساخته و ارسال شد.", reply_markup=back(sid)); return True
        if cmd == "backuplist":
            rows = (await core.api(site, "delta_backup_list", timeout=35))["data"]
            keys = [[InlineKeyboardButton(f"💾 {x['filename'][:40]} ({x['size'] / 1024 / 1024:.1f}MB)", callback_data=f"d:backupget:{sid}:{i}")] for i, x in enumerate(rows)]
            context.user_data["backup_rows"] = rows
            keys.append([InlineKeyboardButton("⬅️ بکاپ", callback_data=f"d:backup:{sid}")]); await _edit(q, "📂 آخرین بکاپ‌های کامل روی سرور", InlineKeyboardMarkup(keys)); return True
        if cmd == "backupget":
            rows = context.user_data.get("backup_rows") or []; idx = int(parts[3])
            if idx >= len(rows):
                await q.answer("لیست منقضی شده؛ دوباره باز کن", show_alert=True); return True
            x = (await core.api(site, "delta_backup_get", {"filename": rows[idx]["filename"]}, timeout=120))["data"]
            raw = base64.b64decode(x["backup_b64"]); bio = io.BytesIO(raw); bio.name = x["filename"]
            await context.bot.send_document(chat_id=update.effective_chat.id, document=bio, filename=bio.name, caption="💾 بکاپ ذخیره‌شده DeltaJanebi"); return True
        if cmd == "backupinterval":
            _flow(context, "d_backup_interval", sid); await _edit(q, "فاصله بکاپ خودکار را به دقیقه بفرست؛ حداقل 5، برای خاموش 0:"); return True
        if cmd == "backuprestore":
            _flow(context, "d_backup_restore", sid); await _edit(q, "فایل .deltabackup را به‌صورت Document بفرست:"); return True
        if cmd == "backuprestoreconfirm":
            path = Path(context.user_data.get("restore_path", ""))
            if not path.is_file():
                await q.answer("فایل موقت پیدا نشد", show_alert=True); return True
            raw = path.read_bytes()
            await core.api(site, "backup_restore", {"filename": path.name, "backup_b64": base64.b64encode(raw).decode()}, timeout=420)
            path.unlink(missing_ok=True); context.user_data.clear(); await _edit(q, "✅ بکاپ کامل Delta بازگردانی شد.", panel(site, uid)); return True
        if cmd == "stories":
            rows = (await core.api(site, "stories"))["data"]
            keys = [[InlineKeyboardButton(f"{'✅' if x['active_now'] else '⛔'} {x['title']}", callback_data=f"d:story:{sid}:{x['id']}")] for x in rows]
            keys += [[InlineKeyboardButton("➕ معرفی محصول", callback_data=f"d:storyadd:{sid}")], [InlineKeyboardButton("⬅️ پنل Delta", callback_data=f"d:home:{sid}")]]
            await _edit(q, "🔴 معرفی محصولات", InlineKeyboardMarkup(keys)); return True
        if cmd == "storyadd":
            _flow(context, "d_story_title", sid); await _edit(q, "عنوان معرفی محصول را بفرست:"); return True
        if cmd == "story":
            x = (await core.api(site, "story_detail", {"id": int(parts[3])}))["data"]
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑 حذف معرفی", callback_data=f"d:storydelete:{sid}:{x['id']}")], [InlineKeyboardButton("⬅️ معرفی‌ها", callback_data=f"d:stories:{sid}")]])
            await _edit(q, f"🔴 {x['title']}\nنوع: {x['media_type']}\nفعال: {'✅' if x['active_now'] else '⛔'}\nلینک: {x['target_url']}\nانقضا: {str(x['expires_at']).replace('T', ' ')[:16]}", kb); return True
        if cmd == "storydelete":
            await core.api(site, "story_delete", {"id": int(parts[3])}); update.callback_query.data = f"d:stories:{sid}"; return await callback(update, context)
    except Exception as exc:
        try:
            await q.answer("عملیات ناموفق بود؛ اتصال سایت حفظ شده است.")
        except Exception:
            pass
        await _edit(q, f"⚠️ عملیات Delta انجام نشد، ولی اتصال سایت حذف نشده است.\n\n{str(exc)[:900]}", panel(site, uid)); return True
    return True


async def message(update, context):
    if context.user_data.get("platform") != "deltajanebi":
        return False
    uid = update.effective_user.id
    sid = context.user_data.get("site_id")
    site = _site(uid, sid) if sid else None
    if not site:
        context.user_data.clear(); await update.message.reply_text("دسترسی سایت پیدا نشد."); return True
    flow = context.user_data.get("flow") or ""
    text = (update.message.text or "").strip()
    try:
        if flow == "d_psearch":
            context.user_data.clear(); rows = (await core.api(site, "delta_products", {"mode": "all", "query": text}))["data"]
            keys = [[InlineKeyboardButton(f"{x['name'][:38]} | {money(x['effective_price'])}", callback_data=f"d:product:{sid}:{x['id']}")] for x in rows[:45]]
            keys.append([InlineKeyboardButton("⬅️ محصولات", callback_data=f"d:pmenu:{sid}")]); await update.message.reply_text(f"🔎 {len(rows)} نتیجه", reply_markup=InlineKeyboardMarkup(keys)); return True
        if flow == "d_usersearch":
            context.user_data.clear(); rows = (await core.api(site, "user_search", {"query": text}))["data"]
            keys = [[InlineKeyboardButton(f"{x.get('customer_code') or '-'} | {(x.get('full_name') or x.get('email') or '')[:36]}", callback_data=f"d:user:{sid}:{x['id']}")] for x in rows[:45]]
            keys.append([InlineKeyboardButton("⬅️ کاربران", callback_data=f"d:users:{sid}")]); await update.message.reply_text(f"🔎 {len(rows)} کاربر پیدا شد.", reply_markup=InlineKeyboardMarkup(keys)); return True
        if flow == "d_manual_name":
            context.user_data.update(flow="d_manual_price", name=text[:300]); await update.message.reply_text("قیمت به تومان:"); return True
        if flow == "d_manual_price":
            if not text.replace(",", "").isdigit():
                await update.message.reply_text("فقط عدد بفرست."); return True
            context.user_data.update(flow="d_manual_stock", price=int(text.replace(",", ""))); await update.message.reply_text("موجودی:"); return True
        if flow == "d_manual_stock":
            if not text.isdigit():
                await update.message.reply_text("فقط عدد بفرست."); return True
            context.user_data.update(flow="d_manual_category", stock=int(text)); await update.message.reply_text("مسیر دسته را با > بفرست؛ مثال کابل > Type-C یا - برای بدون دسته:"); return True
        if flow == "d_manual_category":
            context.user_data.update(flow="d_manual_image", category_path=[] if text == "-" else [x.strip() for x in text.split(">") if x.strip()]); await update.message.reply_text("عکس محصول را بفرست؛ عکس/فایل، لینک یا -:"); return True
        if flow == "d_manual_image":
            payload = {"name": context.user_data["name"], "price": context.user_data["price"], "stock": context.user_data["stock"], "category_path": context.user_data.get("category_path", [])}
            if text != "-":
                if not re.match(r"^https?://", text, re.I):
                    await update.message.reply_text("عکس/فایل، لینک معتبر یا - بفرست."); return True
                payload["image_url"] = text
            p = (await core.api(site, "delta_manual_product_create", payload, timeout=90))["data"]
            context.user_data.clear(); await update.message.reply_text("✅ محصول عادی ثبت شد.\n\n" + _product_text(p), reply_markup=_product_kb(sid, p)); return True
        if flow == "d_special_url":
            context.user_data.update(flow="d_special_markup", source_url=text); await update.message.reply_text("افزایش قیمت را بفرست؛ 20% یا 20000:"); return True
        if flow == "d_special_markup":
            raw = text.replace(",", "").strip(); mt = "percent" if raw.endswith("%") else "fixed"; n = raw[:-1] if mt == "percent" else raw
            try:
                val = float(n)
            except Exception:
                await update.message.reply_text("فرمت نامعتبر؛ 20% یا 20000"); return True
            x = (await core.api(site, "delta_product_from_source", {"source_id": context.user_data["source_id"], "url": context.user_data["source_url"], "markup_type": mt, "markup_value": val}, timeout=180))["data"]
            context.user_data.clear(); p = x["product"]; await update.message.reply_text("✅ محصول خاص ثبت/Sync شد.\n\n" + _product_text(p), reply_markup=_product_kb(sid, p)); return True
        if flow.startswith("d_pedit_"):
            field = flow[8:]; pid = int(context.user_data["product_id"])
            if field in {"price", "stock", "discount", "amazing", "timed"}:
                raw = text.replace(",", "").strip()
                if not raw.isdigit():
                    await update.message.reply_text("فقط عدد بفرست."); return True
                val = int(raw)
                if field == "price":
                    await core.api(site, "product_update", {"id": pid, "price": val})
                elif field == "stock":
                    await core.api(site, "product_update", {"id": pid, "stock": val})
                elif field == "discount":
                    await core.api(site, "product_update", {"id": pid, "discount_price": val})
                elif field == "amazing":
                    await core.api(site, "product_update", {"id": pid, "amazing_price": val})
                else:
                    context.user_data.update(flow="d_timed_duration", timed_price=val); await update.message.reply_text("مدت را بفرست: 30m، 2h، 1d یا فقط تعداد دقیقه:"); return True
            elif field == "name":
                await core.api(site, "product_update", {"id": pid, "name": text})
            elif field == "image":
                if text == "-":
                    await core.api(site, "delta_product_image_remove", {"id": pid})
                elif re.match(r"^https?://", text, re.I):
                    await core.api(site, "delta_product_image_url_set", {"id": pid, "url": text})
                else:
                    await update.message.reply_text("عکس/فایل بفرست، یا لینک معتبر، یا -"); return True
            context.user_data.clear(); p = (await core.api(site, "delta_product_detail", {"id": pid}))["data"]
            await update.message.reply_text("✅ ذخیره شد.\n\n" + _product_text(p), reply_markup=_product_kb(sid, p)); return True
        if flow == "d_timed_duration":
            m = re.fullmatch(r"\s*(\d+)\s*([mhd]?)\s*", text.lower())
            if not m:
                await update.message.reply_text("مدت نامعتبر؛ مثال 30m یا 2h یا 1d"); return True
            n = int(m.group(1)); unit = m.group(2); minutes = n * (60 if unit == "h" else 1440 if unit == "d" else 1)
            pid = int(context.user_data["product_id"])
            await core.api(site, "delta_timed_offer_set", {"id": pid, "price": context.user_data["timed_price"], "minutes": minutes})
            context.user_data.clear(); p = (await core.api(site, "delta_product_detail", {"id": pid}))["data"]
            await update.message.reply_text("✅ پیشنهاد زمان‌دار فعال شد.", reply_markup=_product_kb(sid, p)); return True
        if flow == "d_catadd":
            await core.api(site, "category_create", {"name": text}); context.user_data.clear(); await update.message.reply_text("✅ دسته ساخته شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📂 دسته‌ها", callback_data=f"d:cats:{sid}")]])); return True
        if flow == "d_catedit_name":
            cid = context.user_data["category_id"]; await core.api(site, "category_update", {"id": cid, "name": text}); context.user_data.clear(); await update.message.reply_text("✅ نام دسته تغییر کرد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ دسته", callback_data=f"d:cat:{sid}:{cid}")]])); return True
        if flow == "d_catedit_image":
            cid = context.user_data["category_id"]
            if text == "-":
                await core.api(site, "category_image_remove", {"id": cid})
            elif re.match(r"^https?://", text, re.I):
                await core.api(site, "delta_category_image_url_set", {"id": cid, "url": text})
            else:
                await update.message.reply_text("عکس/فایل، لینک یا - بفرست."); return True
            context.user_data.clear(); await update.message.reply_text("✅ عکس دسته تغییر کرد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ دسته", callback_data=f"d:cat:{sid}:{cid}")]])); return True
        if flow == "d_source_add_url":
            context.user_data.update(flow="d_source_add_terms", source_url=text); await update.message.reply_text("عبارت‌های تبلیغاتی/برندی که باید از محصولات پاک شوند را با کاما بفرست؛ - برای هیچ‌کدام:"); return True
        if flow == "d_source_add_terms":
            x = (await core.api(site, "source_site_create", {"name": re.sub(r"^https?://", "", context.user_data["source_url"]).split("/")[0], "base_url": context.user_data["source_url"], "brand_terms": "" if text == "-" else text}))["data"]
            context.user_data.clear(); await update.message.reply_text("✅ سایت منبع ثبت شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🌐 تنظیمات منبع", callback_data=f"d:source:{sid}:{x['id']}")]])); return True
        if flow in {"d_sourceprice", "d_sourcename", "d_sourceterms"}:
            xid = context.user_data["source_id"]
            if flow == "d_sourceprice":
                raw = text.replace(",", "").strip(); mt = "percent" if raw.endswith("%") else "fixed"; num = raw[:-1] if mt == "percent" else raw
                try:
                    val = float(num)
                except Exception:
                    await update.message.reply_text("فرمت نامعتبر؛ 20% یا 20000"); return True
                x = (await core.api(site, "delta_source_markup_update", {"id": xid, "markup_type": mt, "markup_value": val, "apply_existing": True}))["data"]
                msg = f"✅ قیمت منبع ذخیره شد؛ {x['updated_products']} محصول موجود هم به‌روزرسانی شد."
            else:
                await core.api(site, "source_site_update", {"id": xid, "name": text} if flow == "d_sourcename" else {"id": xid, "brand_terms": "" if text == "-" else text}); msg = "✅ ذخیره شد."
            context.user_data.clear(); await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ منبع", callback_data=f"d:source:{sid}:{xid}")]])); return True
        if flow == "d_wallet_amount":
            raw = text.replace(",", "")
            if not raw.isdigit() or int(raw) <= 0:
                await update.message.reply_text("مبلغ معتبر بفرست."); return True
            context.user_data.update(flow="d_wallet_reason", amount=int(raw) * int(context.user_data["sign"])); await update.message.reply_text("دلیل تراکنش را بنویس؛ - برای بدون توضیح:"); return True
        if flow == "d_wallet_reason":
            uid2 = context.user_data["user_id"]; amount = context.user_data["amount"]
            x = (await core.api(site, "wallet_adjust", {"id": uid2, "amount": amount, "reason": "" if text == "-" else text, "admin_id": str(uid)}))["data"]
            context.user_data.clear(); await update.message.reply_text(f"✅ تراکنش ثبت شد. موجودی جدید: {money(x['balance'])} تومان", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ کاربر", callback_data=f"d:user:{sid}:{uid2}")]])); return True
        if flow in {"d_useredit_email", "d_useredit_phone"}:
            uid2 = context.user_data["user_id"]; field = flow.rsplit("_", 1)[1]
            await core.api(site, "user_update", {"id": uid2, field: text}); context.user_data.clear(); await update.message.reply_text("✅ مشخصات کاربر تغییر کرد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ کاربر", callback_data=f"d:user:{sid}:{uid2}")]])); return True
        if flow == "d_broadcast_subject":
            context.user_data.update(flow="d_broadcast_body", broadcast_subject=text[:180]); await update.message.reply_text("متن کامل ایمیل را بفرست:"); return True
        if flow == "d_broadcast_body":
            context.user_data.update(flow="d_broadcast_confirm", broadcast_body=text)
            await update.message.reply_text(f"📨 پیش‌نمایش\nعنوان: {context.user_data['broadcast_subject']}\n\n{text[:2500]}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ ارسال به همه", callback_data=f"d:broadcastsend:{sid}"), InlineKeyboardButton("❌ لغو", callback_data=f"d:settings:{sid}")]])); return True
        if flow == "d_annadd":
            await core.api(site, "announcement_create", {"text": text}); context.user_data.clear(); await update.message.reply_text("✅ اطلاعیه ثبت شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔔 اطلاع‌رسانی", callback_data=f"d:ann:{sid}")]])); return True
        if flow.startswith("d_set_"):
            field = flow[6:]
            if field == "name":
                await core.api(site, "settings_update", {"site_name": text})
            elif field == "phone":
                await core.api(site, "settings_update", {"contact_phone": text})
            elif field == "topbar":
                await core.api(site, "settings_update", {"announcement": text})
            elif field == "footer":
                await core.api(site, "settings_update", {"footer_description": text})
            elif field == "logo":
                if text == "-":
                    await core.api(site, "logo_remove")
                elif re.match(r"^https?://", text, re.I):
                    await core.api(site, "delta_logo_url_set", {"url": text})
                else:
                    await update.message.reply_text("عکس/فایل، لینک یا - بفرست."); return True
            context.user_data.clear(); await update.message.reply_text("✅ تنظیمات ذخیره شد.", reply_markup=settings_menu(sid)); return True
        if flow == "d_comedit":
            field = context.user_data["field"]; val = "" if text == "-" else text
            if field in {"shipping_cost", "packaging_cost", "free_shipping_threshold"}:
                raw = val.replace(",", "")
                if not raw.isdigit():
                    await update.message.reply_text("فقط عدد بفرست."); return True
                val = int(raw)
            await core.api(site, "delta_commerce_update", {field: val}); context.user_data.clear(); await update.message.reply_text("✅ ذخیره شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ پرداخت", callback_data=f"d:commerce:{sid}")]])); return True
        if flow == "d_discount_code":
            context.user_data.update(flow="d_discount_value", discount_code=text.upper()); await update.message.reply_text("مقدار را بفرست؛ 10% برای درصد یا 50000 برای مبلغ ثابت:"); return True
        if flow == "d_discount_value":
            raw = text.replace(",", "").strip(); typ = "percent" if raw.endswith("%") else "fixed"; num = raw[:-1] if typ == "percent" else raw
            if not num.isdigit() or int(num) <= 0:
                await update.message.reply_text("مقدار نامعتبر."); return True
            await core.api(site, "discount_create", {"code": context.user_data["discount_code"], "discount_type": typ, "value": int(num)}); context.user_data.clear(); await update.message.reply_text("✅ کد تخفیف ساخته شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎟 کدها", callback_data=f"d:discounts:{sid}")]])); return True
        if flow == "d_social_label":
            context.user_data.update(flow="d_social_url", social_label=text[:80]); await update.message.reply_text("لینک کامل شبکه را با https:// بفرست:"); return True
        if flow == "d_social_url":
            await core.api(site, "social_create", {"platform": context.user_data["social_platform"], "title": context.user_data["social_label"], "url": text}); context.user_data.clear(); await update.message.reply_text("✅ شبکه اجتماعی اضافه شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🌐 شبکه‌ها", callback_data=f"d:socials:{sid}")]])); return True
        if flow == "d_banner_title":
            context.user_data.update(flow="d_banner_target", banner_title="" if text == "-" else text[:160]); await update.message.reply_text("لینک مقصد بنر را بفرست؛ - برای بدون لینک:"); return True
        if flow == "d_banner_target":
            context.user_data.update(flow="d_banner_image", banner_target="" if text == "-" else text); await update.message.reply_text("عکس دسکتاپ بنر را بفرست؛ عکس/فایل یا لینک:"); return True
        if flow == "d_banner_image" and re.match(r"^https?://", text, re.I):
            await core.api(site, "delta_banner_create", {"title": context.user_data["banner_title"], "target_url": context.user_data["banner_target"], "image_url": text}); context.user_data.clear(); await update.message.reply_text("✅ بنر اضافه شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📣 بنرها", callback_data=f"d:banners:{sid}")]])); return True
        if flow == "d_banner_mobile" and re.match(r"^https?://", text, re.I):
            bid = context.user_data["banner_id"]; await core.api(site, "delta_banner_media_set", {"id": bid, "mobile": True, "image_url": text}); context.user_data.clear(); await update.message.reply_text("✅ عکس موبایل بنر تغییر کرد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📣 بنرها", callback_data=f"d:banners:{sid}")]])); return True
        if flow == "d_badge_image":
            kind = context.user_data["badge_type"]
            if text == "-":
                await core.api(site, "delta_badge_remove", {"badge_type": kind}); context.user_data.clear(); await update.message.reply_text("✅ نماد حذف شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛡 نمادها", callback_data=f"d:badges:{sid}")]])); return True
            if re.match(r"^https?://", text, re.I):
                context.user_data.update(flow="d_badge_target", badge_image_url=text); await update.message.reply_text("لینک مقصد نماد را بفرست؛ - برای بدون لینک:"); return True
            await update.message.reply_text("عکس/فایل، لینک یا - بفرست."); return True
        if flow == "d_badge_target":
            payload = {"badge_type": context.user_data["badge_type"], "target_url": "" if text == "-" else text}
            if context.user_data.get("badge_image_b64"):
                payload.update(image_b64=context.user_data["badge_image_b64"], image_filename=context.user_data.get("badge_image_filename") or "badge.jpg")
            else:
                payload["image_url"] = context.user_data["badge_image_url"]
            await core.api(site, "delta_badge_set", payload); context.user_data.clear(); await update.message.reply_text("✅ نماد ذخیره شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛡 نمادها", callback_data=f"d:badges:{sid}")]])); return True
        if flow == "d_track":
            oid = context.user_data["order_id"]; await core.api(site, "order_update", {"id": oid, "status": "shipped", "tracking_code": text}); context.user_data.clear(); await update.message.reply_text("✅ کد رهگیری ثبت شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ سفارش", callback_data=f"d:order:{sid}:{oid}")]])); return True
        if flow == "d_receipt_reject":
            rid = context.user_data["receipt_id"]; await core.api(site, "receipt_update", {"id": rid, "status": "rejected", "reason": text}, timeout=45); context.user_data.clear(); await update.message.reply_text("❌ رسید رد شد و منطق برگشت موجودی/کیف پول Delta اجرا شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رسیدها", callback_data=f"d:receipts:{sid}")]])); return True
        if flow == "d_backup_interval":
            if not text.isdigit() or (int(text) != 0 and int(text) < 5):
                await update.message.reply_text("0 یا عدد حداقل 5 بفرست."); return True
            await core.api(site, "backup_interval_set", {"minutes": int(text)}); context.user_data.clear(); await update.message.reply_text("✅ زمان‌بندی بکاپ ذخیره شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💾 بکاپ", callback_data=f"d:backup:{sid}")]])); return True
        if flow == "d_story_title":
            context.user_data.update(flow="d_story_target", story_title=text[:160]); await update.message.reply_text("لینک محصول/مقصد معرفی را بفرست:"); return True
        if flow == "d_story_target":
            context.user_data.update(flow="d_story_hours", story_target=text); await update.message.reply_text("مدت نمایش را به ساعت بفرست؛ مثال 24:"); return True
        if flow == "d_story_hours":
            if not text.isdigit() or int(text) <= 0:
                await update.message.reply_text("فقط تعداد ساعت را بفرست."); return True
            context.user_data.update(flow="d_story_media", story_hours=min(int(text), 720)); await update.message.reply_text("عکس یا ویدئوی معرفی را بفرست:"); return True
    except Exception as exc:
        await update.message.reply_text(f"⚠️ عملیات Delta انجام نشد ولی اتصال سایت حفظ شده است.\n{str(exc)[:900]}", reply_markup=panel(site, uid)); return True
    return True


async def _download_media(message, allow_video=False):
    obj = None; filename = "upload.jpg"
    if message.photo:
        obj = message.photo[-1]; filename = "photo.jpg"
    elif allow_video and message.video:
        obj = message.video; filename = message.video.file_name or "story.mp4"
    elif message.document:
        obj = message.document; filename = message.document.file_name or "upload.bin"
    if not obj:
        raise ValueError("فایل پیدا نشد.")
    if getattr(obj, "file_size", 0) and obj.file_size > MAX_FILE:
        raise ValueError("حجم فایل بیش از حد مجاز است.")
    tg = await obj.get_file(); buf = io.BytesIO(); await tg.download_to_memory(buf); raw = buf.getvalue()
    if len(raw) > MAX_FILE:
        raise ValueError("حجم فایل بیش از حد مجاز است.")
    return raw, filename


async def media(update, context):
    if context.user_data.get("platform") != "deltajanebi":
        return False
    uid = update.effective_user.id; sid = context.user_data.get("site_id"); site = _site(uid, sid) if sid else None; flow = context.user_data.get("flow") or ""
    if not site:
        return False
    try:
        if flow in {"d_manual_image", "d_pedit_image", "d_catedit_image", "d_set_logo", "d_banner_image", "d_banner_mobile", "d_badge_image", "d_story_media"}:
            raw, name = await _download_media(update.message, allow_video=flow == "d_story_media"); b64 = base64.b64encode(raw).decode()
            if flow == "d_manual_image":
                payload = {"name": context.user_data["name"], "price": context.user_data["price"], "stock": context.user_data["stock"], "category_path": context.user_data.get("category_path", []), "image_b64": b64, "image_filename": name}
                p = (await core.api(site, "delta_manual_product_create", payload, timeout=90))["data"]
                context.user_data.clear(); await update.message.reply_text("✅ محصول عادی ثبت شد.\n\n" + _product_text(p), reply_markup=_product_kb(sid, p)); return True
            if flow == "d_pedit_image":
                pid = context.user_data["product_id"]; await core.api(site, "product_image_set", {"id": pid, "image_b64": b64, "image_filename": name}, timeout=90); context.user_data.clear(); p = (await core.api(site, "delta_product_detail", {"id": pid}))["data"]
                await update.message.reply_text("✅ عکس محصول تغییر کرد.", reply_markup=_product_kb(sid, p)); return True
            if flow == "d_catedit_image":
                cid = context.user_data["category_id"]; await core.api(site, "category_image_set", {"id": cid, "image_b64": b64, "image_filename": name}, timeout=90); context.user_data.clear(); await update.message.reply_text("✅ عکس دسته تغییر کرد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ دسته", callback_data=f"d:cat:{sid}:{cid}")]])); return True
            if flow == "d_set_logo":
                await core.api(site, "logo_set", {"image_b64": b64, "image_filename": name}, timeout=90); context.user_data.clear(); await update.message.reply_text("✅ لوگو تغییر کرد.", reply_markup=settings_menu(sid)); return True
            if flow == "d_banner_image":
                await core.api(site, "delta_banner_create", {"title": context.user_data["banner_title"], "target_url": context.user_data["banner_target"], "image_b64": b64, "image_filename": name}, timeout=90); context.user_data.clear(); await update.message.reply_text("✅ بنر اضافه شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📣 بنرها", callback_data=f"d:banners:{sid}")]])); return True
            if flow == "d_banner_mobile":
                bid = context.user_data["banner_id"]; await core.api(site, "delta_banner_media_set", {"id": bid, "mobile": True, "image_b64": b64, "image_filename": name}, timeout=90); context.user_data.clear(); await update.message.reply_text("✅ عکس موبایل بنر تغییر کرد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📣 بنرها", callback_data=f"d:banners:{sid}")]])); return True
            if flow == "d_badge_image":
                context.user_data.update(flow="d_badge_target", badge_image_b64=b64, badge_image_filename=name); await update.message.reply_text("لینک مقصد نماد را بفرست؛ - برای بدون لینک:"); return True
            if flow == "d_story_media":
                typ = "video" if update.message.video or (update.message.document and str(name).lower().endswith((".mp4", ".mov", ".webm"))) else "image"
                await core.api(site, "story_create", {"title": context.user_data["story_title"], "target_url": context.user_data["story_target"], "duration_hours": context.user_data["story_hours"], "media_type": typ, "media_b64": b64, "media_filename": name}, timeout=120)
                context.user_data.clear(); await update.message.reply_text("✅ معرفی محصول ساخته شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 معرفی‌ها", callback_data=f"d:stories:{sid}")]])); return True
        if flow == "d_backup_restore":
            doc = update.message.document
            if not doc or not (doc.file_name or "").lower().endswith(".deltabackup"):
                await update.message.reply_text("فقط فایل .deltabackup بفرست."); return True
            handle = tempfile.NamedTemporaryFile(prefix="delta-restore-", suffix=".deltabackup", delete=False); handle.close(); path = Path(handle.name)
            tg = await doc.get_file(); await tg.download_to_drive(custom_path=str(path))
            context.user_data.update(flow="d_backup_restore_confirm", restore_path=str(path))
            await update.message.reply_text("⚠️ دیتابیس و media فعلی با بکاپ جایگزین شوند؟", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ تایید بازگردانی", callback_data=f"d:backuprestoreconfirm:{sid}"), InlineKeyboardButton("❌ لغو", callback_data=f"d:backup:{sid}")]])); return True
    except Exception as exc:
        await update.message.reply_text(f"❌ دریافت/ارسال فایل ناموفق بود: {str(exc)[:700]}"); return True
    return False
