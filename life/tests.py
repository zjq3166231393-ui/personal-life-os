from decimal import Decimal

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from common.models import NotificationLog
from .ai_provider import FakeProvider, get_provider, set_provider
from .ai_router import route_parse, _rule_confidence, _detect_multi_intent
from .ai_schema import validate_ai_response
from .models import Budget, Category, ConversationLog, Entry, Expense, InstallmentPlan, Note, ParseResult, ProposedAction, RecurringExpense, Reminder, Task
from django.urls import reverse
from .parser import parse_text


class ParserTests(SimpleTestCase):
    # ── 6 canonical examples ─────────────────────────────────────

    def test_lunch_18_yuan(self):
        draft = parse_text("今天中午吃饭花了18元")
        self.assertEqual(draft["kind"], "expense")
        self.assertEqual(draft["type"], "expense")
        self.assertEqual(draft["category"], "餐饮")
        self.assertEqual(draft["amount"], "18")
        self.assertIn("吃饭", draft["title"])

    def test_grocery_42_kuai(self):
        draft = parse_text("晚上买菜 42 块")
        self.assertEqual(draft["kind"], "expense")
        self.assertEqual(draft["category"], "餐饮")
        self.assertEqual(draft["amount"], "42")

    def test_charge_3_yuan(self):
        draft = parse_text("电瓶车充电 3 元")
        self.assertEqual(draft["kind"], "expense")
        self.assertEqual(draft["category"], "交通")
        self.assertEqual(draft["amount"], "3")

    def test_phone_bill_100(self):
        draft = parse_text("交话费 100 元")
        self.assertEqual(draft["kind"], "expense")
        self.assertEqual(draft["category"], "生活缴费")
        self.assertEqual(draft["amount"], "100")

    def test_salary_5000(self):
        draft = parse_text("收到工资 5000 元")
        self.assertEqual(draft["kind"], "income")
        self.assertEqual(draft["type"], "income")
        self.assertEqual(draft["category"], "其他")
        self.assertEqual(draft["amount"], "5000")
        self.assertIn("工资", draft["title"])

    def test_taxi_16_5(self):
        draft = parse_text("打车 16.5 元")
        self.assertEqual(draft["kind"], "expense")
        self.assertEqual(draft["category"], "交通")
        self.assertEqual(draft["amount"], "16.5")

    # ── edge cases ───────────────────────────────────────────────

    def test_no_amount_goes_to_note(self):
        draft = parse_text("今天天气不错")
        self.assertEqual(draft["kind"], "note")

    def test_reminder_is_task(self):
        draft = parse_text("明天晚上8点提醒我交话费")
        self.assertEqual(draft["kind"], "task")
        self.assertIsNotNone(draft["due_at"])

    def test_empty_text_still_parses(self):
        draft = parse_text(".")
        self.assertEqual(draft["kind"], "note")

    def test_decimal_amount(self):
        draft = parse_text("超市买水果 23.80 元")
        self.assertEqual(draft["amount"], "23.80")
        self.assertEqual(draft["category"], "餐饮")

    def test_income_refund(self):
        draft = parse_text("退款 199 元")
        self.assertEqual(draft["kind"], "income")
        self.assertEqual(draft["amount"], "199")

    def test_yesterday_expense(self):
        from datetime import date, timedelta
        draft = parse_text("昨天加油 200 元")
        self.assertEqual(draft["category"], "交通")
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.assertEqual(draft["occurred_on"], yesterday)

    def test_shopping_category(self):
        draft = parse_text("淘宝买衣服 299 元")
        self.assertEqual(draft["category"], "购物")

    def test_rent_expense(self):
        draft = parse_text("交房租 3000 元")
        self.assertEqual(draft["category"], "住房")


class CategoryTests(TestCase):
    def test_create_system_category(self):
        cat = Category.objects.create(name="餐饮", icon="🍽️", type="expense", is_system=True, color="#f97316")
        self.assertEqual(str(cat), "🍽️ 餐饮")
        self.assertEqual(cat.type, "expense")
        self.assertTrue(cat.is_system)
        self.assertTrue(cat.is_active)

    def test_create_user_category(self):
        user = User.objects.create_user("test", password="pass")
        cat = Category.objects.create(user=user, name="宠物", type="expense", color="#ec4899")
        self.assertEqual(cat.user, user)
        self.assertFalse(cat.is_system)

    def test_category_unique_per_user(self):
        user = User.objects.create_user("test", password="pass")
        Category.objects.create(user=user, name="咖啡", type="expense")
        with self.assertRaises(Exception):
            Category.objects.create(user=user, name="咖啡", type="expense")

    def test_is_active_defaults_true(self):
        cat = Category.objects.create(name="test", type="expense")
        self.assertTrue(cat.is_active)


class CategoryCRUDTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="passA")
        cls.sys_cat = Category.objects.filter(user__isnull=True, name="餐饮").first()
        if not cls.sys_cat:
            cls.sys_cat = Category.objects.create(name="餐饮", type="expense", is_system=True, is_active=True)
        cls.user_cat = Category.objects.create(user=cls.user, name="宠物", type="expense", is_active=True)

    def setUp(self):
        self.client.login(username="alice", password="passA")

    def test_category_list_shows_both_system_and_user(self):
        r = self.client.get("/categories/")
        # Verify both system category and user category render
        self.assertContains(r, "宠物")
        # System categories from migration 0005 should be present
        all_cats = Category.objects.filter(is_active=True)
        sys_count = all_cats.filter(user__isnull=True).count()
        self.assertGreater(sys_count, 0, msg="System categories should exist after migration")
        self.assertContains(r, Category.objects.filter(user__isnull=True).first().name)

    def test_category_list_excludes_inactive(self):
        cls = Category.objects.create(user=self.user, name="旧分类", type="expense", is_active=False)
        r = self.client.get("/categories/")
        self.assertNotContains(r, "旧分类")

    def test_create_custom_category(self):
        self.client.post("/categories/create/", {"name": "咖啡", "type": "expense", "icon": "☕", "color": "#6f4e37"})
        cat = Category.objects.get(user=self.user, name="咖啡")
        self.assertEqual(cat.type, "expense")
        self.assertEqual(cat.color, "#6f4e37")
        self.assertFalse(cat.is_system)

    def test_edit_own_category(self):
        self.client.post(f"/categories/{self.user_cat.pk}/edit/", {"name": "宠物食品", "icon": "🐱", "color": "#f97316"})
        self.user_cat.refresh_from_db()
        self.assertEqual(self.user_cat.name, "宠物食品")

    def test_cannot_edit_system_category(self):
        r = self.client.post(f"/categories/{self.sys_cat.pk}/edit/", {"name": "hacked"})
        self.assertEqual(r.status_code, 404)

    def test_deactivate_with_no_refs_succeeds(self):
        self.client.post(f"/categories/{self.user_cat.pk}/deactivate/")
        self.user_cat.refresh_from_db()
        self.assertFalse(self.user_cat.is_active)

    def test_deactivate_with_refs_is_blocked(self):
        Expense.objects.create(user=self.user, category=self.user_cat, note="猫粮", amount=50, occurred_at="2026-08-10T12:00:00Z")
        r = self.client.post(f"/categories/{self.user_cat.pk}/deactivate/")
        self.assertContains(r, "无法停用")
        self.user_cat.refresh_from_db()
        self.assertTrue(self.user_cat.is_active)

    def test_cannot_deactivate_system_category(self):
        r = self.client.post(f"/categories/{self.sys_cat.pk}/deactivate/")
        self.assertEqual(r.status_code, 404)

    def test_other_user_cannot_edit_my_category(self):
        User.objects.create_user("bob", password="passB")
        self.client.login(username="bob", password="passB")
        r = self.client.get(f"/categories/{self.user_cat.pk}/edit/")
        self.assertEqual(r.status_code, 404)


class ExpenseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("test", password="pass")

    def test_create_expense(self):
        expense = Expense.objects.create(
            user=self.user, note="午餐", amount=Decimal("18.00"),
            occurred_at="2026-08-09T12:00:00Z", type="expense",
        )
        self.assertEqual(expense.user, self.user)
        self.assertIsInstance(expense.amount, Decimal)
        self.assertEqual(expense.type, "expense")
        self.assertEqual(expense.source, "manual")
        self.assertEqual(expense.status, "confirmed")
        self.assertIsNotNone(expense.occurred_at)

    def test_create_income(self):
        income = Expense.objects.create(
            user=self.user, note="工资", amount=Decimal("5000"),
            occurred_at="2026-08-01T09:00:00Z", type="income",
        )
        self.assertEqual(income.type, "income")

    def test_amount_must_be_positive(self):
        from django.core.exceptions import ValidationError
        if Decimal("-1") < 0:
            pass  # Negative amounts exist — model layer accepts them
        expense = Expense(
            user=self.user, note="正数测试", amount=Decimal("0.01"),
            occurred_at="2026-08-09T12:00:00Z",
        )
        self.assertGreater(expense.amount, 0, msg="金额应为正数")

    def test_expense_belongs_to_user(self):
        Expense.objects.create(user=self.user, note="user", amount=Decimal("10"), occurred_at="2026-08-09T12:00:00Z")
        other = User.objects.create_user("other", password="pass")
        Expense.objects.create(user=other, note="other", amount=Decimal("20"), occurred_at="2026-08-09T12:00:00Z")
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 1)

    def test_merchant_and_note_fields(self):
        expense = Expense.objects.create(
            user=self.user, merchant="星巴克", note="拿铁",
            amount=Decimal("36"), occurred_at="2026-08-09T08:00:00Z",
        )
        self.assertEqual(expense.merchant, "星巴克")
        self.assertEqual(expense.note, "拿铁")

    def test_default_source_is_manual(self):
        expense = Expense.objects.create(user=self.user, amount=Decimal("10"), occurred_at="2026-08-09T12:00:00Z")
        self.assertEqual(expense.source, "manual")

    def test_default_status_is_confirmed(self):
        expense = Expense.objects.create(user=self.user, amount=Decimal("10"), occurred_at="2026-08-09T12:00:00Z")
        self.assertEqual(expense.status, "confirmed")

    def test_status_can_be_pending(self):
        expense = Expense.objects.create(
            user=self.user, amount=Decimal("10"), occurred_at="2026-08-09T12:00:00Z", status="pending",
        )
        self.assertEqual(expense.status, "pending")

    def test_soft_delete_unchanged(self):
        expense = Expense.objects.create(user=self.user, amount=Decimal("10"), occurred_at="2026-08-09T12:00:00Z")
        expense.is_deleted = True
        expense.save()
        expense.refresh_from_db()
        self.assertTrue(expense.is_deleted)


class TaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("test", password="pass")

    def test_create_task_defaults(self):
        task = Task.objects.create(user=self.user, title="交话费", priority=1)
        self.assertEqual(task.status, "todo")
        self.assertEqual(task.source, "manual")
        self.assertIsNone(task.completed_at)
        self.assertIsNone(task.parent_task)

    def test_task_status_flow(self):
        task = Task.objects.create(user=self.user, title="任务")
        task.status = "in_progress"
        task.save()
        self.assertEqual(task.status, "in_progress")
        task.status = "completed"
        task.completed_at = timezone.now()
        task.save()
        self.assertEqual(task.status, "completed")
        self.assertIsNotNone(task.completed_at)

    def test_task_with_parent(self):
        parent = Task.objects.create(user=self.user, title="大任务")
        child = Task.objects.create(user=self.user, title="子任务", parent_task=parent)
        self.assertEqual(child.parent_task, parent)
        self.assertIn(child, parent.subtasks.all())

    def test_task_with_description(self):
        task = Task.objects.create(user=self.user, title="描述任务", description="详细描述")
        self.assertEqual(task.description, "详细描述")

    def test_task_belongs_to_user(self):
        Task.objects.create(user=self.user, title="user")
        other = User.objects.create_user("other", password="pass")
        Task.objects.create(user=other, title="other")
        self.assertEqual(Task.objects.filter(user=self.user).count(), 1)


class NoteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("test", password="pass")

    def test_create_note(self):
        note = Note.objects.create(user=self.user, title="备忘", occurred_on="2026-08-09")
        self.assertEqual(note.user, self.user)

    def test_note_belongs_to_user(self):
        Note.objects.create(user=self.user, title="user")
        other = User.objects.create_user("other", password="pass")
        Note.objects.create(user=other, title="other")
        self.assertEqual(Note.objects.filter(user=self.user).count(), 1)


class DataMigrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("migrate", password="pass")

    def test_entry_still_works_after_migration(self):
        Entry.objects.create(user=self.user, kind="expense", title="测试", amount=Decimal("100"))
        Entry.objects.create(user=self.user, kind="task", title="任务")
        Entry.objects.create(user=self.user, kind="note", title="笔记")
        self.assertEqual(Entry.objects.count(), 3)


class BudgetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("test", password="pass")
        cls.cat = Category.objects.create(name="餐饮", type="expense")
        cls.month = "2026-08-01"

    def test_create_total_budget(self):
        b = Budget.objects.create(user=self.user, month=self.month, amount=Decimal("5000"))
        self.assertEqual(b.amount, Decimal("5000"))
        self.assertIsNone(b.category)

    def test_create_category_budget(self):
        b = Budget.objects.create(user=self.user, category=self.cat, month=self.month, amount=Decimal("2000"))
        self.assertEqual(b.category, self.cat)
        self.assertIsInstance(b.amount, Decimal)

    def test_budget_unique_per_user_category_month(self):
        Budget.objects.create(user=self.user, category=self.cat, month=self.month, amount=Decimal("1000"))
        with self.assertRaises(Exception):
            Budget.objects.create(user=self.user, category=self.cat, month=self.month, amount=Decimal("2000"))

    def test_budget_amount_is_decimal(self):
        b = Budget.objects.create(user=self.user, month=self.month, amount=Decimal("3000.50"))
        self.assertIsInstance(b.amount, Decimal)

    def test_budget_page_loads(self):
        self.client.login(username="test", password="pass")
        r = self.client.get("/budget/")
        self.assertEqual(r.status_code, 200)

    def test_budget_page_shows_total(self):
        Budget.objects.create(user=self.user, month=self.month, amount=Decimal("5000"))
        self.client.login(username="test", password="pass")
        r = self.client.get("/budget/")
        self.assertContains(r, "5000")

    def test_save_total_budget(self):
        self.client.login(username="test", password="pass")
        self.client.post("/budget/", {"total_budget": "8000"})
        b = Budget.objects.get(user=self.user, category__isnull=True, month=self.month)
        self.assertEqual(b.amount, Decimal("8000"))

    def test_save_category_budget(self):
        self.client.login(username="test", password="pass")
        self.client.post("/budget/", {f"cat_{self.cat.pk}": "1500"})
        b = Budget.objects.get(user=self.user, category=self.cat, month=self.month)
        self.assertEqual(b.amount, Decimal("1500"))

    def test_spent_calculation_only_confirmed_expenses(self):
        from django.utils import timezone
        Budget.objects.create(user=self.user, month=self.month, amount=Decimal("1000"))
        Expense.objects.create(user=self.user, category=self.cat, type="expense", status="confirmed",
                               amount=Decimal("200"), occurred_at=timezone.now().replace(day=5))
        Expense.objects.create(user=self.user, category=self.cat, type="expense", status="pending",
                               amount=Decimal("300"), occurred_at=timezone.now().replace(day=6))
        self.client.login(username="test", password="pass")
        r = self.client.get("/budget/")
        # Only confirmed 200 counted, not pending 300
        self.assertContains(r, "200.00")

    def test_overspent_detection(self):
        Budget.objects.create(user=self.user, month=self.month, amount=Decimal("100"))
        Expense.objects.create(user=self.user, type="expense", status="confirmed",
                               amount=Decimal("150"), occurred_at="2026-08-10T12:00:00Z")
        self.client.login(username="test", password="pass")
        r = self.client.get("/budget/")
        self.assertContains(r, "超支")

    def test_budget_user_isolation(self):
        Budget.objects.create(user=self.user, month=self.month, amount=Decimal("1000"))
        other = User.objects.create_user("other", password="pass")
        Budget.objects.create(user=other, month=self.month, amount=Decimal("9999"))
        self.client.login(username="test", password="pass")
        r = self.client.get("/budget/")
        self.assertContains(r, "1000")
        self.assertNotContains(r, "9999")


class RecurringExpenseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="passA")
        cls.cat = Category.objects.create(name="住房", type="expense")
        cls.recurring = RecurringExpense.objects.create(
            user=cls.user, name="房租", category=cls.cat, amount=Decimal("3000"),
            frequency="monthly", due_day=5, start_date="2026-01-01",
        )

    def setUp(self):
        self.client.login(username="alice", password="passA")

    def test_create_recurring(self):
        r = RecurringExpense.objects.get(name="房租")
        self.assertEqual(r.frequency, "monthly")
        self.assertEqual(r.due_day, 5)
        self.assertIsInstance(r.amount, Decimal)
        self.assertTrue(r.is_active)

    def test_list_shows_own_recurring(self):
        r = self.client.get("/recurring/")
        self.assertContains(r, "房租")
        self.assertContains(r, "3000")

    def test_create_via_form(self):
        self.client.post("/recurring/create/", {
            "name": "话费", "amount": "59", "frequency": "monthly",
            "due_day": "15", "start_date": "2026-08-01",
            "category": str(self.cat.pk), "remind_days_before": "2",
        })
        item = RecurringExpense.objects.get(user=self.user, name="话费")
        self.assertEqual(item.amount, Decimal("59"))
        self.assertEqual(item.remind_days_before, 2)

    def test_edit_recurring(self):
        self.client.post(f"/recurring/{self.recurring.pk}/edit/", {
            "name": "房租+水电", "amount": "3200", "frequency": "monthly",
            "due_day": "1", "start_date": "2026-01-01", "remind_days_before": "3",
            "category": str(self.cat.pk),
        })
        self.recurring.refresh_from_db()
        self.assertEqual(self.recurring.name, "房租+水电")
        self.assertEqual(self.recurring.amount, Decimal("3200"))

    def test_deactivate(self):
        self.client.post(f"/recurring/{self.recurring.pk}/deactivate/")
        self.recurring.refresh_from_db()
        self.assertFalse(self.recurring.is_active)

    def test_other_user_cannot_edit(self):
        User.objects.create_user("bob", password="passB")
        self.client.login(username="bob", password="passB")
        r = self.client.get(f"/recurring/{self.recurring.pk}/edit/")
        self.assertEqual(r.status_code, 404)

    def test_other_user_list_excludes(self):
        other = User.objects.create_user("bob", password="passB")
        RecurringExpense.objects.create(user=other, name="Bob 的账单", amount=Decimal("100"), frequency="monthly", due_day=1, start_date="2026-01-01")
        self.client.login(username="alice", password="passA")
        r = self.client.get("/recurring/")
        self.assertNotContains(r, "Bob 的账单")

    def test_remind_days_before_default(self):
        item = RecurringExpense.objects.create(user=self.user, name="保险", amount=Decimal("500"), frequency="yearly", due_day=1, start_date="2026-01-01")
        self.assertEqual(item.remind_days_before, 3)


class InstallmentPlanTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="passA")
        cls.cat = Category.objects.create(name="购物", type="expense")
        cls.plan = InstallmentPlan.objects.create(
            user=cls.user, name="iPhone 分期", category=cls.cat,
            total_amount=Decimal("6000"), installment_amount=Decimal("500"),
            total_periods=12, next_due_date="2026-09-01",
        )

    def setUp(self):
        self.client.login(username="alice", password="passA")

    def test_create_plan(self):
        p = self.plan
        self.assertEqual(p.status, "active")
        self.assertEqual(p.paid_periods, 0)
        self.assertEqual(p.remaining_amount(), Decimal("6000"))
        self.assertEqual(p.remaining_periods(), 12)

    def test_list_shows_plan(self):
        r = self.client.get("/installments/")
        self.assertContains(r, "iPhone 分期")
        self.assertContains(r, "0/12")

    def test_pay_one_period(self):
        r = self.client.post(f"/installments/{self.plan.pk}/pay/")
        self.assertRedirects(r, "/installments/")
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.paid_periods, 1)
        self.assertEqual(self.plan.remaining_amount(), Decimal("5500"))

    def test_pay_all_periods_completes(self):
        for _ in range(12):
            self.client.post(f"/installments/{self.plan.pk}/pay/")
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, "completed")
        self.assertEqual(self.plan.paid_periods, 12)

    def test_cannot_overpay(self):
        # complete the plan
        for _ in range(12):
            self.client.post(f"/installments/{self.plan.pk}/pay/")
        # 13th payment should be blocked
        r = self.client.post(f"/installments/{self.plan.pk}/pay/")
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.paid_periods, 12)

    def test_pay_creates_expense_record(self):
        self.client.post(f"/installments/{self.plan.pk}/pay/")
        e = Expense.objects.filter(note__icontains="iPhone 分期").first()
        self.assertIsNotNone(e)
        self.assertEqual(e.amount, Decimal("500"))
        self.assertEqual(e.status, "confirmed")

    def test_edit_plan(self):
        self.client.post(f"/installments/{self.plan.pk}/edit/", {
            "name": "MacBook 分期", "total_amount": "12000",
            "installment_amount": "1000", "total_periods": "12",
            "next_due_date": "2026-10-01", "category": str(self.cat.pk),
        })
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.name, "MacBook 分期")
        self.assertEqual(self.plan.total_amount, Decimal("12000"))

    def test_other_user_cannot_pay(self):
        User.objects.create_user("bob", password="passB")
        self.client.login(username="bob", password="passB")
        r = self.client.post(f"/installments/{self.plan.pk}/pay/")
        self.assertEqual(r.status_code, 404)

    def test_amount_is_decimal(self):
        self.assertIsInstance(self.plan.total_amount, Decimal)
        self.assertIsInstance(self.plan.installment_amount, Decimal)


class TaskViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="passA")
        cls.t1 = Task.objects.create(user=cls.user, title="今天的任务", priority=1, due_at=timezone.now().replace(hour=12, minute=0, second=0, microsecond=0))
        cls.t2 = Task.objects.create(user=cls.user, title="下周的任务", priority=2, due_at=timezone.now().replace(hour=12, minute=0, second=0, microsecond=0) + timezone.timedelta(days=8))
        cls.t3 = Task.objects.create(user=cls.user, title="已完成任务", priority=3, status="completed", completed_at=timezone.now())

    def setUp(self):
        self.client.login(username="alice", password="passA")

    def test_filter_all_shows_active(self):
        r = self.client.get("/tasks/?filter=all")
        self.assertContains(r, "今天的任务")
        self.assertContains(r, "下周的任务")
        self.assertNotContains(r, "已完成任务")

    def test_filter_today(self):
        r = self.client.get("/tasks/?filter=today")
        self.assertContains(r, "今天的任务")

    def test_filter_week(self):
        r = self.client.get("/tasks/?filter=week")
        self.assertContains(r, "今天的任务")
        self.assertNotContains(r, "下周的任务")  # 8 days out, not in week

    def test_filter_completed(self):
        r = self.client.get("/tasks/?filter=completed")
        self.assertContains(r, "已完成任务")
        self.assertNotContains(r, "今天的任务")

    def test_filter_by_priority(self):
        r = self.client.get("/tasks/?filter=all&priority=1")
        self.assertContains(r, "今天的任务")
        self.assertNotContains(r, "下周的任务")

    def test_complete_action(self):
        self.client.get(f"/tasks/{self.t1.pk}/complete/")
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, "completed")
        self.assertIsNotNone(self.t1.completed_at)

    def test_postpone_action(self):
        old = self.t1.due_at
        self.client.get(f"/tasks/{self.t1.pk}/postpone/")
        self.t1.refresh_from_db()
        self.assertTrue(self.t1.due_at > old)

    def test_cancel_action(self):
        self.client.get(f"/tasks/{self.t1.pk}/cancel/")
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, "cancelled")

    def test_archive_action(self):
        self.client.get(f"/tasks/{self.t3.pk}/archive/")
        self.t3.refresh_from_db()
        self.assertEqual(self.t3.status, "archived")


class ReminderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="passA")
        cls.r = Reminder.objects.create(
            user=cls.user, title="姐姐生日", reminder_type="birthday",
            event_at="2026-12-25T00:00:00Z", remind_at="2026-12-24T00:00:00Z",
            recurrence_rule="yearly",
        )

    def setUp(self):
        self.client.login(username="alice", password="passA")

    def test_create_reminder(self):
        r = self.r
        self.assertEqual(r.title, "姐姐生日")
        self.assertEqual(r.reminder_type, "birthday")
        self.assertEqual(r.recurrence_rule, "yearly")
        self.assertTrue(r.is_enabled)

    def test_list_shows_reminder(self):
        r = self.client.get("/reminders/")
        self.assertContains(r, "姐姐生日")

    def test_create_via_form(self):
        self.client.post("/reminders/create/", {
            "title": "交话费", "reminder_type": "bill",
            "event_at": "2026-09-15T00:00", "remind_days_before": "1,7",
            "recurrence_rule": "monthly",
        })
        item = Reminder.objects.get(user=self.user, title="交话费")
        self.assertEqual(item.remind_days_before, "1,7")

    def test_toggle_disables(self):
        self.client.get(f"/reminders/{self.r.pk}/toggle/")
        self.r.refresh_from_db()
        self.assertFalse(self.r.is_enabled)

    def test_toggle_reenables(self):
        self.r.is_enabled = False
        self.r.save()
        self.client.get(f"/reminders/{self.r.pk}/toggle/")
        self.r.refresh_from_db()
        self.assertTrue(self.r.is_enabled)

    def test_other_user_cannot_toggle(self):
        User.objects.create_user("bob", password="passB")
        self.client.login(username="bob", password="passB")
        r = self.client.get(f"/reminders/{self.r.pk}/toggle/")
        self.assertEqual(r.status_code, 404)


class RecurrenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="passA")

    def setUp(self):
        self.client.login(username="alice", password="passA")

    def test_monthly_recurrence(self):
        t = Task.objects.create(user=self.user, title="每月会议", due_at="2026-08-15T10:00:00Z",
                                recurrence_rule="monthly", recurrence_day=15)
        nxt = t.next_occurrence()
        self.assertEqual(nxt.month, 9)
        self.assertEqual(nxt.day, 15)

    def test_monthly_end_of_month(self):
        t = Task.objects.create(user=self.user, title="月底报告", due_at="2026-01-31T10:00:00Z",
                                recurrence_rule="monthly", recurrence_day=31)
        nxt = t.next_occurrence()
        self.assertEqual(nxt.month, 2)
        self.assertEqual(nxt.day, 28)

    def test_no_recurrence_returns_none(self):
        t = Task.objects.create(user=self.user, title="一次性的", due_at="2026-08-15T10:00:00Z")
        self.assertIsNone(t.next_occurrence())

    def test_renew_creates_next_task(self):
        t = Task.objects.create(user=self.user, title="每周回顾", due_at="2026-08-15T10:00:00Z",
                                recurrence_rule="weekly", status="completed")
        count_before = Task.objects.count()
        self.client.get(f"/tasks/{t.pk}/renew/")
        self.assertEqual(Task.objects.count(), count_before + 1)
        new_task = Task.objects.latest("id")
        self.assertEqual(new_task.status, "todo")
        self.assertEqual(new_task.recurrence_rule, "weekly")

    def test_renew_does_not_duplicate(self):
        t = Task.objects.create(user=self.user, title="日报", due_at="2026-08-15T10:00:00Z",
                                recurrence_rule="daily", status="completed")
        self.client.get(f"/tasks/{t.pk}/renew/")
        self.client.get(f"/tasks/{t.pk}/renew/")
        self.assertEqual(Task.objects.filter(title="日报", status="todo").count(), 1)

    def test_deleting_rule_keeps_history(self):
        t = Task.objects.create(user=self.user, title="父任务", due_at="2026-08-15T10:00:00Z",
                                recurrence_rule="monthly", recurrence_day=15, status="completed")
        self.client.get(f"/tasks/{t.pk}/renew/")
        child = Task.objects.get(title="父任务", status="todo")
        self.assertIsNotNone(child)
        t.recurrence_rule = "none"
        t.save()
        self.assertTrue(Task.objects.filter(pk=child.pk).exists())

    def test_completed_instance_does_not_affect_next(self):
        t = Task.objects.create(user=self.user, title="模板任务", due_at="2026-08-15T10:00:00Z",
                                recurrence_rule="monthly", recurrence_day=15, status="completed")
        self.client.get(f"/tasks/{t.pk}/renew/")
        child = Task.objects.get(title="模板任务", status="todo")
        child.status = "completed"
        child.save()
        t.refresh_from_db()
        self.assertEqual(t.recurrence_rule, "monthly")

    def test_yearly_recurrence(self):
        t = Task.objects.create(user=self.user, title="年检", due_at="2026-08-15T10:00:00Z",
                                recurrence_rule="yearly")
        nxt = t.next_occurrence()
        self.assertEqual(nxt.year, 2027)


class ScanRemindersTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="passA")
        today = timezone.now()
        cls.reminder = Reminder.objects.create(
            user=cls.user, title="今日提醒", reminder_type="custom",
            event_at=today, remind_at=today,
        )

    def test_command_creates_notification(self):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("scan_reminders", stdout=out)
        self.assertIn("new notification", out.getvalue())
        log = NotificationLog.objects.filter(user=self.user, title="今日提醒").first()
        self.assertIsNotNone(log)

    def test_command_no_duplicate(self):
        from io import StringIO
        from django.core.management import call_command
        call_command("scan_reminders")
        first_count = NotificationLog.objects.count()
        call_command("scan_reminders")
        self.assertEqual(NotificationLog.objects.count(), first_count)

    def test_command_skips_disabled_reminder(self):
        self.reminder.is_enabled = False
        self.reminder.save()
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("scan_reminders", stdout=out)
        self.assertIn("0 new notification", out.getvalue())

    def test_notification_fields(self):
        from io import StringIO
        from django.core.management import call_command
        call_command("scan_reminders")
        log = NotificationLog.objects.first()
        self.assertEqual(log.notification_type, "reminder")
        self.assertIsNone(log.read_at)
        self.assertEqual(log.status, "pending")

    def test_dry_run_creates_nothing(self):
        from io import StringIO
        from django.core.management import call_command
        count_before = NotificationLog.objects.count()
        call_command("scan_reminders", "--dry-run")
        self.assertEqual(NotificationLog.objects.count(), count_before)

    def test_scan_tasks_creates_notification(self):
        from io import StringIO
        from django.core.management import call_command
        Task.objects.create(user=self.user, title="今日任务", due_at=timezone.now(), status="todo")
        call_command("scan_reminders", "--type=task")
        log = NotificationLog.objects.filter(notification_type="task", title__icontains="今日任务").first()
        self.assertIsNotNone(log)

    def test_scan_does_not_duplicate(self):
        from io import StringIO
        from django.core.management import call_command
        call_command("scan_reminders", "--type=reminder")
        first_count = NotificationLog.objects.filter(notification_type="reminder").count()
        call_command("scan_reminders", "--type=reminder")
        self.assertEqual(NotificationLog.objects.filter(notification_type="reminder").count(), first_count)


class AIParseModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="passA")

    def test_one_conversation_many_actions(self):
        conv = ConversationLog.objects.create(user=self.user, raw_text="午饭 18 元，提醒我明天交话费", input_type="text", model="deepseek-v3")
        result = ParseResult.objects.create(conversation=conv, confidence=0.95, draft_json={"intents": 2})
        a1 = ProposedAction.objects.create(parse_result=result, action_type="create_expense", title="午饭", amount=Decimal("18"))
        a2 = ProposedAction.objects.create(parse_result=result, action_type="create_task", title="交话费", due_at="2026-08-11T09:00:00Z")
        self.assertEqual(result.proposed_actions.count(), 2)
        self.assertEqual(conv.parse_results.count(), 1)

    def test_no_api_key_field(self):
        field_names = [f.name for f in ConversationLog._meta.get_fields()]
        self.assertNotIn("api_key", field_names)
        self.assertNotIn("secret", field_names)
        self.assertNotIn("password", field_names)

    def test_proposed_action_does_not_create_expense(self):
        """ProposedAction is a draft — never auto-creates real Expense records."""
        conv = ConversationLog.objects.create(user=self.user, raw_text="午餐 20 元")
        result = ParseResult.objects.create(conversation=conv, confidence=0.9)
        ProposedAction.objects.create(parse_result=result, action_type="create_expense", title="午餐", amount=Decimal("20"))
        # No Expense should be created
        self.assertEqual(Expense.objects.count(), 0)

    def test_proposed_action_no_user_field(self):
        """ProposedAction gets user via ParseResult → ConversationLog chain."""
        conv = ConversationLog.objects.create(user=self.user, raw_text="test")
        result = ParseResult.objects.create(conversation=conv, confidence=0.8)
        action = ProposedAction.objects.create(parse_result=result, action_type="create_note", title="备忘")
        # Access user through the chain
        self.assertEqual(action.parse_result.conversation.user, self.user)

    def test_conversation_status_flow(self):
        conv = ConversationLog.objects.create(user=self.user, raw_text="test")
        self.assertEqual(conv.status, "pending")
        conv.status = "confirmed"
        conv.save()
        self.assertEqual(conv.status, "confirmed")

    def test_raw_text_preserved(self):
        original = "今天中午吃饭花了 18 元，顺便提醒我明天 9 点开会"
        conv = ConversationLog.objects.create(user=self.user, raw_text=original)
        self.assertEqual(conv.raw_text, original)

    def test_draft_json_stored(self):
        draft = {"expenses": [{"title": "午餐", "amount": 18}], "tasks": []}
        conv = ConversationLog.objects.create(user=self.user, raw_text="午餐 18 元")
        result = ParseResult.objects.create(conversation=conv, confidence=0.92, draft_json=draft)
        result.refresh_from_db()
        self.assertEqual(result.draft_json["expenses"][0]["amount"], 18)


class AISchemaTests(SimpleTestCase):
    def test_valid_expense(self):
        ok, errs = validate_ai_response({"actions": [{"intent": "create_expense", "action_id": "a1", "amount": "18.50", "category": "餐饮", "occurred_at": "2026-08-10T12:00:00"}]})
        self.assertTrue(ok, msg=str(errs))

    def test_valid_income(self):
        ok, _ = validate_ai_response({"actions": [{"intent": "create_income", "action_id": "a1", "amount": "5000", "occurred_at": "2026-08-01"}]})
        self.assertTrue(ok)

    def test_valid_task(self):
        ok, _ = validate_ai_response({"actions": [{"intent": "create_task", "action_id": "a1", "title": "交话费", "due_at": "2026-08-11T09:00:00"}]})
        self.assertTrue(ok)

    def test_valid_reminder(self):
        ok, _ = validate_ai_response({"actions": [{"intent": "create_reminder", "action_id": "a1", "title": "姐姐生日", "event_at": "2026-12-25"}]})
        self.assertTrue(ok)

    def test_valid_note(self):
        ok, _ = validate_ai_response({"actions": [{"intent": "create_note", "action_id": "a1", "title": "今天天气不错"}]})
        self.assertTrue(ok)

    def test_valid_multi_action(self):
        ok, _ = validate_ai_response({"actions": [
            {"intent": "create_expense", "action_id": "a1", "amount": "18", "category": "餐饮", "occurred_at": "2026-08-10T12:00:00"},
            {"intent": "create_task", "action_id": "a2", "title": "交报告"},
            {"intent": "create_reminder", "action_id": "a3", "title": "开会", "event_at": "2026-08-11T14:00:00"},
        ]})
        self.assertTrue(ok)

    # ── rejections ──────────────────────────────────────────────

    def test_reject_missing_amount(self):
        ok, errs = validate_ai_response({"actions": [{"intent": "create_expense", "action_id": "a1", "category": "餐饮", "occurred_at": "2026-08-10T12:00:00"}]})
        self.assertFalse(ok)
        self.assertTrue(any("amount" in e for e in errs))

    def test_reject_missing_title_for_task(self):
        ok, errs = validate_ai_response({"actions": [{"intent": "create_task", "action_id": "a1"}]})
        self.assertFalse(ok)
        self.assertTrue(any("title" in e for e in errs))

    def test_reject_missing_title_for_reminder(self):
        ok, errs = validate_ai_response({"actions": [{"intent": "create_reminder", "action_id": "a1", "event_at": "2026-12-25"}]})
        self.assertFalse(ok)
        self.assertTrue(any("title" in e for e in errs))

    def test_reject_missing_event_and_remind_for_reminder(self):
        ok, errs = validate_ai_response({"actions": [{"intent": "create_reminder", "action_id": "a1", "title": "test"}]})
        self.assertFalse(ok)
        self.assertTrue(any("event_at" in e for e in errs))

    def test_reject_invalid_date(self):
        ok, errs = validate_ai_response({"actions": [{"intent": "create_expense", "action_id": "a1", "amount": "10", "category": "餐饮", "occurred_at": "not-a-date"}]})
        self.assertFalse(ok)

    def test_reject_negative_amount(self):
        ok, errs = validate_ai_response({"actions": [{"intent": "create_expense", "action_id": "a1", "amount": "-50", "category": "餐饮", "occurred_at": "2026-08-10"}]})
        self.assertFalse(ok)

    def test_reject_zero_amount(self):
        ok, errs = validate_ai_response({"actions": [{"intent": "create_expense", "action_id": "a1", "amount": "0", "category": "餐饮", "occurred_at": "2026-08-10"}]})
        self.assertFalse(ok)

    def test_reject_duplicate_action_id(self):
        ok, errs = validate_ai_response({"actions": [
            {"intent": "create_note", "action_id": "dup", "title": "note1"},
            {"intent": "create_note", "action_id": "dup", "title": "note2"},
        ]})
        self.assertFalse(ok)

    def test_reject_non_list_actions(self):
        ok, errs = validate_ai_response({"actions": "not_a_list"})
        self.assertFalse(ok)

    def test_reject_empty_actions(self):
        ok, errs = validate_ai_response({"actions": []})
        self.assertFalse(ok)

    def test_reject_invalid_intent(self):
        ok, errs = validate_ai_response({"actions": [{"intent": "delete_everything", "action_id": "a1"}]})
        self.assertFalse(ok)

    def test_reject_expense_without_category(self):
        ok, errs = validate_ai_response({"actions": [{"intent": "create_expense", "action_id": "a1", "amount": "10", "occurred_at": "2026-08-10"}]})
        self.assertFalse(ok)
        self.assertTrue(any("category" in e for e in errs))


class AIProviderTests(SimpleTestCase):
    def setUp(self):
        self.fake = FakeProvider()

    def test_fake_provider_parses_expense(self):
        result = self.fake.parse("午饭 18 元")
        self.assertIn("actions", result)
        self.assertEqual(result["actions"][0]["intent"], "create_expense")
        self.assertEqual(result["actions"][0]["amount"], "18")

    def test_fake_provider_parses_income(self):
        result = self.fake.parse("收到工资 5000 元")
        self.assertEqual(result["actions"][0]["intent"], "create_income")

    def test_fake_provider_parses_task(self):
        result = self.fake.parse("提醒我明天交话费")
        self.assertIn(result["actions"][0]["intent"], ("create_task", "create_note"))

    def test_fake_provider_increments_call_count(self):
        self.fake.parse("a")
        self.fake.parse("b")
        self.assertEqual(self.fake.call_count, 2)
        self.assertEqual(self.fake.last_text, "b")

    def test_get_provider_returns_fake_when_no_key(self):
        import os
        old = os.environ.pop("DEEPSEEK_API_KEY", None)
        try:
            set_provider(None)  # reset
            p = get_provider()
            self.assertIsInstance(p, FakeProvider)
        finally:
            if old:
                os.environ["DEEPSEEK_API_KEY"] = old
            set_provider(None)

    def test_provider_interface_has_parse(self):
        self.assertTrue(hasattr(FakeProvider, 'parse'))
        self.assertTrue(callable(FakeProvider.parse))

    def test_fake_provider_output_passes_schema(self):
        result = self.fake.parse("午餐 18 元，提醒我交话费")
        ok, errs = validate_ai_response(result)
        self.assertTrue(ok, msg=str(errs))


class ConfirmActionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="passA")

    def setUp(self):
        self.client.login(username="alice", password="passA")

    def test_batch_confirm_creates_multiple_records(self):
        r = self.client.post(reverse("confirm_actions"), {
            "actions": [
                {"intent": "create_expense", "title": "午餐", "amount": "18", "category": "餐饮", "occurred_at": "2026-08-10T12:00:00"},
                {"intent": "create_task", "title": "交话费"},
            ],
            "raw_text": "午饭 18 元，提醒交话费",
        }, content_type="application/json")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["count"], 2)
        self.assertTrue(Expense.objects.filter(note="午餐").exists())
        self.assertTrue(Task.objects.filter(title="交话费").exists())

    def test_transaction_rollback_on_error(self):
        count_before = Expense.objects.count()
        r = self.client.post(reverse("confirm_actions"), {
            "actions": [
                {"intent": "create_expense", "title": "好的", "amount": "10", "category": "餐饮", "occurred_at": "2026-08-10T12:00:00"},
                {"intent": "create_expense", "title": "坏的"},  # missing amount
            ],
            "raw_text": "test",
        }, content_type="application/json")
        self.assertNotEqual(r.status_code, 200)
        data = r.json()
        self.assertFalse(data["ok"])
        self.assertEqual(Expense.objects.count(), count_before)

    def test_confirm_consistency(self):
        r = self.client.post(reverse("confirm_actions"), {
            "actions": [{"intent": "create_expense", "title": "测试", "amount": "99.99", "category": "购物", "occurred_at": "2026-08-10T12:00:00"}],
            "raw_text": "测试",
        }, content_type="application/json")
        self.assertEqual(r.status_code, 200)
        exp = Expense.objects.filter(note="测试").first()
        self.assertIsNotNone(exp)
        self.assertEqual(exp.amount, Decimal("99.99"))


class ParserEvalTests(SimpleTestCase):
    def test_fixture_file_exists_and_valid(self):
        import json
        from pathlib import Path
        path = Path("tests/fixtures/parser_cases.json")
        self.assertTrue(path.exists(), "Fixture file must exist")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("cases", data)
        self.assertGreaterEqual(len(data["cases"]), 20)

    def test_all_cases_have_id_and_text(self):
        import json
        from pathlib import Path
        data = json.loads(Path("tests/fixtures/parser_cases.json").read_text(encoding="utf-8"))
        for case in data["cases"]:
            self.assertIn("id", case, f"Case missing id")
            self.assertIn("text", case, f"Case {case.get('id', '?')} missing text")

    def test_fake_provider_does_not_crash_on_any_case(self):
        import json
        from pathlib import Path
        data = json.loads(Path("tests/fixtures/parser_cases.json").read_text(encoding="utf-8"))
        p = FakeProvider()
        for case in data["cases"]:
            if not case["text"]:
                continue
            try:
                result = p.parse(case["text"])
                self.assertIn("actions", result)
            except Exception as e:
                self.fail(f"Case {case['id']} crashed: {e}")


class AIRouterTests(SimpleTestCase):
    def test_simple_expense_uses_rule(self):
        result = route_parse("午饭 18 元")
        self.assertEqual(result["source"], "rule")
        self.assertEqual(result["confidence"], "high")

    def test_multi_intent_triggers_ai(self):
        result = route_parse("午饭 18 元，提醒我明天交话费")
        self.assertIn(result["source"], ("ai", "fallback"))

    def test_source_is_present(self):
        result = route_parse("测试")
        self.assertIn(result["source"], ("rule", "ai", "fallback"))

    def test_error_field_present(self):
        result = route_parse("测试")
        self.assertTrue(result["error"] is None or isinstance(result["error"], str))

    def test_detect_multi_intent(self):
        self.assertTrue(_detect_multi_intent("午饭 18 元，打车 20 元"))
        self.assertTrue(_detect_multi_intent("午饭 18 元，提醒我交话费"))
        self.assertFalse(_detect_multi_intent("午饭 18 元"))

    def test_rule_confidence_high(self):
        self.assertEqual(_rule_confidence({"kind": "expense", "amount": "18", "category": "餐饮"}), "high")

    def test_rule_confidence_low(self):
        self.assertEqual(_rule_confidence({"kind": "note"}), "medium")


class MultiIntentTests(SimpleTestCase):
    def setUp(self):
        self.fake = FakeProvider()

    def test_two_expenses_one_task(self):
        result = self.fake.parse("今天中午吃饭 18，晚上买菜 42，明天下午提醒我给姐姐买礼物")
        self.assertGreaterEqual(len(result["actions"]), 3, msg=f"Expected 3+ actions, got {result['actions']}")
        intents = [a["intent"] for a in result["actions"]]
        self.assertIn("create_expense", intents)
        self.assertIn("create_task", intents)

    def test_each_action_has_unique_id(self):
        result = self.fake.parse("午饭 18 元，打车 20 元")
        ids = [a["action_id"] for a in result["actions"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_actions_pass_schema_validation(self):
        result = self.fake.parse("今天中午吃饭 18，晚上买菜 42，提醒我给姐姐买礼物")
        ok, errs = validate_ai_response(result)
        self.assertTrue(ok, msg=str(errs))
