from decimal import Decimal

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from .models import Category, Entry, Expense, Note, Task
from .parser import parse_text


class ParserTests(SimpleTestCase):
    def test_food_expense(self):
        draft = parse_text("今天中午吃饭花了18元")
        self.assertEqual(draft["kind"], "expense")
        self.assertEqual(draft["category"], "餐饮")
        self.assertEqual(draft["amount"], "18")

    def test_reminder(self):
        draft = parse_text("明天晚上8点提醒我交话费")
        self.assertEqual(draft["kind"], "task")
        self.assertIsNotNone(draft["due_at"])


class CategoryTests(TestCase):
    def test_create_default_category(self):
        cat = Category.objects.create(name="餐饮", icon="🍽️", kind="expense", is_default=True)
        self.assertEqual(str(cat), "🍽️ 餐饮")

    def test_create_user_category(self):
        user = User.objects.create_user("test", password="pass")
        cat = Category.objects.create(user=user, name="宠物", kind="expense")
        self.assertEqual(cat.user, user)

    def test_category_unique_per_user(self):
        user = User.objects.create_user("test", password="pass")
        Category.objects.create(user=user, name="咖啡", kind="expense")
        with self.assertRaises(Exception):
            Category.objects.create(user=user, name="咖啡", kind="expense")


class ExpenseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("test", password="pass")

    def test_create_expense(self):
        expense = Expense.objects.create(user=self.user, title="午餐", amount=Decimal("18.00"), occurred_on="2026-08-09")
        self.assertEqual(expense.user, self.user)
        self.assertIsInstance(expense.amount, Decimal)
        self.assertIsNotNone(expense.created_at)

    def test_expense_belongs_to_user(self):
        Expense.objects.create(user=self.user, title="user", amount=Decimal("10"), occurred_on="2026-08-09")
        other = User.objects.create_user("other", password="pass")
        Expense.objects.create(user=other, title="other", amount=Decimal("20"), occurred_on="2026-08-09")
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 1)


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
