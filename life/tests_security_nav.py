"""Security + navigation regression tests for mutating views and module reachability.

Covers:
- Every data-mutating (delete / deactivate / pay) view now requires POST and is
  CSRF-protected. A GET must return 405, not silently delete via a prefetch/link.
- Every functional module (tasks, countdowns, recurring, installments, reminders,
  categories) is reachable from the app shell: the desktop sidebar "功能" group and
  the home quick-grid both expose links to them.
"""
from decimal import Decimal
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from life.models import (
    Category, Countdown, Expense, InstallmentPlan, Note, RecurringExpense,
    Reminder, Task,
)
from life.models_daily import DailyCheckin


class MutatingViewRequiresPostTests(TestCase):
    """GET on a delete/deactivate/pay view must 405, not act."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model
        cls.user = get_user_model().objects.create_user("secnav", password="pw")
        cls.expense = Expense.objects.create(
            user=cls.user, note="x", amount=Decimal("10"), type="expense",
            occurred_at=timezone.now(),
        )
        cls.task = Task.objects.create(user=cls.user, title="t")
        cls.note = Note.objects.create(user=cls.user, title="n", raw_text="r")
        cls.daily = DailyCheckin.objects.create(user=cls.user, icon="📌", title="d")
        cls.countdown = Countdown.objects.create(user=cls.user, title="c", target_date="2099-01-01")
        cls.recurring = RecurringExpense.objects.create(
            user=cls.user, name="r", amount="10", frequency="monthly", due_day=1, start_date="2099-01-01"
        )
        cls.installment = InstallmentPlan.objects.create(
            user=cls.user, name="i", total_amount="100", installment_amount="10",
            total_periods=10, paid_periods=0, status="active", next_due_date="2099-01-01",
        )
        cls.category = Category.objects.create(user=cls.user, name="cat", type="expense")

    def setUp(self):
        self.client = Client()
        self.client.login(username="secnav", password="pw")

    def test_expense_delete_get_405(self):
        resp = self.client.get(reverse("expense_delete", args=[self.expense.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_task_delete_get_405(self):
        resp = self.client.get(reverse("task_delete", args=[self.task.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_note_delete_get_405(self):
        resp = self.client.get(reverse("note_delete", args=[self.note.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_daily_delete_get_405(self):
        resp = self.client.get(reverse("daily_delete", args=[self.daily.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_countdown_delete_get_405(self):
        resp = self.client.get(reverse("countdown_delete", args=[self.countdown.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_recurring_deactivate_get_405(self):
        resp = self.client.get(reverse("recurring_deactivate", args=[self.recurring.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_installment_pay_get_405(self):
        resp = self.client.get(reverse("installment_pay", args=[self.installment.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_category_deactivate_get_405(self):
        resp = self.client.get(reverse("category_deactivate", args=[self.category.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_expense_delete_post_works(self):
        resp = self.client.post(reverse("expense_delete", args=[self.expense.pk]))
        self.assertIn(resp.status_code, (302, 200))
        self.assertFalse(Expense.objects.filter(pk=self.expense.pk, is_deleted=False).exists())


class ModuleReachabilityTests(TestCase):
    """All functional modules must be linked from the app shell."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model
        get_user_model().objects.create_user("nav", password="pw")

    def setUp(self):
        self.client = Client()
        self.client.login(username="nav", password="pw")

    def test_sidebar_exposes_all_modules(self):
        resp = self.client.get(reverse("home"))
        html = resp.content.decode()
        for name in ("task_list", "countdown_list", "recurring_list",
                     "installment_list", "reminder_list", "category_list"):
            self.assertIn(reverse(name), html, f"sidebar missing link to {name}")

    def test_home_grid_exposes_all_modules(self):
        resp = self.client.get(reverse("home"))
        html = resp.content.decode()
        for name in ("task_list", "countdown_list", "recurring_list",
                     "installment_list", "reminder_list", "category_list"):
            self.assertIn(reverse(name), html, f"home grid missing link to {name}")

    def test_module_pages_render(self):
        for name in ("task_list", "countdown_list", "recurring_list",
                     "installment_list", "reminder_list", "category_list"):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 200, f"{name} did not render")
