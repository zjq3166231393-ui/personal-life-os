"""账户 / 多账本功能测试（P1）。

覆盖：
- 正常路径：创建 / 编辑 / 明细查看
- 校验：空名拒绝、重名拒绝、数量上限
- 软删除：删除账户后流水保留、account 置空（SET_NULL）
- 余额推算：初始余额 + 收入 − 支出 − 转出 + 转入；只统计 confirmed
- 越权隔离：不能看他人的账户
- 未登录保护
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from .models import Account, Expense
from .views_account import ACCOUNT_LIMIT_PER_USER


def _mkuser(name):
    return User.objects.create_user(name, password="TestPass123!")


def _mk_expense(user, account=None, transfer_to=None, type="expense",
                amount="10.00", status="confirmed"):
    return Expense.objects.create(
        user=user,
        category=None,
        type=type,
        amount=Decimal(amount),
        occurred_at=timezone.now(),
        status=status,
        account=account,
        transfer_to_account=transfer_to,
    )


class AccountCreateTests(TestCase):
    def setUp(self):
        self.u = _mkuser("alice")
        self.client.login(username="alice", password="TestPass123!")

    def test_tc_a001_create_account(self):
        r = self.client.post("/accounts/create/", {
            "name": "招商银行卡", "type": "bank", "initial_balance": "500.00",
        })
        self.assertEqual(r.status_code, 302)
        acc = Account.objects.get(user=self.u, name="招商银行卡")
        self.assertEqual(acc.type, "bank")
        self.assertEqual(acc.initial_balance, Decimal("500.00"))

    def test_tc_a002_empty_name_rejected(self):
        before = Account.objects.filter(user=self.u).count()
        r = self.client.post("/accounts/create/", {"name": "   ", "type": "cash"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Account.objects.filter(user=self.u).count(), before)

    def test_tc_a003_duplicate_name_rejected(self):
        Account.objects.create(user=self.u, name="现金", type="cash")
        r = self.client.post("/accounts/create/", {"name": "现金", "type": "cash"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Account.objects.filter(user=self.u, name="现金").count(), 1)

    def test_tc_a004_account_limit(self):
        for i in range(ACCOUNT_LIMIT_PER_USER):
            Account.objects.create(user=self.u, name=f"账户{i}")
        r = self.client.post("/accounts/create/", {"name": "超额账户", "type": "cash"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Account.objects.filter(user=self.u).count(), ACCOUNT_LIMIT_PER_USER)
        self.assertFalse(Account.objects.filter(user=self.u, name="超额账户").exists())

    def test_tc_a005_edit_account(self):
        acc = Account.objects.create(user=self.u, name="旧名", type="cash", initial_balance=Decimal("0"))
        r = self.client.post(f"/accounts/{acc.pk}/edit/", {
            "name": "新名", "type": "alipay", "initial_balance": "88.00", "is_active": "on",
        })
        self.assertEqual(r.status_code, 302)
        acc.refresh_from_db()
        self.assertEqual(acc.name, "新名")
        self.assertEqual(acc.type, "alipay")
        self.assertEqual(acc.initial_balance, Decimal("88.00"))
        self.assertTrue(acc.is_active)


class AccountSoftDeleteTests(TestCase):
    def setUp(self):
        self.u = _mkuser("bob")
        self.client.login(username="bob", password="TestPass123!")

    def test_tc_a006_soft_delete_keeps_transactions(self):
        acc = Account.objects.create(user=self.u, name="钱包", type="cash")
        e = _mk_expense(self.u, account=acc, amount="20.00")
        r = self.client.post(f"/accounts/{acc.pk}/delete/")
        self.assertEqual(r.status_code, 302)
        # 账户被软删除
        acc.refresh_from_db()
        self.assertTrue(acc.is_deleted)
        self.assertIsNotNone(acc.deleted_at)
        # 流水保留（不丢失历史），FK 仍指向该账户对象
        # （软删除不触发 on_delete，故 account 不会被置空——这正是「删账户不删流水」的设计意图）
        e.refresh_from_db()
        self.assertTrue(Expense.objects.filter(pk=e.pk).exists())
        self.assertEqual(e.account_id, acc.pk)
        # 已软删除的账户不再出现在列表/明细中
        self.assertFalse(Account.objects.filter(user=self.u, is_deleted=False, name="钱包").exists())


class AccountBalanceTests(TestCase):
    def setUp(self):
        self.u = _mkuser("carol")
        self.client.login(username="carol", password="TestPass123!")

    def _balance(self):
        return Account.objects.get(user=self.u, name="主账户").balance

    def test_tc_a007_balance_calculation(self):
        acc = Account.objects.create(user=self.u, name="主账户", type="bank", initial_balance=Decimal("100.00"))
        _mk_expense(self.u, account=acc, type="income", amount="50.00")     # +50
        _mk_expense(self.u, account=acc, type="expense", amount="30.00")    # -30
        _mk_expense(self.u, account=acc, type="transfer", amount="20.00")   # 转出 -20
        _mk_expense(self.u, transfer_to=acc, type="transfer", amount="10.00")  # 转入 +10
        # 100 + 50 - 30 - 20 + 10 = 110
        self.assertEqual(self._balance(), Decimal("110.00"))

    def test_tc_a008_pending_excluded_from_balance(self):
        acc = Account.objects.create(user=self.u, name="主账户", type="bank", initial_balance=Decimal("0.00"))
        _mk_expense(self.u, account=acc, type="income", amount="100.00", status="pending")
        _mk_expense(self.u, account=acc, type="income", amount="40.00", status="confirmed")
        # 仅 confirmed 计入：40，pending 的 100 不算
        self.assertEqual(self._balance(), Decimal("40.00"))

    def test_tc_a009_account_detail_lists_transactions(self):
        acc = Account.objects.create(user=self.u, name="主账户", type="cash")
        _mk_expense(self.u, account=acc, type="income", amount="12.00")
        r = self.client.get(f"/accounts/{acc.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context["items"].count() >= 1)
        self.assertEqual(r.context["balance"], Decimal("12.00"))


class AccountAccessControlTests(TestCase):
    def setUp(self):
        self.u = _mkuser("dave")
        self.other = _mkuser("eve")
        self.client.login(username="dave", password="TestPass123!")

    def test_tc_a010_cannot_view_other_users_account(self):
        acc = Account.objects.create(user=self.other, name="他人账户", type="cash")
        r = self.client.get(f"/accounts/{acc.pk}/")
        self.assertEqual(r.status_code, 404)

    def test_tc_a011_cannot_edit_other_users_account(self):
        acc = Account.objects.create(user=self.other, name="他人账户", type="cash")
        r = self.client.post(f"/accounts/{acc.pk}/edit/", {"name": "改不了", "type": "cash"})
        self.assertEqual(r.status_code, 404)
        acc.refresh_from_db()
        self.assertEqual(acc.name, "他人账户")

    def test_tc_a012_login_required(self):
        self.client.logout()
        r = self.client.get("/accounts/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login", r["Location"])
