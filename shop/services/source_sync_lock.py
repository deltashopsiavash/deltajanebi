"""Cross-container catalog synchronization lock.

The manual full-sync worker runs inside the ``web`` container while the periodic
``sync`` service runs in a separate container. A filesystem flock under /tmp is
therefore not shared between them. Use a PostgreSQL advisory lock so only one
catalog writer/discovery loop can run across the whole deployment at a time.
"""
from contextlib import contextmanager

from django.db import connection

# Stable signed 64-bit key dedicated to DeltaJanebi source catalog synchronization.
CATALOG_SYNC_LOCK_ID = 4433581607262026


@contextmanager
def catalog_sync_lock():
    """Yield True when this process owns the deployment-wide catalog lock.

    PostgreSQL advisory locks are session-scoped and automatically disappear if
    a worker/container dies. Non-PostgreSQL development/test databases fall back
    to allowing the caller because the production deployment uses PostgreSQL.
    """
    if connection.vendor != "postgresql":
        yield True
        return

    acquired = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [CATALOG_SYNC_LOCK_ID])
            row = cursor.fetchone()
            acquired = bool(row and row[0])
        yield acquired
    finally:
        if acquired:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [CATALOG_SYNC_LOCK_ID])
            except Exception:
                # Closing the DB session releases a session advisory lock too.
                connection.close()
