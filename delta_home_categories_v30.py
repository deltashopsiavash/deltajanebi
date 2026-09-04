#!/usr/bin/env python3
"""Homepage product-category showcase controls for the external Delta bot."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import delta_bot_native as native

_ORIGINAL_CALLBACK = native.callback


def _parts(data):
    return str(data or "").split(":")


def _valid(data, command, min_parts=3):
    parts = _parts(data)
    return len(parts) >= min_parts and parts[0] == "d" and parts[1] == command and parts[2].isdigit()


async def _source_list(q, site, sid):
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
        [InlineKeyboardButton("🏠 دسته‌بندی محصولات صفحه اصلی از منبع", callback_data=f"d:homecats:{sid}")],
        [InlineKeyboardButton("🔄 همگام‌سازی همه سایت‌ها", callback_data=f"d:syncall:{sid}")],
        [InlineKeyboardButton("➕ ثبت سایت منبع", callback_data=f"d:sourceadd:{sid}")],
        [InlineKeyboardButton("⬅️ تنظیمات مدیریتی", callback_data=f"d:admin:{sid}")],
    ]
    await native._edit(
        q,
        "🌐 سایت‌های منبع Delta\n\n"
        "برای هر سایت می‌توانی جداگانه Sync بزنی یا فقط بلوک «دسته‌بندی محصولات» صفحه اصلی را از یکی از منابع کپی کنی.",
        InlineKeyboardMarkup(keys),
    )
    return True


async def _choose_home_source(q, site, sid):
    rows = (await native.core.api(site, "source_sites", timeout=35)).get("data") or []
    status = (await native.core.api(site, "delta_home_categories_status", timeout=35)).get("data") or {}
    keys = []
    for item in rows:
        if not item.get("is_active"):
            continue
        xid = int(item["id"])
        selected = "✅ " if int(status.get("source_site_id") or 0) == xid and status.get("enabled") else ""
        keys.append([
            InlineKeyboardButton(
                f"{selected}🏠 {str(item.get('name') or item.get('hostname') or '-')[:32]}",
                callback_data=f"d:homecatclone:{sid}:{xid}",
            )
        ])
    if status.get("enabled"):
        keys.append([InlineKeyboardButton("↩️ بازگشت به دسته‌بندی پیش‌فرض Delta", callback_data=f"d:homecatreset:{sid}")])
    keys.append([InlineKeyboardButton("⬅️ سایت‌های منبع", callback_data=f"d:sources:{sid}")])

    if status.get("enabled"):
        current = (
            f"\n\nفعلی: «{status.get('source_site_name') or '-'}»"
            f"\n📦 {int(status.get('count') or 0)} دسته در صفحه اصلی"
        )
    else:
        current = "\n\nفعلاً صفحه اصلی از دسته‌بندی پیش‌فرض خود Delta استفاده می‌کند."

    await native._edit(
        q,
        "🏠 بلوک «دسته‌بندی محصولات» صفحه اصلی را طبق کدام سایت منبع بچینم؟\n\n"
        "فقط همان بخشی که روی سایت منبع عنوان «دسته‌بندی محصولات» دارد خوانده می‌شود؛ "
        "بخش‌های دیگر صفحه، پیشنهادها، برندها، بنرها و منوی همبرگری اصلاً کپی یا تغییر داده نمی‌شوند."
        + current,
        InlineKeyboardMarkup(keys),
    )
    return True


async def _clone(q, site, sid, source_id):
    await q.answer("در حال خواندن دسته‌بندی محصولات")
    await native._edit(
        q,
        "⏳ در حال خواندن فقط بلوک «دسته‌بندی محصولات» سایت منبع...\n"
        "هیچ بخش دیگری از صفحه اصلی و منوی همبرگری تغییر نمی‌کند.",
    )
    try:
        result = (await native.core.api(
            site,
            "delta_home_categories_clone",
            {"source_site_id": int(source_id)},
            timeout=55,
        ))["data"]
    except Exception as exc:
        await native._edit(
            q,
            f"❌ کپی بلوک دسته‌بندی محصولات انجام نشد:\n{str(exc)[:800]}",
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ انتخاب سایت منبع", callback_data=f"d:homecats:{sid}")]]),
        )
        return True

    names = [str(x) for x in (result.get("items") or [])]
    preview = "\n".join(f"• {name[:70]}" for name in names[:14])
    if len(names) > 14:
        preview += f"\n• ... و {len(names) - 14} دسته دیگر"
    await native._edit(
        q,
        f"✅ فقط «دسته‌بندی محصولات» از «{result.get('source_site_name') or '-'}» کپی شد.\n\n"
        f"📦 تعداد دسته‌ها: {int(result.get('count') or 0)}\n"
        f"🔗 متصل به دسته‌های فروشگاه: {int(result.get('matched_categories') or 0)}\n"
        f"🔎 بدون تطبیق مستقیم: {int(result.get('unmatched_categories') or 0)}\n"
        f"🧭 منوی همبرگری: بدون تغییر\n"
        f"🚫 سایر بخش‌های صفحه اصلی منبع: نادیده گرفته شد\n\n{preview}",
        InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 دوباره از همین منبع بخوان", callback_data=f"d:homecatclone:{sid}:{int(source_id)}")],
            [InlineKeyboardButton("🌐 انتخاب منبع دیگر", callback_data=f"d:homecats:{sid}")],
            [InlineKeyboardButton("⬅️ سایت‌های منبع", callback_data=f"d:sources:{sid}")],
        ]),
    )
    return True


async def callback(update, context):
    q = update.callback_query
    data = q.data or ""

    if _valid(data, "sources"):
        sid = int(_parts(data)[2])
        site = native._site(q.from_user.id, sid)
        if not site:
            await q.answer("عدم دسترسی", show_alert=True)
            return True
        await q.answer()
        return await _source_list(q, site, sid)

    if _valid(data, "homecats"):
        sid = int(_parts(data)[2])
        site = native._site(q.from_user.id, sid)
        if not site:
            await q.answer("عدم دسترسی", show_alert=True)
            return True
        await q.answer()
        return await _choose_home_source(q, site, sid)

    if _valid(data, "homecatclone", 4):
        parts = _parts(data)
        if not parts[3].isdigit():
            return await _ORIGINAL_CALLBACK(update, context)
        sid = int(parts[2])
        site = native._site(q.from_user.id, sid)
        if not site:
            await q.answer("عدم دسترسی", show_alert=True)
            return True
        return await _clone(q, site, sid, int(parts[3]))

    if _valid(data, "homecatreset"):
        sid = int(_parts(data)[2])
        site = native._site(q.from_user.id, sid)
        if not site:
            await q.answer("عدم دسترسی", show_alert=True)
            return True
        await q.answer("در حال بازگردانی")
        try:
            await native.core.api(site, "delta_home_categories_reset", timeout=35)
        except Exception as exc:
            await native._edit(q, f"❌ بازگردانی انجام نشد:\n{str(exc)[:700]}")
            return True
        await native._edit(
            q,
            "✅ صفحه اصلی به دسته‌بندی پیش‌فرض Delta برگشت.\n🧭 منوی همبرگری هیچ تغییری نکرد.",
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ انتخاب سایت منبع", callback_data=f"d:homecats:{sid}")]]),
        )
        return True

    return await _ORIGINAL_CALLBACK(update, context)


def install():
    if getattr(native, "_delta_home_categories_v30_installed", False):
        return
    native.callback = callback
    native._delta_home_categories_v30_installed = True
