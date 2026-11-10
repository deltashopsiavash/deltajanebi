# DeltaJanebi

## نصب

### سرور ایران — سایت

```bash
curl -fsSL https://raw.githubusercontent.com/deltashopsiavash/deltajanebi/main/install-site.sh | sudo bash
```

### سرور خارج — ربات

```bash
curl -fsSL https://raw.githubusercontent.com/deltashopsiavash/deltajanebi/main/install-bot.sh | sudo bash
```

## آپدیت

### سرور ایران — سایت

```bash
curl -fsSL https://raw.githubusercontent.com/deltashopsiavash/deltajanebi/main/update-site.sh | sudo bash
```

### سرور خارج — ربات

```bash
curl -fsSL https://raw.githubusercontent.com/deltashopsiavash/deltajanebi/main/update-bot.sh | sudo bash
```

## خطای اتصال pip هنگام ساخت image

ساخت Docker به صورت پیش‌فرض ابتدا از آینه HTTPS علی‌بابا، سپس آینه Tsinghua و در آخر PyPI رسمی برای نصب پکیج‌ها تلاش می‌کند. نسخه‌های ثابت در `requirements.txt` تغییر نمی‌کنند.

اگر آینه‌های پیش‌فرض از شبکه سرور قابل دسترس نبودند، می‌توان مخزن موردنظر را برای یک بار ساخت تعیین کرد:

```bash
cd /opt/deltajanebi
DELTA_PIP_INDEXES="https://pypi.org/simple/" docker compose build --pull web
```

اگر شبکه میزبان به اینترنت دسترسی دارد اما شبکه Docker ندارد، شبکه ساخت را برای یک اجرا host قرار بده:

```bash
cd /opt/deltajanebi
DELTA_DOCKER_BUILD_NETWORK=host docker compose build --pull web
```

برای اجرای مجدد مراحل نصب، `install-site.sh` را اجرا کن. فایل `.env` موجود حفظ می‌شود. اگر هیچ مخزن HTTPS قابل دسترس نباشد، مشکل اتصال شبکه یا پروکسی سرور باید جداگانه بررسی شود.
