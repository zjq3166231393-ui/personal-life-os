"""报表增强（2026-08-30）功能测试。

覆盖：
- 登录保护
- 默认（本月）区间计算与 KPI
- 环比 / 同比 增幅计算
- 自定义区间
- 全部区间不显示对比
- 分类构成
"""
from datetime import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from django.contrib.auth import get_user_model
from life.models import Category, Expense

User = get_user_model()


class ReportsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("reportuser", password="pass")
        self.client.login(username="reportuser", password="pass")
        self.cat = Category.objects.create(user=self.user, name="餐饮", type="expense", icon="🍽️", color="#f97316")
        self.income_cat = Category.objects.create(user=self.user, name="工资", type="income", icon="💰", color="#22c55e")

    def _mk(self, year, month, day, amount, typ="expense", cat=None):
        dt = timezone.make_aware(datetime(year, month, day, 12, 0))
        Expense.objects.create(
            user=self.user, category=cat or self.cat, amount=str(amount),
            occurred_at=dt, type=typ, note="测试", source="manual",
        )

    def test_reports_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse("reports"))
        self.assertEqual(resp.status_code, 302)

    def test_reports_month_default_renders(self):
        today = timezone.localdate()
        self._mk(today.year, today.month, 15, 100)
        resp = self.client.get(reverse("reports"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "报表")
        self.assertContains(resp, "支出构成")

    def test_reports_totals_context(self):
        today = timezone.localdate()
        self._mk(today.year, today.month, 10, 120)
        self._mk(today.year, today.month, 12, 80, typ="income", cat=self.income_cat)
        resp = self.client.get(reverse("reports"))
        ctx = resp.context
        self.assertEqual(ctx["total_expense"], 120)
        self.assertEqual(ctx["total_income"], 80)
        self.assertEqual(ctx["balance"], -40)

    def test_reports_mom_delta(self):
        today = timezone.localdate()
        # 本月 100，上月 50 → 环比 +100%
        self._mk(today.year, today.month, 15, 100)
        if today.month == 1:
            ly, lm = today.year - 1, 12
        else:
            ly, lm = today.year, today.month - 1
        self._mk(ly, lm, 15, 50)
        resp = self.client.get(reverse("reports"))
        self.assertEqual(resp.context["delta_expense_mom"], 100.0)

    def test_reports_yoy_delta(self):
        today = timezone.localdate()
        # 今年本月 200，去年同月 100 → 同比 +100%
        self._mk(today.year, today.month, 15, 200)
        self._mk(today.year - 1, today.month, 15, 100)
        resp = self.client.get(reverse("reports"))
        self.assertEqual(resp.context["delta_expense_yoy"], 100.0)

    def test_reports_custom_range(self):
        self._mk(2026, 1, 10, 300)
        self._mk(2026, 2, 10, 700)
        resp = self.client.get(reverse("reports"), {"preset": "custom", "start": "2026-01-01", "end": "2026-01-31"})
        ctx = resp.context
        self.assertEqual(ctx["preset"], "custom")
        self.assertEqual(ctx["total_expense"], 300)

    def test_reports_all_preset_no_compare(self):
        self._mk(2025, 5, 1, 10)
        self._mk(2026, 3, 1, 20)
        resp = self.client.get(reverse("reports"), {"preset": "all"})
        ctx = resp.context
        self.assertTrue(ctx["is_all"])
        self.assertIsNone(ctx["delta_expense_mom"])
        self.assertIsNone(ctx["delta_expense_yoy"])
        self.assertNotContains(resp, "环比（上一周期）")

    def test_reports_category_breakdown(self):
        today = timezone.localdate()
        cat2 = Category.objects.create(user=self.user, name="交通", type="expense", icon="🚌", color="#3b82f6")
        self._mk(today.year, today.month, 10, 100, cat=self.cat)
        self._mk(today.year, today.month, 11, 30, cat=cat2)
        resp = self.client.get(reverse("reports"))
        names = [c["name"] for c in resp.context["expense_cats"]]
        self.assertIn("餐饮", names)
        self.assertIn("交通", names)
        # 金额大者排前
        self.assertEqual(resp.context["expense_cats"][0]["name"], "餐饮")
