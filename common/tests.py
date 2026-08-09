import json

from django.contrib.auth.models import User
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse

from .audit import record
from .models import AuditLog


class SmokeTests(SimpleTestCase):
    def test_audit_log_model_exists(self):
        self.assertTrue(hasattr(AuditLog, 'user'))


class AuditLogModelTests(TestCase):
    def test_create_audit_with_user(self):
        user = User.objects.create_user("test", password="pass")
        record(user, "expense.create", 1, "午餐 ¥18")
        log = AuditLog.objects.first()
        self.assertEqual(log.user, user)
        self.assertIn("午餐", log.summary)

    def test_audit_log_no_passwords(self):
        record(None, "login.failed", None, "用户名: alice")
        log = AuditLog.objects.first()
        self.assertNotIn("password", log.summary.lower())

    def test_audit_log_truncates(self):
        record(None, "note.update", 1, "A" * 600)
        self.assertEqual(len(AuditLog.objects.first().summary), 500)


class AuditLogViewTests(TestCase):
    def test_audit_log_requires_login(self):
        response = self.client.get(reverse("my_audit_log"))
        self.assertEqual(response.status_code, 302)

    def test_audit_log_shows_own(self):
        user = User.objects.create_user("alice", password="passA")
        record(user, "expense.create", 1, "Alice 的支出")
        self.client.login(username="alice", password="passA")
        response = self.client.get(reverse("my_audit_log"))
        self.assertContains(response, "Alice 的支出")

    def test_audit_log_excludes_others(self):
        alice = User.objects.create_user("alice", password="passA")
        bob = User.objects.create_user("bob", password="passB")
        record(alice, "expense.create", 1, "Alice 的记录")
        record(bob, "task.create", 2, "Bob 的记录")
        self.client.login(username="alice", password="passA")
        response = self.client.get(reverse("my_audit_log"))
        self.assertNotContains(response, "Bob 的记录")


class NoPasswordsInLogTests(SimpleTestCase):
    def test_audit_log_fields_no_password(self):
        field_names = [f.name for f in AuditLog._meta.get_fields()]
        self.assertNotIn("password", field_names)
        self.assertNotIn("token", field_names)
        self.assertNotIn("secret", field_names)
