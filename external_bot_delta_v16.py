#!/usr/bin/env python3
"""Final DeltaJanebi multi-site entrypoint.

v16 keeps the platform-aware v15 router and restores Delta-native additions
without reintroducing any SanaShop fallback for Delta-specific callbacks.
"""
import logging
import os
import sys

RUNTIME_DIR = os.environ.get("DELTAJANEBI_RUNTIME_DIR", "/opt/deltajanebi-bot-runtime")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

# httpx INFO logs include the full Telegram Bot API URL. Keep transport-level
# request URLs out of journald while retaining actionable WARNING/ERROR logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

import external_bot_delta_v15 as v15
import delta_footer_restore

core = v15.core

# Install restorations in a deliberate chain. Source restoration is imported
# only after footer restoration has wrapped native.callback, so it delegates
# every unrelated callback through the already-restored footer flow.
delta_footer_restore.install()
import delta_source_restore  # noqa: E402

delta_source_restore.install()


async def cancel(update, context):
    uid = update.effective_user.id
    site_id = context.user_data.get("site_id")
    context.user_data.clear()
    if site_id:
        try:
            site_id = int(site_id)
        except (TypeError, ValueError):
            site_id = None
    if site_id and core.can_access(uid, site_id):
        site = core.get_site(site_id)
        return await update.message.reply_text(
            "❌ عملیات جاری لغو شد.",
            reply_markup=v15.routed_site_panel(site, uid),
        )
    if core.is_owner(uid):
        return await update.message.reply_text("❌ عملیات جاری لغو شد.", reply_markup=core.owner_home())
    site = core.assigned_site(uid)
    if site:
        return await update.message.reply_text(
            "❌ عملیات جاری لغو شد.",
            reply_markup=v15.routed_site_panel(site, uid),
        )
    return await update.message.reply_text("❌ عملیات جاری لغو شد.")


def run():
    try:
        v15.acquire_single_instance_lock()
    except RuntimeError as exc:
        v15.logger.error("Duplicate bot startup refused: %s", exc)
        raise SystemExit(73) from exc
    v15.ensure_schema()
    core.site_panel = v15.routed_site_panel
    app = (
        Application.builder()
        .token(core.TOKEN)
        .concurrent_updates(16)
        .connection_pool_size(32)
        .pool_timeout(10.0)
        .post_init(v15.post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", v15.start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(v15.callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, v15.media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, v15.message))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run()
