"""每日记账提醒（2026-08-30 增强）功能测试。

覆盖：
- 偏好开关保存 → UserProfile 字段持久化
- 开关启用 → 生成一条 daily Recurrence 提醒；停用 → 提醒被禁用
- 首页 nudge：启用且当天未记账时显示「今天还没记账」；已记账则隐藏
- profile 页面包含「每日记账提醒」开关
"""
import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from django.contrib.auth import get_user_model
from life.models import Category, Expense, Reminder

User = get_user_model()


class DailyLogReminderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("reminduser", password="pass")
        self.client.login(username="reminduser", password="pass")

    def _post_profile(self, **extra):
        data = {
            "display_name": "提醒测试",
            "timezone": "Asia/Shanghai",
            "currency": "CNY",
            "monthly_budget": "",
            "ai_parsing_enabled": "on",
            "daily_ai_limit": "100",
            "default_reminder_time": "10:00",
            "daily_log_reminder_time": "21:00",
        }
        data.update(extra)
        return self.client.post(reverse("profile"), data)

    def test_profile_save_enables_daily_reminder(self):
        resp = self._post_profile(
            daily_log_reminder_enabled="on",
            daily_log_reminder_time="21:00",
        )
        self.assertRedirects(resp, reverse("profile"))
        profile = self.user.profile
        profile.refresh_from_db()
        self.assertTrue(profile.daily_log_reminder_enabled)
        self.assertEqual(profile.daily_log_reminder_time, datetime.time(21, 0))

        reminder = Reminder.objects.filter(
            user=self.user, title="💰 每日记账提醒",
            recurrence_rule=Reminder.Recurrence.DAILY,
        ).first()
        self.assertIsNotNone(reminder)
        self.assertTrue(reminder.is_enabled)

    def test_profile_disable_disables_reminder(self):
        # 先启用
        self._post_profile(daily_log_reminder_enabled="on", daily_log_reminder_time="21:00")
        self.assertTrue(
            Reminder.objects.filter(
                user=self.user, title="💰 每日记账提醒",
                recurrence_rule=Reminder.Recurrence.DAILY, is_enabled=True,
            ).exists()
        )
        # 再停用
        self._post_profile()  # 不传开关
        profile = self.user.profile
        profile.refresh_from_db()
        self.assertFalse(profile.daily_log_reminder_enabled)
        self.assertFalse(
            Reminder.objects.filter(
                user=self.user, title="💰 每日记账提醒",
                recurrence_rule=Reminder.Recurrence.DAILY, is_enabled=True,
            ).exists()
        )

    def test_home_nudge_shown_when_enabled_and_not_logged(self):
        profile = self.user.profile
        profile.daily_log_reminder_enabled = True
        profile.daily_log_reminder_time = datetime.time(21, 0)
        profile.save(update_fields=["daily_log_reminder_enabled", "daily_log_reminder_time"])

        resp = self.client.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["daily_log_reminder_enabled"])
        self.assertFalse(resp.context["logged_today"])
        self.assertContains(resp, "今天还没记账")

    def test_home_nudge_hidden_when_logged_today(self):
        profile = self.user.profile
        profile.daily_log_reminder_enabled = True
        profile.daily_log_reminder_time = datetime.time(21, 0)
        profile.save(update_fields=["daily_log_reminder_enabled", "daily_log_reminder_time"])

        cat = Category.objects.create(user=self.user, name="餐饮", type="expense")
        Expense.objects.create(
            user=self.user, category=cat, amount="18.00",
            occurred_at=timezone.now(), note="午饭", source="manual",
        )

        resp = self.client.get(reverse("home"))
        self.assertTrue(resp.context["logged_today"])
        self.assertNotContains(resp, "今天还没记账")

    def test_home_no_nudge_when_disabled(self):
        profile = self.user.profile
        profile.daily_log_reminder_enabled = False
        profile.save(update_fields=["daily_log_reminder_enabled"])

        resp = self.client.get(reverse("home"))
        self.assertFalse(resp.context["daily_log_reminder_enabled"])
        self.assertNotContains(resp, "今天还没记账")

    def test_profile_page_contains_toggle(self):
        resp = self.client.get(reverse("profile"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "每日记账提醒")
        self.assertContains(resp, "id_daily_log_reminder_enabled")
