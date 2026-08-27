from django.test import TestCase
from django.urls import reverse

from .forms import RegisterForm
from .models import Banner, Category, Product, User


class ProgressiveAuthTests(TestCase):
    def test_email_check_reports_existing_and_new_accounts(self):
        User.objects.create_user(email="old@example.com", password="StrongPass123!")
        existing = self.client.get(reverse("auth_email_check"), {"email": "old@example.com"})
        fresh = self.client.get(reverse("auth_email_check"), {"email": "new@example.com"})
        self.assertEqual(existing.status_code, 200)
        self.assertTrue(existing.json()["exists"])
        self.assertFalse(fresh.json()["exists"])

    def test_email_check_rejects_invalid_email(self):
        response = self.client.get(reverse("auth_email_check"), {"email": "not-an-email"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])

    def test_registration_requires_first_and_last_name(self):
        form = RegisterForm(data={
            "email": "new@example.com",
            "password1": "VeryStrongPass123!",
            "password2": "VeryStrongPass123!",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("first_name", form.errors)
        self.assertIn("last_name", form.errors)


class CategoryBubbleTests(TestCase):
    def test_child_categories_render_as_circular_bubbles(self):
        parent = Category.objects.create(name="کابل", slug="cable")
        child = Category.objects.create(name="کابل شارژ", slug="charge-cable", parent=parent, image_url="https://example.com/cable.jpg")
        Product.objects.create(name="کابل تست", price=100000, stock=2, category=child)
        response = self.client.get(reverse("category", args=[parent.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="subcat-bubbles"')
        self.assertContains(response, 'class="subcat-circle"')
        self.assertContains(response, "کابل شارژ")

    def test_home_categories_are_circular_even_without_banner(self):
        Category.objects.create(name="جانبی رایانه", slug="computer-accessories", image_url="https://example.com/category-logo.png")
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Banner.objects.exists())
        self.assertContains(response, 'class="home-cat-circle"')
        self.assertContains(response, 'object-fit:contain!important')
        self.assertContains(response, 'grid-template-columns:repeat(3,minmax(0,1fr))')
        self.assertContains(response, 'src="https://example.com/category-logo.png"')

    def test_home_category_css_does_not_depend_on_banner_block(self):
        Category.objects.create(name="موبایل", slug="mobile")
        response = self.client.get(reverse("home"))
        html = response.content.decode("utf-8")
        self.assertIn(".home-cat-section{margin-top:30px}", html)
        self.assertNotIn('id="home-banner-carousel"', html)

    def test_global_enhancement_assets_are_loaded(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, "css/enhancements.css")
        self.assertContains(response, "js/store-enhancements.js")
