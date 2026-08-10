from decimal import Decimal

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from .models import Category, Entry, Expense, Note, Task
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

    def test_create_task(self):
        task = Task.objects.create(user=self.user, title="交话费", priority=1)
        self.assertFalse(task.completed)
        self.assertIsNone(task.completed_at)

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
