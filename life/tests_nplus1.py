"""P1-9 消除列表视图 N+1 查询的回归测试。

核心手法：用 CaptureQueriesContext 统计渲染某列表页的 SQL 条数，
断言「查询数不随数据行数增长」——这正是 N+1 的特征（每行 +1 次查询）。

覆盖：
- note_list：循环访问 n.user → select_related("user")
- account_list：逐账户调用 Account.balance（每账户 4 次聚合）→ 改为单次批量 GROUP BY
- dashboard：large_items 调 display_title → e.category → base.select_related("category")
"""

from datetime import timedelta

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import Account, Category, Expense, Note


def _mkuser(name):
    return get_user_model().objects.create_user(name, password="TestPass123!")


def _note(user, title):
    return Note.objects.create(user=user, title=title)


def _exp(user, amount, type_="expense", cat=None, account=None, days_ago=0):
    return Expense.objects.create(
        user=user, amount=Decimal(amount), type=type_, category=cat, account=account,
        status="confirmed", source="manual",
        occurred_at=timezone.now() - timedelta(days=days_ago),
    )


class _NPlusOneBase(TestCase):
    def setUp(self):
        self.u = _mkuser("n1_user")
        self.client.login(username="n1_user", password="TestPass123!")

    def _count_queries(self, url, **params):
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(url, params)
        return len(ctx.captured_queries)


class NoteListNPlusOneTests(_NPlusOneBase):
    def test_queries_stable_with_row_count(self):
        """note_list 循环访问 n.user；未 select_related 时每行 +1 次查询。"""
        for i in range(3):
            _note(self.u, f"note-{i}")
        q_small = self._count_queries(reverse("note_list"))

        for i in range(3, 12):
            _note(self.u, f"note-{i}")
        q_big = self._count_queries(reverse("note_list"))

        self.assertEqual(q_small, q_big,
                         "note_list 查询数随笔记数增长，疑似 n.user N+1（需 select_related('user')）")


class AccountListNPlusOneTests(_NPlusOneBase):
    def test_balance_no_per_account_n1(self):
        """account_list 旧实现逐账户调用 Account.balance（每账户 4 次聚合）。"""
        for i in range(3):
            a = Account.objects.create(user=self.u, name=f"acc-{i}")
            _exp(self.u, "10", type_="income", account=a)
        q_small = self._count_queries(reverse("account_list"))

        for i in range(3, 12):
            a = Account.objects.create(user=self.u, name=f"acc-{i}")
            _exp(self.u, "10", type_="income", account=a)
        q_big = self._count_queries(reverse("account_list"))

        self.assertEqual(q_small, q_big,
                         "account_list 查询数随账户数增长，疑似 Account.balance N+1（需批量聚合）")

    def test_balance_value_matches_property(self):
        """批量聚合算出的余额须与 Account.balance property 完全一致。"""
        a = Account.objects.create(user=self.u, name="校验账户", initial_balance=Decimal("100"))
        _exp(self.u, "50", type_="income", account=a)            # +50
        _exp(self.u, "20", type_="expense", account=a)           # -20
        _exp(self.u, "30", type_="transfer", account=a)          # 转出 -30（无接收方）
        b = Account.objects.create(user=self.u, name="转入方")
        _exp(self.u, "15", type_="transfer", account=a)          # 先建一笔从 a 转出的 15
        last = Expense.objects.filter(user=self.u, type="transfer", account=a).order_by("id").last()
        last.transfer_to_account = b                             # 落地为 a → b 转账
        last.save()

        res = self.client.get(reverse("account_list"))
        rows = res.context["rows"]
        by_name = {r["obj"].name: r["balance"] for r in rows}
        self.assertEqual(by_name["校验账户"], a.balance)
        self.assertEqual(by_name["转入方"], b.balance)


class DashboardNPlusOneTests(_NPlusOneBase):
    def test_queries_stable_with_expense_count(self):
        """dashboard 全部用单次 GROUP BY 聚合；查询数不应随支出行数增长。"""
        cat = Category.objects.create(user=self.u, name="餐饮", type="expense")
        _exp(self.u, "10", cat=cat)
        q_small = self._count_queries(reverse("dashboard"))

        for i in range(8):
            _exp(self.u, "10", cat=cat, days_ago=i)
        q_big = self._count_queries(reverse("dashboard"))

        self.assertEqual(q_small, q_big,
                         "dashboard 查询数随支出数增长，疑似逐笔聚合 N+1")

    def test_display_title_no_category_query_with_select_related(self):
        """Expense.display_title 访问 category.name；select_related 后不应逐行查 category。"""
        cat = Category.objects.create(user=self.u, name="餐饮", type="expense")
        e = Expense.objects.create(
            user=self.u, category=cat, type="expense", amount=10,
            status="confirmed", source="manual", occurred_at=timezone.now(),
        )
        # 不带 select_related：取对象 1 次 + 访问 category 1 次 = 2
        qs = Expense.objects.filter(pk=e.pk)
        with self.assertNumQueries(2):
            for x in qs:
                _ = x.display_title
        # 带 select_related：1 次查询内已 join category = 1
        qs2 = Expense.objects.filter(pk=e.pk).select_related("category")
        with self.assertNumQueries(1):
            for x in qs2:
                _ = x.display_title
