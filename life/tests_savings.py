"""储蓄目标（心愿单 / 存钱罐）功能测试。

覆盖：
- 新建目标（含默认值兜底）
- 编辑目标（金额 / 截止日 / 备注）
- 软删除（is_active=False，列表不再出现）
- 存入 / 取出（adjust：不为负、取空也不变负）
- 进度属性：progress_pct / remaining / is_reached
- 首页储蓄目标摘要展示
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from life.models import SavingsGoal

User = get_user_model()


class SavingsGoalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("svuser", password="pass")
        self.client.login(username="svuser", password="pass")

    def _create(self, **data):
        base = {
            "name": "旅行基金",
            "target_amount": "5000",
            "current_amount": "1000",
            "icon": "✈️",
            "deadline": "",
            "note": "去日本",
        }
        base.update(data)
        return self.client.post(reverse("savings_goal_create"), base)

    def test_create_savings_goal(self):
        resp = self._create()
        self.assertRedirects(resp, reverse("savings_goals"))
        g = SavingsGoal.objects.get(user=self.user)
        self.assertEqual(g.name, "旅行基金")
        self.assertEqual(g.target_amount, Decimal("5000"))
        self.assertEqual(g.current_amount, Decimal("1000"))
        self.assertEqual(g.icon, "✈️")
        self.assertTrue(g.is_active)

    def test_create_defaults_when_blank(self):
        # 名称为空 → 兜底；目标 <=0 → 兜底 100
        resp = self._create(name="", target_amount="0")
        self.assertRedirects(resp, reverse("savings_goals"))
        g = SavingsGoal.objects.get(user=self.user)
        self.assertEqual(g.name, "我的储蓄目标")
        self.assertEqual(g.target_amount, Decimal("100"))

    def test_edit_savings_goal(self):
        self._create()
        g = SavingsGoal.objects.get(user=self.user)
        resp = self.client.post(reverse("savings_goal_edit", args=[g.pk]), {
            "name": "新手机", "target_amount": "8000", "current_amount": "3200",
            "icon": "📱", "deadline": "2026-12-31", "note": "攒钱买",
        })
        self.assertRedirects(resp, reverse("savings_goals"))
        g.refresh_from_db()
        self.assertEqual(g.name, "新手机")
        self.assertEqual(g.target_amount, Decimal("8000"))
        self.assertEqual(g.current_amount, Decimal("3200"))
        self.assertEqual(g.icon, "📱")
        self.assertEqual(g.deadline.isoformat(), "2026-12-31")

    def test_delete_soft(self):
        # 用唯一名称，避免与空状态示例文案「旅行基金 5000 元」误匹配
        self._create(name="应急备用金测试专用")
        g = SavingsGoal.objects.get(user=self.user)
        resp = self.client.post(reverse("savings_goal_delete", args=[g.pk]))
        self.assertRedirects(resp, reverse("savings_goals"))
        g.refresh_from_db()
        self.assertFalse(g.is_active)
        # 列表页不再出现
        resp2 = self.client.get(reverse("savings_goals"))
        self.assertEqual(resp2.status_code, 200)
        self.assertNotContains(resp2, "应急备用金测试专用")

    def test_adjust_deposit_and_withdraw(self):
        self._create(current_amount="1000")
        g = SavingsGoal.objects.get(user=self.user)
        # 存入 500
        self.client.post(reverse("savings_goal_adjust", args=[g.pk]), {"amount": "500"})
        g.refresh_from_db()
        self.assertEqual(g.current_amount, Decimal("1500"))
        # 取出 200
        self.client.post(reverse("savings_goal_adjust", args=[g.pk]), {"amount": "-200"})
        g.refresh_from_db()
        self.assertEqual(g.current_amount, Decimal("1300"))
        # 取出超过余额 → 不为负
        self.client.post(reverse("savings_goal_adjust", args=[g.pk]), {"amount": "-99999"})
        g.refresh_from_db()
        self.assertEqual(g.current_amount, Decimal("0"))

    def test_progress_properties(self):
        self._create(target_amount="1000", current_amount="400")
        g = SavingsGoal.objects.get(user=self.user)
        self.assertEqual(g.progress_pct, 40)
        self.assertEqual(g.remaining, Decimal("600"))
        self.assertFalse(g.is_reached)
        # 达标
        g.current_amount = Decimal("1000")
        g.save()
        self.assertTrue(g.is_reached)
        self.assertEqual(g.progress_pct, 100)
        # 超额封顶 100
        g.current_amount = Decimal("1200")
        g.save()
        self.assertEqual(g.progress_pct, 100)

    def test_home_shows_savings_summary(self):
        self._create(name="应急金", target_amount="20000", current_amount="5000")
        resp = self.client.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "应急金")
        self.assertContains(resp, "储蓄目标")

    def test_monthly_needed_with_future_deadline(self):
        from datetime import timedelta

        from django.utils import timezone

        deadline = timezone.localdate() + timedelta(days=150)  # 约 5 个月
        self._create(target_amount="5000", current_amount="1000", deadline=deadline.isoformat())
        resp = self.client.get(reverse("savings_goals"))
        self.assertEqual(resp.status_code, 200)
        item = resp.context["goals"][0]
        self.assertEqual(item["months_left"], 5)
        self.assertEqual(item["monthly_needed"], Decimal("800"))  # (5000-1000)/5
        # 无截止日期的目标不显示需月攒
        self._create(name="无期限目标", target_amount="3000", current_amount="500", deadline="")
        resp2 = self.client.get(reverse("savings_goals"))
        no_deadline = [g for g in resp2.context["goals"] if g["obj"].name == "无期限目标"][0]
        self.assertIsNone(no_deadline["monthly_needed"])
