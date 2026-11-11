# DeltaJanebi

## نصب جدید روی Ubuntu 24.04 LTS — بدون Docker (مناسب VPS کوچک)

روی سرور ایران تازه نصب‌شده با Ubuntu 24.04 LTS همین دستور را اجرا کن. نصب از تو دامنه، نام فروشگاه، ایمیل مدیر، رمز ادمین و مشخصات اختیاری Resend را می‌پرسد:

```bash
curl -fsSL https://raw.githubusercontent.com/deltashopsiavash/deltajanebi/main/install-site.sh | sudo bash
```

در نصب تازه Docker وجود ندارد: PostgreSQL محلی + Gunicorn کم‌مصرف + Caddy/HTTPS و سرویس‌های systemd. روی VPS کم‌رم یک Swap دوگیگابایتی ساخته می‌شود. Sync دستی فعال و Sync دوره‌ای سنگین به‌طور پیش‌فرض خاموش است؛ برای روشن‌کردن آن پس از ارتقای RAM: `sudo systemctl enable --now deltajanebi-auto-sync`. آپدیت: `sudo deltajanebi-update`. نصب‌های موجود Docker به همان مسیر قدیمی می‌مانند.

کلید API برای اتصال مجدد ربات مشترک در `/root/deltajanebi-bot-api-key.txt` ذخیره می‌شود. برای HTTPS عمومی، DNS باید به آی‌پی سرور اشاره کند و پورت‌های ۸۰ و ۴۴۳ باز باشند.

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

### سایت نصب شده ولی در مرورگر باز نمی‌شود

بدون حذف Docker یا اطلاعات سایت، اول اسکریپت عیب‌یابی را اجرا کنید:

```bash
sudo bash /opt/deltajanebi/scripts/site-diagnostics.sh
```

این ابزار دامنه واقعی داخل `.env`، DNS، پورت‌ها، وضعیت فایروال UFW، کانتینرها، پاسخ محلی Caddy، اعتبار گواهی TLS و پاسخ دامنه از شبکه میزبان را جداگانه آزمایش می‌کند. اگر پاسخ محلی موفق و پاسخ عمومی ناموفق بود، DNS، فایروال سرور/ارائه‌دهنده و تنظیمات CDN را بررسی کنید. در صورت استفاده از Cloudflare، رکورد A باید به IP جدید سرور اشاره کند؛ رکورد AAAA قدیمی را اصلاح یا حذف کنید. HTTPS از داخل همان سرور ممکن است به‌دلیل hairpin routing قطع باشد، پس برای تأیید نهایی از شبکه‌ای دیگر هم آزمایش کنید.

### بازیابی نصب ناتمام

دریافت کلید ربات به‌معنی پایان نصب سایت نیست. پیام موفقیت تنها بعد از بالا آمدن وب، PostgreSQL، Caddy و تست HTTPS نمایش داده می‌شود. اگر کانتینری ندارید، بدون حذف اطلاعات و بدون ساخت مجدد ایمیج موجود، نصب را ادامه دهید:

```bash
sudo bash /opt/deltajanebi/scripts/resume-site.sh
```

اگر ایمیج وجود نداشته باشد، دستور مرحله ساخت را اجرا می‌کند. خطاها با لاگ تشخیصی نمایش داده می‌شوند. فایل `.env` و حجم‌های Docker دست‌نخورده می‌مانند.

### اجرای نصب از طریق pipe و ادامه نصب

تمام دستورات `docker compose exec` در نصب، آپدیت، راستی‌آزمایی و بازیابی با `--interactive=false -T` اجرا می‌شوند؛ در غیر این صورت Docker Compose می‌تواند باقی متن `curl ... | bash` را از ورودی استاندارد بخواند و نصب ظاهراً بدون خطا پیش از راه‌اندازی Caddy تمام شود.

اسکریپت بازیابی را می‌توان به شکل `curl -fsSL https://raw.githubusercontent.com/deltashopsiavash/deltajanebi/main/scripts/resume-site.sh | sudo bash` اجرا کرد. در CI نیز همین رفتار با `cat scripts/resume-site.sh | bash` پس از حذف کانتینرهای موقت بررسی می‌شود.

## یک ربات مشترک با پنل مستقل SanaShop و DeltaJanebi

این روش مخصوص حالتی است که `sanashop-bot` **از قبل روی سرور مشترک ربات فعال است** و می‌خواهید همین توکن تلگرام دو سایت را مدیریت کند. کد و Git مربوط به `deltashopsiavash/sanashop` هیچ تغییری نمی‌کند. ربات مشترک، ماژول‌های فعلی SanaShop را به‌عنوان runtime می‌خواند و فقط برای سایت‌هایی که API آنها `platform=deltajanebi` برمی‌گرداند پنل و callback اختصاصی Delta نمایش می‌دهد.

**روی سرور مشترک ربات** (نه سرور سایت‌ها):

```bash
curl -fsSL https://raw.githubusercontent.com/deltashopsiavash/deltajanebi/main/scripts/activate-unified-bot.sh | sudo bash
```

اسکریپت پیش از تغییر سرویس‌ها از دیتابیس ربات SanaShop نسخه امن می‌گیرد، از همان توکن، صاحب، دیتابیس و lock استفاده می‌کند، اتصال‌های قدیمی Delta را (اگر دیتابیس مجزای Delta با همین توکن وجود داشته باشد) بدون حذف سایت‌های SanaShop وارد می‌کند و پس از preflight فقط یک polling service را فعال می‌کند: `deltajanebi-unified-bot`. در صورت شکست فعال‌سازی، نسخه قبلی سرویس و دیتابیس را برمی‌گرداند. اجرای مجدد همین دستور روش به‌روزرسانی همین overlay است.

**مهم:** از آن پس نصب/آپدیت مستقیم ربات SanaShop روی همین سرور می‌تواند سرویس قدیمی آن را دوباره فعال و با توکن مشترک تداخل ایجاد کند؛ پس بعد از چنین آپدیتی دستور فعال‌سازی یکپارچه را مجدد اجرا کنید. هیچ‌وقت دو سرویس با یک توکن را همزمان اجرا نکنید.

بررسی نسخه فعال:

```bash
systemctl status deltajanebi-unified-bot --no-pager
```

در تلگرام، `/start` → سایت‌های متصل → بازکردن سایت؛ Delta پنل بنفش مخصوص خود و SanaShop پنل گالری و گزینه‌های خودش را خواهد داشت.

### اگر فعال‌سازی ربات مشترک پیام Missing token or numeric owner ID داد

فعال‌ساز جدید فقط از یک فایل ثابت برای خواندن توکن استفاده نمی‌کند؛ ابتدا محیط پردازش ربات‌های فعال را با دسترسی root می‌خواند، سپس فایل‌های `/etc/sanashop-bot.env`، `/etc/deltajanebi-unified-bot.env` و `/etc/deltajanebi-bot.env` را بررسی می‌کند. اگر توکن یا آیدی عددی مالک واقعاً هیچ‌جا ذخیره نشده باشد، صرفاً همان مقدار گمشده را از طریق ترمینال می‌پرسد؛ توکن در خروجی نمایش داده نمی‌شود. هیچ توکن جدیدی تولید نمی‌کند. در صورتی که دو ربات فعال با توکن‌های متفاوت شناسایی شوند، بدون توقف سرویس‌ها عملیات متوقف می‌شود.
