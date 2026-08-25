from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import User
class RegisterForm(UserCreationForm):
    class Meta: model=User; fields=("email","phone","password1","password2")
class CheckoutForm(forms.Form):
    full_name=forms.CharField(label="نام و نام خانوادگی",max_length=160); phone=forms.CharField(label="شماره تماس",max_length=30); province=forms.CharField(label="استان",max_length=80); city=forms.CharField(label="شهر",max_length=80); address=forms.CharField(label="آدرس",widget=forms.Textarea(attrs={"rows":3})); postal_code=forms.CharField(label="کدپستی",max_length=20,required=False); receipt=forms.ImageField(label="تصویر رسید پرداخت")
