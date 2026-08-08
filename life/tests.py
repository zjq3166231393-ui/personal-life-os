from django.test import SimpleTestCase
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

