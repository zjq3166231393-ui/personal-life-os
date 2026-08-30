"""现金流预测：服务单测 + 视图测试。"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse

from .forecast import _recurring_occurrences, cashflow_forecast
from .models import Account, RecurringExpense

User = get_user_model()


def _make_user(username="cfuser"):
    return User.objects.create_user(username=username, password="pw123456")


class RecurringOccurrenceTests(TestCase):
    def setUp(self):
        self.user = _make_user()

    def test_monthly_within_window(self):
        re = RecurringExpense.objects.create(
            user=self.user, name="Rent", amount=Decimal("500"),
            frequency="monthly", due_day=15, start_date=date(2026, 1, 1),
            is_active=True,
        )
        occ = list(_recurring_occurrences(re, date(2026, 8, 10), date(2026, 9, 9)))
        self.assertEqual(occ, [date(2026, 8, 15)])  # 9/15 超出窗口

    def test_monthly_clamps_day_to_month_end(self):
        re = RecurringExpense.objects.create(
            user=self.user, name="X", amount=Decimal("10"),
            frequency="monthly", due_day=31, start_date=date(2026, 1, 1),
            is_active=True,
        )
        occ = list(_recurring_occurrences(re, date(2026, 1, 15), date(2026, 3, 15)))
        self.assertIn(date(2026, 2, 28), occ)  # 2 月没有 31 日 → 收敛到 28
        self.assertNotIn(date(2026, 3, 31), occ)  # 超出窗口上界

    def test_weekly_every_seven_days(self):
        re = RecurringExpense.objects.create(
            user=self.user, name="W", amount=Decimal("100"),
            frequency="weekly", due_day=1, start_date=date(2026, 8, 3),
            is_active=True,
        )
        occ = list(_recurring_occurrences(re, date(2026, 8, 10), date(2026, 9, 9)))
        self.assertEqual(
            occ,
            [date(2026, 8, 10), date(2026, 8, 17), date(2026, 8, 24),
             date(2026, 8, 31), date(2026, 9, 7)],
        )

    def test_quarterly_steps_three_months(self):
        re = RecurringExpense.objects.create(
            user=self.user, name="Q", amount=Decimal("50"),
            frequency="quarterly", due_day=10, start_date=date(2026, 1, 10),
            is_active=True,
        )
        occ = list(_recurring_occurrences(re, date(2026, 2, 1), date(2026, 12, 31)))
        self.assertEqual(occ, [date(2026, 4, 10), date(2026, 7, 10), date(2026, 10, 10)])

    def test_yearly_same_month_each_year(self):
        re = RecurringExpense.objects.create(
            user=self.user, name="Y", amount=Decimal("99"),
            frequency="yearly", due_day=5, start_date=date(2026, 3, 5),
            is_active=True,
        )
        occ = list(_recurring_occurrences(re, date(2026, 1, 1), date(2027, 12, 31)))
        self.assertEqual(occ, [date(2026, 3, 5), date(2027, 3, 5)])

    def test_end_date_excludes_later_occurrences(self):
        re = RecurringExpense.objects.create(
            user=self.user, name="E", amount=Decimal("20"),
            frequency="monthly", due_day=15, start_date=date(2026, 1, 1),
            end_date=date(2026, 8, 20), is_active=True,
        )
        occ = list(_recurring_occurrences(re, date(2026, 8, 10), date(2026, 9, 9)))
        self.assertEqual(occ, [date(2026, 8, 15)])

    def test_inactive_excluded(self):
        re = RecurringExpense.objects.create(
            user=self.user, name="I", amount=Decimal("20"),
            frequency="monthly", due_day=15, start_date=date(2026, 1, 1),
            is_active=False,
        )
        self.assertEqual(list(_recurring_occurrences(re, date(2026, 8, 1), date(2026, 9, 30))), [])


class CashflowForecastTests(TestCase):
    def setUp(self):
        self.user = _make_user()

    def test_with_account_and_one_bill(self):
        Account.objects.create(user=self.user, name="Wallet", initial_balance=Decimal("1000"))
        RecurringExpense.objects.create(
            user=self.user, name="Rent", amount=Decimal("500"),
            frequency="monthly", due_day=15, start_date=date(2026, 1, 1),
            is_active=True,
        )
        cf = cashflow_forecast(self.user, days=30, as_of=date(2026, 8, 10))
        self.assertTrue(cf["has_accounts"])
        self.assertEqual(cf["start_balance"], Decimal("1000"))
        self.assertEqual(len(cf["series"]), 31)  # 含今天共 31 天
        self.assertEqual(cf["min_balance"], Decimal("500"))  # 8/15 扣 500
        self.assertEqual(cf["min_date"], date(2026, 8, 15))
        self.assertFalse(cf["goes_negative"])
        self.assertEqual(cf["next_bill"]["date"], date(2026, 8, 15))
        self.assertEqual(cf["projected_end"], Decimal("500"))

    def test_goes_negative_flagged(self):
        Account.objects.create(user=self.user, name="Wallet", initial_balance=Decimal("300"))
        RecurringExpense.objects.create(
            user=self.user, name="Rent", amount=Decimal("500"),
            frequency="monthly", due_day=15, start_date=date(2026, 1, 1),
            is_active=True,
        )
        cf = cashflow_forecast(self.user, days=30, as_of=date(2026, 8, 10))
        self.assertTrue(cf["goes_negative"])
        self.assertEqual(cf["min_balance"], Decimal("-200"))
        self.assertEqual(cf["min_date"], date(2026, 8, 15))

    def test_no_accounts_starts_at_zero(self):
        RecurringExpense.objects.create(
            user=self.user, name="Rent", amount=Decimal("200"),
            frequency="monthly", due_day=15, start_date=date(2026, 1, 1),
            is_active=True,
        )
        cf = cashflow_forecast(self.user, days=30, as_of=date(2026, 8, 10))
        self.assertFalse(cf["has_accounts"])
        self.assertEqual(cf["start_balance"], Decimal("0"))
        self.assertEqual(cf["min_balance"], Decimal("-200"))
        self.assertTrue(cf["goes_negative"])

    def test_soft_deleted_account_excluded(self):
        Account.objects.create(user=self.user, name="Old", initial_balance=Decimal("999"),
                               is_deleted=True)
        cf = cashflow_forecast(self.user, days=10, as_of=date(2026, 8, 10))
        self.assertFalse(cf["has_accounts"])
        self.assertEqual(cf["start_balance"], Decimal("0"))


class ForecastViewTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.client = Client()
        self.client.force_login(self.user)

    def test_page_renders_with_data(self):
        Account.objects.create(user=self.user, name="Wallet", initial_balance=Decimal("1000"))
        RecurringExpense.objects.create(
            user=self.user, name="Rent", amount=Decimal("500"),
            frequency="monthly", due_day=15, start_date=date(2026, 1, 1),
            is_active=True,
        )
        resp = self.client.get(reverse("cashflow_forecast"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "现金流预测")
        self.assertContains(resp, "cfChart")

    def test_empty_state_when_no_data(self):
        resp = self.client.get(reverse("cashflow_forecast"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "还没有可预测的数据")

    def test_home_shows_cashflow_card(self):
        Account.objects.create(user=self.user, name="Wallet", initial_balance=Decimal("1000"))
        resp = self.client.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "现金流预测")  # 首页卡片（有账户时显示）
