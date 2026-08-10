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
        Expense.objects.create(user=self.user, category=self.user_cat, title="猫粮", amount=50, occurred_on="2026-08-10")
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
