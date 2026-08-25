from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import User


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
    full_name = forms.CharField(label="نام و نام خانوادگی", max_length=160)
    phone = forms.CharField(label="شماره تماس", max_length=30)
    province = forms.CharField(label="استان", max_length=80)
    city = forms.CharField(label="شهر", max_length=80)
    address = forms.CharField(label="آدرس", widget=forms.Textarea(attrs={"rows": 3}))
    postal_code = forms.CharField(label="کدپستی", max_length=20, required=False)
    receipt = forms.ImageField(label="تصویر رسید پرداخت")
