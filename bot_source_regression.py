#!/usr/bin/env python3
import asyncio

import external_bot_delta_v16  # installs footer + source restoration chain
import delta_bot_native as native
import delta_source_restore as source


class ReadOnlyQuery:
    def __init__(self, data):
        self._data = data
        self.from_user = type("User", (), {"id": 1})()
        self.answers = []
        self.edits = []
        self.message = self

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
    def __init__(self, q):
        self.callback_query = q


class FakeApplication:
    def __init__(self):
        self.created = 0

    def create_task(self, coro):
        self.created += 1
        # The regression only verifies that monitoring is scheduled; avoid
        # actually polling forever in this standalone script.
        coro.close()
        return None


class Context:
    def __init__(self):
        self.user_data = {}
        self.application = FakeApplication()


async def test_sourcebulk_does_not_mutate_callback_data():
    q = ReadOnlyQuery("d:sourcebulk:7:9")
    update = Update(q)
    context = Context()
    site = {"id": 7, "name": "Delta", "base_url": "https://delta.example", "platform": "deltajanebi"}
    detail_before = {
        "id": 9,
        "name": "Source",
        "hostname": "source.example",
        "is_active": True,
        "bulk_import_enabled": False,
        "markup_label": "0%",
        "brand_terms": "",
        "last_discovered_count": 0,
        "product_count": 0,
    }
    detail_after = dict(detail_before, bulk_import_enabled=True)
    edits = []

    original_site = native._site
    original_api = native.core.api
    original_edit = native._edit
    try:
        native._site = lambda uid, sid: site if (uid, sid) == (1, 7) else None

        async def api(_site, action, payload=None, timeout=None):
            if action == "source_site_detail":
                return {"ok": True, "data": detail_after if edits else detail_before}
            if action == "source_site_update":
                assert payload == {"id": 9, "bulk_import_enabled": True}
                return {"ok": True, "data": detail_after}
            raise AssertionError(action)

        async def edit(_q, text, markup=None):
            edits.append((text, markup))

        native.core.api = api
        native._edit = edit
        handled = await source.callback(update, context)
    finally:
        native._site = original_site
        native.core.api = original_api
        native._edit = original_edit

    assert handled is True
    assert q.data == "d:sourcebulk:7:9"
    assert edits
    assert "آپلود همه فعال شد" in edits[-1][0]


async def test_single_source_sync_sends_exact_source_id():
    q = ReadOnlyQuery("d:syncsource:7:9")
    update = Update(q)
    context = Context()
    site = {"id": 7, "name": "Delta", "base_url": "https://delta.example", "platform": "deltajanebi"}
    calls = []
    native_edits = []

    original_site = native._site
    original_api = native.core.api
    original_edit = native._edit
    original_progress_edit = source._progress_edit
    try:
        native._site = lambda uid, sid: site if (uid, sid) == (1, 7) else None

        async def api(_site, action, payload=None, timeout=None):
            calls.append((action, payload or {}))
            if action == "delta_source_sync_start":
                assert payload == {"source_site_id": 9}
                return {
                    "ok": True,
                    "data": {
                        "job_id": "job-single-9",
                        "status": "queued",
                        "total": 0,
                        "checked": 0,
                        "created": 0,
                        "changed": 0,
                        "skipped": 0,
                        "errors": 0,
                        "target_source_site_id": 9,
                        "target_source_site_name": "Source Nine",
                        "sync_scope": "single_source",
                        "reused": False,
                    },
                }
            raise AssertionError(action)

        async def edit(_q, text, markup=None):
            native_edits.append((text, markup))

        async def progress_edit(_q, text, markup=None):
            native_edits.append((text, markup))
            return True

        native.core.api = api
        native._edit = edit
        source._progress_edit = progress_edit
        handled = await source.callback(update, context)
    finally:
        native._site = original_site
        native.core.api = original_api
        native._edit = original_edit
        source._progress_edit = original_progress_edit
        source._MONITORS.discard((7, "job-single-9"))

    assert handled is True
    assert calls == [("delta_source_sync_start", {"source_site_id": 9})]
    assert context.application.created == 1
    assert any("Source Nine" in text for text, _ in native_edits)


def test_progress_text_and_install_contract():
    text = source._progress_text({
        "status": "running",
        "total": 100,
        "checked": 25,
        "created": 20,
        "changed": 22,
        "skipped": 2,
        "errors": 1,
        "current_site": "Source",
    })
    assert "25%" in text
    assert "20" in text
    assert "Source" in text

    single = source._progress_text({
        "status": "running",
        "total": 10,
        "checked": 2,
        "created": 0,
        "changed": 1,
        "skipped": 0,
        "errors": 0,
        "target_source_site_id": 9,
        "target_source_site_name": "Source Nine",
        "sync_scope": "single_source",
    })
    assert "Source Nine" in single
    assert "همگام‌سازی سایت" in single
    assert getattr(native, "_delta_source_restore_v18_installed", False) is True


if __name__ == "__main__":
    asyncio.run(test_sourcebulk_does_not_mutate_callback_data())
    asyncio.run(test_single_source_sync_sends_exact_source_id())
    test_progress_text_and_install_contract()
    print("Delta source controls regression: OK")
    print("Delta per-source sync regression: OK")
