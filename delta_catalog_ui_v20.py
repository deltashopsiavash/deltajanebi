#!/usr/bin/env python3
"""Additive Delta catalog/category management UI improvements.

Only product/category list rendering and their navigation callbacks are
intercepted. Every other native Delta callback continues through the existing
restoration chain unchanged.
"""
import math

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import delta_bot_native as native

_ORIGINAL_CALLBACK = native.callback
PAGE_SIZE = 25
CATEGORY_PAGE_SIZE = 25


def _title(mode):
    return {
        "manual": "🧰 محصولات عادی",
        "synced": "🔗 محصولات خاص",
        "offers": "⭐ پیشنهادهای فعال",
        "all": "📊 تمامی محصولات",
    }.get(mode, "📦 محصولات")


async def show_products(q, site, sid, mode="all", query="", page=1):
    page = max(1, int(page or 1))
    response = await native.core.api(
        site,
        "delta_products",
        {"mode": mode, "query": query, "page": page, "per_page": PAGE_SIZE},
        timeout=45,
    )
    rows = response.get("data") or []
    pagination = response.get("pagination") or {}

    # Compatibility with a site that has not yet received API v17: old API
    # returns a plain list. Keep it usable and cap the Telegram page at 25.
    if not pagination:
        rows = rows[:PAGE_SIZE]
        total = len(rows)
        page = 1
        pages = 1
    else:
        page = max(1, int(pagination.get("page") or page))
        pages = max(1, int(pagination.get("pages") or 1))
        total = max(0, int(pagination.get("total") or 0))

    keys = []
    for product in rows[:PAGE_SIZE]:
        badge = "🔥" if product.get("amazing_active") else (
            "🏷" if product.get("discount_active") else ("✅" if product.get("is_active") else "⛔")
        )
        keys.append([
            InlineKeyboardButton(
                f"{badge} {product['name'][:36]} | {native.money(product.get('effective_price'))}",
                callback_data=f"d:product:{sid}:{product['id']}",
            )
        ])

    if not query and pages > 1:
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"d:products:{sid}:{mode}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page}/{pages}", callback_data=f"d:products:{sid}:{mode}:{page}"))
        if page < pages:
            nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"d:products:{sid}:{mode}:{page + 1}"))
        keys.append(nav)

    keys.append([InlineKeyboardButton("⬅️ مدیریت محصولات", callback_data=f"d:pmenu:{sid}")])
    suffix = f"\nجستجو: {query}" if query else ""
    return await native._edit(
        q,
        f"{_title(mode)}\n\n📦 کل: {total:,} محصول\n📄 صفحه {page} از {pages}{suffix}",
        InlineKeyboardMarkup(keys),
    )


async def show_categories(q, site, sid, page=1):
    response = await native.core.api(site, "categories", timeout=45)
    rows = response.get("data") or []
    total = len(rows)
    pages = max(1, math.ceil(total / CATEGORY_PAGE_SIZE))
    page = min(max(1, int(page or 1)), pages)
    start = (page - 1) * CATEGORY_PAGE_SIZE
    visible = rows[start:start + CATEGORY_PAGE_SIZE]

    keys = []
    for item in visible:
        depth = max(0, min(int(item.get("depth") or 0), 5))
        prefix = "↳ " * depth
        status = "✅" if item.get("is_active") else "⛔"
        name = str(item.get("name") or "-")
        keys.append([
            InlineKeyboardButton(
                f"{status} {prefix}{name[:38]} ({int(item.get('product_count') or 0)})",
                callback_data=f"d:cat:{sid}:{item['id']}",
            )
        ])

    if pages > 1:
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"d:cats:{sid}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page}/{pages}", callback_data=f"d:cats:{sid}:{page}"))
        if page < pages:
            nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"d:cats:{sid}:{page + 1}"))
        keys.append(nav)

    keys.extend([
        [InlineKeyboardButton("➕ دسته جدید", callback_data=f"d:catadd:{sid}")],
        [InlineKeyboardButton("⬅️ محصولات", callback_data=f"d:pmenu:{sid}")],
    ])
    return await native._edit(
        q,
        f"📂 دسته‌بندی‌های مرتب Delta\n\n📁 کل: {total:,} دسته\n📄 صفحه {page} از {pages}\n↳ یعنی زیردسته",
        InlineKeyboardMarkup(keys),
    )


async def callback(update, context):
    data = update.callback_query.data or ""
    parts = data.split(":")

    if len(parts) == 5 and parts[0] == "d" and parts[1] == "products" and parts[2].isdigit() and parts[4].isdigit():
        sid = int(parts[2])
        mode = parts[3] or "all"
        page = max(1, int(parts[4]))
        site = native._site(update.callback_query.from_user.id, sid)
        if not site:
            await update.callback_query.answer("عدم دسترسی", show_alert=True)
            return True
        await update.callback_query.answer()
        await show_products(update.callback_query, site, sid, mode=mode, page=page)
        return True

    if len(parts) in {3, 4} and parts[0] == "d" and parts[1] == "cats" and parts[2].isdigit():
        if len(parts) == 4 and not parts[3].isdigit():
            return await _ORIGINAL_CALLBACK(update, context)
        sid = int(parts[2])
        page = int(parts[3]) if len(parts) == 4 else 1
        site = native._site(update.callback_query.from_user.id, sid)
        if not site:
            await update.callback_query.answer("عدم دسترسی", show_alert=True)
            return True
        await update.callback_query.answer()
        await show_categories(update.callback_query, site, sid, page=page)
        return True

    return await _ORIGINAL_CALLBACK(update, context)


def install():
    if getattr(native, "_delta_catalog_ui_v20_installed", False):
        return
    native.show_products = show_products
    native.callback = callback
    native._delta_catalog_ui_v20_installed = True
