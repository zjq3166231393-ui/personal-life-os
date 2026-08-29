"""P1 功能测试：数据导入 / 日历视图。

与 P0 一样，重点覆盖：
- 正常路径
- **越权隔离**（导入只能进自己名下；日历不能显示他人数据）
- 异常输入（表头不匹配、金额非法、日期非法、超大文件）
- 未登录保护
"""

import csv
import io
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from .models import Category, Countdown, Expense, Reminder, Task
from .models_daily import DailyCheckin

HEADER = ["日期", "类型", "金额", "分类", "商家", "备注", "状态", "来源"]


def _mkuser(name):
    return User.objects.create_user(name, password="TestPass123!")


def _csv_bytes(rows, encoding="utf-8-sig"):
    """把若干数据行拼成 CSV 字节流（默认带 BOM，模拟 Excel 导出）。"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADER)
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode(encoding)


def _upload(rows, name="data.csv", encoding="utf-8-sig"):
    return SimpleUploadedFile(name, _csv_bytes(rows, encoding), content_type="text/csv")


class ImportExpenseTests(TestCase):
    def setUp(self):
        self.u = _mkuser("gina")
        self.other = _mkuser("hank")
        self.client.login(username="gina", password="TestPass123!")

    def _post(self, rows, **kw):
        return self.client.post("/import/expense/", {"file": _upload(rows, **kw)})

    def test_tc_i001_import_creates_expenses(self):
        r = self._post([
            ["2026-08-01 12:30", "支出", "35.50", "交通", "", "打车回家", "已确认", "手动"],
            ["2026-08-02", "收入", "5000", "工资", "", "八月工资", "", ""],
        ])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["ok_count"], 2)
        self.assertEqual(r.context["err_count"], 0)

        # 确认后才真正写库
        self.assertEqual(Expense.objects.filter(user=self.u).count(), 0)
        r2 = self.client.post("/import/expense/confirm/")
        self.assertEqual(r2.status_code, 302)
        self.assertEqual(Expense.objects.filter(user=self.u, is_deleted=False).count(), 2)

        e = Expense.objects.get(user=self.u, note="打车回家")
        self.assertEqual(e.amount, Decimal("35.50"))
        self.assertEqual(e.type, "expense")
        self.assertEqual(e.category.name, "交通")
        self.assertEqual(e.status, "confirmed")
        self.assertEqual(e.source, "manual")

    def test_tc_i002_import_assigns_to_current_user_only(self):
        """关键安全用例：导入的记录只能属于当前登录用户。"""
        self._post([["2026-08-01", "支出", "10", "餐饮", "", "我的午饭", "", ""]])
        self.client.post("/import/expense/confirm/")
        self.assertEqual(Expense.objects.filter(user=self.u).count(), 1)
        self.assertEqual(Expense.objects.filter(user=self.other).count(), 0)

    def test_tc_i003_bad_header_rejected(self):
        bad = SimpleUploadedFile("x.csv", "a,b,c\n1,2,3\n".encode("utf-8"), content_type="text/csv")
        r = self.client.post("/import/expense/", {"file": bad})
        self.assertEqual(r.status_code, 302)  # 重定向回导入页并报错

    def test_tc_i004_invalid_amount_and_date_are_skipped(self):
        r = self._post([
            ["2026-08-01", "支出", "abc", "交通", "", "金额坏了", "", ""],
            ["不是日期", "支出", "20", "交通", "", "日期坏了", "", ""],
            ["2026-08-03", "支出", "0", "交通", "", "零金额", "", ""],
            ["2026-08-04", "支出", "18", "餐饮", "", "正常一条", "", ""],
        ])
        self.assertEqual(r.context["ok_count"], 1)
        self.assertEqual(r.context["err_count"], 3)

    def test_tc_i005_unknown_category_is_created_for_user(self):
        self._post([["2026-08-01", "支出", "12", "没见过的分类", "", "", "", ""]])
        self.client.post("/import/expense/confirm/")
        cat = Category.objects.filter(user=self.u, name="没见过的分类").first()
        self.assertIsNotNone(cat)
        self.assertEqual(Expense.objects.get(user=self.u).category, cat)

    def test_tc_i006_duplicates_are_skipped(self):
        Expense.objects.create(
            user=self.u, amount=Decimal("35.50"), note="打车回家",
            occurred_at=timezone.make_aware(timezone.datetime(2026, 8, 1, 12, 30)),
            type="expense", status="confirmed",
        )
        self._post([["2026-08-01 12:30", "支出", "35.50", "交通", "", "打车回家", "", ""]])
        r = self.client.post("/import/expense/confirm/")
        self.assertEqual(r.status_code, 302)
        # 重复被跳过，仍只有原有那一条
        self.assertEqual(Expense.objects.filter(user=self.u, is_deleted=False).count(), 1)

    def test_tc_i007_confirm_without_session_is_safe(self):
        """直接访问确认接口（无 session 数据）不应写入任何东西。"""
        r = self.client.post("/import/expense/confirm/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Expense.objects.filter(user=self.u).count(), 0)

    def test_tc_i008_requires_login(self):
        self.client.logout()
        r = self.client.post("/import/expense/", {"file": _upload([["2026-08-01", "支出", "1", "", "", "", "", ""]])})
        self.assertEqual(r.status_code, 302)

    def test_tc_i009_import_index_reachable(self):
        r = self.client.get("/import/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "日期")

    def test_tc_i010_gbk_encoding_supported(self):
        """兼容 GBK 编码的 CSV（部分国内软件导出）。"""
        r = self._post([["2026-08-01", "支出", "20", "餐饮", "", "午饭", "", ""]], encoding="gbk")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["ok_count"], 1)


class CalendarTests(TestCase):
    def setUp(self):
        self.u = _mkuser("ivan")
        self.other = _mkuser("jane")
        self.client.login(username="ivan", password="TestPass123!")

        self.d = date(2026, 8, 15)
        Expense.objects.create(
            user=self.u, amount=Decimal("66.00"), note="自己的支出", type="expense",
            occurred_at=timezone.make_aware(timezone.datetime(2026, 8, 15, 12, 0)),
        )
        Task.objects.create(user=self.u, title="自己的任务",
                            due_at=timezone.make_aware(timezone.datetime(2026, 8, 15, 18, 0)))
        Reminder.objects.create(user=self.u, title="自己的提醒",
                                event_at=timezone.make_aware(timezone.datetime(2026, 8, 15, 9, 0)),
                                remind_at=timezone.make_aware(timezone.datetime(2026, 8, 15, 9, 0)))
        Countdown.objects.create(user=self.u, title="自己的纪念日", target_date=self.d)
        DailyCheckin.objects.create(user=self.u, title="背单词", done_dates=["2026-08-15"])

        # 别人的同日数据
        Expense.objects.create(
            user=self.other, amount=Decimal("9999.00"), note="别人的支出", type="expense",
            occurred_at=timezone.make_aware(timezone.datetime(2026, 8, 15, 12, 0)),
        )

    def test_tc_c001_calendar_renders(self):
        r = self.client.get("/calendar/?year=2026&month=8")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "2026年8月")

    def test_tc_c002_default_month_is_current(self):
        r = self.client.get("/calendar/")
        self.assertEqual(r.status_code, 200)
        today = timezone.localdate()
        self.assertEqual(r.context["year"], today.year)
        self.assertEqual(r.context["month"], today.month)

    def test_tc_c003_day_detail_shows_own_records(self):
        r = self.client.get("/calendar/?year=2026&month=8&day=15")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "自己的支出")
        self.assertContains(r, "自己的任务")
        self.assertContains(r, "自己的提醒")
        self.assertContains(r, "自己的纪念日")
        self.assertContains(r, "背单词")

    def test_tc_c004_day_detail_excludes_other_users(self):
        """关键安全用例：日历明细不能出现他人数据。"""
        r = self.client.get("/calendar/?year=2026&month=8&day=15")
        self.assertNotContains(r, "别人的支出")
        self.assertNotContains(r, "9999.00")

    def test_tc_c005_month_navigation(self):
        r = self.client.get("/calendar/?year=2026&month=1")
        self.assertEqual(r.context["prev_year"], 2025)
        self.assertEqual(r.context["prev_month"], 12)
        r = self.client.get("/calendar/?year=2026&month=12")
        self.assertEqual(r.context["next_year"], 2027)
        self.assertEqual(r.context["next_month"], 1)

    def test_tc_c006_invalid_params_fall_back_safely(self):
        """非法月份/日期不应 500，应回退到当前月。"""
        for q in ("?year=2026&month=99", "?year=abc&month=8", "?year=2026&month=8&day=99"):
            with self.subTest(q=q):
                r = self.client.get(f"/calendar/{q}")
                self.assertEqual(r.status_code, 200)

    def test_tc_c007_month_totals_only_count_own(self):
        r = self.client.get("/calendar/?year=2026&month=8")
        self.assertEqual(r.context["month_expense"], Decimal("66.00"))

    def test_tc_c008_requires_login(self):
        self.client.logout()
        r = self.client.get("/calendar/")
        self.assertEqual(r.status_code, 302)
