"""账户接入记账入口的测试。

验证：
- 快速记账（API）能写入 expense.account，且余额随之变化
- 越权 / 失效的 account_id 被忽略（不报错、不越权）
- 编辑页能设置 / 清除账户；转账可设双账户，非转账时转入账户被清空
- 详情页展示账户
"""

import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Account, Expense


def _mkuser(name):
    return User.objects.create_user(name, password="TestPass123!")


class QuickAddAccountTests(TestCase):
    def setUp(self):
        self.u = _mkuser("finn")
        self.other = _mkuser("gina")
        self.client.login(username="finn", password="TestPass123!")
        self.acc = Account.objects.create(user=self.u, name="支付宝", type="alipay", initial_balance=Decimal("0"))

    def _quick(self, payload):
        return self.client.post(
            "/api/quick-expense/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_tc_ae001_quick_add_writes_account(self):
        r = self._quick({"amount": "30.00", "type": "expense", "account_id": self.acc.pk})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        e = Expense.objects.get(user=self.u)
        self.assertEqual(e.account_id, self.acc.pk)

    def test_tc_ae002_quick_add_balance_changes(self):
        # 初始 0，记一笔支出 50 → 余额 -50
        self._quick({"amount": "50.00", "type": "expense", "account_id": self.acc.pk})
        self.assertEqual(Account.objects.get(pk=self.acc.pk).balance, Decimal("-50.00"))

    def test_tc_ae003_invalid_account_ignored(self):
        # 他人的账户：越权引用被忽略，expense.account 为 None，且不报错
        others = Account.objects.create(user=self.other, name="他人卡", type="bank")
        r = self._quick({"amount": "20.00", "type": "expense", "account_id": others.pk})
        self.assertEqual(r.status_code, 200)
        e = Expense.objects.get(user=self.u)
        self.assertIsNone(e.account_id)

    def test_tc_ae004_no_account_option(self):
        # 不传 account_id → 仍成功，account 为 None
        r = self._quick({"amount": "12.00", "type": "income"})
        self.assertEqual(r.status_code, 200)
        e = Expense.objects.get(user=self.u)
        self.assertIsNone(e.account_id)


class EditAccountTests(TestCase):
    def setUp(self):
        self.u = _mkuser("hugo")
        self.client.login(username="hugo", password="TestPass123!")
        self.acc = Account.objects.create(user=self.u, name="招行卡", type="bank", initial_balance=Decimal("100"))
        self.from_acc = Account.objects.create(user=self.u, name="现金", type="cash", initial_balance=Decimal("100"))
        self.to_acc = Account.objects.create(user=self.u, name="微信", type="wechat", initial_balance=Decimal("100"))

    def _make_expense(self, type="expense", account=None):
        return Expense.objects.create(
            user=self.u, type=type, amount=Decimal("10.00"),
            occurred_at=timezone.now(),
            status="confirmed", account=account,
        )

    def test_tc_ae005_edit_sets_account(self):
        e = self._make_expense()
        r = self.client.post(reverse("expense_edit", args=[e.pk]), {
            "amount": "10.00", "type": "expense",
            "occurred_at": "2026-08-01T10:00", "account": self.acc.pk,
        })
        self.assertEqual(r.status_code, 302)
        e.refresh_from_db()
        self.assertEqual(e.account_id, self.acc.pk)

    def test_tc_ae006_transfer_sets_both_accounts(self):
        e = self._make_expense(type="transfer")
        r = self.client.post(reverse("expense_edit", args=[e.pk]), {
            "amount": "10.00", "type": "transfer",
            "occurred_at": "2026-08-01T10:00",
            "account": self.from_acc.pk, "transfer_to_account": self.to_acc.pk,
        })
        self.assertEqual(r.status_code, 302)
        e.refresh_from_db()
        self.assertEqual(e.account_id, self.from_acc.pk)
        self.assertEqual(e.transfer_to_account_id, self.to_acc.pk)
        # 余额：转出账户 -10，转入账户 +10
        self.assertEqual(Account.objects.get(pk=self.from_acc.pk).balance, Decimal("90.00"))
        self.assertEqual(Account.objects.get(pk=self.to_acc.pk).balance, Decimal("110.00"))

    def test_tc_ae007_non_transfer_clears_transfer_to(self):
        e = self._make_expense(type="transfer", account=self.from_acc)
        e.transfer_to_account = self.to_acc
        e.save()
        r = self.client.post(reverse("expense_edit", args=[e.pk]), {
            "amount": "10.00", "type": "expense",
            "occurred_at": "2026-08-01T10:00", "account": self.acc.pk,
        })
        self.assertEqual(r.status_code, 302)
        e.refresh_from_db()
        self.assertEqual(e.account_id, self.acc.pk)
        self.assertIsNone(e.transfer_to_account_id)  # 非转账必须清空

    def test_tc_ae008_invalid_account_cleared(self):
        # 篡改 POST 传入不存在的 account_id → 置空，不报错
        e = self._make_expense(account=self.acc)
        r = self.client.post(reverse("expense_edit", args=[e.pk]), {
            "amount": "10.00", "type": "expense",
            "occurred_at": "2026-08-01T10:00", "account": "999999",
        })
        self.assertEqual(r.status_code, 302)
        e.refresh_from_db()
        self.assertIsNone(e.account_id)


class DetailShowsAccountTests(TestCase):
    def setUp(self):
        self.u = _mkuser("iris")
        self.client.login(username="iris", password="TestPass123!")
        self.acc = Account.objects.create(user=self.u, name="支付宝", type="alipay")
        self.e = Expense.objects.create(
            user=self.u, type="expense", amount=Decimal("8.00"),
            occurred_at=timezone.now(),
            status="confirmed", account=self.acc,
        )

    def test_tc_ae009_detail_shows_account(self):
        r = self.client.get(reverse("expense_detail", args=[self.e.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "支付宝")
        self.assertContains(r, "账户")
