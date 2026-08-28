#!/usr/bin/env python3
import asyncio

import delta_bot_native as native
import delta_catalog_ui_v20 as catalog_ui


class Query:
    def __init__(self):
        self.edits = []
        self.message = self

    async def edit_message_text(self, text, reply_markup=None, **kwargs):
        self.edits.append((text, reply_markup))
        return self

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.edits.append((text, reply_markup))
        return self


def labels(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


async def run_test():
    calls = []
    original_api = native.core.api

    async def fake_api(site, action, payload=None, timeout=None):
        calls.append((action, payload or {}))
        assert action == "delta_products"
        page = int((payload or {}).get("page") or 1)
        rows = [
            {
                "id": (page - 1) * 25 + i + 1,
                "name": f"محصول صفحه {page} شماره {i + 1}",
                "effective_price": 100000 + i,
                "is_active": True,
                "amazing_active": False,
                "discount_active": False,
            }
            for i in range(25)
        ]
        return {
            "ok": True,
            "data": rows,
            "pagination": {
                "page": page,
                "pages": 4,
                "per_page": 25,
                "total": 100,
                "has_previous": page > 1,
                "has_next": page < 4,
            },
        }

    native.core.api = fake_api
    try:
        q = Query()
        await catalog_ui.show_products(q, {"id": 2}, 2, mode="all", page=2)
        assert calls[-1][1]["page"] == 2
        assert calls[-1][1]["per_page"] == 25
        text, markup = q.edits[-1]
        all_labels = labels(markup)
        product_buttons = [x for x in all_labels if x.startswith("✅ محصول")]
        assert len(product_buttons) == 25, len(product_buttons)
        assert "⬅️ قبلی" in all_labels
        assert "بعدی ➡️" in all_labels
        assert "2/4" in all_labels
        assert "کل: 100 محصول" in text
        assert "صفحه 2 از 4" in text
    finally:
        native.core.api = original_api


def main():
    asyncio.run(run_test())
    print("Delta 25-item product pagination: OK")


if __name__ == "__main__":
    main()
