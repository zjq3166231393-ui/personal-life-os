
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from .audit import record
from .models import AuditLog, NotificationLog


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


class NotificationListViewTests(TestCase):
    """回归测试：通知列表曾因「先切片 [:50] 再 .filter()」崩溃 —

    TypeError: Cannot filter a query once a slice has been taken

    修复：先从未切片的 queryset 聚合 unread_count，再切片取展示列表。
    """

    def setUp(self):
        self.user = User.objects.create_user("alice", password="passA")
        self.client.login(username="alice", password="passA")

    def _make(self, title, status, minutes=0):
        return NotificationLog.objects.create(
            user=self.user,
            title=title,
            body=title,
            status=status,
            scheduled_at=timezone.now() + timedelta(minutes=minutes),
        )

    def test_list_renders_and_counts_unread(self):
        self._make("待推送提醒", "pending", 1)
        self._make("已推送提醒", "delivered", 2)
        self._make("已读提醒", "read", 3)
        resp = self.client.get(reverse("notification_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["unread_count"], 2)
        self.assertContains(resp, "待推送提醒")

    def test_unread_count_spans_beyond_slice(self):
        """未读数必须统计全部（60 条），而不是切片后的 50 条。"""
        NotificationLog.objects.bulk_create([
            NotificationLog(
                user=self.user, title=f"通知{i}", body="", status="pending",
                scheduled_at=timezone.now(),
            )
            for i in range(60)
        ])
        resp = self.client.get(reverse("notification_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["unread_count"], 60)
        self.assertEqual(len(resp.context["notifications"]), 50)

    def test_list_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse("notification_list"))
        self.assertEqual(resp.status_code, 302)

    def test_list_excludes_others(self):
        bob = User.objects.create_user("bob", password="passB")
        NotificationLog.objects.create(
            user=bob, title="Bob 的通知", status="pending",
            scheduled_at=timezone.now(),
        )
        resp = self.client.get(reverse("notification_list"))
        self.assertNotContains(resp, "Bob 的通知")
