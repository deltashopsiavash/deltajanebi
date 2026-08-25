import uuid

from django.db import connection, transaction
from django.utils import timezone

from shop.models import User


def _now_text():
    return timezone.localtime(timezone.now()).isoformat(timespec="seconds")


def _ensure_account(cursor, user_id, now):
    cursor.execute("SELECT balance FROM shop_wallet_account WHERE user_id = %s", [int(user_id)])
    row = cursor.fetchone()
    if row is not None:
        return int(row[0])
    cursor.execute(
        "INSERT INTO shop_wallet_account (user_id, balance, updated_at) VALUES (%s, %s, %s)",
        [int(user_id), 0, now],
    )
    return 0


def _insert_transaction(cursor, user_id, amount, balance_after, reason, admin_id, now):
    txid = str(uuid.uuid4())
    cursor.execute(
        "INSERT INTO shop_wallet_transaction "
        "(id, user_id, amount, balance_after, reason, admin_id, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        [
            txid,
            int(user_id),
            int(amount),
            int(balance_after),
            str(reason or "")[:1000],
            str(admin_id or "")[:64],
            now,
        ],
    )
    return txid


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
            balance = _ensure_account(cursor, user_id, now)
            new_balance = balance + amount
            if new_balance < 0:
                raise ValueError("موجودی کیف پول برای این کاهش کافی نیست.")
            cursor.execute(
                "UPDATE shop_wallet_account SET balance = %s, updated_at = %s WHERE user_id = %s",
                [new_balance, now, int(user_id)],
            )
            _insert_transaction(cursor, user_id, amount, new_balance, reason, admin_id, now)
    return new_balance


def order_wallet_info(order_id):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT wallet_amount, balance_before, balance_after, refunded, debit_tx_id, refund_tx_id "
            "FROM shop_order_wallet WHERE order_id = %s",
            [int(order_id)],
        )
        row = cursor.fetchone()
    if not row:
        return {
            "wallet_amount": 0,
            "balance_before": 0,
            "balance_after": 0,
            "refunded": False,
            "debit_tx_id": "",
            "refund_tx_id": "",
        }
    return {
        "wallet_amount": int(row[0]),
        "balance_before": int(row[1]),
        "balance_after": int(row[2]),
        "refunded": bool(row[3]),
        "debit_tx_id": row[4] or "",
        "refund_tx_id": row[5] or "",
    }


def external_payable(order):
    info = order_wallet_info(order.id)
    return max(0, int(order.total or 0) - int(info["wallet_amount"] or 0))


def apply_wallet_to_order(order, requested=True):
    """Debit up to the order total and persist a per-order audit record.

    Safe to call inside the checkout transaction. Repeated calls for the same order
    return the previously allocated amount instead of charging twice.
    """
    if not requested or not order.pk or int(order.total or 0) <= 0:
        return order_wallet_info(order.pk) if order.pk else {
            "wallet_amount": 0, "balance_before": 0, "balance_after": 0,
            "refunded": False, "debit_tx_id": "", "refund_tx_id": "",
        }

    user_id = int(order.user_id)
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user_id)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT wallet_amount, balance_before, balance_after, refunded, debit_tx_id, refund_tx_id "
                "FROM shop_order_wallet WHERE order_id = %s",
                [int(order.pk)],
            )
            existing = cursor.fetchone()
            if existing:
                return {
                    "wallet_amount": int(existing[0]),
                    "balance_before": int(existing[1]),
                    "balance_after": int(existing[2]),
                    "refunded": bool(existing[3]),
                    "debit_tx_id": existing[4] or "",
                    "refund_tx_id": existing[5] or "",
                }

            now = _now_text()
            balance_before = _ensure_account(cursor, user_id, now)
            amount = min(balance_before, int(order.total))
            balance_after = balance_before - amount
            debit_tx_id = ""
            if amount > 0:
                cursor.execute(
                    "UPDATE shop_wallet_account SET balance = %s, updated_at = %s WHERE user_id = %s",
                    [balance_after, now, user_id],
                )
                debit_tx_id = _insert_transaction(
                    cursor,
                    user_id,
                    -amount,
                    balance_after,
                    f"پرداخت سفارش #{order.id} از کیف پول",
                    "system:checkout",
                    now,
                )
            cursor.execute(
                "INSERT INTO shop_order_wallet "
                "(order_id, wallet_amount, balance_before, balance_after, refunded, debit_tx_id, refund_tx_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [int(order.pk), amount, balance_before, balance_after, False, debit_tx_id, ""],
            )
    return order_wallet_info(order.pk)


def refund_order_wallet(order, reason=""):
    """Refund the wallet share exactly once for an unpaid/cancelled order."""
    if not getattr(order, "pk", None):
        return False
    user_id = int(order.user_id)
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user_id)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT wallet_amount, refunded FROM shop_order_wallet WHERE order_id = %s",
                [int(order.pk)],
            )
            row = cursor.fetchone()
            if not row or bool(row[1]) or int(row[0] or 0) <= 0:
                return False
            amount = int(row[0])
            now = _now_text()
            balance = _ensure_account(cursor, user_id, now)
            new_balance = balance + amount
            cursor.execute(
                "UPDATE shop_wallet_account SET balance = %s, updated_at = %s WHERE user_id = %s",
                [new_balance, now, user_id],
            )
            refund_tx_id = _insert_transaction(
                cursor,
                user_id,
                amount,
                new_balance,
                reason or f"برگشت سهم کیف پول سفارش #{order.id}",
                "system:refund",
                now,
            )
            cursor.execute(
                "UPDATE shop_order_wallet SET refunded = %s, refund_tx_id = %s WHERE order_id = %s",
                [True, refund_tx_id, int(order.pk)],
            )
    return True
