from django import forms
from django.contrib.auth.forms import UserCreationForm

from .iran_locations import province_choices, valid_city
from .models import Order, SiteSetting, User


class RegisterForm(UserCreationForm):
    first_name = forms.CharField(label="نام", max_length=150, required=True)
    last_name = forms.CharField(label="نام خانوادگی", max_length=150, required=True)

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "phone", "password1", "password2")

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("این ایمیل قبلاً ثبت شده است.")
        return email


class AccountProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "phone")
        labels = {
            "first_name": "نام",
            "last_name": "نام خانوادگی",
            "email": "ایمیل",
            "phone": "شماره موبایل",
        }
        widgets = {
            "first_name": forms.TextInput(attrs={"placeholder": "نام"}),
            "last_name": forms.TextInput(attrs={"placeholder": "نام خانوادگی"}),
            "email": forms.EmailInput(attrs={"placeholder": "example@email.com"}),
            "phone": forms.TextInput(attrs={"placeholder": "09xxxxxxxxx"}),
        }

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.exclude(pk=self.instance.pk).filter(email__iexact=email).exists():
            raise forms.ValidationError("این ایمیل قبلاً ثبت شده است.")
        return email


class CheckoutForm(forms.Form):
    first_name = forms.CharField(label="نام", max_length=80, widget=forms.TextInput(attrs={"autocomplete": "given-name"}))
    last_name = forms.CharField(label="نام خانوادگی", max_length=80, widget=forms.TextInput(attrs={"autocomplete": "family-name"}))
    province = forms.ChoiceField(label="استان", choices=())
    city = forms.CharField(label="شهر", max_length=80)
    address = forms.CharField(label="آدرس کامل", widget=forms.Textarea(attrs={"rows": 4, "autocomplete": "street-address"}))
    postal_code = forms.CharField(label="کد پستی", max_length=10, min_length=10, widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "postal-code", "maxlength": "10", "placeholder": "کد پستی ۱۰ رقمی"}))
    phone = forms.CharField(label="شماره همراه", max_length=30, widget=forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}))
    order_note = forms.CharField(label="یادداشت سفارش", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    use_wallet = forms.BooleanField(label="پرداخت از موجودی کیف پول", required=False)
    payment_method = forms.ChoiceField(label="روش پرداخت باقی‌مانده", choices=(), required=False)
    accept_terms = forms.BooleanField(label="قوانین و مقررات را می‌پذیرم", required=True)

    def __init__(self, *args, settings=None, wallet_balance=0, order_total=0, **kwargs):
        super().__init__(*args, **kwargs)
        self.settings = settings or SiteSetting.load()
        self.wallet_balance = max(0, int(wallet_balance or 0))
        self.order_total = max(0, int(order_total or 0))
        self.fields["province"].choices = [("", "انتخاب استان")] + province_choices()
        methods = []
        if self.settings.zarinpal_payment_enabled and self.settings.zarinpal_merchant_id:
            methods.append((Order.PAYMENT_ZARINPAL, "پرداخت با درگاه زرین‌پال"))
        if self.settings.card_payment_enabled and self.settings.card_number:
            methods.append((Order.PAYMENT_CARD, "پرداخت کارت به کارت"))
        self.fields["payment_method"].choices = methods

    def clean_phone(self):
        phone = self.cleaned_data["phone"].strip().replace(" ", "").replace("-", "")
        if len(phone) < 10 or not phone.lstrip("+").isdigit():
            raise forms.ValidationError("شماره همراه معتبر نیست.")
        return phone

    def clean_postal_code(self):
        value = (self.cleaned_data.get("postal_code") or "").strip()
        fa = "۰۱۲۳۴۵۶۷۸۹"
        ar = "٠١٢٣٤٥٦٧٨٩"
        value = value.translate(str.maketrans(fa + ar, "0123456789" * 2)).replace(" ", "").replace("-", "")
        if len(value) != 10 or not value.isdigit():
            raise forms.ValidationError("کد پستی باید دقیقاً ۱۰ رقم باشد.")
        return value

    def clean(self):
        cleaned = super().clean()
        province = cleaned.get("province")
        city = (cleaned.get("city") or "").strip()
        if province and city and not valid_city(province, city):
            self.add_error("city", "شهر انتخاب‌شده مربوط به این استان نیست.")

        use_wallet = bool(cleaned.get("use_wallet"))
        wallet_cover = min(self.wallet_balance, self.order_total) if use_wallet else 0
        remaining = max(0, self.order_total - wallet_cover)
        method = cleaned.get("payment_method") or ""
        allowed = {key for key, _ in self.fields["payment_method"].choices if key}

        if remaining > 0:
            if not method:
                self.add_error("payment_method", "برای مبلغ باقی‌مانده یک روش پرداخت انتخاب کنید.")
            elif method not in allowed:
                self.add_error("payment_method", "این روش پرداخت فعال نیست.")
        else:
            cleaned["payment_method"] = ""

        cleaned["wallet_preview_amount"] = wallet_cover
        cleaned["remaining_preview_amount"] = remaining
        return cleaned


class ReceiptUploadForm(forms.Form):
    receipt = forms.ImageField(label="تصویر رسید پرداخت")
