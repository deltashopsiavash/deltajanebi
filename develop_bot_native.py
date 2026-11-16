"""Independent DeltaDevelop bot panel inside the existing multi-site router.

A DeltaDevelop site owns its own Django admin, API key, leads and services.
Never route this platform to SanaShop or DeltaJanebi handlers.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def panel(site, uid):
    sid = int(site["id"])
    rows = [
        [InlineKeyboardButton("🧭 داشبورد DeltaDevelop", callback_data=f"dev:home:{sid}")],
        [
            InlineKeyboardButton("🧩 خدمات نرم‌افزاری", callback_data=f"dev:services:{sid}"),
            InlineKeyboardButton("📨 درخواست‌های پروژه", callback_data=f"dev:requests:{sid}"),
        ],
        [InlineKeyboardButton("⚙️ اطلاعات مجموعه", callback_data=f"dev:profile:{sid}")],
        [InlineKeyboardButton("🌐 پنل سایت", url=site["base_url"].rstrip("/") + "/admin/")],
        [InlineKeyboardButton("⬅️ سایت‌های متصل", callback_data="owner_sites")],
    ]
    return InlineKeyboardMarkup(rows)


def back(sid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ پنل DeltaDevelop", callback_data=f"dev:home:{sid}")
    ]])


def _safe(text, limit=3000):
    return str(text or "").replace("<", "‹").replace(">", "›")[:limit]


async def callback(update, context, core):
    """Called only after the router verifies access and platform."""
    q = update.callback_query
    uid = q.from_user.id
    parts = (q.data or "").split(":")
    if len(parts) < 3 or parts[0] != "dev" or not parts[2].isdigit():
        return await q.answer("دکمه نامعتبر است.", show_alert=True)
    sid = int(parts[2])
    if not core.can_access(uid, sid):
        return await q.answer("دسترسی ندارید.", show_alert=True)
    site = core.get_site(sid)
    if not site:
        return await q.answer("سایت پیدا نشد.", show_alert=True)
    action = parts[1]
    await q.answer()

    try:
        if action == "home" and len(parts) == 3:
            data = (await core.api(site, "dashboard"))["data"]
            text = (
                "💻 پنل اختصاصی DeltaDevelop\n\n"
                f"🧩 خدمات فعال: {data['service_count']}\n"
                f"📨 درخواست‌های جدید: {data['new_requests']}\n"
                f"📋 تمام درخواست‌ها: {data['total_requests']}\n\n"
                "این پنل فقط به سایت DeltaDevelop متصل است."
            )
            return await q.edit_message_text(text, reply_markup=panel(site, uid))

        if action == "profile" and len(parts) == 3:
            data = (await core.api(site, "settings_get"))["data"]
            text = (
                "⚙️ اطلاعات مجموعه DeltaDevelop\n\n"
                f"نام: {_safe(data.get('name'))}\n"
                f"شعار: {_safe(data.get('slogan'))}\n"
                f"ایمیل: {_safe(data.get('email') or 'ثبت نشده')}\n"
                f"تلفن: {_safe(data.get('phone') or 'ثبت نشده')}\n"
                f"آدرس: {_safe(data.get('address') or 'ثبت نشده')}\n\n"
                "برای ویرایش اطلاعات، پنل Django اختصاصی را باز کنید."
            )
            return await q.edit_message_text(text, reply_markup=back(sid))

        if action == "services" and len(parts) == 3:
            data = (await core.api(site, "services_list"))["data"]
            rows = []
            for item in data[:45]:
                label = ("✅ " if item["is_active"] else "⛔ ") + _safe(item["title"], 55)
                rows.append([InlineKeyboardButton(
                    label, callback_data=f"dev:service:{sid}:{int(item['id'])}"
                )])
            rows.append([InlineKeyboardButton("⬅️ پنل اصلی", callback_data=f"dev:home:{sid}")])
            return await q.edit_message_text(
                "🧩 خدمات نرم‌افزاری\nبرای مدیریت نمایش هر خدمت، روی آن بزنید."
                if data else "هنوز خدمتی ثبت نشده. از Django Admin اضافه کنید.",
                reply_markup=InlineKeyboardMarkup(rows),
            )

        if action == "service" and len(parts) == 4 and parts[3].isdigit():
            key = int(parts[3])
            return await q.edit_message_text(
                "⚙️ وضعیت نمایش این خدمت در سایت:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 فعال / غیرفعال", callback_data=f"dev:toggle:{sid}:{key}")],
                    [InlineKeyboardButton("⬅️ خدمات", callback_data=f"dev:services:{sid}")],
                ]),
            )

        if action == "toggle" and len(parts) == 4 and parts[3].isdigit():
            info = (await core.api(site, "service_toggle", {"id": int(parts[3])}))["data"]
            return await q.edit_message_text(
                "✅ وضعیت نمایش خدمت تغییر کرد: " + ("فعال" if info["is_active"] else "غیرفعال"),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ خدمات", callback_data=f"dev:services:{sid}")
                ]]),
            )

        if action == "requests" and len(parts) == 3:
            data = (await core.api(site, "requests_list"))["data"]
            rows = []
            for item in data[:30]:
                name = _safe(item["name"], 33)
                rows.append([InlineKeyboardButton(
                    f"📨 #{item['id']} | {name} | {item['status']}",
                    callback_data=f"dev:request:{sid}:{int(item['id'])}",
                )])
            rows.append([InlineKeyboardButton("⬅️ پنل اصلی", callback_data=f"dev:home:{sid}")])
            return await q.edit_message_text(
                "📨 درخواست‌های ثبت‌شده (جدیدترین‌ها)" if data else "هنوز درخواستی ثبت نشده است.",
                reply_markup=InlineKeyboardMarkup(rows),
            )

        if action == "request" and len(parts) == 4 and parts[3].isdigit():
            item = (await core.api(site, "request_detail", {"id": int(parts[3])}))["data"]
            text = (
                f"📨 درخواست #{item['id']}\n\n"
                f"نام: {_safe(item['name'], 140)}\n"
                f"خدمت: {_safe(item['service'], 130)}\n"
                f"ایمیل: {_safe(item['email'], 220)}\n"
                f"تلفن: {_safe(item['phone'], 50)}\n"
                f"وضعیت: {_safe(item['status'], 30)}\n\n"
                f"توضیحات:\n{_safe(item['description'], 1600)}"
            )
            request_id = int(item["id"])
            return await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏳ در حال بررسی", callback_data=f"dev:status:{sid}:{request_id}:reviewing"),
                 InlineKeyboardButton("✅ تماس گرفته شد", callback_data=f"dev:status:{sid}:{request_id}:contacted")],
                [InlineKeyboardButton("📁 بستن درخواست", callback_data=f"dev:status:{sid}:{request_id}:closed")],
                [InlineKeyboardButton("⬅️ درخواست‌ها", callback_data=f"dev:requests:{sid}")],
            ]))

        if action == "status" and len(parts) == 5 and parts[3].isdigit():
            new_status = parts[4]
            if new_status not in {"new", "reviewing", "contacted", "closed"}:
                return await q.message.reply_text("وضعیت انتخاب‌شده معتبر نیست.")
            await core.api(site, "request_status", {"id": int(parts[3]), "status": new_status})
            return await q.edit_message_text(
                "✅ وضعیت درخواست ذخیره شد.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ درخواست‌ها", callback_data=f"dev:requests:{sid}")
                ]]),
            )

        return await q.message.reply_text("این دکمه پشتیبانی نمی‌شود.", reply_markup=back(sid))
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "DeltaDevelop bot API operation failed site_id=%s action=%s", sid, action
        )
        return await q.message.reply_text(
            "⚠️ ارتباط با API DeltaDevelop انجام نشد. اتصال سایت و اطلاعات آن حفظ شده‌اند.",
            reply_markup=back(sid),
        )
