#!/usr/bin/env python3
import asyncio

import delta_bot_native as native
import delta_catalog_ui_v20 as catalog_ui


class Query:
    def __init__(self, data=""):
        self._data = data
        self.edits = []
        self.answers = []
        self.message = self
        self.from_user = type("User", (), {"id": 1})()

    @property
    def data(self):
        return self._data

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)

    async def edit_message_text(self, text, reply_markup=None, **kwargs):
        self.edits.append((text, reply_markup))
        return self

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.edits.append((text, reply_markup))
        return self


class Update:
    def __init__(self, query):
        self.callback_query = query


class Context:
    user_data = {}


def labels(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


async def run_test():
    calls = []
    original_api = native.core.api
    original_site = native._site

    async def fake_api(site, action, payload=None, timeout=None):
        calls.append((action, payload or {}))
        if action == "categories":
            rows = []
            for index in range(52):
                depth = 0 if index % 3 == 0 else 1
                rows.append({
                    "id": index + 1,
                    "name": f"دسته {index + 1}",
                    "path": f"والد > دسته {index + 1}" if depth else f"دسته {index + 1}",
                    "depth": depth,
                    "is_active": True,
                    "product_count": index,
                })
            return {"ok": True, "data": rows}

        assert action == "delta_products"
        page = int((payload or {}).get("page") or 1)
        category_id = int((payload or {}).get("category_id") or 0)
        rows = [
            {
                "id": (page - 1) * 45 + i + 1,
                "name": f"محصول صفحه {page} شماره {i + 1}",
                "effective_price": 100000 + i,
                "is_active": True,
                "amazing_active": False,
                "discount_active": False,
            }
            for i in range(45)
        ]
        return {
            "ok": True,
            "data": rows,
            "category": ({"id": category_id, "name": f"دسته {category_id}"} if category_id else None),
            "pagination": {
                "page": page,
                "pages": 4,
                "per_page": 45,
                "total": 180,
                "has_previous": page > 1,
                "has_next": page < 4,
            },
        }

    native.core.api = fake_api
    native._site = lambda uid, sid: {"id": sid, "name": "Delta"} if (uid, sid) == (1, 2) else None
    try:
        q = Query()
        await catalog_ui.show_products(q, {"id": 2}, 2, mode="all", page=2)
        assert calls[-1][1]["page"] == 2
        assert calls[-1][1]["per_page"] == 45
        text, markup = q.edits[-1]
        all_labels = labels(markup)
        product_buttons = [x for x in all_labels if x.startswith("✅ محصول")]
        assert len(product_buttons) == 45, len(product_buttons)
        assert "📂 انتخاب دسته‌بندی" in all_labels
        assert "⬅️ قبلی" in all_labels
        assert "بعدی ➡️" in all_labels
        assert "2/4" in all_labels
        assert "کل: 180 محصول" in text
        assert "صفحه 2 از 4" in text

        # Regression for the production bug: menu callbacks have FOUR parts.
        # They must be intercepted by this paginated layer, not fall through to
        # delta_bot_native's old lexical show_products that displayed rows[:45].
        calls.clear()
        q_first = Query("d:products:2:all")
        handled = await catalog_ui.callback(Update(q_first), Context())
        assert handled is True
        assert calls[-1][0] == "delta_products"
        assert calls[-1][1]["page"] == 1
        assert calls[-1][1]["per_page"] == 45
        first_text, first_markup = q_first.edits[-1]
        assert "صفحه 1 از 4" in first_text
        assert "بعدی ➡️" in labels(first_markup)

        calls.clear()
        q_filter = Query()
        await catalog_ui.show_products(q_filter, {"id": 2}, 2, mode="all", page=3, category_id=17)
        assert calls[-1][1]["category_id"] == 17
        filter_text, filter_markup = q_filter.edits[-1]
        filter_labels = labels(filter_markup)
        assert "دسته: دسته 17" in filter_text
        assert "❌ همه دسته‌ها" in filter_labels
        # Category selection must survive previous/next page callbacks.
        callbacks = [button.callback_data for row in filter_markup.inline_keyboard for button in row if button.callback_data]
        assert "d:products:2:all:2:17" in callbacks
        assert "d:products:2:all:4:17" in callbacks

        q2 = Query()
        await catalog_ui.show_categories(q2, {"id": 2}, 2, page=2)
        text2, markup2 = q2.edits[-1]
        category_labels = labels(markup2)
        category_buttons = [x for x in category_labels if x.startswith("✅")]
        assert len(category_buttons) == 25, len(category_buttons)
        assert any("↳" in x for x in category_buttons)
        assert "⬅️ قبلی" in category_labels
        assert "بعدی ➡️" in category_labels
        assert "2/3" in category_labels
        assert "کل: 52 دسته" in text2

        q3 = Query()
        await catalog_ui.show_product_categories(q3, {"id": 2}, 2, mode="all", page=1)
        text3, markup3 = q3.edits[-1]
        assert "دسته‌های دارای محصول: 51" in text3
        category_filter_callbacks = [button.callback_data for row in markup3.inline_keyboard for button in row if button.callback_data]
        assert any(value.startswith("d:pcat:2:all:") for value in category_filter_callbacks)
    finally:
        native.core.api = original_api
        native._site = original_site


def main():
    asyncio.run(run_test())
    print("Delta all-page product pagination: OK")
    print("Delta product category filters: OK")
    print("Delta ordered category pagination: OK")


if __name__ == "__main__":
    main()
