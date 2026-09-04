#!/usr/bin/env python3
"""Delta source-site controls plus resilient background full-sync monitoring."""
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import delta_bot_native as native

_ORIGINAL_CALLBACK = native.callback
_TERMINAL = {"completed", "failed", "cancelled"}
_MONITORS = set()


def _is_source_command(data, names):
    parts = str(data or "").split(":")
    return len(parts) >= 3 and parts[0] == "d" and parts[1] in names and parts[2].isdigit()


def _source_markup(sid, x):
    xid = x["id"]
    return InlineKeyboardMarkup([
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
    ])


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
    lines = [
        "🔄 همگام‌سازی کامل کاتالوگ Delta",
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
        lines[0] = "✅ همگام‌سازی کامل کاتالوگ تمام شد"
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


async def _progress_edit(q, text, markup=None):
    """Edit only the original progress message; never spam fallback messages."""
    try:
        await q.edit_message_text(text[:4000], reply_markup=markup)
        return True
    except Exception as exc:
        # Telegram treats an identical edit as an error. That is a successful
        # no-op for our monitor; every other error is retried on the next poll.
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
                # Do not terminate the monitor because one HTTPS request failed.
                # The next poll reconnects to the same durable PostgreSQL job.
                await asyncio.sleep(min(20, 5 + failures * 2))
                continue

            text = _progress_text(job)
            if text != last_text:
                markup = native.admin_menu(sid) if job.get("status") in _TERMINAL else None
                if await _progress_edit(q, text, markup):
                    last_text = text

            if job.get("status") in _TERMINAL:
                return
            await asyncio.sleep(5)
    finally:
        _MONITORS.discard(key)


async def callback(update, context):
    q = update.callback_query
    data = q.data or ""

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
                "✅ آپلود همه فعال شد. با «همگام‌سازی همه»، تمام کاتالوگ قابل کشف سایت منبع همراه دسته‌بندی‌ها وارد می‌شود."
                if updated[field]
                else "⛔ آپلود همه خاموش شد؛ فقط محصولات قبلاً ثبت‌شده همگام می‌شوند."
            )
        else:
            prefix = "✅ وضعیت سایت منبع تغییر کرد."
        await _show_source(q, site, sid, source_id, prefix)
        return True

    if _is_source_command(data, {"syncall"}):
        parts = data.split(":")
        uid = q.from_user.id
        sid = int(parts[2])
        site = native._site(uid, sid)
        if not site:
            await q.answer("عدم دسترسی", show_alert=True)
            return True
        await q.answer("در حال اتصال به Sync")
        await native._edit(q, "⏳ اتصال به صف همگام‌سازی پایدار...")
        try:
            started = (await native.core.api(site, "delta_source_sync_start", timeout=25))["data"]
        except Exception as exc:
            await native._edit(q, f"❌ شروع همگام‌سازی ناموفق بود:\n{str(exc)[:700]}", native.admin_menu(sid))
            return True

        job_id = started["job_id"]
        key = (int(sid), str(job_id))
        prefix = "🔁 به Sync در حال اجرای قبلی دوباره وصل شد.\n\n" if started.get("reused") else "✅ Job پایدار ساخته شد.\n\n"
        await _progress_edit(q, prefix + _progress_text(started))

        if key not in _MONITORS:
            _MONITORS.add(key)
            context.application.create_task(_monitor_sync(q, site, sid, job_id))
        return True

    return await _ORIGINAL_CALLBACK(update, context)


def install():
    if getattr(native, "_delta_source_restore_v18_installed", False):
        return
    native.callback = callback
    native._delta_source_restore_v18_installed = True
