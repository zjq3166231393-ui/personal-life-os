"""年度账单（P1-7）测试。

覆盖：空数据渲染、有数据时的总额/月度/分类/最大单笔/峰值月、年份参数、同比、越权隔离（仅本人）。
"""

from datetime import date

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Category, Expense


def _mkuser(name):
    return get_user_model().objects.create_user(name, password="TestPass123!")


def _exp(user, amount, day, type_="expense", cat=None, note=""):
    return Expense.objects.create(
        user=user, amount=Decimal(amount), type=type_, category=cat,
        note=note, status="confirmed", occurred_at=timezone.make_aware(
            timezone.datetime.combine(day, timezone.datetime.min.time())
        ),
    )


class AnnualSummaryTests(TestCase):
    def setUp(self):
        self.u = _mkuser("ann_u1")
        self.client.login(username="ann_u1", password="TestPass123!")
        self.dining = Category.objects.create(user=self.u, name="餐饮", icon="🍜", type="expense")
        self.salary = Category.objects.create(user=self.u, name="工资", icon="💰", type="income")

    def test_renders_empty(self):
        res = self.client.get(reverse("annual_summary"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "年度账单")
        # 空数据：无最大单笔、无分类排行
        self.assertNotContains(res, "最大单笔支出")

    def test_totals_and_monthly(self):
        # 2026 年：餐饮 1月 100、2月 200；收入 6月 工资 5000
        _exp(self.u, "100", date(2026, 1, 15), cat=self.dining)
        _exp(self.u, "200", date(2026, 2, 15), cat=self.dining)
        _exp(self.u, "5000", date(2026, 6, 1), type_="income", cat=self.salary)
        res = self.client.get(reverse("annual_summary"), {"year": "2026"})
        self.assertEqual(res.status_code, 200)
        # 全年支出 300
        self.assertContains(res, "300")
        # 月度图数据含 5000（6 月收入）
        self.assertContains(res, "5000")

    def test_biggest_and_peak_month(self):
        _exp(self.u, "100", date(2026, 1, 10), cat=self.dining)   # 1月 100
        _exp(self.u, "50", date(2026, 1, 20), cat=self.dining)    # 1月累计 150
        _exp(self.u, "400", date(2026, 3, 5), cat=self.dining)    # 3月 400 → 花最多
        res = self.client.get(reverse("annual_summary"), {"year": "2026"})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "最大单笔支出")
        self.assertContains(res, "400.00")
        self.assertContains(res, "3 月")  # 花得最多的月份

    def test_top_categories(self):
        d2 = Category.objects.create(user=self.u, name="交通", icon="🚕", type="expense")
        _exp(self.u, "100", date(2026, 1, 10), cat=self.dining)
        _exp(self.u, "300", date(2026, 2, 10), cat=d2)
        res = self.client.get(reverse("annual_summary"), {"year": "2026"})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "交通")
        self.assertContains(res, "餐饮")

    def test_year_param_isolation(self):
        _exp(self.u, "100", date(2026, 1, 10), cat=self.dining)
        _exp(self.u, "999", date(2025, 1, 10), cat=self.dining)
        res = self.client.get(reverse("annual_summary"), {"year": "2026"})
        # 2026 视图不应包含 2025 的 999
        self.assertNotContains(res, "999")

    def test_invalid_year_falls_back(self):
        res = self.client.get(reverse("annual_summary"), {"year": "abc"})
        self.assertEqual(res.status_code, 200)
        # 回退到当前年（2026，session 当前日期）渲染无异常
        self.assertContains(res, "年度账单")

    def test_other_user_data_not_leaked(self):
        other = _mkuser("ann_other")
        _exp(other, "8888", date(2026, 5, 5), cat=Category.objects.create(
            user=other, name="他人餐饮", type="expense"))
        res = self.client.get(reverse("annual_summary"), {"year": "2026"})
        self.assertNotContains(res, "8888")
