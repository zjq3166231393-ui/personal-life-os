"""P1-5 多币种支持测试。

覆盖：
- currency.format_money 符号/小数位
- Expense.save 自动折算 amount_base（本位币 CNY / 外币+汇率）
- 快速记账 API 接受 currency + rate 并回写折算金额
- 编辑页接受 currency + rate 并重新折算
"""

import json

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .currency import BASE_CURRENCY, format_money, to_base
from .models import Expense


def _mkuser(name):
    return get_user_model().objects.create_user(name, password="TestPass123!")


class CurrencyUtilTests(TestCase):
    def test_format_money_cny(self):
        self.assertEqual(format_money(Decimal("18.5"), "CNY"), "¥18.50")

    def test_format_money_usd(self):
        self.assertEqual(format_money(Decimal("12"), "USD"), "$12.00")

    def test_format_money_jpy_no_decimals(self):
        self.assertEqual(format_money(Decimal("1200"), "JPY"), "¥1,200")

    def test_to_base_identity_for_cny(self):
        self.assertEqual(to_base(Decimal("10"), "CNY", 7), Decimal("10"))

    def test_to_base_foreign(self):
        self.assertEqual(to_base(Decimal("100"), "USD", Decimal("7.2")), Decimal("720.00"))


class ExpenseAmountBaseTests(TestCase):
    def setUp(self):
        self.u = _mkuser("cur_u1")

    def test_cny_amount_base_equals_amount(self):
        e = Expense.objects.create(user=self.u, amount=Decimal("18.5"), type="expense", status="confirmed", source="manual", occurred_at=timezone.now())
        self.assertEqual(e.currency, BASE_CURRENCY)
        self.assertEqual(e.rate, Decimal("1"))
        self.assertEqual(e.amount_base, Decimal("18.50"))

    def test_foreign_currency_converts(self):
        tz = timezone.now
        e = Expense.objects.create(
            user=self.u, amount=Decimal("100"), currency="USD", rate=Decimal("7.2"),
            type="expense", status="confirmed", source="manual", occurred_at=tz(),
        )
        self.assertEqual(e.amount_base, Decimal("720.00"))

    def test_foreign_without_rate_falls_back_to_one(self):
        tz = timezone.now
        e = Expense.objects.create(
            user=self.u, amount=Decimal("50"), currency="EUR", rate=Decimal("1"),
            type="expense", status="confirmed", source="manual", occurred_at=tz(),
        )
        self.assertEqual(e.amount_base, Decimal("50.00"))


class QuickAddCurrencyTests(TestCase):
    def setUp(self):
        self.u = _mkuser("cur_u2")
        self.client.login(username="cur_u2", password="TestPass123!")

    def _post(self, payload):
        return self.client.post(
            reverse("quick_add_expense"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_quick_add_foreign_currency(self):
        res = self._post({
            "amount": "100", "type": "expense", "currency": "USD", "rate": "7.2",
            "note": "coffee",
        })
        self.assertEqual(res.status_code, 200)
        d = res.json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["currency"], "USD")
        self.assertEqual(d["rate"], "7.2")
        self.assertEqual(d["amount_base"], "720.00")

        e = Expense.objects.get(pk=d["id"])
        self.assertEqual(e.amount_base, Decimal("720.00"))
        self.assertEqual(e.display_amount, "$100.00")
        self.assertTrue(e.is_foreign_currency)

    def test_quick_add_invalid_currency_falls_back_to_cny(self):
        res = self._post({"amount": "10", "type": "expense", "currency": "XXX"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["currency"], "CNY")


class ExpenseEditCurrencyTests(TestCase):
    def setUp(self):
        self.u = _mkuser("cur_u3")
        self.client.login(username="cur_u3", password="TestPass123!")
        tz = timezone.now
        self.e = Expense.objects.create(
            user=self.u, amount=Decimal("10"), currency="CNY", type="expense",
            status="confirmed", source="manual", occurred_at=tz(),
        )

    def test_edit_sets_foreign_currency_and_rate(self):
        res = self.client.post(reverse("expense_edit", args=[self.e.pk]), {
            "amount": "200", "type": "expense", "currency": "USD", "rate": "7.1",
            "occurred_at": self.e.occurred_at.strftime("%Y-%m-%dT%H:%M"),
        })
        self.assertEqual(res.status_code, 302)
        self.e.refresh_from_db()
        self.assertEqual(self.e.currency, "USD")
        self.assertEqual(self.e.rate, Decimal("7.1"))
        self.assertEqual(self.e.amount_base, Decimal("1420.00"))
