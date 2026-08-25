from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from shop.models import Order, Product, SiteSetting, User
from shop.services.order_workflow import expire_reservations, order_report_text
from shop.services.wallet import adjust_wallet, external_payable, order_wallet_info, refund_order_wallet, wallet_balance


class WalletCheckoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="buyer-wallet@example.com",
            password="StrongPass123!",
            first_name="علی",
            last_name="خریدار",
        )
        self.product = Product.objects.create(name="محصول کیف پول", price=500000, stock=5, is_active=True)
        self.site = SiteSetting.load()
        self.site.card_payment_enabled = True
        self.site.card_number = "6037991234567890"
        self.site.card_owner = "دلتا جانبی"
        self.site.zarinpal_payment_enabled = False
        self.site.shipping_cost = 0
        self.site.packaging_cost = 0
        self.site.save()
        self.client.force_login(self.user)

    def _cart(self):
        session = self.client.session
        session["cart"] = {str(self.product.id): 1}
        session.save()

    def _checkout_data(self, use_wallet=True, method="card"):
        data = {
            "first_name": "علی",
            "last_name": "خریدار",
            "province": "تهران",
            "city": "تهران",
            "address": "تهران، خیابان تست، پلاک ۱",
            "postal_code": "1234567890",
            "phone": "09121234567",
            "order_note": "",
            "accept_terms": "1",
        }
        if use_wallet:
            data["use_wallet"] = "1"
        if method:
            data["payment_method"] = method
        return data

    def test_partial_wallet_pays_rest_by_card(self):
        adjust_wallet(self.user.id, 300000, "شارژ تست", "test")
        self._cart()
        response = self.client.post(reverse("checkout"), self._checkout_data(), follow=False)
        order = Order.objects.get(user=self.user)
        info = order_wallet_info(order.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("card_payment", args=[order.id]))
        self.assertEqual(info["wallet_amount"], 300000)
        self.assertEqual(info["balance_before"], 300000)
        self.assertEqual(info["balance_after"], 0)
        self.assertEqual(wallet_balance(self.user.id), 0)
        self.assertEqual(external_payable(order), 200000)
        self.assertEqual(order.payment_status, Order.PAY_PENDING)
        report = order_report_text(order, "گزارش تست")
        self.assertIn("سهم کیف پول: 300,000 تومان", report)
        self.assertIn("سهم پرداخت بیرونی: 200,000 تومان", report)
        self.assertIn("ترکیبی: کیف پول + کارت به کارت", report)

    def test_full_wallet_completes_order_without_external_method(self):
        self.site.card_payment_enabled = False
        self.site.card_number = ""
        self.site.save(update_fields=["card_payment_enabled", "card_number"])
        adjust_wallet(self.user.id, 600000, "شارژ تست", "test")
        self._cart()
        response = self.client.post(reverse("checkout"), self._checkout_data(method=""), follow=False)
        order = Order.objects.get(user=self.user)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("account_order_detail", args=[order.id]))
        self.assertEqual(order.payment_status, Order.PAY_PAID)
        self.assertEqual(order.status, "preparing")
        self.assertEqual(external_payable(order), 0)
        self.assertEqual(order_wallet_info(order.id)["wallet_amount"], 500000)
        self.assertEqual(wallet_balance(self.user.id), 100000)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 4)
        self.assertEqual(self.product.reserved_stock, 0)

    def test_wallet_refund_is_exactly_once(self):
        adjust_wallet(self.user.id, 300000, "شارژ تست", "test")
        self._cart()
        self.client.post(reverse("checkout"), self._checkout_data(), follow=False)
        order = Order.objects.get(user=self.user)
        self.assertEqual(wallet_balance(self.user.id), 0)
        self.assertTrue(refund_order_wallet(order, "لغو تست"))
        self.assertEqual(wallet_balance(self.user.id), 300000)
        self.assertFalse(refund_order_wallet(order, "لغو دوباره"))
        self.assertEqual(wallet_balance(self.user.id), 300000)
        self.assertTrue(order_wallet_info(order.id)["refunded"])

    def test_expired_unpaid_order_refunds_wallet(self):
        adjust_wallet(self.user.id, 300000, "شارژ تست", "test")
        self._cart()
        self.client.post(reverse("checkout"), self._checkout_data(), follow=False)
        order = Order.objects.get(user=self.user)
        order.reservation_expires_at = timezone.now() - timedelta(minutes=1)
        order.save(update_fields=["reservation_expires_at"])
        expired = expire_reservations()
        order.refresh_from_db()
        self.assertIn(order.id, expired)
        self.assertEqual(wallet_balance(self.user.id), 300000)
        self.assertTrue(order_wallet_info(order.id)["refunded"])
        self.assertEqual(order.status, "cancelled")

    def test_checkout_page_shows_wallet_checkbox_and_balance(self):
        adjust_wallet(self.user.id, 300000, "شارژ تست", "test")
        self._cart()
        response = self.client.get(reverse("checkout"))
        self.assertContains(response, "پرداخت از موجودی کیف پول")
        self.assertContains(response, "300,000 تومان")
        self.assertContains(response, 'id="use-wallet"')
