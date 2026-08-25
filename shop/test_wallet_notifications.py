from django.test import TestCase
from django.urls import reverse

from shop.management.commands import telegram_bot_v12
from shop.models import Announcement, User
from shop.services.wallet import adjust_wallet, wallet_balance, wallet_history


class WalletAndCustomerCodeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="wallet@example.com",
            password="StrongPass123!",
            first_name="علی",
            last_name="احمدی",
        )

    def test_customer_code_is_short(self):
        self.user.refresh_from_db()
        self.assertEqual(self.user.customer_code, f"#{1000 + self.user.pk}")
        self.assertTrue(self.user.customer_code.startswith("#1"))

    def test_wallet_adjustment_and_history(self):
        self.assertEqual(wallet_balance(self.user.pk), 0)
        balance = adjust_wallet(self.user.pk, 250000, "برگشت وجه سفارش", "12345")
        self.assertEqual(balance, 250000)
        self.assertEqual(wallet_balance(self.user.pk), 250000)
        balance = adjust_wallet(self.user.pk, -50000, "اصلاح اعتبار", "12345")
        self.assertEqual(balance, 200000)
        rows = wallet_history(self.user.pk)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["balance_after"], 200000)
        self.assertEqual(rows[1]["reason"], "برگشت وجه سفارش")

    def test_wallet_cannot_go_negative(self):
        with self.assertRaises(ValueError):
            adjust_wallet(self.user.pk, -1, "کاهش اشتباه", "12345")
        self.assertEqual(wallet_balance(self.user.pk), 0)

    def test_account_profile_shows_wallet(self):
        adjust_wallet(self.user.pk, 125000, "هدیه", "admin")
        self.client.force_login(self.user)
        response = self.client.get(reverse("account_profile"))
        self.assertContains(response, "کیف پول")
        self.assertContains(response, "125,000")
        self.assertContains(response, self.user.customer_code)

    def test_bot_user_keyboard_contains_wallet_actions(self):
        labels = [button.text for row in telegram_bot_v12._user_keyboard(self.user.pk).inline_keyboard for button in row]
        self.assertIn("➕ افزایش موجودی", labels)
        self.assertIn("➖ کاهش موجودی", labels)
        self.assertIn("📜 تراکنش‌های کیف پول", labels)


class GuestAnnouncementTests(TestCase):
    def setUp(self):
        Announcement.objects.create(text="اطلاعیه تست", is_active=True)

    def test_guest_gets_notification_drawer_and_guest_config(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'id="notification-backdrop"')
        self.assertContains(response, 'data-auth="0"')
        self.assertContains(response, "اطلاعیه تست")

    def test_registered_user_gets_notification_drawer(self):
        user = User.objects.create_user(email="member@example.com", password="StrongPass123!")
        self.client.force_login(user)
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'id="notification-backdrop"')
        self.assertContains(response, 'data-auth="1"')
