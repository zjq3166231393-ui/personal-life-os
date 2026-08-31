"""信封预算（Envelope Budget）功能测试。

复用已有的 Budget（分类预算）+ Expense 月度支出：
- GET 渲染、空状态
- 有预算 + 有支出 → 信封余额 / 进度 / 超支计算正确
- POST 新增信封（new_cat/new_amount）与调整已有信封（env_<id>）
- 总预算未分配金额（unallocated）计算
"""
from calendar import monthrange
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from life.models import Budget, Category, Expense

User = get_user_model()


class EnvelopeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("envuser", password="pass")
        self.client.login(username="envuser", password="pass")
        self.cat = Category.objects.create(
            user=self.user, name="餐饮", type="expense", is_active=True,
        )
        self.today = timezone.localdate()
        self.month_start = date(self.today.year, self.today.month, 1)

    def _make_expense(self, amount, cat=None):
        return Expense.objects.create(
            user=self.user, category=cat or self.cat, amount=Decimal(amount),
            occurred_at=timezone.now(), type="expense", status="confirmed",
        )

    def test_envelopes_empty(self):
        resp = self.client.get(reverse("envelopes"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["alloc_total"], Decimal(0))
        self.assertEqual(resp.context["spent_total"], Decimal(0))
        self.assertEqual(len(resp.context["env_rows"]), 0)

    def test_envelopes_with_budget_and_spend(self):
        Budget.objects.create(
            user=self.user, category=self.cat, month=self.month_start, amount=Decimal("500"),
        )
        self._make_expense("120")
        self._make_expense("80")
        resp = self.client.get(reverse("envelopes"))
        self.assertEqual(resp.status_code, 200)
        rows = resp.context["env_rows"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["budget"], Decimal("500"))
        self.assertEqual(row["spent"], Decimal("200"))
        self.assertEqual(row["remaining"], Decimal("300"))
        self.assertEqual(row["pct"], 40)
        self.assertFalse(row["over"])
        # 总分配 = 500，已用 = 200
        self.assertEqual(resp.context["alloc_total"], Decimal("500"))
        self.assertEqual(resp.context["spent_total"], Decimal("200"))
        self.assertEqual(resp.context["overall_pct"], 40)

    def test_envelope_over_budget(self):
        Budget.objects.create(
            user=self.user, category=self.cat, month=self.month_start, amount=Decimal("100"),
        )
        self._make_expense("130")
        resp = self.client.get(reverse("envelopes"))
        row = resp.context["env_rows"][0]
        self.assertTrue(row["over"])
        self.assertEqual(row["over_amount"], Decimal("30"))
        self.assertEqual(row["remaining"], Decimal("-30"))

    def test_post_creates_and_updates_envelope(self):
        # 新增信封
        resp = self.client.post(reverse("envelopes"), {
            "new_cat": str(self.cat.pk), "new_amount": "600",
        })
        self.assertRedirects(resp, reverse("envelopes"))
        b = Budget.objects.get(user=self.user, category=self.cat, month=self.month_start)
        self.assertEqual(b.amount, Decimal("600"))
        # 调整已有信封
        resp2 = self.client.post(reverse("envelopes"), {
            f"env_{self.cat.pk}": "750",
        })
        self.assertRedirects(resp2, reverse("envelopes"))
        b.refresh_from_db()
        self.assertEqual(b.amount, Decimal("750"))

    def test_unallocated_from_total_budget(self):
        # 总预算 1000，分类信封分配 400 → 未分配 600
        Budget.objects.create(
            user=self.user, category=None, month=self.month_start, amount=Decimal("1000"),
        )
        Budget.objects.create(
            user=self.user, category=self.cat, month=self.month_start, amount=Decimal("400"),
        )
        resp = self.client.get(reverse("envelopes"))
        self.assertEqual(resp.context["total_amount"], Decimal("1000"))
        self.assertEqual(resp.context["alloc_total"], Decimal("400"))
        self.assertEqual(resp.context["unallocated"], Decimal("600"))

    def test_envelope_pace_fast_with_overrun_projection(self):
        # 预算 100，已花 130（超支）→ 按当前速度预测月末超支，节奏偏快
        Budget.objects.create(
            user=self.user, category=self.cat, month=self.month_start, amount=Decimal("100"),
        )
        self._make_expense("130")
        resp = self.client.get(reverse("envelopes"))
        row = resp.context["env_rows"][0]
        self.assertEqual(row["pace"], "fast")
        self.assertGreater(row["projected_over"], Decimal(0))
        self.assertGreater(row["daily_avg"], Decimal(0))
        expected_days_left = monthrange(self.today.year, self.today.month)[1] - self.today.day
        self.assertEqual(row["days_left"], expected_days_left)

    def test_envelope_pace_slow_under_budget(self):
        # 预算 500，已花 200（远未超）→ 节奏偏慢、不预测超支
        Budget.objects.create(
            user=self.user, category=self.cat, month=self.month_start, amount=Decimal("500"),
        )
        self._make_expense("200")
        resp = self.client.get(reverse("envelopes"))
        row = resp.context["env_rows"][0]
        self.assertEqual(row["pace"], "slow")
        self.assertEqual(row["projected_over"], Decimal(0))
