from decimal import Decimal

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

URL_MAX_LENGTH = 4096


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20, blank=True)
    customer_code = models.CharField(max_length=20, unique=True, blank=True, null=True, db_index=True)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)
        if self.pk and not self.customer_code:
            code = f"CU-{self.pk:07d}"
            User.objects.filter(pk=self.pk, customer_code__isnull=True).update(customer_code=code)
            User.objects.filter(pk=self.pk, customer_code="").update(customer_code=code)
            self.customer_code = code


class Category(models.Model):
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, allow_unicode=True)
    image_url = models.URLField(max_length=URL_MAX_LENGTH, blank=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name, allow_unicode=True)[:110] or "category"
            slug = base
            i = 2
            while Category.objects.exclude(pk=self.pk).filter(slug=slug).exists():
                slug = f"{base}-{i}"
                i += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def ancestor_chain(self):
        chain, seen, current = [], set(), self
        while current and current.pk not in seen:
            seen.add(current.pk)
            chain.append(current)
            current = current.parent
        return list(reversed(chain))

    def descendant_ids(self):
        ids, frontier = [self.pk], [self.pk]
        while frontier:
            child_ids = list(Category.objects.filter(parent_id__in=frontier, is_active=True).values_list("id", flat=True))
            child_ids = [pk for pk in child_ids if pk not in ids]
            if not child_ids:
                break
            ids.extend(child_ids)
            frontier = child_ids
        return ids


class Product(models.Model):
    MANUAL, SYNCED = "manual", "synced"
    MARKUP_PERCENT, MARKUP_FIXED = "percent", "fixed"
    SOURCE_CHOICES = [(MANUAL, "عادی"), (SYNCED, "خاص/همگام")]
    MARKUP_CHOICES = [(MARKUP_PERCENT, "درصد"), (MARKUP_FIXED, "مبلغ ثابت")]

    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    public_code = models.CharField(max_length=24, unique=True, blank=True, null=True, db_index=True)
    name = models.CharField(max_length=300)
    slug = models.SlugField(max_length=340, unique=True, allow_unicode=True, blank=True)
    sku = models.CharField(max_length=80, unique=True, blank=True, null=True)
    description = models.TextField(blank=True)
    price = models.PositiveBigIntegerField(default=0, help_text="قیمت پایه فروش به تومان")
    source_price = models.PositiveBigIntegerField(default=0, help_text="تومان")
    stock = models.PositiveIntegerField(default=0)
    reserved_stock = models.PositiveIntegerField(default=0, help_text="تعداد رزرو موقت سفارش‌های پرداخت‌نشده")
    is_active = models.BooleanField(default=True)
    image = models.ImageField(upload_to="products/%Y/%m/", blank=True)
    image_url = models.URLField(max_length=URL_MAX_LENGTH, blank=True)
    manual_image_url_override = models.URLField(max_length=URL_MAX_LENGTH, blank=True)
    gallery = models.JSONField(default=list, blank=True)
    specs = models.JSONField(default=dict, blank=True)
    source_type = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=MANUAL)
    source_url = models.URLField(max_length=URL_MAX_LENGTH, blank=True)
    source_product_code = models.CharField(max_length=100, blank=True)
    markup_type = models.CharField(max_length=10, choices=MARKUP_CHOICES, default=MARKUP_PERCENT)
    markup_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    manual_name_override = models.CharField(max_length=300, blank=True)
    manual_price_override = models.PositiveBigIntegerField(null=True, blank=True)
    manual_stock_override = models.PositiveIntegerField(null=True, blank=True)
    sale_price = models.PositiveBigIntegerField(null=True, blank=True)
    sale_starts_at = models.DateTimeField(null=True, blank=True)
    sale_ends_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    sync_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.public_code or '-'} | {self.name}"

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if self.manual_name_override:
            self.name = self.manual_name_override
        if self.manual_price_override is not None:
            self.price = self.manual_price_override
        if self.manual_stock_override is not None:
            if update_fields and "stock" in update_fields and "manual_stock_override" not in update_fields:
                self.manual_stock_override = self.stock
                kwargs["update_fields"] = list(set(update_fields) | {"manual_stock_override"})
            else:
                self.stock = self.manual_stock_override
        if self.reserved_stock > self.stock:
            self.reserved_stock = self.stock
            if update_fields:
                kwargs["update_fields"] = list(set(kwargs["update_fields"]) | {"reserved_stock"})
        if not self.slug:
            base = slugify(self.name, allow_unicode=True)[:280] or "product"
            slug = base
            i = 2
            while Product.objects.exclude(pk=self.pk).filter(slug=slug).exists():
                slug = f"{base}-{i}"
                i += 1
            self.slug = slug
        super().save(*args, **kwargs)
        if not self.public_code and self.pk:
            self.public_code = f"DJ-{self.pk:06d}"
            Product.objects.filter(pk=self.pk, public_code__isnull=True).update(public_code=self.public_code)
            Product.objects.filter(pk=self.pk, public_code="").update(public_code=self.public_code)
        image = self.primary_image
        if self.category_id and image:
            current, seen = self.category, set()
            while current and current.pk not in seen:
                seen.add(current.pk)
                if not current.image_url:
                    Category.objects.filter(pk=current.pk, image_url="").update(image_url=image)
                    current.image_url = image
                current = current.parent

    @property
    def available_stock(self):
        return max(0, int(self.stock or 0) - int(self.reserved_stock or 0))

    @property
    def primary_image(self):
        if self.image:
            try:
                return self.image.url
            except ValueError:
                pass
        return self.manual_image_url_override or self.image_url

    def gallery_images(self):
        images = []
        for value in [self.primary_image, *(self.gallery or [])]:
            value = str(value or "").strip()
            if value and value not in images:
                images.append(value)
        return images[:12]

    def apply_markup(self, source_price=None):
        base = Decimal(source_price if source_price is not None else self.source_price)
        if self.markup_type == self.MARKUP_PERCENT:
            value = base * (Decimal("1") + self.markup_value / Decimal("100"))
        else:
            value = base + self.markup_value
        return max(0, int(value.quantize(Decimal("1"))))

    @property
    def is_sale_active(self):
        if self.sale_price is None or self.sale_price >= self.price:
            return False
        now = timezone.now()
        if self.sale_starts_at and now < self.sale_starts_at:
            return False
        if self.sale_ends_at and now >= self.sale_ends_at:
            return False
        return True

    @property
    def effective_price(self):
        return self.sale_price if self.is_sale_active else self.price

    @property
    def sale_remaining_seconds(self):
        if not self.is_sale_active or not self.sale_ends_at:
            return 0
        return max(0, int((self.sale_ends_at - timezone.now()).total_seconds()))

    def clear_sale(self):
        self.sale_price = None
        self.sale_starts_at = None
        self.sale_ends_at = None
        self.save(update_fields=["sale_price", "sale_starts_at", "sale_ends_at"])

    def get_absolute_url(self):
        return reverse("product_detail", args=[self.slug])


class SiteSetting(models.Model):
    store_name = models.CharField(max_length=120, default="دلتا جانبی")
    logo = models.ImageField(upload_to="site/logo/", blank=True)
    logo_url = models.URLField(max_length=URL_MAX_LENGTH, blank=True)
    home_banner_url = models.URLField(max_length=URL_MAX_LENGTH, blank=True)
    top_bar_text = models.CharField(max_length=240, default="خرید آنلاین • قیمت و موجودی به‌روز • ارسال مطمئن")
    shipping_cost = models.PositiveBigIntegerField(default=0)
    packaging_cost = models.PositiveBigIntegerField(default=0)
    free_shipping_threshold = models.PositiveBigIntegerField(default=0, help_text="صفر یعنی غیرفعال")
    hide_out_of_stock = models.BooleanField(default=False)
    phone = models.CharField(max_length=30, blank=True)
    footer_phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    contact_email = models.EmailField(blank=True)
    footer_description = models.TextField(blank=True)
    card_number = models.CharField(max_length=32, blank=True)
    card_owner = models.CharField(max_length=120, blank=True)
    card_payment_enabled = models.BooleanField(default=True)
    zarinpal_payment_enabled = models.BooleanField(default=False)
    zarinpal_merchant_id = models.CharField(max_length=64, blank=True)
    terms_text = models.TextField(blank=True)
    support_text = models.CharField(max_length=240, blank=True)

    @property
    def logo_src(self):
        if self.logo:
            try:
                return self.logo.url
            except ValueError:
                pass
        return self.logo_url

    def shipping_for(self, subtotal):
        if self.free_shipping_threshold and subtotal >= self.free_shipping_threshold:
            return 0
        return self.shipping_cost

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class SourceSite(models.Model):
    MARKUP_PERCENT, MARKUP_FIXED = "percent", "fixed"
    MARKUP_CHOICES = [(MARKUP_PERCENT, "درصد"), (MARKUP_FIXED, "مبلغ ثابت")]

    name = models.CharField(max_length=120)
    base_url = models.URLField(max_length=URL_MAX_LENGTH)
    hostname = models.CharField(max_length=255, unique=True, db_index=True)
    brand_terms = models.CharField(max_length=500, blank=True, help_text="عبارت‌های برند برای پاک‌سازی متن، جداشده با کاما")
    is_active = models.BooleanField(default=True)
    bulk_import_enabled = models.BooleanField(default=False, help_text="در همگام‌سازی همه، کل کاتالوگ قابل کشف این سایت وارد شود")
    default_markup_type = models.CharField(max_length=10, choices=MARKUP_CHOICES, default=MARKUP_PERCENT)
    default_markup_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    last_bulk_sync_at = models.DateTimeField(null=True, blank=True)
    last_discovered_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return f"{self.name} ({self.hostname})"

    def markup_label(self):
        value = f"{self.default_markup_value:g}"
        return f"{value}%" if self.default_markup_type == self.MARKUP_PERCENT else f"{value} تومان"


class SocialLink(models.Model):
    PLATFORM_CHOICES = [
        ("instagram", "اینستاگرام"),
        ("telegram", "تلگرام"),
        ("whatsapp", "واتساپ"),
        ("rubika", "روبیکا"),
        ("eitaa", "ایتا"),
        ("youtube", "یوتیوب"),
        ("aparat", "آپارات"),
        ("x", "ایکس"),
        ("facebook", "فیسبوک"),
        ("other", "سایر"),
    ]
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES, default="other")
    label = models.CharField(max_length=80)
    url = models.URLField(max_length=URL_MAX_LENGTH)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.label


class TrustBadge(models.Model):
    ENAMAD, ZARINPAL = "enamad", "zarinpal"
    TYPE_CHOICES = [(ENAMAD, "اینماد"), (ZARINPAL, "زرین‌پال")]
    badge_type = models.CharField(max_length=20, choices=TYPE_CHOICES, unique=True)
    image = models.ImageField(upload_to="site/trust/%Y/%m/", blank=True)
    image_url = models.URLField(max_length=URL_MAX_LENGTH, blank=True)
    target_url = models.URLField(max_length=URL_MAX_LENGTH, blank=True)
    is_active = models.BooleanField(default=True)

    @property
    def image_src(self):
        if self.image:
            try:
                return self.image.url
            except ValueError:
                pass
        return self.image_url

    def __str__(self):
        return self.get_badge_type_display()


class Banner(models.Model):
    title = models.CharField(max_length=160, blank=True)
    image = models.ImageField(upload_to="site/banners/%Y/%m/", blank=True)
    image_url = models.URLField(max_length=URL_MAX_LENGTH, blank=True)
    mobile_image = models.ImageField(upload_to="site/banners/mobile/%Y/%m/", blank=True)
    mobile_image_url = models.URLField(max_length=URL_MAX_LENGTH, blank=True)
    target_url = models.URLField(max_length=URL_MAX_LENGTH, blank=True)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "-id"]

    @property
    def image_src(self):
        if self.image:
            try:
                return self.image.url
            except ValueError:
                pass
        return self.image_url

    @property
    def mobile_image_src(self):
        if self.mobile_image:
            try:
                return self.mobile_image.url
            except ValueError:
                pass
        return self.mobile_image_url or self.image_src

    def __str__(self):
        return self.title or f"بنر #{self.pk}"


class DiscountCode(models.Model):
    PERCENT, FIXED = "percent", "fixed"
    TYPE_CHOICES = [(PERCENT, "درصدی"), (FIXED, "مبلغ ثابت")]

    code = models.CharField(max_length=60, unique=True, db_index=True)
    discount_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=PERCENT)
    value = models.PositiveBigIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        self.code = (self.code or "").strip().upper()
        super().save(*args, **kwargs)

    @property
    def is_valid_now(self):
        now = timezone.now()
        if not self.is_active:
            return False
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now >= self.ends_at:
            return False
        return True

    def calculate(self, subtotal):
        if not self.is_valid_now:
            return 0
        if self.discount_type == self.PERCENT:
            return min(subtotal, int(Decimal(subtotal) * Decimal(self.value) / Decimal(100)))
        return min(subtotal, int(self.value))

    def __str__(self):
        return self.code


class Order(models.Model):
    PAYMENT_CARD, PAYMENT_ZARINPAL = "card", "zarinpal"
    PAYMENT_METHODS = [(PAYMENT_CARD, "کارت به کارت"), (PAYMENT_ZARINPAL, "درگاه زرین‌پال")]
    PAY_PENDING, PAY_RECEIPT, PAY_PAID, PAY_REJECTED, PAY_FAILED = "pending", "receipt", "paid", "rejected", "failed"
    PAYMENT_STATUS = [
        (PAY_PENDING, "در انتظار پرداخت"),
        (PAY_RECEIPT, "رسید ارسال شده"),
        (PAY_PAID, "پرداخت موفق"),
        (PAY_REJECTED, "پرداخت رد شده"),
        (PAY_FAILED, "پرداخت ناموفق"),
    ]
    STATUS = [
        ("payment_pending", "در انتظار پرداخت"),
        ("receipt_pending", "در انتظار تایید رسید"),
        ("payment_rejected", "پرداخت رد شده"),
        ("preparing", "در حال آماده‌سازی"),
        ("shipped", "ارسال شده"),
        ("delivered", "تحویل شده"),
        ("cancelled", "لغو شده"),
    ]
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=30, choices=STATUS, default="payment_pending")
    first_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80, blank=True)
    full_name = models.CharField(max_length=160)
    phone = models.CharField(max_length=30)
    province = models.CharField(max_length=80)
    city = models.CharField(max_length=80)
    address = models.TextField()
    postal_code = models.CharField(max_length=20, blank=True)
    order_note = models.TextField(blank=True)
    subtotal = models.PositiveBigIntegerField(default=0)
    discount_code = models.CharField(max_length=60, blank=True)
    discount_amount = models.PositiveBigIntegerField(default=0)
    packaging_cost = models.PositiveBigIntegerField(default=0)
    shipping_cost = models.PositiveBigIntegerField(default=0)
    total = models.PositiveBigIntegerField(default=0)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default=PAYMENT_CARD)
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS, default=PAY_PENDING)
    receipt = models.ImageField(upload_to="receipts/%Y/%m/", blank=True)
    receipt_rejection_reason = models.TextField(blank=True)
    zarinpal_authority = models.CharField(max_length=80, blank=True, db_index=True)
    zarinpal_ref_id = models.CharField(max_length=80, blank=True)
    zarinpal_card_pan = models.CharField(max_length=40, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    reservation_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    stock_committed = models.BooleanField(default=False)
    reservation_released = models.BooleanField(default=False)
    tracking_code = models.CharField(max_length=100, blank=True)
    admin_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def payment_label(self):
        return self.get_payment_method_display()

    @property
    def reservation_active(self):
        return bool(
            self.reservation_expires_at
            and timezone.now() < self.reservation_expires_at
            and not self.stock_committed
            and not self.reservation_released
            and self.status not in ("cancelled", "payment_rejected")
        )

    @property
    def reservation_remaining_seconds(self):
        if not self.reservation_active:
            return 0
        return max(0, int((self.reservation_expires_at - timezone.now()).total_seconds()))

    @property
    def card_last4(self):
        digits = "".join(ch for ch in (self.zarinpal_card_pan or "") if ch.isdigit())
        return digits[-4:] if len(digits) >= 4 else ""


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, null=True, on_delete=models.SET_NULL)
    title = models.CharField(max_length=300)
    price = models.PositiveBigIntegerField()
    quantity = models.PositiveIntegerField(default=1)

    @property
    def total(self):
        return self.price * self.quantity


class Announcement(models.Model):
    text = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.text[:60]


class AnnouncementRead(models.Model):
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="reads")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="announcement_reads")
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["announcement", "user"], name="unique_announcement_read"),
        ]
