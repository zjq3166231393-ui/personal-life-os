"""批量操作（P2）测试：删除 / 打标签 / 改账户，重点验证越权隔离与转账排除。"""
import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Account, Expense, Tag


class BatchExpenseTest(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("u", "u@x.com", "pw")
        self.other = User.objects.create_user("other", "o@x.com", "pw")
        self.client.login(username="u", password="pw")

        now = timezone.now()
        self.e1 = Expense.objects.create(user=self.u, type="expense", amount=Decimal("10"), status="confirmed", occurred_at=now)
        self.e2 = Expense.objects.create(user=self.u, type="income", amount=Decimal("20"), status="confirmed", occurred_at=now)
        self.e3 = Expense.objects.create(user=self.u, type="transfer", amount=Decimal("30"), status="confirmed", occurred_at=now)
        self.e_other = Expense.objects.create(user=self.other, type="expense", amount=Decimal("99"), status="confirmed", occurred_at=now)

        self.tag = Tag.objects.create(user=self.u, name="旅行")
        self.acc = Account.objects.create(user=self.u, name="招行", type="bank", initial_balance=Decimal("0"))
        self.acc_stopped = Account.objects.create(user=self.u, name="停用", type="cash", initial_balance=Decimal("0"), is_deleted=True)

    def _post(self, payload):
        return self.client.post(
            reverse("batch_expense_action"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    # ── 删除 ──
    def test_delete_soft(self):
        r = self._post({"action": "delete", "ids": [self.e1.pk, self.e2.pk]})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.e1.refresh_from_db()
        self.e2.refresh_from_db()
        self.assertTrue(self.e1.is_deleted)
        self.assertTrue(self.e2.is_deleted)

    def test_delete_excludes_other_user(self):
        r = self._post({"action": "delete", "ids": [self.e_other.pk]})
        self.assertEqual(r.status_code, 400)  # 过滤后 0 条 -> 报错
        self.assertFalse(r.json()["ok"])
        self.e_other.refresh_from_db()
        self.assertFalse(self.e_other.is_deleted)

    def test_delete_mixed_ids_only_own(self):
        r = self._post({"action": "delete", "ids": [self.e1.pk, self.e_other.pk]})
        self.assertTrue(r.json()["ok"])
        self.e1.refresh_from_db()
        self.e_other.refresh_from_db()
        self.assertTrue(self.e1.is_deleted)
        self.assertFalse(self.e_other.is_deleted)

    def test_delete_excluded_from_balance(self):
        self.e1.account = self.acc
        self.e1.save()
        self.assertEqual(self.acc.balance, Decimal("-10"))
        self._post({"action": "delete", "ids": [self.e1.pk]})
        self.e1.refresh_from_db()
        self.assertTrue(self.e1.is_deleted)
        self.assertEqual(self.acc.balance, Decimal("0"))  # 软删除后不计入余额

    # ── 打标签 ──
    def test_add_tag(self):
        r = self._post({"action": "add_tag", "ids": [self.e1.pk], "tag_ids": [self.tag.pk]})
        self.assertTrue(r.json()["ok"])
        self.assertIn(self.tag, self.e1.tags.all())

    def test_add_tag_ignores_other_user_tag(self):
        ot = Tag.objects.create(user=self.other, name="别人的")
        r = self._post({"action": "add_tag", "ids": [self.e1.pk], "tag_ids": [ot.pk]})
        self.assertEqual(r.status_code, 400)  # 没有任何合法（本人）标签 -> 拒绝
        self.e1.refresh_from_db()
        self.assertEqual(self.e1.tags.count(), 0)

    def test_add_tag_empty(self):
        r = self._post({"action": "add_tag", "ids": [self.e1.pk], "tag_ids": []})
        self.assertEqual(r.status_code, 400)

    # ── 改账户 ──
    def test_set_account_excludes_transfer(self):
        r = self._post({"action": "set_account", "ids": [self.e1.pk, self.e2.pk, self.e3.pk], "account_id": self.acc.pk})
        self.assertTrue(r.json()["ok"])
        self.assertEqual(r.json()["count"], 2)  # 转账被排除
        self.e1.refresh_from_db()
        self.e2.refresh_from_db()
        self.e3.refresh_from_db()
        self.assertEqual(self.e1.account, self.acc)
        self.assertEqual(self.e2.account, self.acc)
        self.assertIsNone(self.e3.account)

    def test_set_account_invalid(self):
        r = self._post({"action": "set_account", "ids": [self.e1.pk], "account_id": 9999})
        self.assertEqual(r.status_code, 400)

    def test_set_account_stopped_ignored(self):
        r = self._post({"action": "set_account", "ids": [self.e1.pk], "account_id": self.acc_stopped.pk})
        self.assertEqual(r.status_code, 400)

    def test_clear_account(self):
        self.e1.account = self.acc
        self.e1.save()
        r = self._post({"action": "clear_account", "ids": [self.e1.pk]})
        self.assertTrue(r.json()["ok"])
        self.e1.refresh_from_db()
        self.assertIsNone(self.e1.account)

    # ── 边界 ──
    def test_empty_ids(self):
        r = self._post({"action": "delete", "ids": []})
        self.assertEqual(r.status_code, 400)

    def test_unknown_action(self):
        r = self._post({"action": "frobnicate", "ids": [self.e1.pk]})
        self.assertEqual(r.status_code, 400)

    def test_login_required(self):
        self.client.logout()
        r = self._post({"action": "delete", "ids": [self.e1.pk]})
        self.assertEqual(r.status_code, 302)  # 重定向到登录页
