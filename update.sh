#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="/opt/deltajanebi"
cd "$APP_DIR"

install_updater_command() {
  cat > /usr/local/bin/deltajanebi-update <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
exec bash /opt/deltajanebi/update.sh
EOF
  chmod 755 /usr/local/bin/deltajanebi-update
}

install_updater_command

echo "==> دریافت نسخه جدید"
git fetch origin main
git reset --hard origin/main
install_updater_command

echo "==> بررسی تنظیمات"
docker compose config -q

echo "==> ساخت image جدید"
docker compose build --pull web

echo "==> بررسی migrationها"
docker run --rm \
  --entrypoint python \
  -e DJANGO_SECRET_KEY=update-smoke-test-only \
  -e DEBUG=0 \
  -e ALLOWED_HOSTS=testserver,localhost,127.0.0.1 \
  deltajanebi-app:latest \
  manage.py makemigrations --check --dry-run

echo "==> بررسی ربات v12، کیف پول، رزرو، پرداخت و اعلان‌ها"
docker run --rm \
  --entrypoint python \
  -e DJANGO_SECRET_KEY=update-smoke-test-only \
  -e DEBUG=0 \
  -e ALLOWED_HOSTS=testserver,localhost,127.0.0.1 \
  deltajanebi-app:latest \
  manage.py shell -c 'from shop.management.commands import telegram_bot_v12 as b; from shop.management.commands import reservation_loop, sync_loop; from shop.services.wallet import adjust_wallet, wallet_balance, wallet_history; from shop.services.order_workflow import expire_reservations, mark_paid; from shop.services.payments import request_zarinpal_payment, verify_zarinpal_payment; from shop.iran_locations import province_city_map; from shop.models import Announcement, DiscountCode, Product, SiteSetting, User; from shop.templatetags.store_filters import money; labels=[x.text for r in b.main_menu().inline_keyboard for x in r]; settings_labels=[x.text for r in b.settings_menu().inline_keyboard for x in r]; wallet_labels=[x.text for r in b._user_keyboard(1).inline_keyboard for x in r]; assert "👥 کاربران" in labels and "🔔 اطلاع‌رسانی" in labels; assert "💳 پرداخت، تخفیف و ارسال" in settings_labels; assert "➕ افزایش موجودی" in wallet_labels and "📜 تراکنش‌های کیف پول" in wallet_labels; assert callable(adjust_wallet) and callable(wallet_balance) and callable(wallet_history); assert callable(expire_reservations) and callable(mark_paid); assert callable(request_zarinpal_payment) and callable(verify_zarinpal_payment); assert len(province_city_map())>=31; assert money(230000)=="230,000"; assert DiscountCode.PERCENT=="percent"; assert hasattr(Product,"reserved_stock") and hasattr(User,"customer_code"); print("telegram_bot_v12 + wallet + reservations + notifications + checkout: OK")'

echo "==> اجرای تست‌های قبل از انتشار"
docker run --rm \
  --entrypoint python \
  -e DJANGO_SECRET_KEY=update-smoke-test-only \
  -e DEBUG=0 \
  -e ALLOWED_HOSTS=testserver,localhost,127.0.0.1 \
  deltajanebi-app:latest \
  manage.py test shop --verbosity 1

echo "==> اعمال آپدیت"
docker compose up -d --remove-orphans

WEB_ID="$(docker compose ps -q --all web)"
[ -n "$WEB_ID" ] || { echo "خطا: کانتینر web پیدا نشد." >&2; exit 1; }

healthy=0
for _ in $(seq 1 60); do
  status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$WEB_ID" 2>/dev/null || true)"
  if [ "$status" = "healthy" ]; then
    healthy=1
    break
  fi
  if [ "$status" = "unhealthy" ] || [ "$status" = "exited" ] || [ "$status" = "dead" ]; then
    docker compose logs --tail=160 web bot sync reservations >&2 || true
    exit 1
  fi
  sleep 2
done

[ "$healthy" -eq 1 ] || { docker compose logs --tail=160 web >&2 || true; exit 1; }
docker compose exec -T web python manage.py check --fail-level ERROR

sleep 3
for svc in db web bot sync reservations caddy; do
  cid="$(docker compose ps -q --all "$svc")"
  [ -n "$cid" ] || { echo "خطا: کانتینر $svc پیدا نشد." >&2; exit 1; }
  running="$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || true)"
  if [ "$running" != "true" ]; then
    docker compose logs --tail=160 "$svc" >&2 || true
    echo "خطا: سرویس $svc در حال اجرا نیست." >&2
    exit 1
  fi
done

docker image prune -f >/dev/null 2>&1 || true

echo "آپدیت با موفقیت انجام شد."
echo "از این به بعد برای آپدیت فقط اجرا کن: sudo deltajanebi-update"
docker compose ps
