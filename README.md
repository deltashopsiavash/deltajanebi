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

## رفع خطای Network is unreachable و PyPI

اسکریپت نصب و آپدیت سایت، کتابخانه‌های Python را با `docker run --network host` دریافت و به Wheel تبدیل می‌کند. ساخت نهایی با `docker compose build` این Wheelها را **بدون اینترنت در مرحله pip** نصب می‌کند. این روش نیازی به فعال کردن `network.host` در BuildKit ندارد و شبکه معمول اجرای سرویس‌ها را تغییر نمی‌دهد.

برای اجرای دستی ساخت اصلاح‌شده:

```bash
cd /opt/deltajanebi
sudo bash scripts/build-site-image.sh
```

Wheelها در `.build-wheels` کش می‌شوند و فقط پس از تغییر `requirements.txt` دوباره تهیه می‌شوند. این فایل‌ها با named build context به Docker معرفی می‌شوند و وارد image نهایی نمی‌شوند. اگر خود شبکه سرور به هیچ مخزن HTTPS دسترسی نداشته باشد، اسکریپت با پیام دقیق متوقف می‌شود؛ در این حالت تنظیمات شبکه، فایروال، DNS یا پروکسی میزبان باید بررسی شود.

## عیب‌یابی نصب و کلید اتصال ربات

کلید ربات **پیش از ساخت Docker image** در ترمینال SSH نمایش داده و در مسیر `/root/deltajanebi-bot-api-key.txt` (با مجوز `600`) ذخیره می‌شود. هیچ‌وقت این کلید را در گزارش خطا یا پیام عمومی منتشر نکنید.

برای بررسی وضعیت نصب، بدون چاپ مقادیر محرمانه:

```bash
sudo bash /opt/deltajanebi/scripts/site-diagnostics.sh
```

تست مستقل سرور داخلی Django، صفحه اصلی و عبور درخواست از Caddy:

```bash
cd /opt/deltajanebi
sudo bash scripts/verify-deployment.sh
```

این تست HTTPS را با اتصال مستقیم به `127.0.0.1:443` بررسی می‌کند و به وضعیت DNS عمومی وابسته نیست. موفقیت آن به معنی در دسترس بودن سایت از بیرون نیست؛ برای دسترسی عمومی رکورد A/AAAA، پروکسی Cloudflare، وضعیت SSL، فایروال و باز بودن پورت‌های 80 و 443 را جداگانه بررسی کنید.
