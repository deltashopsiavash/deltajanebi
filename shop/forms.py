from django import forms
from django.contrib.auth.forms import UserCreationForm

from .iran_locations import province_choices, valid_city
from .models import Order, SiteSetting, User


class RegisterForm(UserCreationForm):
    class Meta:
        model = User
        fields = ("email", "phone", "password1", "password2")


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
    postal_code = forms.CharField(label="کد پستی", max_length=10, min_length=10)
    phone = forms.CharField(label="شماره همراه", max_length=30, widget=forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}))
    order_note = forms.CharField(label="یادداشت سفارش", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    payment_method = forms.ChoiceField(label="روش پرداخت", choices=())
    accept_terms = forms.BooleanField(label="قوانین و مقررات را می‌پذیرم", required=True)

    def __init__(self, *args, settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.settings = settings or SiteSetting.load()
        self.fields["province"].choices = [("", "انتخاب استان")] + province_choices()
        methods = []
        if self.settings.zarinpal_payment_enabled and self.settings.zarinpal_merchant_id:
            methods.append((Order.PAYMENT_ZARINPAL, "پرداخت با درگاه زرین‌پال"))
        if self.settings.card_payment_enabled and self.settings.card_number:
            methods.append((Order.PAYMENT_CARD, "پرداخت کارت به کارت"))
        self.fields["payment_method"].choices = methods
        if not methods:
            self.fields["payment_method"].choices = [("", "هیچ روش پرداختی فعال نیست")]

    @staticmethod
    def _latin_digits(value):
        table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
        return str(value or "").translate(table)

    def clean_phone(self):
        phone = self._latin_digits(self.cleaned_data["phone"]).strip().replace(" ", "").replace("-", "")
        if len(phone) < 10 or not phone.lstrip("+").isdigit():
            raise forms.ValidationError("شماره همراه معتبر نیست.")
        return phone

    def clean_postal_code(self):
        postal_code = self._latin_digits(self.cleaned_data["postal_code"]).strip().replace(" ", "").replace("-", "")
        if len(postal_code) != 10 or not postal_code.isdigit():
            raise forms.ValidationError("کد پستی باید دقیقاً ۱۰ رقم باشد.")
        return postal_code

    def clean(self):
        cleaned = super().clean()
        province = cleaned.get("province")
        city = (cleaned.get("city") or "").strip()
        if province and city and not valid_city(province, city):
            self.add_error("city", "شهر انتخاب‌شده مربوط به این استان نیست.")
        method = cleaned.get("payment_method")
        allowed = {key for key, _ in self.fields["payment_method"].choices if key}
        if method and method not in allowed:
            self.add_error("payment_method", "این روش پرداخت فعال نیست.")
        return cleaned


class ReceiptUploadForm(forms.Form):
    receipt = forms.ImageField(label="تصویر رسید پرداخت")
