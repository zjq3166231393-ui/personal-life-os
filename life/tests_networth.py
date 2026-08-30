"""净值趋势图（Net Worth Trend）功能测试。

数据底座是 BalanceSnapshot（每日余额快照）：
- daily_balance_series：按日余额序列计算正确（初始 + 收入 - 支出 - 转出 + 转入）
- snapshot_balances 命令：回填历史快照，可重跑（已存在日期忽略）
- net_worth_data：净值 = 当日所有活跃账户余额之和，区间变化 / 30 天变化正确
- net_worth 视图：渲染 200 且上下文净值正确
- home_data：注入 net_worth_now / net_worth_change_30
"""
from datetime import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from life.models import Account, BalanceSnapshot, Expense
from life.services import daily_balance_series, net_worth_data

User = get_user_model()


def _dt(y, m, d, hh=12):
    return datetime(y, m, d, hh, 0, tzinfo=timezone.get_current_timezone())


class NetWorthSeriesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("nwuser", password="pass")
        self.client.login(username="nwuser", password="pass")
        self.a = Account.objects.create(
            user=self.user, name="招行", type="bank", initial_balance=Decimal("1000"),
            is_active=True, is_deleted=False,
        )
        self.b = Account.objects.create(
            user=self.user, name="支付宝", type="alipay", initial_balance=Decimal("0"),
            is_active=True, is_deleted=False,
        )
        # A：1/10 收入 +500，1/15 支出 -200，1/20 转出 -300 到 B
        Expense.objects.create(user=self.user, account=self.a, amount=Decimal("500"), type="income", status="confirmed", occurred_at=_dt(2026, 1, 10))
        Expense.objects.create(user=self.user, account=self.a, amount=Decimal("200"), type="expense", status="confirmed", occurred_at=_dt(2026, 1, 15))
        Expense.objects.create(user=self.user, account=self.a, amount=Decimal("300"), type="transfer", status="confirmed", occurred_at=_dt(2026, 1, 20), transfer_to_account=self.b)
        self.start = timezone.localdate(_dt(2026, 1, 1))
        self.end = timezone.localdate(_dt(2026, 1, 31))

    def _at(self, series, day):
        return dict(series)[day]

    def test_daily_balance_series_account_a(self):
        s = dict(daily_balance_series(self.a, self.start, self.end))
        self.assertEqual(s[timezone.localdate(_dt(2026, 1, 9))], Decimal("1000"))
        self.assertEqual(s[timezone.localdate(_dt(2026, 1, 10))], Decimal("1500"))  # +500 收入
        self.assertEqual(s[timezone.localdate(_dt(2026, 1, 15))], Decimal("1300"))  # -200 支出
        self.assertEqual(s[timezone.localdate(_dt(2026, 1, 20))], Decimal("1000"))  # -300 转出
        self.assertEqual(s[timezone.localdate(_dt(2026, 1, 31))], Decimal("1000"))

    def test_daily_balance_series_account_b_receives_transfer(self):
        s = dict(daily_balance_series(self.b, self.start, self.end))
        self.assertEqual(s[timezone.localdate(_dt(2026, 1, 19))], Decimal("0"))
        self.assertEqual(s[timezone.localdate(_dt(2026, 1, 20))], Decimal("300"))  # 转入 +300
        self.assertEqual(s[timezone.localdate(_dt(2026, 1, 31))], Decimal("300"))

    def test_snapshot_command_backfills_and_is_idempotent(self):
        call_command("snapshot_balances", username=self.user.username)
        # 窗口从最早交易日 (1/10) 到今天；两账户各有连续快照
        snaps = BalanceSnapshot.objects.filter(user=self.user)
        self.assertGreater(snaps.count(), 0)
        # 同一天同一账户唯一
        from django.db.models import Count
        dup = snaps.values("account", "date").annotate(c=Count("id")).filter(c__gt=1)
        self.assertEqual(dup.count(), 0)
        # 1/20 当天两账户余额之和 = 1000 + 300 = 1300
        d = timezone.localdate(_dt(2026, 1, 20))
        total = sum((x.balance for x in snaps.filter(date=d)), Decimal("0"))
        self.assertEqual(total, Decimal("1300"))
        # 可重跑：再次执行行数不变
        count1 = snaps.count()
        call_command("snapshot_balances", username=self.user.username)
        self.assertEqual(BalanceSnapshot.objects.filter(user=self.user).count(), count1)

    def test_net_worth_data_aggregates_accounts(self):
        call_command("snapshot_balances", username=self.user.username)
        data = net_worth_data(self.user)
        # 当前净值 = A 1000 + B 300 = 1300
        self.assertEqual(data["current"], Decimal("1300"))
        self.assertTrue(data["has_data"])
        self.assertIsNotNone(data["change_30"])  # 回填窗口 > 30 天
        self.assertEqual(len(data["labels"]), len(data["series"]))
        self.assertEqual(len(data["accounts"]), 2)

    def test_net_worth_view_renders(self):
        call_command("snapshot_balances", username=self.user.username)
        resp = self.client.get(reverse("net_worth"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["current"], Decimal("1300"))
        self.assertContains(resp, "净值趋势")

    def test_net_worth_data_range_filter(self):
        call_command("snapshot_balances", username=self.user.username)
        full = net_worth_data(self.user)
        windowed = net_worth_data(self.user, days=30)
        self.assertEqual(windowed["days"], 30)
        self.assertLessEqual(len(windowed["labels"]), len(full["labels"]))
        self.assertEqual(windowed["current"], Decimal("1300"))
        self.assertEqual(windowed["labels"][-1], full["labels"][-1])

    def test_net_worth_view_range_param(self):
        call_command("snapshot_balances", username=self.user.username)
        resp = self.client.get(reverse("net_worth") + "?range=30")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["range"], "30")
        self.assertEqual(resp.context["current"], Decimal("1300"))

    def test_ensure_today_snapshots_idempotent(self):
        # 未跑命令时，net_worth_data 懒确保今日快照
        before = BalanceSnapshot.objects.filter(user=self.user, date=timezone.localdate()).count()
        net_worth_data(self.user)
        after = BalanceSnapshot.objects.filter(user=self.user, date=timezone.localdate()).count()
        # 每个活跃账户恰好一条今日快照，且不重复累加
        self.assertEqual(after, 2)
        self.assertGreaterEqual(after, before)
        net_worth_data(self.user)  # 再调用一次不应新增
        self.assertEqual(BalanceSnapshot.objects.filter(user=self.user, date=timezone.localdate()).count(), 2)


class NetWorthHomeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("nwhome", password="pass")
        self.client.login(username="nwhome", password="pass")
        self.a = Account.objects.create(
            user=self.user, name="现金", type="cash", initial_balance=Decimal("800"),
            is_active=True, is_deleted=False,
        )
        Expense.objects.create(user=self.user, account=self.a, amount=Decimal("200"), type="income", status="confirmed", occurred_at=_dt(2026, 2, 1))

    def test_home_injects_net_worth(self):
        resp = self.client.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)
        # 当前净值 = 800 + 200 = 1000（无快照时退化到实时余额）
        self.assertEqual(resp.context["net_worth_now"], Decimal("1000"))
        self.assertContains(resp, "净值概览")

    def test_home_net_worth_sparkline(self):
        from life.services import home_data

        call_command("snapshot_balances", username=self.user.username)
        data = home_data(self.user)
        self.assertIsNotNone(data["net_worth_sparkline"])
        self.assertIn("points", data["net_worth_sparkline"])
        self.assertTrue(len(data["net_worth_sparkline"]["points"].split()) >= 2)
        # 不足 2 天快照时返回 None（首页不渲染走势）
        from life.models import BalanceSnapshot
        BalanceSnapshot.objects.all().delete()
        self.assertIsNone(home_data(self.user)["net_worth_sparkline"])
