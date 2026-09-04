#!/usr/bin/env python3
"""Delta catalog/category management UI v27.

Product lists always use the paginated API (including the first page opened from
legacy menus), expose every page, and can be filtered by a category subtree.
"""
import math

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import delta_bot_native as native

_ORIGINAL_CALLBACK = native.callback
PAGE_SIZE = 45
CATEGORY_PAGE_SIZE = 25
PRODUCT_CATEGORY_PAGE_SIZE = 22
_VALID_MODES = {"manual", "synced", "offers", "all"}


def _title(mode):
    return {
        "manual": "🧰 محصولات عادی",
        "synced": "🔗 محصولات خاص",
        "offers": "⭐ پیشنهادهای فعال",
        "all": "📊 تمامی محصولات",
    }.get(mode, "📦 محصولات")


def _products_cb(sid, mode, page, category_id=0):
    base = f"d:products:{sid}:{mode}:{max(1, int(page or 1))}"
    return f"{base}:{int(category_id)}" if int(category_id or 0) else base


async def show_products(q, site, sid, mode="all", query="", page=1, category_id=0):
    mode = mode if mode in _VALID_MODES else "all"
    page = max(1, int(page or 1))
    category_id = max(0, int(category_id or 0))
    payload = {"mode": mode, "query": query, "page": page, "per_page": PAGE_SIZE}
    if category_id:
        payload["category_id"] = category_id
    response = await native.core.api(site, "delta_products", payload, timeout=45)
    rows = response.get("data") or []
    pagination = response.get("pagination") or {}
    category = response.get("category") or None

    # Compatibility fallback. Current Delta sites always return pagination, but
    # never silently show more than one unlabelled partial page.
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
    if not query:
        filter_row = [
            InlineKeyboardButton(
                "📂 انتخاب دسته‌بندی",
                callback_data=f"d:pcats:{sid}:{mode}:1",
            )
        ]
        if category_id:
            filter_row.append(
                InlineKeyboardButton("❌ همه دسته‌ها", callback_data=_products_cb(sid, mode, 1, 0))
            )
        keys.append(filter_row)

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
            nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=_products_cb(sid, mode, page - 1, category_id)))
        nav.append(InlineKeyboardButton(f"{page}/{pages}", callback_data=_products_cb(sid, mode, page, category_id)))
        if page < pages:
            nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=_products_cb(sid, mode, page + 1, category_id)))
        keys.append(nav)

    keys.append([InlineKeyboardButton("⬅️ مدیریت محصولات", callback_data=f"d:pmenu:{sid}")])
    suffix = f"\n🔎 جستجو: {query}" if query else ""
    if category:
        suffix += f"\n📂 دسته: {category.get('name') or '-'} (همراه زیردسته‌ها)"
    empty = "\n\nمحصولی در این فیلتر نیست." if not rows else ""
    return await native._edit(
        q,
        f"{_title(mode)}\n\n📦 کل: {total:,} محصول\n📄 صفحه {page} از {pages}{suffix}{empty}",
        InlineKeyboardMarkup(keys),
    )


async def show_product_categories(q, site, sid, mode="all", page=1):
    mode = mode if mode in _VALID_MODES else "all"
    response = await native.core.api(site, "categories", timeout=45)
    rows = [x for x in (response.get("data") or []) if int(x.get("product_count") or 0) > 0]
    total = len(rows)
    pages = max(1, math.ceil(total / PRODUCT_CATEGORY_PAGE_SIZE))
    page = min(max(1, int(page or 1)), pages)
    start = (page - 1) * PRODUCT_CATEGORY_PAGE_SIZE
    visible = rows[start:start + PRODUCT_CATEGORY_PAGE_SIZE]

    keys = [[InlineKeyboardButton("📊 همه دسته‌ها", callback_data=_products_cb(sid, mode, 1, 0))]]
    for item in visible:
        depth = max(0, min(int(item.get("depth") or 0), 4))
        prefix = "↳ " * depth
        name = str(item.get("name") or "-")
        count = int(item.get("product_count") or 0)
        keys.append([
            InlineKeyboardButton(
                f"📂 {prefix}{name[:34]} ({count:,})",
                callback_data=f"d:pcat:{sid}:{mode}:{item['id']}",
            )
        ])

    if pages > 1:
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"d:pcats:{sid}:{mode}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page}/{pages}", callback_data=f"d:pcats:{sid}:{mode}:{page}"))
        if page < pages:
            nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"d:pcats:{sid}:{mode}:{page + 1}"))
        keys.append(nav)
    keys.append([InlineKeyboardButton("⬅️ لیست محصولات", callback_data=_products_cb(sid, mode, 1, 0))])
    return await native._edit(
        q,
        f"📂 فیلتر محصولات بر اساس دسته‌بندی\n\n📁 دسته‌های دارای محصول: {total:,}\n📄 صفحه {page} از {pages}\nانتخاب هر دسته، محصولات خودش و همه زیردسته‌هایش را نشان می‌دهد.",
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
        f"📂 دسته‌بندی‌های مرتب Delta\n\n📁 کل: {total:,} دسته\n📄 صفحه {page} از {pages}\nعدد هر دسته شامل محصولات زیردسته‌ها هم هست.",
        InlineKeyboardMarkup(keys),
    )


def _site_for(update, sid):
    return native._site(update.callback_query.from_user.id, sid)


async def callback(update, context):
    data = update.callback_query.data or ""
    parts = data.split(":")

    # Intercept both the original 4-part first-page callback and every new page.
    if 4 <= len(parts) <= 6 and parts[0] == "d" and parts[1] == "products" and parts[2].isdigit():
        mode = parts[3] if parts[3] in _VALID_MODES else "all"
        if len(parts) >= 5 and not parts[4].isdigit():
            return await _ORIGINAL_CALLBACK(update, context)
        if len(parts) == 6 and not parts[5].isdigit():
            return await _ORIGINAL_CALLBACK(update, context)
        sid = int(parts[2])
        page = int(parts[4]) if len(parts) >= 5 else 1
        category_id = int(parts[5]) if len(parts) == 6 else 0
        site = _site_for(update, sid)
        if not site:
            await update.callback_query.answer("عدم دسترسی", show_alert=True)
            return True
        await update.callback_query.answer()
        await show_products(update.callback_query, site, sid, mode=mode, page=page, category_id=category_id)
        return True

    if len(parts) == 5 and parts[0] == "d" and parts[1] == "pcats" and parts[2].isdigit() and parts[4].isdigit():
        sid = int(parts[2])
        mode = parts[3] if parts[3] in _VALID_MODES else "all"
        site = _site_for(update, sid)
        if not site:
            await update.callback_query.answer("عدم دسترسی", show_alert=True)
            return True
        await update.callback_query.answer()
        await show_product_categories(update.callback_query, site, sid, mode=mode, page=int(parts[4]))
        return True

    if len(parts) == 5 and parts[0] == "d" and parts[1] == "pcat" and parts[2].isdigit() and parts[4].isdigit():
        sid = int(parts[2])
        mode = parts[3] if parts[3] in _VALID_MODES else "all"
        site = _site_for(update, sid)
        if not site:
            await update.callback_query.answer("عدم دسترسی", show_alert=True)
            return True
        await update.callback_query.answer()
        await show_products(update.callback_query, site, sid, mode=mode, page=1, category_id=int(parts[4]))
        return True

    if len(parts) in {3, 4} and parts[0] == "d" and parts[1] == "cats" and parts[2].isdigit():
        if len(parts) == 4 and not parts[3].isdigit():
            return await _ORIGINAL_CALLBACK(update, context)
        sid = int(parts[2])
        page = int(parts[3]) if len(parts) == 4 else 1
        site = _site_for(update, sid)
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
