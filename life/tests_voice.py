"""语音记账端点 /api/voice-expense/ 测试（规则解析器同步识别）。"""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from life.models import Category, Expense

User = get_user_model()


class VoiceExpenseTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("voiceuser", password="pass")
        self.client.force_login(self.user)
        self.cat = Category.objects.create(user=self.user, name="餐饮", icon="🍜",
                                            type="expense", is_system=False)

    def _post(self, text):
        return self.client.post(
            reverse("voice_expense"),
            data=json.dumps({"text": text}),
            content_type="application/json",
        )

    def test_voice_expense_chinese_number(self):
        # 语音转写「午饭十八元」→ 规则归一化为 18，分类餐饮
        r = self._post("午饭十八元")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["kind"], "expense")
        self.assertEqual(d["amount"], "18.00" if "." in d["amount"] else "18")
        e = Expense.objects.get(user=self.user)
        self.assertEqual(e.amount, 18)
        self.assertEqual(e.source, "voice")
        self.assertEqual(e.category, self.cat)

    def test_voice_expense_arabic_amount(self):
        r = self._post("打车 32.5 元")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["ok"])
        e = Expense.objects.get(user=self.user)
        self.assertEqual(e.amount, 32.5)

    def test_voice_income(self):
        r = self._post("发工资 8000 元")
        d = r.json()
        self.assertTrue(d["ok"])
        e = Expense.objects.get(user=self.user)
        self.assertEqual(e.type, "income")
        self.assertEqual(e.amount, 8000)

    def test_voice_missing_amount_returns_not_ok(self):
        # 「买菜」被识别为支出意图但无金额 → 落到无金额分支
        r = self._post("买菜")
        d = r.json()
        self.assertFalse(d["ok"])
        self.assertIn("金额", d["error"])
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 0)

    def test_voice_non_money_text_returns_not_ok(self):
        # 纯描述、无金额也无消费动词 → 非记账意图分支
        r = self._post("今天天气真好")
        d = r.json()
        self.assertFalse(d["ok"])
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 0)

    def test_voice_empty_text_rejected(self):
        r = self._post("")
        self.assertEqual(r.status_code, 400)
        r2 = self._post("   ")
        self.assertEqual(r2.status_code, 400)

    def test_voice_requires_login(self):
        self.client.logout()
        r = self._post("午饭 18 元")
        self.assertEqual(r.status_code, 302)

    def test_voice_non_money_intent_returns_title(self):
        # 任务类语句不应创建账单，但应回传 title 供前端放入备注
        r = self._post("提醒我明天交话费")
        d = r.json()
        self.assertFalse(d["ok"])
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 0)
        self.assertTrue(d["title"])
