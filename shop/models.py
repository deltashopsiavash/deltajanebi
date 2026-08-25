from decimal import Decimal
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.urls import reverse
from django.utils.text import slugify

class UserManager(BaseUserManager):
    use_in_migrations=True
    def create_user(self,email,password=None,**extra_fields):
        if not email: raise ValueError("Email is required")
        email=self.normalize_email(email); user=self.model(email=email,**extra_fields); user.set_password(password); user.save(using=self._db); return user
    def create_superuser(self,email,password=None,**extra_fields):
        extra_fields.setdefault("is_staff",True); extra_fields.setdefault("is_superuser",True); extra_fields.setdefault("is_active",True); return self.create_user(email,password,**extra_fields)
class User(AbstractUser):
    username=None; email=models.EmailField(unique=True); phone=models.CharField(max_length=20,blank=True); USERNAME_FIELD="email"; REQUIRED_FIELDS=[]; objects=UserManager()
class Category(models.Model):
    name=models.CharField(max_length=120); slug=models.SlugField(max_length=140,unique=True,allow_unicode=True); image_url=models.URLField(blank=True); order=models.PositiveIntegerField(default=0); is_active=models.BooleanField(default=True)
    class Meta: ordering=["order","name"]
    def __str__(self): return self.name
class Product(models.Model):
    MANUAL,SYNCED="manual","synced"; MARKUP_PERCENT,MARKUP_FIXED="percent","fixed"
    SOURCE_CHOICES=[(MANUAL,"عادی"),(SYNCED,"خاص/همگام")]; MARKUP_CHOICES=[(MARKUP_PERCENT,"درصد"),(MARKUP_FIXED,"مبلغ ثابت")]
    category=models.ForeignKey(Category,null=True,blank=True,on_delete=models.SET_NULL,related_name="products"); name=models.CharField(max_length=300); slug=models.SlugField(max_length=340,unique=True,allow_unicode=True,blank=True); sku=models.CharField(max_length=80,unique=True,blank=True,null=True); description=models.TextField(blank=True); price=models.PositiveBigIntegerField(default=0,help_text="تومان"); source_price=models.PositiveBigIntegerField(default=0,help_text="تومان"); stock=models.PositiveIntegerField(default=0); is_active=models.BooleanField(default=True); image_url=models.URLField(blank=True); gallery=models.JSONField(default=list,blank=True); specs=models.JSONField(default=dict,blank=True); source_type=models.CharField(max_length=10,choices=SOURCE_CHOICES,default=MANUAL); source_url=models.URLField(blank=True); source_product_code=models.CharField(max_length=100,blank=True); markup_type=models.CharField(max_length=10,choices=MARKUP_CHOICES,default=MARKUP_PERCENT); markup_value=models.DecimalField(max_digits=12,decimal_places=2,default=0); last_synced_at=models.DateTimeField(null=True,blank=True); sync_error=models.TextField(blank=True); created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
    class Meta: ordering=["-created_at"]
    def __str__(self): return self.name
    def save(self,*args,**kwargs):
        if not self.slug:
            base=slugify(self.name,allow_unicode=True)[:280] or "product"; slug=base; i=2
            while Product.objects.exclude(pk=self.pk).filter(slug=slug).exists(): slug=f"{base}-{i}"; i+=1
            self.slug=slug
        super().save(*args,**kwargs)
    def apply_markup(self,source_price=None):
        base=Decimal(source_price if source_price is not None else self.source_price)
        value=base*(Decimal("1")+self.markup_value/Decimal("100")) if self.markup_type==self.MARKUP_PERCENT else base+self.markup_value
        return max(0,int(value.quantize(Decimal("1"))))
    def get_absolute_url(self): return reverse("product_detail",args=[self.slug])
class SiteSetting(models.Model):
    store_name=models.CharField(max_length=120,default="دلتا جانبی"); logo_url=models.URLField(blank=True); home_banner_url=models.URLField(blank=True); shipping_cost=models.PositiveBigIntegerField(default=0); phone=models.CharField(max_length=30,blank=True); card_number=models.CharField(max_length=32,blank=True); card_owner=models.CharField(max_length=120,blank=True); support_text=models.CharField(max_length=240,blank=True)
    @classmethod
    def load(cls): obj,_=cls.objects.get_or_create(pk=1); return obj
class Order(models.Model):
    STATUS=[("receipt_pending","در انتظار تایید رسید"),("preparing","در حال آماده‌سازی"),("shipped","ارسال شده"),("delivered","تحویل شده"),("cancelled","لغو شده")]
    user=models.ForeignKey(User,on_delete=models.PROTECT,related_name="orders"); status=models.CharField(max_length=30,choices=STATUS,default="receipt_pending"); full_name=models.CharField(max_length=160); phone=models.CharField(max_length=30); province=models.CharField(max_length=80); city=models.CharField(max_length=80); address=models.TextField(); postal_code=models.CharField(max_length=20,blank=True); subtotal=models.PositiveBigIntegerField(default=0); shipping_cost=models.PositiveBigIntegerField(default=0); total=models.PositiveBigIntegerField(default=0); receipt=models.ImageField(upload_to="receipts/%Y/%m/",blank=True); tracking_code=models.CharField(max_length=100,blank=True); admin_note=models.TextField(blank=True); created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
    class Meta: ordering=["-created_at"]
class OrderItem(models.Model):
    order=models.ForeignKey(Order,on_delete=models.CASCADE,related_name="items"); product=models.ForeignKey(Product,null=True,on_delete=models.SET_NULL); title=models.CharField(max_length=300); price=models.PositiveBigIntegerField(); quantity=models.PositiveIntegerField(default=1)
    @property
    def total(self): return self.price*self.quantity
