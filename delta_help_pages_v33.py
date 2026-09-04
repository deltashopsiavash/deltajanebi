#!/usr/bin/env python3
"""Dynamic storefront help-page controls for the external Delta bot."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import delta_bot_native as native

_ORIGINAL_CALLBACK = native.callback
_ORIGINAL_MESSAGE = native.message
_ORIGINAL_SETTINGS_MENU = native.settings_menu


def _parts(data):
    return str(data or "").split(":")


def _valid(data, command, min_parts=3):
    parts = _parts(data)
    return len(parts) >= min_parts and parts[0] == "d" and parts[1] == command and parts[2].isdigit()


def settings_menu(sid):
    rows = [
        [InlineKeyboardButton("✏️ نام سایت", callback_data=f"d:set:{sid}:name"), InlineKeyboardButton("🖼 لوگوی سایت", callback_data=f"d:set:{sid}:logo")],
        [InlineKeyboardButton("☎️ تلفن", callback_data=f"d:set:{sid}:phone"), InlineKeyboardButton("✨ متن بالای سایت", callback_data=f"d:set:{sid}:topbar")],
        [InlineKeyboardButton("📝 توضیحات و فوتر", callback_data=f"d:set:{sid}:footer"), InlineKeyboardButton("🌐 شبکه‌های اجتماعی", callback_data=f"d:socials:{sid}")],
        [InlineKeyboardButton("📣 بنرهای تبلیغاتی", callback_data=f"d:banners:{sid}"), InlineKeyboardButton("🛡 نمادها", callback_data=f"d:badges:{sid}")],
        [InlineKeyboardButton("📚 راهنما و صفحات", callback_data=f"d:helppages:{sid}"), InlineKeyboardButton("💳 پرداخت، تخفیف و ارسال", callback_data=f"d:commerce:{sid}")],
        [InlineKeyboardButton("📨 ایمیل همگانی", callback_data=f"d:broadcast:{sid}"), InlineKeyboardButton("💾 بکاپ", callback_data=f"d:backup:{sid}")],
        [InlineKeyboardButton("⬅️ پنل Delta", callback_data=f"d:home:{sid}")],
    ]
    return InlineKeyboardMarkup(rows)


def _help_back(sid):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ راهنما و صفحات", callback_data=f"d:helppages:{sid}")]])


async def _show_commerce(q, site, sid):
    x = (await native.core.api(site, "delta_commerce_get"))["data"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🏦 کارت‌به‌کارت: {'روشن' if x['card_payment_enabled'] else 'خاموش'}", callback_data=f"d:comtoggle:{sid}:card"), InlineKeyboardButton(f"💳 زرین‌پال: {'روشن' if x['zarinpal_payment_enabled'] else 'خاموش'}", callback_data=f"d:comtoggle:{sid}:zarinpal")],
        [InlineKeyboardButton("💳 شماره کارت", callback_data=f"d:comedit:{sid}:card_number"), InlineKeyboardButton("👤 صاحب حساب", callback_data=f"d:comedit:{sid}:card_owner")],
        [InlineKeyboardButton("🔑 مرچنت زرین‌پال", callback_data=f"d:comedit:{sid}:zarinpal_merchant_id")],
        [InlineKeyboardButton("🚚 هزینه ارسال", callback_data=f"d:comedit:{sid}:shipping_cost"), InlineKeyboardButton("📦 بسته‌بندی", callback_data=f"d:comedit:{sid}:packaging_cost")],
        [InlineKeyboardButton("🎁 حد ارسال رایگان", callback_data=f"d:comedit:{sid}:free_shipping_threshold"), InlineKeyboardButton(f"📦 مخفی ناموجود: {'روشن' if x['hide_out_of_stock'] else 'خاموش'}", callback_data=f"d:comtoggle:{sid}:hide")],
        [InlineKeyboardButton("🎟 کدهای تخفیف", callback_data=f"d:discounts:{sid}"), InlineKeyboardButton("📚 راهنما و قوانین", callback_data=f"d:helppages:{sid}")],
        [InlineKeyboardButton("⬅️ تنظیمات", callback_data=f"d:settings:{sid}")],
    ])
    await native._edit(
        q,
        f"💳 پرداخت، تخفیف و ارسال\n\nکارت: {x.get('card_number') or '-'}\nصاحب حساب: {x.get('card_owner') or '-'}\nارسال: {native.money(x['shipping_cost'])}\nبسته‌بندی: {native.money(x['packaging_cost'])}\nارسال رایگان از: {native.money(x['free_shipping_threshold'])}",
        kb,
    )
    return True


async def _show_help_pages(q, site, sid):
    rows = (await native.core.api(site, "delta_help_pages", timeout=35))["data"]
    keys = []
    for item in rows:
        badge = "✅" if item.get("is_visible") else "⛔"
        content = "📝" if item.get("has_content") else "▫️"
        keys.append([InlineKeyboardButton(f"{badge} {content} {item['title'][:34]}", callback_data=f"d:helppage:{sid}:{item['id']}")])
    keys += [
        [InlineKeyboardButton("➕ افزودن دکمه جدید", callback_data=f"d:helpadd:{sid}")],
        [InlineKeyboardButton("⬅️ تنظیمات اختصاصی", callback_data=f"d:settings:{sid}")],
    ]
    await native._edit(
        q,
        "📚 راهنما و صفحات پایین سایت\n\n"
        "از اینجا تعیین می‌کنی کدام دکمه در بخش «راهنما» فوتر نمایش داده شود، متن هر صفحه چه باشد و دکمه جدید هم می‌توانی بسازی.\n"
        "سه صفحه اصلی قوانین، بازگشت کالا و راهنمای خرید حذف نمی‌شوند ولی می‌توانی مخفی‌شان کنی.",
        InlineKeyboardMarkup(keys),
    )
    return True


async def _show_help_page(q, site, sid, page_id):
    item = (await native.core.api(site, "delta_help_page_detail", {"id": int(page_id)}, timeout=35))["data"]
    preview = (item.get("content") or "").strip().replace("\r", "")
    if len(preview) > 900:
        preview = preview[:900] + "…"
    keys = [
        [InlineKeyboardButton("📝 تنظیم متن", callback_data=f"d:helpedit:{sid}:{item['id']}:content"), InlineKeyboardButton("✏️ عنوان دکمه", callback_data=f"d:helpedit:{sid}:{item['id']}:title")],
        [InlineKeyboardButton(f"👁 {'مخفی کردن' if item['is_visible'] else 'نمایش در فوتر'}", callback_data=f"d:helptoggle:{sid}:{item['id']}"), InlineKeyboardButton("🔢 ترتیب نمایش", callback_data=f"d:helpedit:{sid}:{item['id']}:order")],
    ]
    if not item.get("is_builtin"):
        keys.append([InlineKeyboardButton("🗑 حذف دکمه", callback_data=f"d:helpdelask:{sid}:{item['id']}")])
    keys.append([InlineKeyboardButton("⬅️ راهنما و صفحات", callback_data=f"d:helppages:{sid}")])
    await native._edit(
        q,
        f"📄 {item['title']}\n\n"
        f"وضعیت فوتر: {'✅ نمایش داده می‌شود' if item['is_visible'] else '⛔ مخفی است'}\n"
        f"ترتیب: {item.get('sort_order', 0)}\n"
        f"نوع: {'صفحه اصلی' if item.get('is_builtin') else 'دکمه سفارشی'}\n\n"
        f"📝 متن فعلی:\n{preview or 'هنوز متنی ثبت نشده است.'}",
        InlineKeyboardMarkup(keys),
    )
    return True


async def callback(update, context):
    q = update.callback_query
    data = q.data or ""

    if not any(_valid(data, cmd, 3) for cmd in {"settings", "commerce", "helppages", "helppage", "helpadd", "helpedit", "helptoggle", "helpdelask", "helpdelete"}):
        return await _ORIGINAL_CALLBACK(update, context)

    parts = _parts(data)
    sid = int(parts[2])
    site = native._site(q.from_user.id, sid)
    if not site:
        await q.answer("عدم دسترسی", show_alert=True)
        return True

    try:
        await q.answer()
        cmd = parts[1]
        if cmd == "settings":
            s = (await native.core.api(site, "settings_get"))["data"]
            await native._edit(q, f"⚙️ تنظیمات اختصاصی Delta\n\nنام: {s.get('site_name')}\n☎️ تلفن: {s.get('contact_phone') or '-'}\n🚚 ارسال: {native.money(s.get('shipping_fee'))} تومان\n📦 بسته‌بندی و روش‌های پرداخت از بخش پرداخت مدیریت می‌شوند.", settings_menu(sid))
            return True
        if cmd == "commerce":
            return await _show_commerce(q, site, sid)
        if cmd == "helppages":
            return await _show_help_pages(q, site, sid)
        if cmd == "helppage" and len(parts) >= 4 and parts[3].isdigit():
            return await _show_help_page(q, site, sid, int(parts[3]))
        if cmd == "helpadd":
            native._flow(context, "d_help_new_title", sid)
            await native._edit(q, "➕ عنوان دکمه جدید را بفرست؛ مثال: سوالات متداول", _help_back(sid))
            return True
        if cmd == "helpedit" and len(parts) >= 5 and parts[3].isdigit():
            page_id = int(parts[3]); field = parts[4]
            prompt = {
                "content": "📝 متن کامل این صفحه را بفرست. برای خالی کردن متن، - بفرست:",
                "title": "✏️ عنوان جدید دکمه را بفرست:",
                "order": "🔢 شماره ترتیب نمایش را بفرست؛ عدد کمتر بالاتر نمایش داده می‌شود:",
            }.get(field)
            if not prompt:
                return await _ORIGINAL_CALLBACK(update, context)
            native._flow(context, f"d_help_edit_{field}", sid, help_page_id=page_id)
            await native._edit(q, prompt, _help_back(sid))
            return True
        if cmd == "helptoggle" and len(parts) >= 4 and parts[3].isdigit():
            page_id = int(parts[3])
            item = (await native.core.api(site, "delta_help_page_detail", {"id": page_id}))["data"]
            await native.core.api(site, "delta_help_page_update", {"id": page_id, "is_visible": not item["is_visible"]})
            return await _show_help_page(q, site, sid, page_id)
        if cmd == "helpdelask" and len(parts) >= 4 and parts[3].isdigit():
            page_id = int(parts[3])
            item = (await native.core.api(site, "delta_help_page_detail", {"id": page_id}))["data"]
            if item.get("is_builtin"):
                await q.answer("صفحه اصلی قابل حذف نیست؛ می‌توانی مخفی‌اش کنی.", show_alert=True)
                return True
            await native._edit(q, f"⚠️ دکمه «{item['title']}» و متنش کامل حذف شود؟", InlineKeyboardMarkup([[InlineKeyboardButton("✅ حذف", callback_data=f"d:helpdelete:{sid}:{page_id}"), InlineKeyboardButton("❌ انصراف", callback_data=f"d:helppage:{sid}:{page_id}")]]))
            return True
        if cmd == "helpdelete" and len(parts) >= 4 and parts[3].isdigit():
            await native.core.api(site, "delta_help_page_delete", {"id": int(parts[3])})
            return await _show_help_pages(q, site, sid)
    except Exception as exc:
        try:
            await q.answer("عملیات انجام نشد", show_alert=False)
        except Exception:
            pass
        await native._edit(q, f"⚠️ مدیریت راهنما انجام نشد ولی اتصال سایت حفظ شده است.\n{str(exc)[:800]}", _help_back(sid))
        return True

    return await _ORIGINAL_CALLBACK(update, context)


async def message(update, context):
    if context.user_data.get("platform") != "deltajanebi":
        return await _ORIGINAL_MESSAGE(update, context)
    flow = context.user_data.get("flow") or ""
    if not flow.startswith("d_help_"):
        return await _ORIGINAL_MESSAGE(update, context)

    sid = context.user_data.get("site_id")
    site = native._site(update.effective_user.id, sid) if sid else None
    if not site:
        return await _ORIGINAL_MESSAGE(update, context)
    text = str(update.message.text or "").strip()

    try:
        if flow == "d_help_new_title":
            if not text or text == "-":
                await update.message.reply_text("عنوان نمی‌تواند خالی باشد.")
                return True
            context.user_data.update(flow="d_help_new_content", help_new_title=text[:140])
            await update.message.reply_text("📝 متن این صفحه را بفرست. اگر فعلاً متن نمی‌خواهی، - بفرست:")
            return True
        if flow == "d_help_new_content":
            item = (await native.core.api(site, "delta_help_page_create", {"title": context.user_data["help_new_title"], "content": "" if text == "-" else text, "is_visible": True}, timeout=45))["data"]
            context.user_data.clear()
            await update.message.reply_text(f"✅ دکمه «{item['title']}» ساخته شد و در فوتر نمایش داده می‌شود.", reply_markup=_help_back(sid))
            return True
        if flow == "d_help_edit_content":
            page_id = int(context.user_data["help_page_id"])
            await native.core.api(site, "delta_help_page_update", {"id": page_id, "content": "" if text == "-" else text}, timeout=45)
            context.user_data.clear()
            await update.message.reply_text("✅ متن صفحه ذخیره شد.", reply_markup=_help_back(sid))
            return True
        if flow == "d_help_edit_title":
            if not text or text == "-":
                await update.message.reply_text("عنوان نمی‌تواند خالی باشد.")
                return True
            page_id = int(context.user_data["help_page_id"])
            await native.core.api(site, "delta_help_page_update", {"id": page_id, "title": text[:140]}, timeout=45)
            context.user_data.clear()
            await update.message.reply_text("✅ عنوان دکمه ذخیره شد.", reply_markup=_help_back(sid))
            return True
        if flow == "d_help_edit_order":
            raw = text.replace(",", "").strip()
            if not raw.isdigit():
                await update.message.reply_text("فقط یک عدد صفر یا بزرگ‌تر بفرست.")
                return True
            page_id = int(context.user_data["help_page_id"])
            await native.core.api(site, "delta_help_page_update", {"id": page_id, "sort_order": int(raw)}, timeout=45)
            context.user_data.clear()
            await update.message.reply_text("✅ ترتیب نمایش ذخیره شد.", reply_markup=_help_back(sid))
            return True
    except Exception as exc:
        await update.message.reply_text(f"⚠️ ذخیره انجام نشد ولی اتصال سایت حفظ شده است.\n{str(exc)[:800]}", reply_markup=_help_back(sid))
        return True

    return await _ORIGINAL_MESSAGE(update, context)


def install():
    if getattr(native, "_delta_help_pages_v33_installed", False):
        return
    native.settings_menu = settings_menu
    native.callback = callback
    native.message = message
    native._delta_help_pages_v33_installed = True
