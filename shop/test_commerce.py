from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import CheckoutForm
from .iran_locations import province_city_map
from .models import Announcement, DiscountCode, Order, OrderItem, Product, SiteSetting, User
from .services.order_workflow import expire_reservations, mark_paid


class CommerceStorefrontTests(TestCase):
    def setUp(self):
        self.available = Product.objects.create(name="محصول موجود", price=230000, stock=4, is_active=True)
        self.unavailable = Product.objects.create(name="محصول ناموجود", price=190000, stock=0, is_active=True)
        self.settings = SiteSetting.load()

    def test_newest_products_excludes_out_of_stock(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, "محصول موجود")
        self.assertNotContains(response, "محصول ناموجود")
        self.assertContains(response, "230,000")

    def test_hide_out_of_stock_toggle_affects_search(self):
        self.settings.hide_out_of_stock = False
        self.settings.save(update_fields=["hide_out_of_stock"])
        self.assertContains(self.client.get(reverse("search")), "محصول ناموجود")
        self.settings.hide_out_of_stock = True
        self.settings.save(update_fields=["hide_out_of_stock"])
        self.assertNotContains(self.client.get(reverse("search")), "محصول ناموجود")

    def test_stale_cart_entry_is_removed_and_badge_becomes_zero(self):
        session = self.client.session
        session["cart"] = {str(self.unavailable.id): 1}
        session.save()
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'class="mnav-count">0</span>')
        self.assertEqual(self.client.session.get("cart"), {})

    def test_ajax_add_cart_returns_product_and_count(self):
        response = self.client.post(
            reverse("cart_add", args=[self.available.id]),
            {"next": "/"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["added"]["name"], self.available.name)

    def test_discount_packaging_and_free_shipping_totals(self):
        self.settings.shipping_cost = 40000
        self.settings.packaging_cost = 10000
        self.settings.free_shipping_threshold = 200000
        self.settings.save(update_fields=["shipping_cost", "packaging_cost", "free_shipping_threshold"])
        DiscountCode.objects.create(code="DELTA10", discount_type=DiscountCode.PERCENT, value=10)
        session = self.client.session
        session["cart"] = {str(self.available.id): 1}
        session["discount_code"] = "DELTA10"
        session.save()
        response = self.client.get(reverse("cart"))
        self.assertContains(response, "ارسال این سفارش رایگان است")
        self.assertContains(response, "23,000")
        self.assertContains(response, "217,000")

    def test_reserved_units_are_not_available_to_another_cart(self):
        self.available.reserved_stock = 3
        self.available.save(update_fields=["reserved_stock"])
        response = self.client.post(
            reverse("cart_set", args=[self.available.id]),
            {"qty": 4},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.json()["lines"][0]["qty"], 1)


class CheckoutSettingsTests(TestCase):
    def setUp(self):
        self.settings = SiteSetting.load()

    def _enable_card_payment(self):
        self.settings.card_payment_enabled = True
        self.settings.card_number = "6037991234567890"
        self.settings.card_owner = "دلتا"
        self.settings.zarinpal_payment_enabled = False
        self.settings.save()

    def _checkout_data(self, postal_code="1234567890"):
        return {
            "first_name": "سیاوش",
            "last_name": "قادری",
            "province": "آذربایجان شرقی",
            "city": "تبریز",
            "address": "تبریز، خیابان تست، پلاک ۱",
            "postal_code": postal_code,
            "phone": "09121234567",
            "order_note": "",
            "payment_method": "card",
            "accept_terms": "1",
        }

    def test_iran_locations_contains_all_provinces_and_cities(self):
        locations = province_city_map()
        self.assertGreaterEqual(len(locations), 31)
        self.assertIn("آذربایجان شرقی", locations)
        self.assertTrue(locations["آذربایجان شرقی"])

    def test_checkout_payment_choices_follow_enabled_methods(self):
        self.settings.card_payment_enabled = True
        self.settings.card_number = "6037991234567890"
        self.settings.card_owner = "دلتا"
        self.settings.zarinpal_payment_enabled = False
        self.settings.zarinpal_merchant_id = ""
        self.settings.save()
        form = CheckoutForm(settings=self.settings)
        self.assertEqual([x[0] for x in form.fields["payment_method"].choices], ["card"])

        self.settings.card_payment_enabled = False
        self.settings.zarinpal_payment_enabled = True
        self.settings.zarinpal_merchant_id = "00000000-0000-0000-0000-000000000000"
        self.settings.save()
        form = CheckoutForm(settings=self.settings)
        self.assertEqual([x[0] for x in form.fields["payment_method"].choices], ["zarinpal"])

    def test_postal_code_is_required_and_exactly_ten_digits(self):
        self._enable_card_payment()
        data = self._checkout_data(postal_code="12345")
        form = CheckoutForm(data=data, settings=self.settings)
        self.assertFalse(form.is_valid())
        self.assertIn("postal_code", form.errors)

    def test_postal_code_accepts_persian_digits_and_normalizes_them(self):
        self._enable_card_payment()
        form = CheckoutForm(data=self._checkout_data(postal_code="۱۲۳۴۵۶۷۸۹۰"), settings=self.settings)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["postal_code"], "1234567890")

    def test_checkout_uses_custom_searchable_province_and_city_controls(self):
        self._enable_card_payment()
        user = User.objects.create_user(email="checkout@example.com", password="StrongPass123")
        product = Product.objects.create(name="محصول پرداخت", price=100000, stock=2)
        self.client.force_login(user)
        session = self.client.session
        session["cart"] = {str(product.id): 1}
        session.save()
        response = self.client.get(reverse("checkout"))
        self.assertContains(response, 'id="province-trigger"')
        self.assertContains(response, 'id="province-search"')
        self.assertContains(response, 'id="city-trigger"')
        self.assertContains(response, 'id="city-search"')
        self.assertContains(response, "کد پستی")

    def test_terms_page_uses_managed_text(self):
        self.settings.terms_text = "قوانین اختصاصی دلتا"
        self.settings.save(update_fields=["terms_text"])
        response = self.client.get(reverse("terms"))
        self.assertContains(response, "قوانین اختصاصی دلتا")


class ReservationWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="reserve@example.com", password="StrongPass123")
        self.product = Product.objects.create(name="محصول رزروی", price=100000, stock=5)

    def _order(self, qty=2, expired=False):
        order = Order.objects.create(
            user=self.user,
            full_name="کاربر تست",
            first_name="کاربر",
            last_name="تست",
            phone="09120000000",
            province="تهران",
            city="تهران",
            address="آدرس تست",
            subtotal=100000 * qty,
            total=100000 * qty,
            payment_method=Order.PAYMENT_CARD,
            reservation_expires_at=timezone.now() + (timedelta(minutes=-1) if expired else timedelta(minutes=45)),
        )
        OrderItem.objects.create(order=order, product=self.product, title=self.product.name, price=100000, quantity=qty)
        self.product.reserved_stock += qty
        self.product.save(update_fields=["reserved_stock"])
        return order

    def test_reservation_does_not_reduce_physical_stock(self):
        self._order(qty=2)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)
        self.assertEqual(self.product.reserved_stock, 2)
        self.assertEqual(self.product.available_stock, 3)

    def test_confirmed_payment_commits_stock_once(self):
        order = self._order(qty=2)
        mark_paid(order)
        self.product.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(self.product.stock, 3)
        self.assertEqual(self.product.reserved_stock, 0)
        self.assertTrue(order.stock_committed)
        self.assertEqual(order.payment_status, Order.PAY_PAID)

    def test_expired_reservation_releases_without_changing_stock(self):
        order = self._order(qty=2, expired=True)
        expired = expire_reservations()
        self.product.refresh_from_db()
        order.refresh_from_db()
        self.assertIn(order.id, expired)
        self.assertEqual(self.product.stock, 5)
        self.assertEqual(self.product.reserved_stock, 0)
        self.assertTrue(order.reservation_released)
        self.assertEqual(order.status, "cancelled")

    def test_source_stock_change_does_not_erase_reservation_counter(self):
        self.product.reserved_stock = 3
        self.product.save(update_fields=["reserved_stock"])
        self.product.stock = 1
        self.product.save(update_fields=["stock"])
        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved_stock, 3)
        self.assertEqual(self.product.available_stock, 0)


class CustomerAndNotificationTests(TestCase):
    def test_customer_code_is_generated_and_visible_in_account(self):
        user = User.objects.create_user(email="customer@example.com", password="StrongPass123", first_name="دلتا")
        self.assertEqual(user.customer_code, f"CU-{user.pk:07d}")
        self.client.force_login(user)
        response = self.client.get(reverse("account_profile"))
        self.assertContains(response, user.customer_code)

    def test_announcement_badge_is_unread_until_opened(self):
        user = User.objects.create_user(email="notice@example.com", password="StrongPass123")
        Announcement.objects.create(text="اطلاعیه تست")
        self.client.force_login(user)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.context["unread_announcement_count"], 1)
        self.assertContains(response, "اطلاعیه تست")
        read = self.client.post(reverse("notifications_mark_read"), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(read.status_code, 200)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.context["unread_announcement_count"], 0)

    def test_ajax_bad_password_returns_json_instead_of_rendering_login_page(self):
        User.objects.create_user(email="login@example.com", password="CorrectPass123")
        response = self.client.post(
            reverse("auth_login_ajax"),
            {"email": "login@example.com", "password": "wrong", "next": "/"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])


class PaymentBotSmokeTests(TestCase):
    def test_bot_v11_and_payment_menus_import(self):
        from .management.commands import telegram_bot_v9, telegram_bot_v10, telegram_bot_v11
        from .services.payments import request_zarinpal_payment, verify_zarinpal_payment

        site = SiteSetting.load()
        labels = [button.text for row in telegram_bot_v11.main_menu().inline_keyboard for button in row]
        self.assertIn("👥 کاربران", labels)
        self.assertIn("🔔 اطلاع‌رسانی", labels)
        settings_labels = [button.text for row in telegram_bot_v11.settings_menu().inline_keyboard for button in row]
        self.assertIn("✨ متن بالا", settings_labels)
        commerce_labels = [button.text for row in telegram_bot_v9.commerce_menu(site).inline_keyboard for button in row]
        self.assertIn("🎟 کدهای تخفیف", commerce_labels)
        self.assertTrue(callable(telegram_bot_v10.show_commerce))
        self.assertTrue(callable(request_zarinpal_payment))
        self.assertTrue(callable(verify_zarinpal_payment))
