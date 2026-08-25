from django.db import migrations


def shorten_customer_codes(apps, schema_editor):
    User = apps.get_model("shop", "User")
    User.objects.filter(is_staff=False).update(customer_code=None)
    for user in User.objects.filter(is_staff=False).order_by("id").iterator():
        user.customer_code = f"#{1000 + user.pk}"
        user.save(update_fields=["customer_code"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("shop", "0013_customer_reservations_announcements")]

    operations = [
        migrations.RunPython(shorten_customer_codes, noop_reverse),
        migrations.RunSQL(
            "CREATE TABLE IF NOT EXISTS shop_wallet_account ("
            "user_id BIGINT PRIMARY KEY REFERENCES shop_user(id) ON DELETE CASCADE, "
            "balance BIGINT NOT NULL DEFAULT 0, "
            "updated_at VARCHAR(40) NOT NULL DEFAULT ''"
            ")",
            reverse_sql="DROP TABLE IF EXISTS shop_wallet_account",
        ),
        migrations.RunSQL(
            "CREATE TABLE IF NOT EXISTS shop_wallet_transaction ("
            "id VARCHAR(36) PRIMARY KEY, "
            "user_id BIGINT NOT NULL REFERENCES shop_user(id) ON DELETE CASCADE, "
            "amount BIGINT NOT NULL, "
            "balance_after BIGINT NOT NULL, "
            "reason TEXT NOT NULL DEFAULT '', "
            "admin_id VARCHAR(64) NOT NULL DEFAULT '', "
            "created_at VARCHAR(40) NOT NULL"
            ")",
            reverse_sql="DROP TABLE IF EXISTS shop_wallet_transaction",
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS shop_wallet_transaction_user_idx "
            "ON shop_wallet_transaction(user_id, created_at)",
            reverse_sql="DROP INDEX IF EXISTS shop_wallet_transaction_user_idx",
        ),
    ]
