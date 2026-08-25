import uuid

from django.db import connection, transaction
from django.utils import timezone

from shop.models import User


def _now_text():
    return timezone.localtime(timezone.now()).isoformat(timespec="seconds")


def wallet_balance(user_id):
    with connection.cursor() as cursor:
        cursor.execute("SELECT balance FROM shop_wallet_account WHERE user_id = %s", [int(user_id)])
        row = cursor.fetchone()
    return int(row[0]) if row else 0


def wallet_history(user_id, limit=20):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, amount, balance_after, reason, admin_id, created_at "
            "FROM shop_wallet_transaction WHERE user_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            [int(user_id), int(limit)],
        )
        rows = cursor.fetchall()
    return [
        {
            "id": row[0],
            "amount": int(row[1]),
            "balance_after": int(row[2]),
            "reason": row[3] or "",
            "admin_id": row[4] or "",
            "created_at": row[5] or "",
        }
        for row in rows
    ]


def adjust_wallet(user_id, amount, reason="", admin_id=""):
    amount = int(amount)
    if amount == 0:
        raise ValueError("مبلغ تراکنش نمی‌تواند صفر باشد.")

    with transaction.atomic():
        User.objects.select_for_update().get(pk=int(user_id))
        now = _now_text()
        with connection.cursor() as cursor:
            cursor.execute("SELECT balance FROM shop_wallet_account WHERE user_id = %s", [int(user_id)])
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    "INSERT INTO shop_wallet_account (user_id, balance, updated_at) VALUES (%s, %s, %s)",
                    [int(user_id), 0, now],
                )
                balance = 0
            else:
                balance = int(row[0])

            new_balance = balance + amount
            if new_balance < 0:
                raise ValueError("موجودی کیف پول برای این کاهش کافی نیست.")

            cursor.execute(
                "UPDATE shop_wallet_account SET balance = %s, updated_at = %s WHERE user_id = %s",
                [new_balance, now, int(user_id)],
            )
            txid = str(uuid.uuid4())
            cursor.execute(
                "INSERT INTO shop_wallet_transaction "
                "(id, user_id, amount, balance_after, reason, admin_id, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [txid, int(user_id), amount, new_balance, str(reason or "")[:1000], str(admin_id or "")[:64], now],
            )
    return new_balance
