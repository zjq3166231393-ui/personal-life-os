"""周期性账单自动入账测试（P0-1）。

核心保证：
- **幂等**：同一到期日只入账一次，重复调用 / cron 每天跑都不会重复
- **不抢用户的账**：用户已手动记过的，不再自动记第二遍
- **可控**：auto_post=False 只提醒不记账
- **边界**：end_date 之后不生成；2 月 31 日这类不存在的日期取月末
- **越权隔离**：只处理指定用户自己的计划
"""

from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from life.recurring import due_dates_for, generate_due_recurring, maybe_generate_for_user

from .models import Category, Expense, RecurringExpense


def _mkuser(name):
    return User.objects.create_user(name, password="TestPass123!")


def _mkplan(user, **kw):
    defaults = {
        "name": "房租",
        "amount": Decimal("3500"),
        "frequency": RecurringExpense.Frequency.MONTHLY,
        "due_day": 5,
        "start_date": date(2026, 1, 1),
    }
    defaults.update(kw)
    return RecurringExpense.objects.create(user=user, **defaults)


class DueDateCalculationTests(TestCase):
    """到期日推算的边界情况。"""

    def setUp(self):
        self.u = _mkuser("due_u1")

    def test_monthly_dates(self):
        plan = _mkplan(self.u, frequency="monthly", due_day=5, start_date=date(2026, 1, 1))
        dates = due_dates_for(plan, date(2026, 4, 30))
        self.assertEqual(dates, [date(2026, 1, 5), date(2026, 2, 5), date(2026, 3, 5), date(2026, 4, 5)])

    def test_monthly_clamps_to_month_end(self):
        """31 日在 2 月不存在 → 取月末（避免丢失一个月的账）。"""
        plan = _mkplan(self.u, frequency="monthly", due_day=31, start_date=date(2026, 1, 1))
        dates = due_dates_for(plan, date(2026, 3, 31))
        self.assertEqual(dates, [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)])

    def test_weekly_dates(self):
        plan = _mkplan(self.u, frequency="weekly", start_date=date(2026, 1, 1))
        dates = due_dates_for(plan, date(2026, 1, 29))
        self.assertEqual(dates[:5], [
            date(2026, 1, 1), date(2026, 1, 8), date(2026, 1, 15), date(2026, 1, 22), date(2026, 1, 29),
        ])

    def test_quarterly_dates(self):
        plan = _mkplan(self.u, frequency="quarterly", due_day=10, start_date=date(2026, 1, 1))
        dates = due_dates_for(plan, date(2026, 12, 31))
        self.assertEqual(dates, [date(2026, 1, 10), date(2026, 4, 10), date(2026, 7, 10), date(2026, 10, 10)])

    def test_yearly_dates(self):
        plan = _mkplan(self.u, frequency="yearly", due_day=3, start_date=date(2026, 6, 1))
        dates = due_dates_for(plan, date(2028, 12, 31))
        self.assertEqual(dates, [date(2026, 6, 3), date(2027, 6, 3), date(2028, 6, 3)])

    def test_end_date_stops_generation(self):
        plan = _mkplan(self.u, frequency="monthly", due_day=5,
                       start_date=date(2026, 1, 1), end_date=date(2026, 2, 28))
        dates = due_dates_for(plan, date(2026, 6, 30))
        self.assertEqual(dates, [date(2026, 1, 5), date(2026, 2, 5)])

    def test_no_dates_before_start(self):
        plan = _mkplan(self.u, frequency="monthly", due_day=5, start_date=date(2026, 3, 10))
        dates = due_dates_for(plan, date(2026, 4, 30))
        self.assertEqual(dates, [date(2026, 4, 5)], "start_date 之前的到期日不应生成")


class AutoPostTests(TestCase):
    """自动生成账目的行为。"""

    def setUp(self):
        self.u = _mkuser("post_u1")
        self.cat = Category.objects.create(user=self.u, name="居住", type="expense")

    def test_generates_expense_for_due_dates(self):
        plan = _mkplan(self.u, category=self.cat, amount=Decimal("3500"), due_day=5,
                       start_date=date(2026, 1, 1))
        stats = generate_due_recurring(user=self.u, today=date(2026, 3, 31))
        self.assertEqual(stats["created"], 3)
        self.assertEqual(
            Expense.objects.filter(user=self.u, source="recurring", is_deleted=False).count(), 3
        )
        exp = Expense.objects.filter(user=self.u, source="recurring").order_by("occurred_at").first()
        self.assertEqual(exp.amount, Decimal("3500"))
        self.assertEqual(exp.occurred_at.date(), date(2026, 1, 5))
        self.assertEqual(exp.category_id, self.cat.pk)
        self.assertEqual(exp.status, "confirmed")

    def test_idempotent_across_repeated_runs(self):
        """重复执行（cron 每天跑）不应重复入账。"""
        _mkplan(self.u, category=self.cat, due_day=5, start_date=date(2026, 1, 1))
        generate_due_recurring(user=self.u, today=date(2026, 3, 31))
        generate_due_recurring(user=self.u, today=date(2026, 3, 31))
        stats = generate_due_recurring(user=self.u, today=date(2026, 3, 31))
        self.assertEqual(stats["created"], 0)
        self.assertEqual(
            Expense.objects.filter(user=self.u, source="recurring", is_deleted=False).count(), 3
        )

    def test_does_not_duplicate_manually_recorded_expense(self):
        """用户已手动记过同分类同金额的账 → 跳过，不记第二遍。"""
        plan = _mkplan(self.u, category=self.cat, amount=Decimal("3500"),
                       due_day=5, start_date=date(2026, 1, 1))
        Expense.objects.create(
            user=self.u, category=self.cat, amount=Decimal("3500"),
            occurred_at=timezone.make_aware(timezone.datetime(2026, 1, 6, 12, 0)),
            note="房租（我手记的）", status="confirmed",
        )
        stats = generate_due_recurring(user=self.u, today=date(2026, 1, 31))
        self.assertEqual(stats["created"], 0)
        self.assertEqual(stats["skipped"], 1)

    def test_auto_post_disabled_skips(self):
        _mkplan(self.u, category=self.cat, due_day=5, start_date=date(2026, 1, 1), auto_post=False)
        stats = generate_due_recurring(user=self.u, today=date(2026, 3, 31))
        self.assertEqual(stats["created"], 0)
        self.assertEqual(Expense.objects.filter(user=self.u).count(), 0)

    def test_inactive_plan_skipped(self):
        _mkplan(self.u, category=self.cat, due_day=5, start_date=date(2026, 1, 1), is_active=False)
        stats = generate_due_recurring(user=self.u, today=date(2026, 3, 31))
        self.assertEqual(stats["created"], 0)

    def test_dry_run_writes_nothing(self):
        _mkplan(self.u, category=self.cat, due_day=5, start_date=date(2026, 1, 1))
        stats = generate_due_recurring(user=self.u, today=date(2026, 3, 31), dry_run=True)
        self.assertEqual(stats["created"], 3)
        self.assertEqual(Expense.objects.filter(user=self.u).count(), 0)

    def test_deleted_auto_expense_is_not_regenerated(self):
        """用户删掉自动生成的账目后，不应第二天又被重新生成。"""
        _mkplan(self.u, category=self.cat, due_day=5, start_date=date(2026, 1, 1))
        generate_due_recurring(user=self.u, today=date(2026, 1, 31))
        Expense.objects.filter(user=self.u, source="recurring").update(is_deleted=True)
        stats = generate_due_recurring(user=self.u, today=date(2026, 1, 31))
        self.assertEqual(stats["created"], 0)


class AutoPostIsolationTests(TestCase):
    """多用户隔离。"""

    def setUp(self):
        self.a = _mkuser("iso_a")
        self.b = _mkuser("iso_b")
        self.cat_a = Category.objects.create(user=self.a, name="居住A", type="expense")
        self.cat_b = Category.objects.create(user=self.b, name="居住B", type="expense")
        _mkplan(self.a, category=self.cat_a, name="A的房租", due_day=5, start_date=date(2026, 1, 1))
        _mkplan(self.b, category=self.cat_b, name="B的房租", due_day=5, start_date=date(2026, 1, 1))

    def test_user_scoped_generation(self):
        stats = generate_due_recurring(user=self.a, today=date(2026, 2, 28))
        self.assertEqual(stats["created"], 2)
        self.assertEqual(Expense.objects.filter(user=self.b).count(), 0)

    def test_all_users_generation(self):
        stats = generate_due_recurring(today=date(2026, 2, 28))
        self.assertEqual(stats["created"], 4)
        self.assertEqual(Expense.objects.filter(source="recurring").count(), 4)


class MaybeGenerateThrottleTests(TestCase):
    """首页惰性触发的每日节流。"""

    def setUp(self):
        self.u = _mkuser("thr_u1")
        self.cat = Category.objects.create(user=self.u, name="居住", type="expense")
        self.plan = _mkplan(self.u, category=self.cat, due_day=5, start_date=date(2026, 1, 1))

    def tearDown(self):
        from django.core.cache import cache
        cache.clear()

    def test_runs_once_per_day(self):
        first = maybe_generate_for_user(self.u, today=date(2026, 2, 28))
        self.assertIsNotNone(first)
        self.assertEqual(first["created"], 2)

        second = maybe_generate_for_user(self.u, today=date(2026, 2, 28))
        self.assertIsNone(second, "同一天第二次应被缓存节流跳过")

    def test_runs_again_next_day(self):
        maybe_generate_for_user(self.u, today=date(2026, 2, 28))
        nxt = maybe_generate_for_user(self.u, today=date(2026, 3, 1))
        self.assertIsNotNone(nxt)


class GenerateCommandTests(TestCase):
    """管理命令。"""

    def setUp(self):
        self.u = _mkuser("cmd_u1")
        self.cat = Category.objects.create(user=self.u, name="居住", type="expense")
        _mkplan(self.u, category=self.cat, due_day=5, start_date=date(2026, 1, 1))

    def test_dry_run_command(self):
        out = StringIO()
        call_command("generate_recurring", "--dry-run", stdout=out)
        self.assertIn("预览", out.getvalue())
        self.assertEqual(Expense.objects.filter(user=self.u).count(), 0)

    def test_command_generates(self):
        """命令真实执行（以今天为基准日，能生成即算通过）。"""
        out = StringIO()
        call_command("generate_recurring", stdout=out)
        self.assertIn("扫描固定支出", out.getvalue())
        self.assertGreater(
            Expense.objects.filter(user=self.u, source="recurring").count(), 0
        )

    def test_command_unknown_user(self):
        out = StringIO()
        call_command("generate_recurring", "--user=nobody_here", stderr=out)
        self.assertIn("用户不存在", out.getvalue())


class AutoPostToggleTests(TestCase):
    """UI 侧的自动入账开关（recurring_create / recurring_edit）。"""

    def setUp(self):
        self.u = _mkuser("toggle_u1")
        self.cat = Category.objects.create(user=self.u, name="居住", type="expense")
        self.client.force_login(self.u)

    def _post(self, **extra):
        payload = {
            "name": "房租",
            "amount": "3000",
            "category": self.cat.pk,
            "frequency": "monthly",
            "due_day": "5",
            "start_date": "2026-01-01",
            "remind_days_before": "3",
        }
        payload.update(extra)
        return payload

    def test_create_default_auto_post_on(self):
        """新建时勾选即开启。"""
        self.client.post(reverse("recurring_create"), self._post(auto_post="on"))
        plan = RecurringExpense.objects.get(user=self.u, name="房租")
        self.assertTrue(plan.auto_post)

    def test_create_without_toggle_keeps_reminder_only(self):
        """不勾选 = 只提醒不记账（复选框未选中时浏览器不提交该字段）。"""
        self.client.post(reverse("recurring_create"), self._post())
        plan = RecurringExpense.objects.get(user=self.u, name="房租")
        self.assertFalse(plan.auto_post)

    def test_edit_can_turn_off(self):
        plan = _mkplan(self.u, category=self.cat, name="房租", due_day=5,
                       start_date=date(2026, 1, 1), auto_post=True)
        self.client.post(reverse("recurring_edit", args=[plan.pk]), self._post())
        plan.refresh_from_db()
        self.assertFalse(plan.auto_post)

    def test_edit_can_turn_on(self):
        plan = _mkplan(self.u, category=self.cat, name="房租", due_day=5,
                       start_date=date(2026, 1, 1), auto_post=False)
        self.client.post(reverse("recurring_edit", args=[plan.pk]), self._post(auto_post="on"))
        plan.refresh_from_db()
        self.assertTrue(plan.auto_post)

    def test_edit_form_renders_toggle(self):
        plan = _mkplan(self.u, category=self.cat, name="房租", due_day=5,
                       start_date=date(2026, 1, 1), auto_post=True)
        html = self.client.get(reverse("recurring_edit", args=[plan.pk])).content.decode()
        self.assertIn('name="auto_post"', html)
        self.assertIn("checked", html)

    def test_create_form_defaults_to_checked(self):
        html = self.client.get(reverse("recurring_create")).content.decode()
        self.assertIn('name="auto_post"', html)
        self.assertIn("checked", html)

    def test_list_shows_status_badge(self):
        _mkplan(self.u, category=self.cat, name="房租", due_day=5,
                start_date=date(2026, 1, 1), auto_post=True)
        _mkplan(self.u, category=self.cat, name="话费", due_day=8,
                start_date=date(2026, 1, 1), auto_post=False)
        html = self.client.get(reverse("recurring_list")).content.decode()
        self.assertIn("自动入账", html)
        self.assertIn("仅提醒", html)

    def test_only_active_auto_post_plans_generate(self):
        """停用或关闭自动入账的计划都不会生成账目。"""
        _mkplan(self.u, category=self.cat, name="房租", due_day=5,
                start_date=date(2026, 1, 1), auto_post=True, is_active=False)
        _mkplan(self.u, category=self.cat, name="话费", due_day=8,
                start_date=date(2026, 1, 1), auto_post=False)
        stats = generate_due_recurring(user=self.u, today=date(2026, 12, 31))
        self.assertEqual(stats["created"], 0)
        self.assertEqual(
            Expense.objects.filter(user=self.u, source="recurring").count(), 0
        )
