from django.test import TestCase
from django.urls import reverse

from .models import Category, Product


class UnicodeSlugTests(TestCase):
    def test_product_with_persian_slug_reverses_and_renders(self):
        product = Product.objects.create(
            name="کابل شارژ تایپ سی",
            price=260000,
            stock=1,
            is_active=True,
        )
        url = reverse("product_detail", args=[product.slug])
        self.assertIn("/p/", url)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_home_renders_when_product_slug_is_persian(self):
        Product.objects.create(
            name="بند گردنی و کابل شارژر موبایل",
            price=260000,
            stock=1,
            is_active=True,
        )
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_category_with_persian_slug_reverses_and_renders(self):
        category = Category.objects.create(
            name="کابل و شارژر",
            slug="کابل-و-شارژر",
            is_active=True,
        )
        url = reverse("category", args=[category.slug])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
