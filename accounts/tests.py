from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import UserProfile


class RegisterTests(TestCase):
    def test_register_page_loads(self):
        response = self.client.get(reverse("register"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "注册")

    def test_register_creates_user_and_redirects(self):
        response = self.client.post(reverse("register"), {
            "username": "testuser", "email": "test@example.com",
            "password1": "ComplexPass123!", "password2": "ComplexPass123!",
        })
        self.assertRedirects(response, reverse("home"))
        self.assertTrue(User.objects.filter(username="testuser").exists())

    def test_register_password_mismatch(self):
        response = self.client.post(reverse("register"), {
            "username": "testuser", "email": "test@example.com",
            "password1": "ComplexPass123!", "password2": "DifferentPass456!",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="testuser").exists())

    def test_register_duplicate_username(self):
        User.objects.create_user("testuser", password="SomePass123!")
        response = self.client.post(reverse("register"), {
            "username": "testuser", "email": "another@example.com",
            "password1": "ComplexPass123!", "password2": "ComplexPass123!",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(username="testuser").count(), 1)


class LoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("testuser", password="CorrectPass123!")

    def test_login_page_loads(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "登录")

    def test_login_success_redirects_home(self):
        response = self.client.post(reverse("login"), {
            "username": "testuser", "password": "CorrectPass123!",
        })
        self.assertRedirects(response, reverse("home"))

    def test_login_wrong_password(self):
        response = self.client.post(reverse("login"), {
            "username": "testuser", "password": "WrongPass456!",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "不正确")

    def test_login_nonexistent_user(self):
        response = self.client.post(reverse("login"), {
            "username": "nobody", "password": "SomePass123!",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "不正确")


class LogoutTests(TestCase):
    def test_logout_redirects_to_login(self):
        User.objects.create_user("testuser", password="CorrectPass123!")
        self.client.login(username="testuser", password="CorrectPass123!")
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("login"))


class AccessControlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("testuser", password="CorrectPass123!")

    def test_home_redirects_when_not_logged_in(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 302)

    def test_home_accessible_when_logged_in(self):
        self.client.login(username="testuser", password="CorrectPass123!")
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)

    def test_parse_api_redirects_when_not_logged_in(self):
        response = self.client.post(reverse("parse_entry"),
            {"text": "午餐 20 元"}, content_type="application/json")
        self.assertEqual(response.status_code, 302)

    def test_save_api_redirects_when_not_logged_in(self):
        response = self.client.post(reverse("save_entry"),
            {"draft": {"kind": "expense", "title": "test"}}, content_type="application/json")
        self.assertEqual(response.status_code, 302)


class UserProfileTests(TestCase):
    def test_profile_created_on_user_creation(self):
        user = User.objects.create_user("newuser", password="Pass123!")
        self.assertTrue(UserProfile.objects.filter(user=user).exists())

    def test_profile_created_on_register(self):
        self.client.post(reverse("register"), {
            "username": "freshuser", "email": "fresh@example.com",
            "password1": "ComplexPass123!", "password2": "ComplexPass123!",
        })
        user = User.objects.get(username="freshuser")
        self.assertTrue(UserProfile.objects.filter(user=user).exists())
        profile = user.profile
        self.assertEqual(profile.timezone, "Asia/Shanghai")
        self.assertEqual(profile.currency, "CNY")
        self.assertTrue(profile.ai_parsing_enabled)

    def test_profile_page_requires_login(self):
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 302)

    def test_profile_page_loads(self):
        user = User.objects.create_user("testuser", password="CorrectPass123!")
        self.client.login(username="testuser", password="CorrectPass123!")
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "个人设置")

    def test_profile_save_preferences(self):
        user = User.objects.create_user("testuser", password="CorrectPass123!")
        self.client.login(username="testuser", password="CorrectPass123!")
        response = self.client.post(reverse("profile"), {
            "display_name": "小明", "timezone": "Asia/Tokyo",
            "currency": "JPY", "monthly_budget": "5000.00",
            "ai_parsing_enabled": "on",
        })
        self.assertRedirects(response, reverse("profile"))
        profile = user.profile
        profile.refresh_from_db()
        self.assertEqual(profile.display_name, "小明")
        self.assertEqual(profile.monthly_budget, Decimal("5000.00"))

    def test_monthly_budget_is_decimal(self):
        user = User.objects.create_user("testuser", password="CorrectPass123!")
        profile = user.profile
        profile.monthly_budget = Decimal("3000.50")
        profile.save()
        profile.refresh_from_db()
        self.assertIsInstance(profile.monthly_budget, Decimal)

    def test_profile_must_be_unique_per_user(self):
        user = User.objects.create_user("testuser", password="CorrectPass123!")
        with self.assertRaises(Exception):
            UserProfile.objects.create(user=user)
