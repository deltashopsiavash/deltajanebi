#!/usr/bin/env python3
"""Delta source controls, resilient sync monitoring and v29 cleanup terms."""
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import delta_bot_native as native

_ORIGINAL_CALLBACK = native.callback
_ORIGINAL_MESSAGE = native.message
_TERMINAL = {"completed", "failed", "cancelled"}
_MONITORS = set()


def _is_source_command(data, names):
    parts = str(data or "").split(":")
    return len(parts) >= 3 and parts[0] == "d" and parts[1] in names and parts[2].isdigit()


def _source_markup(sid, x):
    xid = x["id"]
    rows = [
        [InlineKeyboardButton("🔄 همگام‌سازی فقط همین سایت", callback_data=f"d:syncsource:{sid}:{xid}")],
        [
            InlineKeyboardButton(
                f"📥 آپلود همه: {'روشن' if x['bulk_import_enabled'] else 'خاموش'}",
                callback_data=f"d:sourcebulk:{sid}:{xid}",
            ),
            InlineKeyboardButton("💵 قیمت", callback_data=f"d:sourceprice:{sid}:{xid}"),
        ],
        [
            InlineKeyboardButton("✏️ نام", callback_data=f"d:sourcename:{sid}:{xid}"),
            InlineKeyboardButton("🧹 عبارات پاکسازی", callback_data=f"d:sourceterms:{sid}:{xid}"),
        ],
        [
            InlineKeyboardButton("⏯ فعال/غیرفعال", callback_data=f"d:sourcetoggle:{sid}:{xid}"),
            InlineKeyboardButton("🗑 حذف سایت", callback_data=f"d:sourcedelask:{sid}:{xid}"),
        ],
        [InlineKeyboardButton("⬅️ سایت‌های منبع", callback_data=f"d:sources:{sid}")],
    ]
    return InlineKeyboardMarkup(rows)


def _source_text(x):
    return (
        f"🌐 {x['name']}\n"
        f"دامنه: {x['hostname']}\n"
        f"وضعیت: {'✅ فعال' if x['is_active'] else '⛔ غیرفعال'}\n"
        f"📥 آپلود همه: {'✅ روشن' if x['bulk_import_enabled'] else '⛔ خاموش'}\n"
        f"💵 قیمت پیش‌فرض: {x['markup_label']}\n"
        f"🧹 پاکسازی: {x['brand_terms'] or '-'}\n"
        f"🔎 آخرین تعداد کشف‌شده: {x.get('last_discovered_count', 0)}\n"
        f"📦 محصولات مرتبط: {x['product_count']}"
    )


async def _show_sources(q, site, sid):
    rows = (await native.core.api(site, "source_sites", timeout=35)).get("data") or []
    keys = []
    for item in rows:
        xid = int(item["id"])
        status = "✅" if item.get("is_active") else "⛔"
        keys.append([
            InlineKeyboardButton(
                f"{status} {str(item.get('name') or '-')[:28]} | {int(item.get('product_count') or 0)}",
                callback_data=f"d:source:{sid}:{xid}",
            ),
            InlineKeyboardButton(
                "🔄 Sync" if item.get("is_active") else "⛔ خاموش",
                callback_data=(f"d:syncsource:{sid}:{xid}" if item.get("is_active") else f"d:source:{sid}:{xid}"),
            ),
        ])
    keys += [
        [InlineKeyboardButton("🔄 همگام‌سازی همه سایت‌ها", callback_data=f"d:syncall:{sid}")],
        [InlineKeyboardButton("➕ ثبت سایت منبع", callback_data=f"d:sourceadd:{sid}")],
        [InlineKeyboardButton("⬅️ تنظیمات مدیریتی", callback_data=f"d:admin:{sid}")],
    ]
    await native._edit(
        q,
        "🌐 سایت‌های منبع Delta\n\nبرای هر سایت می‌توانی جداگانه Sync بزنی، یا همه سایت‌ها را یکجا همگام‌سازی کنی.",
        InlineKeyboardMarkup(keys),
    )


async def _show_source(q, site, sid, source_id, prefix=""):
    x = (await native.core.api(site, "source_site_detail", {"id": int(source_id)}, timeout=35))["data"]
    text = _source_text(x)
    if prefix:
        text = prefix.rstrip() + "\n\n" + text
    await native._edit(q, text, _source_markup(sid, x))
    return x


def _progress_text(job):
    total = int(job.get("total") or 0)
    checked = int(job.get("checked") or 0)
    percent = int(checked * 100 / total) if total else (100 if job.get("status") == "completed" else 0)
    width = 16
    filled = min(width, int(width * percent / 100))
    bar = "█" * filled + "░" * (width - filled)
    target_name = str(job.get("target_source_site_name") or "").strip()
    single = str(job.get("sync_scope") or "") == "single_source" or bool(job.get("target_source_site_id"))
    title = f"🔄 همگام‌سازی سایت «{target_name or 'انتخاب‌شده'}»" if single else "🔄 همگام‌سازی همه سایت‌های منبع"
    lines = [
        title,
        "",
        f"{bar} {percent}%",
        f"📦 بررسی‌شده: {checked:,} / {total:,}",
        f"➕ جدید: {int(job.get('created') or 0):,}",
        f"♻️ تغییرکرده: {int(job.get('changed') or 0):,}",
        f"⏭ ردشده: {int(job.get('skipped') or 0):,}",
        f"⚠️ خطا: {int(job.get('errors') or 0):,}",
    ]
    if job.get("current_site"):
        lines.append(f"🌐 در حال پردازش: {job['current_site']}")
    if job.get("status") == "queued":
        lines.append("⏳ در صف supervisor...")
    elif job.get("status") == "completed":
        lines[0] = (f"✅ همگام‌سازی «{target_name}» تمام شد" if single and target_name else "✅ همگام‌سازی کامل کاتالوگ تمام شد")
    elif job.get("status") in {"failed", "cancelled"}:
        lines[0] = "❌ همگام‌سازی کامل نشد"
        if job.get("message"):
            lines.append(str(job["message"])[:700])
        if job.get("error"):
            lines.append(f"کد خطا: {str(job['error'])[:120]}")
    warnings = job.get("warnings") or []
    if job.get("status") in _TERMINAL and warnings:
        lines += ["", "نمونه هشدارها:"] + [f"• {str(x)[:180]}" for x in warnings[:7]]
    return "\n".join(lines)


def _terminal_markup(sid, job):
    target_id = int(job.get("target_source_site_id") or 0)
    if target_id:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 بازگشت به همین سایت منبع", callback_data=f"d:source:{sid}:{target_id}")],
            [InlineKeyboardButton("🧭 تنظیمات مدیریتی", callback_data=f"d:admin:{sid}")],
        ])
    return native.admin_menu(sid)


async def _progress_edit(q, text, markup=None):
    """Edit only the original progress message; never spam fallback messages."""
    try:
        await q.edit_message_text(text[:4000], reply_markup=markup)
        return True
    except Exception as exc:
        if "message is not modified" in str(exc).casefold():
            return True
        return False


async def _monitor_sync(q, site, sid, job_id):
    """Poll independently from the callback and survive transient API/Telegram errors."""
    key = (int(sid), str(job_id))
    last_text = ""
    failures = 0
    try:
        while True:
            try:
                response = await native.core.api(
                    site,
                    "delta_source_sync_status",
                    {"job_id": job_id},
                    timeout=25,
                )
                job = response["data"]
                failures = 0
            except Exception:
                failures += 1
                await asyncio.sleep(min(20, 5 + failures * 2))
                continue

            text = _progress_text(job)
            if text != last_text:
                markup = _terminal_markup(sid, job) if job.get("status") in _TERMINAL else None
                if await _progress_edit(q, text, markup):
                    last_text = text

            if job.get("status") in _TERMINAL:
                return
            await asyncio.sleep(5)
    finally:
        _MONITORS.discard(key)


async def _start_sync(q, context, site, sid, source_id=0):
    await q.answer("در حال اتصال به Sync")
    label = "همین سایت منبع" if source_id else "همه سایت‌های منبع"
    await native._edit(q, f"⏳ اتصال {label} به صف همگام‌سازی پایدار...")
    payload = {"source_site_id": int(source_id)} if source_id else {}
    try:
        started = (await native.core.api(site, "delta_source_sync_start", payload, timeout=25))["data"]
    except Exception as exc:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ سایت منبع", callback_data=f"d:source:{sid}:{source_id}")]]) if source_id else native.admin_menu(sid)
        await native._edit(q, f"❌ شروع همگام‌سازی ناموفق بود:\n{str(exc)[:700]}", markup)
        return True

    job_id = started["job_id"]
    key = (int(sid), str(job_id))
    if started.get("reused"):
        prefix = "🔁 یک Sync از قبل در حال اجرا بود؛ به همان Job وصل شدی.\n\n"
    elif source_id:
        prefix = f"✅ Sync اختصاصی سایت «{started.get('target_source_site_name') or label}» ساخته شد.\n\n"
    else:
        prefix = "✅ Sync همه سایت‌های منبع ساخته شد.\n\n"
    await _progress_edit(q, prefix + _progress_text(started))

    if key not in _MONITORS:
        _MONITORS.add(key)
        context.application.create_task(_monitor_sync(q, site, sid, job_id))
    return True


async def callback(update, context):
    q = update.callback_query
    data = q.data or ""

    if _is_source_command(data, {"sources"}):
        parts = data.split(":")
        sid = int(parts[2])
        site = native._site(q.from_user.id, sid)
        if not site:
            await q.answer("عدم دسترسی", show_alert=True)
            return True
        await q.answer()
        await _show_sources(q, site, sid)
        return True

    if _is_source_command(data, {"sourcebulk", "sourcetoggle"}):
        parts = data.split(":")
        uid = q.from_user.id
        sid = int(parts[2])
        source_id = int(parts[3])
        site = native._site(uid, sid)
        if not site:
            await q.answer("عدم دسترسی", show_alert=True)
            return True
        await q.answer()
        current = (await native.core.api(site, "source_site_detail", {"id": source_id}, timeout=35))["data"]
        field = "bulk_import_enabled" if parts[1] == "sourcebulk" else "is_active"
        updated = (await native.core.api(
            site,
            "source_site_update",
            {"id": source_id, field: not bool(current[field])},
            timeout=35,
        ))["data"]
        if field == "bulk_import_enabled":
            prefix = (
                "✅ آپلود همه فعال شد. Sync این سایت یا Sync همه، کل کاتالوگ قابل کشف آن را وارد می‌کند."
                if updated[field]
                else "⛔ آپلود همه خاموش شد؛ فقط محصولات قبلاً ثبت‌شده این سایت همگام می‌شوند."
            )
        else:
            prefix = "✅ وضعیت سایت منبع تغییر کرد."
        await _show_source(q, site, sid, source_id, prefix)
        return True

    if _is_source_command(data, {"syncsource"}):
        parts = data.split(":")
        if len(parts) < 4 or not parts[3].isdigit():
            return await _ORIGINAL_CALLBACK(update, context)
        sid = int(parts[2])
        source_id = int(parts[3])
        site = native._site(q.from_user.id, sid)
        if not site:
            await q.answer("عدم دسترسی", show_alert=True)
            return True
        return await _start_sync(q, context, site, sid, source_id=source_id)

    if _is_source_command(data, {"syncall"}):
        parts = data.split(":")
        sid = int(parts[2])
        site = native._site(q.from_user.id, sid)
        if not site:
            await q.answer("عدم دسترسی", show_alert=True)
            return True
        return await _start_sync(q, context, site, sid, source_id=0)

    return await _ORIGINAL_CALLBACK(update, context)


async def message(update, context):
    """Save cleanup terms through the v29 endpoint and apply them immediately."""
    if context.user_data.get("flow") != "d_sourceterms":
        return await _ORIGINAL_MESSAGE(update, context)

    sid = context.user_data.get("site_id")
    source_id = context.user_data.get("source_id")
    site = native._site(update.effective_user.id, sid) if sid else None
    if not site or not source_id:
        context.user_data.clear()
        await update.message.reply_text("❌ سایت منبع یا دسترسی پیدا نشد.")
        return True

    text = str(getattr(update.message, "text", "") or "").strip()
    if not text:
        await update.message.reply_text("عبارت‌های پاکسازی را با کاما، ویرگول فارسی یا هر خط جدا بفرست؛ - برای خالی کردن.")
        return True

    try:
        result = (await native.core.api(
            site,
            "delta_source_terms_update",
            {
                "id": int(source_id),
                "brand_terms": "" if text == "-" else text,
                "apply_existing": True,
            },
            timeout=90,
        ))["data"]
    except Exception as exc:
        await update.message.reply_text(
            f"❌ ذخیره/اجرای فیلتر ناموفق بود:\n{str(exc)[:700]}\n\nعبارت‌ها هنوز در این مرحله قابل ویرایش‌اند؛ دوباره بفرست."
        )
        return True

    context.user_data.clear()
    changed = int(result.get("products_changed") or 0)
    offers = int(result.get("offers_changed") or 0)
    terms = result.get("brand_terms") or "-"
    await update.message.reply_text(
        "✅ عبارات پاکسازی ذخیره و اجرا شد.\n"
        f"🧹 فیلتر فعال: {terms}\n"
        f"♻️ محصولات اصلاح‌شده: {changed:,}\n"
        f"📦 داده‌های منبع اصلاح‌شده: {offers:,}\n\n"
        "از این به بعد Syncهای بعدی هم همین فیلتر را اعمال می‌کنند؛ فاصله، نیم‌فاصله و ویرگول فارسی هم پشتیبانی می‌شود.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ منبع", callback_data=f"d:source:{sid}:{int(source_id)}")]
        ]),
    )
    return True


def install():
    if getattr(native, "_delta_source_restore_v18_installed", False):
        return
    native.callback = callback
    native.message = message
    native._delta_source_restore_v18_installed = True
