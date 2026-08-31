"""邮件通知与 Web Push 订阅的失败处理测试。

覆盖 ``docs/v1-release-audit.md``「缺失覆盖」中的两项：

* **通知推送失败处理** —— 订阅管理的异常路径（无效 JSON / 缺 endpoint /
  重复订阅幂等 / 用户隔离）
* **邮件发送重试边界** —— 失败计数、错误截断、失败不抛异常、隐私契约

说明：本项目目前**没有实现 Web Push 的实际投递**（无 pywebpush / 发送代码），
只有订阅存储与 VAPID 公钥接口。因此这里覆盖的是**订阅层**的失败处理，
端到端投递需浏览器环境，不在本次范围内。
"""

import json
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from common.email_util import send_notification_email
from common.models import NotificationLog, PushSubscription
from life.management.commands.scan_reminders import Command as ScanRemindersCommand


def _json_post(client, url, payload):
    """POST 一个 JSON body；payload 非字符串时序列化。"""
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return client.post(url, data=data, content_type="application/json")


class SendNotificationEmailTests(TestCase):
    """``common.email_util.send_notification_email`` 的边界行为。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="mailer", password="pw12345678", email="mailer@example.com"
        )
        self.user.profile.email_notifications = True
        self.user.profile.email_important_only = False
        self.user.profile.save()

    def test_no_email_address(self):
        user = User.objects.create_user(username="noaddr", password="pw12345678", email="")
        ok, err = send_notification_email(user, "标题", "正文")
        self.assertFalse(ok)
        self.assertEqual(err, "No email address.")

    def test_disabled_by_profile(self):
        self.user.profile.email_notifications = False
        self.user.profile.save()
        ok, err = send_notification_email(self.user, "标题", "正文")
        self.assertFalse(ok)
        self.assertEqual(err, "Email notifications disabled.")

    def test_missing_profile_treated_as_disabled(self):
        """profile 缺失时不应抛异常，按「未启用」处理。"""
        self.user.profile.delete()
        user = User.objects.get(pk=self.user.pk)
        ok, err = send_notification_email(user, "标题", "正文")
        self.assertFalse(ok)
        self.assertEqual(err, "Email notifications disabled.")

    @mock.patch("common.email_util.send_mail")
    def test_success(self, mocked):
        ok, err = send_notification_email(self.user, "提醒标题", "提醒正文")
        self.assertTrue(ok)
        self.assertEqual(err, "")
        mocked.assert_called_once()
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["recipient_list"], ["mailer@example.com"])
        self.assertEqual(kwargs["subject"], "[Life OS] 提醒标题")
        self.assertIn("提醒正文", kwargs["message"])

    @mock.patch("common.email_util.send_mail", side_effect=RuntimeError("SMTP down"))
    def test_failure_returns_error_instead_of_raising(self, mocked):
        """发送失败必须返回 (False, err)，绝不能把异常抛给调用方。"""
        ok, err = send_notification_email(self.user, "标题", "正文")
        self.assertFalse(ok)
        self.assertEqual(err, "SMTP down")

    @mock.patch("common.email_util.send_mail", side_effect=RuntimeError("E" * 900))
    def test_error_truncated_to_500(self, mocked):
        ok, err = send_notification_email(self.user, "标题", "正文")
        self.assertFalse(ok)
        self.assertEqual(len(err), 500)


class TryEmailRetryBoundaryTests(TestCase):
    """``scan_reminders.Command._try_email`` 的重试 / 失败计数边界。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="reminded", password="pw12345678", email="reminded@example.com"
        )
        self.user.profile.email_notifications = True
        self.user.profile.email_important_only = False
        self.user.profile.save()
        self.cmd = ScanRemindersCommand()

    def _notification(self, **kwargs):
        defaults = {
            "user": self.user,
            "title": "提醒",
            "body": "正文",
            "status": NotificationLog.Status.PENDING,
        }
        defaults.update(kwargs)
        return NotificationLog.objects.create(**defaults)

    @mock.patch("common.email_util.send_mail")
    def test_success_marks_delivered(self, mocked):
        n = self._notification()
        self.cmd._try_email(n, self.user, "提醒")
        n.refresh_from_db()
        self.assertEqual(n.status, NotificationLog.Status.DELIVERED)
        self.assertIsNotNone(n.delivered_at)

    @mock.patch("common.email_util.send_mail", side_effect=RuntimeError("boom"))
    def test_failure_increments_retry_count(self, mocked):
        n = self._notification()
        self.cmd._try_email(n, self.user, "提醒")
        n.refresh_from_db()
        self.assertEqual(n.email_retry_count, 1)
        self.assertEqual(n.email_last_error, "boom")
        self.assertEqual(n.status, NotificationLog.Status.PENDING)  # 未误标已送达

    @mock.patch("common.email_util.send_mail", side_effect=RuntimeError("boom"))
    def test_retry_count_accumulates(self, mocked):
        n = self._notification()
        for _ in range(3):
            self.cmd._try_email(n, self.user, "提醒")
        n.refresh_from_db()
        self.assertEqual(n.email_retry_count, 3)

    @mock.patch("common.email_util.send_mail", side_effect=RuntimeError("E" * 900))
    def test_last_error_truncated_to_500(self, mocked):
        n = self._notification()
        self.cmd._try_email(n, self.user, "提醒")
        n.refresh_from_db()
        self.assertEqual(len(n.email_last_error), 500)

    @mock.patch("common.email_util.send_mail")
    def test_skipped_when_email_disabled(self, mocked):
        self.user.profile.email_notifications = False
        self.user.profile.save()
        n = self._notification()
        self.cmd._try_email(n, self.user, "提醒")
        mocked.assert_not_called()
        n.refresh_from_db()
        self.assertEqual(n.email_retry_count, 0)
        self.assertEqual(n.status, NotificationLog.Status.PENDING)

    @mock.patch("common.email_util.send_mail")
    def test_important_only_gating(self, mocked):
        """email_important_only=True 时，非重要提醒不发信。"""
        self.user.profile.email_important_only = True
        self.user.profile.save()

        n = self._notification()
        self.cmd._try_email(n, self.user, "普通提醒", important_only=False)
        mocked.assert_not_called()

        self.cmd._try_email(n, self.user, "重要提醒", important_only=True)
        mocked.assert_called_once()

    @mock.patch("common.email_util.send_mail")
    def test_email_body_never_leaks_category(self, mocked):
        """隐私契约（docs/privacy-and-data.md）：邮件不得包含分类细节。

        站内通知 body 含分类名是允许的，但**邮件**正文必须保持泛化。
        """
        n = self._notification(
            title="账单到期: 房租", body="每月5日 · 住房"
        )
        self.cmd._try_email(n, self.user, "账单到期")
        message = mocked.call_args.kwargs["message"]
        self.assertNotIn("住房", message)
        self.assertNotIn("每月5日", message)


class PushSubscribeTests(TestCase):
    """``/common/push/subscribe/`` 的失败处理与幂等性。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pusher", password="pw12345678")
        self.client.force_login(self.user)
        self.url = reverse("push_subscribe")

    def test_requires_login(self):
        self.client.logout()
        resp = _json_post(self.client, self.url, {"endpoint": "https://e/1"})
        self.assertEqual(resp.status_code, 302)  # 重定向到登录

    def test_get_not_allowed(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_invalid_json_returns_400(self):
        resp = _json_post(self.client, self.url, "{not json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(PushSubscription.objects.count(), 0)

    def test_missing_endpoint_returns_400(self):
        resp = _json_post(self.client, self.url, {"keys": {"p256dh": "a", "auth": "b"}})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(PushSubscription.objects.count(), 0)

    def test_creates_subscription(self):
        resp = _json_post(
            self.client,
            self.url,
            {"endpoint": "https://push.example/abc", "keys": {"p256dh": "dh", "auth": "au"}},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True})
        sub = PushSubscription.objects.get()
        self.assertEqual(sub.user, self.user)
        self.assertEqual(sub.endpoint, "https://push.example/abc")
        self.assertEqual(sub.p256dh, "dh")
        self.assertEqual(sub.auth, "au")
        self.assertTrue(sub.is_active)

    def test_re_subscribe_is_idempotent(self):
        """同一 endpoint 重复订阅应更新而非新建（endpoint 有 unique 约束）。"""
        payload = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "dh"}}
        for _ in range(2):
            resp = _json_post(self.client, self.url, payload)
            self.assertEqual(resp.status_code, 200)
        self.assertEqual(PushSubscription.objects.count(), 1)

    def test_re_subscribe_reactivates_and_rebinds_user(self):
        """重新订阅应把 is_active 复位为 True。"""
        sub = PushSubscription.objects.create(
            user=self.user, endpoint="https://push.example/abc",
            p256dh="x", auth="y", is_active=False,
        )
        _json_post(self.client, self.url, {"endpoint": sub.endpoint, "keys": {"p256dh": "z"}})
        sub.refresh_from_db()
        self.assertTrue(sub.is_active)
        self.assertEqual(sub.p256dh, "z")


class PushUnsubscribeTests(TestCase):
    """``/common/push/unsubscribe/`` 的失败处理。"""

    def setUp(self):
        self.user = User.objects.create_user(username="unsub", password="pw12345678")
        self.client.force_login(self.user)
        self.url = reverse("push_unsubscribe")

    def _sub(self, endpoint, active=True, **kwargs):
        return PushSubscription.objects.create(
            user=self.user, endpoint=endpoint, p256dh="dh", auth="au",
            is_active=active, **kwargs
        )

    def test_deactivates_specific_endpoint(self):
        keep = self._sub("https://push/keep")
        target = self._sub("https://push/target")
        resp = _json_post(self.client, self.url, {"endpoint": target.endpoint})
        self.assertEqual(resp.status_code, 200)
        target.refresh_from_db()
        keep.refresh_from_db()
        self.assertFalse(target.is_active)
        self.assertTrue(keep.is_active)

    def test_empty_endpoint_deactivates_all_for_user(self):
        a = self._sub("https://push/a")
        b = self._sub("https://push/b")
        resp = _json_post(self.client, self.url, {"endpoint": ""})
        self.assertEqual(resp.status_code, 200)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertFalse(a.is_active)
        self.assertFalse(b.is_active)

    def test_invalid_json_deactivates_all_for_user(self):
        """JSON 解析失败不应 500，按「全部退订」处理且不崩。"""
        a = self._sub("https://push/a")
        resp = _json_post(self.client, self.url, "{bad json")
        self.assertEqual(resp.status_code, 200)
        a.refresh_from_db()
        self.assertFalse(a.is_active)

    def test_does_not_touch_other_users_subscription(self):
        other = User.objects.create_user(username="other", password="pw12345678")
        foreign = PushSubscription.objects.create(
            user=other, endpoint="https://push/foreign", p256dh="d", auth="a", is_active=True
        )
        _json_post(self.client, self.url, {"endpoint": ""})
        foreign.refresh_from_db()
        self.assertTrue(foreign.is_active)


class VapidPublicKeyTests(TestCase):
    def test_returns_key_from_env(self):
        user = User.objects.create_user(username="vapid", password="pw12345678")
        self.client.force_login(user)
        with mock.patch.dict("os.environ", {"VAPID_PUBLIC_KEY": "TESTKEY"}):
            resp = self.client.get(reverse("vapid_public_key"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"publicKey": "TESTKEY"})

    def test_empty_when_unconfigured(self):
        user = User.objects.create_user(username="vapid2", password="pw12345678")
        self.client.force_login(user)
        with mock.patch.dict("os.environ", {}, clear=False):
            from os import environ
            environ.pop("VAPID_PUBLIC_KEY", None)
            resp = self.client.get(reverse("vapid_public_key"))
        self.assertEqual(resp.json(), {"publicKey": ""})
