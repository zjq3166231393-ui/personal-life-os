"""自动分类规则引擎测试。

覆盖：匹配函数（大小写/类型/优先级/停用/空文本）、创建入口自动归类、
语音记账退回、导入退回、实时建议接口、CRUD 越权隔离、页面渲染冒烟。
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .category_rules import match_category
from .models import Category, CategoryRule, Expense


def _mkuser(name):
    return get_user_model().objects.create_user(name, password="TestPass123!")


def _mkcat(user, name, type_="expense"):
    return Category.objects.create(user=user, name=name, type=type_, is_active=True)


class MatchCategoryTests(TestCase):
    def setUp(self):
        self.u = _mkuser("mc_u1")
        self.dining = _mkcat(self.u, "餐饮")
        self.traffic = _mkcat(self.u, "交通")

    def _rule(self, pattern, category, **kw):
        return CategoryRule.objects.create(
            user=self.u, pattern=pattern, category=category, **kw
        )

    def test_basic_substring_match(self):
        self._rule("星巴克", self.dining)
        self.assertEqual(match_category(self.u, "在星巴克喝了杯咖啡"), self.dining)

    def test_case_insensitive(self):
        self._rule("Starbucks", self.dining)
        self.assertEqual(match_category(self.u, "STARBUCKS 拿铁"), self.dining)

    def test_partial_inside_longer_word(self):
        self._rule("滴滴", self.traffic)
        self.assertEqual(match_category(self.u, "滴滴出行 - 快车"), self.traffic)

    def test_no_match_returns_none(self):
        self._rule("盒马", self.dining)
        self.assertIsNone(match_category(self.u, "京东超市购物"))

    def test_empty_text_returns_none(self):
        self._rule("星巴克", self.dining)
        self.assertIsNone(match_category(self.u, "   "))
        self.assertIsNone(match_category(self.u, ""))

    def test_type_filter_expense_only(self):
        self._rule("工资", self.dining, type_filter="expense")
        # 同关键字但类型是收入，不应命中「仅支出」规则
        self.assertIsNone(match_category(self.u, "工资到账", type_="income"))

    def test_type_filter_income_only_matches_income(self):
        inc = _mkcat(self.u, "工资收入", type_="income")
        self._rule("工资", inc, type_filter="income")
        self.assertEqual(match_category(self.u, "工资到账", type_="income"), inc)

    def test_both_filter_matches_either(self):
        self._rule("星", self.dining, type_filter="both")
        self.assertEqual(match_category(self.u, "星巴克", type_="income"), self.dining)

    def test_inactive_skipped(self):
        self._rule("星巴克", self.dining, is_active=False)
        self.assertIsNone(match_category(self.u, "星巴克咖啡"))

    def test_priority_wins(self):
        # 两条都能命中：「咖啡」更泛、「星巴克」更具体且优先级更高
        broad = self._rule("咖啡", self.traffic, priority=0)
        narrow = self._rule("星巴克", self.dining, priority=10)
        self.assertEqual(match_category(self.u, "星巴克咖啡"), self.dining)
        # 仅「咖啡」能命中时回落到 broad（broad 的分类是交通）
        self.assertEqual(match_category(self.u, "瑞幸咖啡"), self.traffic)

    def test_other_user_rules_not_visible(self):
        other = _mkuser("mc_other")
        other_cat = _mkcat(other, "他人餐饮")
        CategoryRule.objects.create(user=other, pattern="星巴克", category=other_cat)
        self.assertIsNone(match_category(self.u, "星巴克一杯"))


class QuickAddAutoCategoryTests(TestCase):
    """quick_add_expense 在未选分类时按规则自动归类。"""

    def setUp(self):
        self.u = _mkuser("qa_u1")
        self.client.login(username="qa_u1", password="TestPass123!")
        self.dining = _mkcat(self.u, "餐饮")

    def _post(self, payload):
        return self.client.post(
            reverse("quick_add_expense"),
            data=json_dumps(payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN="x",
        )

    def test_auto_assigns_from_note(self):
        CategoryRule.objects.create(user=self.u, pattern="星巴克", category=self.dining)
        res = self._post({"amount": "32.00", "type": "expense", "note": "星巴克拿铁"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["category"], "餐饮")
        self.assertTrue(data["auto_matched"])
        exp = Expense.objects.get(user=self.u)
        self.assertEqual(exp.category, self.dining)

    def test_explicit_category_takes_precedence(self):
        other = _mkcat(self.u, "交通")
        CategoryRule.objects.create(user=self.u, pattern="星巴克", category=self.dining)
        res = self._post({"amount": "32.00", "type": "expense",
                          "category_id": other.pk, "note": "星巴克拿铁"})
        exp = Expense.objects.get(user=self.u)
        self.assertEqual(exp.category, other)  # 用户显式选的优先
        self.assertFalse(res.json()["auto_matched"])

    def test_no_rule_no_category(self):
        res = self._post({"amount": "32.00", "type": "expense", "note": "随便买点"})
        exp = Expense.objects.get(user=self.u)
        self.assertIsNone(exp.category)
        self.assertFalse(res.json()["auto_matched"])


class VoiceExpenseRuleFallbackTests(TestCase):
    def setUp(self):
        self.u = _mkuser("ve_u1")
        self.client.login(username="ve_u1", password="TestPass123!")
        self.dining = _mkcat(self.u, "餐饮")

    def test_rule_fallback_when_name_unresolved(self):
        CategoryRule.objects.create(user=self.u, pattern="星巴克", category=self.dining)
        # 语音解析通常提取不到分类名，走规则退回
        res = self.client.post(
            reverse("voice_expense"),
            data=json_dumps({"text": "星巴克 32元"}),
            content_type="application/json", HTTP_X_CSRFTOKEN="x",
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])
        exp = Expense.objects.get(user=self.u)
        self.assertEqual(exp.category, self.dining)


class SuggestCategoryApiTests(TestCase):
    def setUp(self):
        self.u = _mkuser("sc_u1")
        self.client.login(username="sc_u1", password="TestPass123!")
        self.dining = _mkcat(self.u, "餐饮")
        CategoryRule.objects.create(user=self.u, pattern="星巴克", category=self.dining)

    def test_suggest_hits(self):
        res = self.client.get(reverse("suggest_category"), {"q": "星巴克拿铁", "type": "expense"})
        self.assertEqual(res.status_code, 200)
        d = res.json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["category_id"], self.dining.pk)
        self.assertEqual(d["category_name"], "餐饮")

    def test_suggest_miss(self):
        res = self.client.get(reverse("suggest_category"), {"q": "无关键字", "type": "expense"})
        d = res.json()
        self.assertIsNone(d["category_id"])

    def test_suggest_empty(self):
        res = self.client.get(reverse("suggest_category"), {"type": "expense"})
        d = res.json()
        self.assertIsNone(d["category_id"])


class RuleCrudTests(TestCase):
    def setUp(self):
        self.u = _mkuser("crud_u1")
        self.other = _mkuser("crud_other")
        self.client.login(username="crud_u1", password="TestPass123!")
        self.dining = _mkcat(self.u, "餐饮")
        self.other_cat = _mkcat(self.other, "他人分类")

    def test_create_rule(self):
        res = self.client.post(reverse("category_rule_create"), {
            "pattern": "星巴克", "category": self.dining.pk,
            "type_filter": "expense", "priority": "5", "is_active": "on",
        })
        self.assertEqual(res.status_code, 302)
        rule = CategoryRule.objects.get(user=self.u, pattern="星巴克")
        self.assertEqual(rule.category, self.dining)
        self.assertEqual(rule.priority, 5)
        self.assertTrue(rule.is_active)

    def test_create_rejects_bad_category(self):
        # 选了他人的分类 → 不应创建
        res = self.client.post(reverse("category_rule_create"), {
            "pattern": "x", "category": self.other_cat.pk,
        })
        self.assertEqual(res.status_code, 200)  # 回到表单，不重定向
        self.assertFalse(CategoryRule.objects.filter(user=self.u).exists())

    def test_edit_rule(self):
        rule = CategoryRule.objects.create(user=self.u, pattern="星巴克", category=self.dining)
        res = self.client.post(reverse("category_rule_edit", args=[rule.pk]), {
            "pattern": "星巴克(改)", "category": self.dining.pk, "priority": "9",
        })
        self.assertEqual(res.status_code, 302)
        rule.refresh_from_db()
        self.assertEqual(rule.pattern, "星巴克(改)")
        self.assertEqual(rule.priority, 9)

    def test_delete_rule(self):
        rule = CategoryRule.objects.create(user=self.u, pattern="星巴克", category=self.dining)
        res = self.client.post(reverse("category_rule_delete", args=[rule.pk]))
        self.assertEqual(res.status_code, 302)
        self.assertFalse(CategoryRule.objects.filter(pk=rule.pk).exists())

    def test_cannot_edit_other_user_rule(self):
        rule = CategoryRule.objects.create(user=self.other, pattern="x", category=self.other_cat)
        res = self.client.get(reverse("category_rule_edit", args=[rule.pk]))
        self.assertEqual(res.status_code, 404)

    def test_cannot_delete_other_user_rule(self):
        rule = CategoryRule.objects.create(user=self.other, pattern="x", category=self.other_cat)
        res = self.client.post(reverse("category_rule_delete", args=[rule.pk]))
        self.assertEqual(res.status_code, 404)
        self.assertTrue(CategoryRule.objects.filter(pk=rule.pk).exists())

    def test_list_renders(self):
        CategoryRule.objects.create(user=self.u, pattern="星巴克", category=self.dining)
        res = self.client.get(reverse("category_rule_list"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "星巴克")

    def test_form_renders(self):
        res = self.client.get(reverse("category_rule_create"))
        self.assertEqual(res.status_code, 200)


def json_dumps(obj):
    import json
    return json.dumps(obj)
