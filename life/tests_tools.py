"""P0 三功能的回归测试：全局搜索 / 数据导出 / 快速记账。

覆盖重点：
- 功能正常路径（能搜到、能导出、能记账）
- **越权隔离**（这是本项目的安全底线：任何新接口都不得泄露他人数据）
- 输入校验（金额非法值、未知导出类型）
- 未登录保护
"""

import csv
import io
import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from .models import Category, Expense, Note, Reminder, Task
from .templatetags.life_extras import highlight


def _mkuser(name):
    return User.objects.create_user(name, password="TestPass123!")


def _cat(user, name, type_="expense"):
    return Category.objects.create(user=user, name=name, type=type_)


class SearchTests(TestCase):
    def setUp(self):
        self.u = _mkuser("alice")
        self.other = _mkuser("bob")
        self.client.login(username="alice", password="TestPass123!")
        # 自己的数据
        self.cat = _cat(self.u, "餐饮")
        Expense.objects.create(user=self.u, amount=Decimal("18.00"), note="午饭 麻辣烫",
                               category=self.cat, occurred_at=timezone.now())
        Task.objects.create(user=self.u, title="交电费", description="记得带上月账单")
        Note.objects.create(user=self.u, title="灵感", raw_text="做一个个人生活操作系统")
        Reminder.objects.create(user=self.u, title="妈妈生日", reminder_type="birthday",
                                event_at=timezone.now(), remind_at=timezone.now())
        # 别人的数据，含相同关键词，用于验证隔离
        Expense.objects.create(user=self.other, amount=Decimal("9999.00"), note="午饭 别人吃的",
                               occurred_at=timezone.now())

    def test_tc_s001_search_hits_own_expense(self):
        r = self.client.get("/search/?q=麻辣烫")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "麻辣烫")

    def test_tc_s002_search_hits_task_note_reminder(self):
        for q in ("交电费", "灵感", "妈妈生日"):
            with self.subTest(q=q):
                r = self.client.get(f"/search/?q={q}")
                self.assertContains(r, q)

    def test_tc_s003_search_excludes_other_users_data(self):
        """关键安全用例：搜「午饭」必须只出现自己的 18 元，不能出现别人的 9999。"""
        r = self.client.get("/search/?q=午饭")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "18.00")
        self.assertNotContains(r, "9999.00")

    def test_tc_s004_empty_query_shows_prompt_not_error(self):
        r = self.client.get("/search/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "输入关键词")

    def test_tc_s005_no_match_shows_empty_state(self):
        r = self.client.get("/search/?q=zzz不存在的关键词zzz")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "没有找到")

    def test_tc_s006_type_filter_narrows_results(self):
        r = self.client.get("/search/?q=午饭&type=task")
        self.assertEqual(r.status_code, 200)
        # 指定 type=task 后不应再出现账目结果
        self.assertNotContains(r, "麻辣烫")

    def test_tc_s007_search_requires_login(self):
        self.client.logout()
        r = self.client.get("/search/?q=午饭")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r["Location"])

    def test_tc_s008_highlight_escapes_html(self):
        """高亮必须防 XSS：搜索词含 HTML 时只原样显示，不被执行。"""
        out = highlight("午饭 <script>alert(1)</script>", "<script>")
        self.assertNotIn("<script>alert", out.replace("<mark class='lf-hl'>&lt;script&gt;</mark>", ""))
        self.assertIn("&lt;script&gt;", out)

    def test_tc_s009_highlight_empty_query_passthrough(self):
        self.assertEqual(highlight("原文", ""), "原文")
        self.assertEqual(highlight("原文", None), "原文")


class ExportTests(TestCase):
    def setUp(self):
        self.u = _mkuser("carol")
        self.other = _mkuser("dave")
        self.client.login(username="carol", password="TestPass123!")
        self.cat = _cat(self.u, "交通")
        Expense.objects.create(user=self.u, amount=Decimal("35.50"), note="打车回家",
                               category=self.cat, occurred_at=timezone.now())
        Task.objects.create(user=self.u, title="写周报")
        # 别人的数据
        Expense.objects.create(user=self.other, amount=Decimal("8888.00"), note="别人的打车",
                               occurred_at=timezone.now())

    def _rows(self, kind):
        r = self.client.get(f"/export/{kind}/")
        self.assertEqual(r.status_code, 200)
        text = r.content.decode("utf-8-sig")
        return list(csv.reader(io.StringIO(text)))

    def test_tc_e001_export_expense_contains_own_row(self):
        rows = self._rows("expense")
        self.assertEqual(rows[0][0], "日期")
        body = "\n".join(",".join(r) for r in rows[1:])
        self.assertIn("打车回家", body)
        self.assertIn("35.50", body)
        self.assertIn("交通", body)

    def test_tc_e002_export_excludes_other_users_data(self):
        """关键安全用例：导出绝不能包含他人记录。"""
        body = "\n".join(",".join(r) for r in self._rows("expense")[1:])
        self.assertNotIn("8888.00", body)
        self.assertNotIn("别人的", body)

    def test_tc_e003_all_kinds_exportable(self):
        for kind in ("expense", "task", "note", "reminder", "countdown"):
            with self.subTest(kind=kind):
                r = self.client.get(f"/export/{kind}/")
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r["Content-Type"], "text/csv; charset=utf-8-sig")

    def test_tc_e004_unknown_kind_returns_404(self):
        r = self.client.get("/export/not_a_real_kind/")
        self.assertEqual(r.status_code, 404)

    def test_tc_e005_filename_has_bom_and_disposition(self):
        # 文件名含中文，Django 会按 RFC 5987 把整个 header 做 base64 编码，
        # 所以先解码再断言（这是标准行为，浏览器侧能正确识别）。
        from email.header import decode_header
        r = self.client.get("/export/expense/")
        decoded = "".join(
            part.decode(enc or "utf-8") if isinstance(part, bytes) else part
            for part, enc in decode_header(r["Content-Disposition"])
        )
        self.assertIn("attachment;", decoded)
        self.assertIn("lifeos-账目-", decoded)
        self.assertTrue(decoded.rstrip('"').endswith(".csv"))
        # BOM 头，保证 Excel 打开中文不乱码
        self.assertTrue(r.content.startswith(b"\xef\xbb\xbf"))

    def test_tc_e006_export_index_lists_counts(self):
        r = self.client.get("/export/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "账目")
        self.assertContains(r, "数据导出")

    def test_tc_e007_export_requires_login(self):
        self.client.logout()
        r = self.client.get("/export/expense/")
        self.assertEqual(r.status_code, 302)


class QuickAddExpenseTests(TestCase):
    def setUp(self):
        self.u = _mkuser("erin")
        self.other = _mkuser("frank")
        self.client.login(username="erin", password="TestPass123!")
        self.cat = _cat(self.u, "餐饮")
        self.other_cat = _cat(self.other, "别人的分类")

    def _post(self, payload):
        return self.client.post(
            "/api/quick-expense/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_tc_q001_quick_add_creates_expense(self):
        r = self._post({"amount": "18.50", "type": "expense", "category_id": self.cat.id, "note": "午饭"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["ok"])
        e = Expense.objects.get(pk=data["id"])
        self.assertEqual(e.amount, Decimal("18.50"))
        self.assertEqual(e.user, self.u)
        self.assertEqual(e.category, self.cat)
        self.assertEqual(e.status, "confirmed")
        self.assertEqual(e.source, "manual")

    def test_tc_q002_quick_add_income(self):
        inc = _cat(self.u, "工资", type_="income")
        r = self._post({"amount": "5000", "type": "income", "category_id": inc.id})
        self.assertTrue(r.json()["ok"])
        self.assertEqual(Expense.objects.get(pk=r.json()["id"]).type, "income")

    def test_tc_q003_minimal_payload_amount_only(self):
        """只填金额即可保存——这是「3 秒记账」的核心承诺。"""
        r = self._post({"amount": "9.9"})
        self.assertTrue(r.json()["ok"])
        e = Expense.objects.get(pk=r.json()["id"])
        self.assertIsNone(e.category)
        self.assertEqual(e.note, "")

    def test_tc_q004_rejects_non_numeric_amount(self):
        r = self._post({"amount": "abc"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json()["ok"])

    def test_tc_q005_rejects_zero_and_negative(self):
        for bad in ("0", "-5", "0.00"):
            with self.subTest(amount=bad):
                r = self._post({"amount": bad})
                self.assertEqual(r.status_code, 400)

    def test_tc_q006_rejects_more_than_two_decimals(self):
        r = self._post({"amount": "1.234"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("两位小数", r.json()["error"])

    def test_tc_q007_rejects_invalid_type(self):
        r = self._post({"amount": "10", "type": "hack"})
        self.assertEqual(r.status_code, 400)

    def test_tc_q008_cannot_use_another_users_category(self):
        """关键安全用例：不能把账记到别人的分类上，回退为无分类。"""
        r = self._post({"amount": "20", "type": "expense", "category_id": self.other_cat.id})
        self.assertEqual(r.status_code, 200)
        e = Expense.objects.get(pk=r.json()["id"])
        self.assertIsNone(e.category)

    def test_tc_q009_requires_post(self):
        r = self.client.get("/api/quick-expense/")
        self.assertEqual(r.status_code, 405)

    def test_tc_q010_requires_login(self):
        self.client.logout()
        r = self._post({"amount": "10"})
        self.assertEqual(r.status_code, 302)

    def test_tc_q011_quick_categories_only_returns_accessible(self):
        r = self.client.get("/api/quick-categories/?type=expense")
        self.assertEqual(r.status_code, 200)
        names = [c["name"] for c in r.json()["categories"]]
        self.assertIn("餐饮", names)
        self.assertNotIn("别人的分类", names)

    def test_tc_q012_quick_categories_filters_by_type(self):
        _cat(self.u, "奖金", type_="income")
        r = self.client.get("/api/quick-categories/?type=income")
        names = [c["name"] for c in r.json()["categories"]]
        self.assertIn("奖金", names)
        self.assertNotIn("餐饮", names)
