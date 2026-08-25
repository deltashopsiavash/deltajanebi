from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("shop", "0014_wallet_short_customer_codes")]

    operations = [
        migrations.RunSQL(
            "CREATE TABLE IF NOT EXISTS shop_order_wallet ("
            "order_id BIGINT PRIMARY KEY REFERENCES shop_order(id) ON DELETE CASCADE, "
            "wallet_amount BIGINT NOT NULL DEFAULT 0, "
            "balance_before BIGINT NOT NULL DEFAULT 0, "
            "balance_after BIGINT NOT NULL DEFAULT 0, "
            "refunded BOOLEAN NOT NULL DEFAULT FALSE, "
            "debit_tx_id VARCHAR(36) NOT NULL DEFAULT '', "
            "refund_tx_id VARCHAR(36) NOT NULL DEFAULT ''"
            ")",
            reverse_sql="DROP TABLE IF EXISTS shop_order_wallet",
        ),
    ]
