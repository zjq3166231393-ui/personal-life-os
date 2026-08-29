"""日期边界与时区专项测试（针对「按日期区间统计金额」的高风险路径）。

测试设计方法
------------
* 边界值分析  ：月初 00:00:00 / 月末 23:59:59 / 次月 00:00:00 / 上月月末 23:59:59
* 等价类划分  ：28 天（平年 2 月）/ 29 天（闰年 2 月）/ 31 天；跨年（1 月的上月为去年 12 月）
* 负向测试    ：区间外数据不得计入；他人数据不得计入
* 一致性测试  ：代码内并存的两种区间写法（aware 区间 / __date 查找）结果必须一致
* 配置测试    ：服务端时区改为 UTC 时归属仍正确

用例编号 TC-<模块>-<序号>
    BUD  预算页        DASH 财务看板      REV  复盘页
    HOME 首页服务      TZ   时区一致性     SEC  越权隔离
"""
import json
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from .models import Category, Expense, RecurringExpense
from .services import aware_day_end, aware_day_start, home_data
from .views_crud import _budget_last_month_total


def aware(y, m, d, hh=0, mm=0, ss=0):
    """构造感知默认时区（Asia/Shanghai）的 datetime，避免 naive datetime。"""
    return timezone.make_aware(datetime(y, m, d, hh, mm, ss))


class BudgetBoundaryTests(TestCase):
    """TC-BUD：预算页本月区间应为 [月初 00:00:00, 月末 23:59:59.999999]。"""

    def setUp(self):
        self.user = User.objects.create_user("u1", password="p1")
        self.cat = Category.objects.create(name="餐饮", type="expense")
        self.client.login(username="u1", password="p1")

    def _add(self, amount, when, user=None):
        return Expense.objects.create(user=user or self.user, category=self.cat, type="expense",
                                      status="confirmed", amount=Decimal(str(amount)), occurred_at=when)

    def _spent(self, today):
        with patch("django.utils.timezone.localdate", return_value=today):
            return self.client.get("/budget/").context["spent_total"]

    def test_tc_bud_001_first_second_of_month_counted(self):
        """TC-BUD-001 边界-下点：月初 00:00:00 应计入。"""
        self._add(100, aware(2026, 8, 1, 0, 0, 0))
        self.assertEqual(self._spent(date(2026, 8, 15)), Decimal("100"))

    def test_tc_bud_002_last_second_of_month_counted(self):
        """TC-BUD-002 边界-上点（缺陷回归）：月末 23:59:59 应计入。

        历史 P1 缺陷：__lte=月末(date) 被 Django 补成当天 00:00，
        导致当月最后一天的记录被整日排除。
        """
        self._add(200, aware(2026, 8, 31, 23, 59, 59))
        self.assertEqual(self._spent(date(2026, 8, 15)), Decimal("200"))

    def test_tc_bud_003_next_month_first_second_excluded(self):
        """TC-BUD-003 边界-越界点：次月 00:00:00 不应计入。"""
        self._add(300, aware(2026, 9, 1, 0, 0, 0))
        self.assertEqual(self._spent(date(2026, 8, 15)), Decimal("0"))

    def test_tc_bud_004_prev_month_last_second_excluded(self):
        """TC-BUD-004 边界-越界点：上月月末 23:59:59 不应计入。"""
        self._add(400, aware(2026, 7, 31, 23, 59, 59))
        self.assertEqual(self._spent(date(2026, 8, 15)), Decimal("0"))

    def test_tc_bud_005_four_boundary_points_together(self):
        """TC-BUD-005 组合边界：四点同时存在时仅区间内两点计入（合计 300）。"""
        self._add(400, aware(2026, 7, 31, 23, 59, 59))   # 越界：上月
        self._add(100, aware(2026, 8, 1, 0, 0, 0))       # 计入
        self._add(200, aware(2026, 8, 31, 23, 59, 59))   # 计入
        self._add(300, aware(2026, 9, 1, 0, 0, 0))       # 越界：次月
        self.assertEqual(self._spent(date(2026, 8, 15)), Decimal("300"))

    def test_tc_bud_006_feb_common_year_28_days(self):
        """TC-BUD-006 等价类-平年 2 月（28 天）：28 日 23:59:59 应计入。"""
        self._add(500, aware(2027, 2, 28, 23, 59, 59))
        self.assertEqual(self._spent(date(2027, 2, 10)), Decimal("500"))

    def test_tc_bud_007_feb_leap_year_29_days(self):
        """TC-BUD-007 等价类-闰年 2 月（29 天）：29 日 23:59:59 应计入。"""
        self._add(600, aware(2028, 2, 29, 23, 59, 59))
        self.assertEqual(self._spent(date(2028, 2, 10)), Decimal("600"))

    def test_tc_bud_008_category_breakdown_includes_last_day(self):
        """TC-BUD-008 分类小计路径同样须包含月末当天（另一处独立查询）。"""
        self._add(777, aware(2026, 8, 31, 20, 0, 0))
        with patch("django.utils.timezone.localdate", return_value=date(2026, 8, 15)):
            rows = self.client.get("/budget/").context["cat_rows"]
        mine = [r for r in rows if r["obj"].pk == self.cat.pk][0]
        self.assertEqual(mine["spent"], Decimal("777"))

    def test_tc_bud_009_last_month_helper_year_boundary(self):
        """TC-BUD-009 跨年等价类：1 月时「上月」为去年 12 月，12-31 须计入。"""
        self._add(900, aware(2026, 12, 31, 23, 59, 59))
        total = _budget_last_month_total(self.user, date(2027, 1, 15), date(2027, 1, 1))
        self.assertEqual(total, Decimal("900"))


class DashboardBoundaryTests(TestCase):
    """TC-DASH：财务看板选定月份、每日趋势与近 6 月趋势的日期边界。"""

    def setUp(self):
        self.user = User.objects.create_user("u2", password="p2")
        self.cat = Category.objects.create(name="交通", type="expense")
        self.client.login(username="u2", password="p2")

    def _add(self, amount, when):
        Expense.objects.create(user=self.user, category=self.cat, type="expense", status="confirmed",
                               amount=Decimal(str(amount)), occurred_at=when)

    def _ctx(self, today, qs=""):
        with patch("django.utils.timezone.localdate", return_value=today):
            return self.client.get(f"/dashboard/{qs}").context

    def test_tc_dash_001_selected_month_last_second_counted(self):
        """TC-DASH-001 看板选定月份：月末 23:59:59 应计入本月支出。"""
        self._add(150, aware(2026, 8, 31, 23, 59, 59))
        ctx = self._ctx(date(2026, 8, 15), "?year=2026&month=8")
        self.assertEqual(ctx["total_expense"], Decimal("150"))

    def test_tc_dash_002_feb_common_year(self):
        """TC-DASH-002 等价类-平年 2 月：28 日 23:59:59 应计入。"""
        self._add(250, aware(2027, 2, 28, 23, 59, 59))
        ctx = self._ctx(date(2027, 2, 10), "?year=2027&month=2")
        self.assertEqual(ctx["total_expense"], Decimal("250"))

    def test_tc_dash_003_daily_trend_last_day(self):
        """TC-DASH-003 每日趋势：当月最后一天必须出现（曾整日丢失）。"""
        self._add(360, aware(2026, 8, 31, 18, 0, 0))
        ctx = self._ctx(date(2026, 8, 15), "?year=2026&month=8")
        self.assertEqual(ctx["daily"][-1]["day"], 31)
        self.assertEqual(ctx["daily"][-1]["amount"], Decimal("360"))

    def test_tc_dash_004_six_month_trend_span_edges(self):
        """TC-DASH-004 近 6 月趋势：跨月区间首月月初与末月月末均须计入。

        sel=2026-08 时区间应为 [2026-03-01 00:00, 2026-08-31 23:59:59]。
        """
        self._add(11, aware(2026, 3, 1, 0, 0, 0))      # 区间首月月初
        self._add(22, aware(2026, 8, 31, 23, 59, 59))  # 区间末月月末
        self._add(99, aware(2026, 2, 28, 23, 59, 59))  # 区间外，应排除
        ctx = self._ctx(date(2026, 8, 15), "?year=2026&month=8")
        monthly = json.loads(ctx["chart_data"])["monthlyExpense"]
        self.assertEqual(len(monthly), 6)
        self.assertEqual(monthly[0], 11.0)
        self.assertEqual(monthly[-1], 22.0)

    def test_tc_dash_005_dashboard_ok_on_every_day_of_month(self):
        """TC-DASH-005 日历扫描：当月每一天打开看板都不得崩溃（日历炸弹回归）。

        建议在 today.day % SUGGESTION_GEN_EVERY_N_DAYS(3) == 0 时才生成，
        该分支曾因 float * Decimal 抛 TypeError，导致每月约 10 天看板 500。
        此类缺陷与「跑测试的日期」强相关，必须按天扫描而非只测当天。
        """
        Expense.objects.create(user=self.user, category=self.cat, type="expense", status="confirmed",
                               amount=Decimal("300"), occurred_at=aware(2026, 8, 12, 12, 0, 0))
        for day in range(1, 32):
            with self.subTest(day=day):
                with patch("django.utils.timezone.localdate", return_value=date(2026, 8, day)):
                    r = self.client.get("/dashboard/")
                self.assertEqual(r.status_code, 200)

    def test_tc_dash_006_recurring_amount_zero_no_crash(self):
        """TC-DASH-006 回归：固定账单金额为 0 时看板不得 500（ZeroDivisionError）。

        异常检测会对每条 RecurringExpense 取最近一笔同名支出，并做
        abs(recent.amount - r.amount) / r.amount 的环比判断。若某条固定账
        单金额为 0（免费订阅 / 手动填 0 / 历史脏数据），旧代码会除零拖垮
        整个看板。修复后在 r.amount == 0 时直接跳过该条。
        """
        # 一笔金额为 0 的固定账单，且名称能命中现有支出（recent 不为 None）
        RecurringExpense.objects.create(
            user=self.user, name="免费会员", amount=Decimal("0"),
            frequency="monthly", due_day=1, start_date=date(2026, 8, 1), is_active=True,
        )
        Expense.objects.create(user=self.user, category=self.cat, type="expense", status="confirmed",
                               amount=Decimal("300"), note="免费会员 自动续期", occurred_at=aware(2026, 8, 12, 12, 0, 0))
        with patch("django.utils.timezone.localdate", return_value=date(2026, 8, 15)):
            r = self.client.get("/dashboard/")
        self.assertEqual(r.status_code, 200)


class ReviewBoundaryTests(TestCase):
    """TC-REV：复盘页周/月区间的日期边界。"""

    def setUp(self):
        self.user = User.objects.create_user("u3", password="p3")
        self.cat = Category.objects.create(name="购物", type="expense")
        self.client.login(username="u3", password="p3")

    def _add(self, amount, when):
        Expense.objects.create(user=self.user, category=self.cat, type="expense", status="confirmed",
                               amount=Decimal(str(amount)), occurred_at=when)

    def _draft(self, today, period):
        with patch("django.utils.timezone.localdate", return_value=today):
            return self.client.get(f"/review/?period={period}").context["draft"]

    def test_tc_rev_001_weekly_sunday_last_second_counted(self):
        """TC-REV-001 周复盘：周日 23:59:59 应计入本周（曾整日丢失）。

        2026-08-26 为周三 → 区间 [08-24 周一, 08-30 周日]。
        """
        self._add(80, aware(2026, 8, 30, 23, 59, 59))
        self.assertIn("支出: ¥80.00", self._draft(date(2026, 8, 26), "weekly"))

    def test_tc_rev_002_weekly_next_monday_excluded(self):
        """TC-REV-002 周复盘越界点：下周一 00:00:00 不应计入。"""
        self._add(90, aware(2026, 8, 31, 0, 0, 0))
        self.assertIn("支出: ¥0.00", self._draft(date(2026, 8, 26), "weekly"))

    def test_tc_rev_003_monthly_last_second_counted(self):
        """TC-REV-003 月复盘：月末 23:59:59 应计入本月。"""
        self._add(120, aware(2026, 8, 31, 23, 59, 59))
        self.assertIn("支出: ¥120.00", self._draft(date(2026, 8, 15), "monthly"))


class HomeBoundaryTests(TestCase):
    """TC-HOME：首页预算卡（services.home_data）的日期边界。"""

    def setUp(self):
        self.user = User.objects.create_user("u4", password="p4")
        self.cat = Category.objects.create(name="日用品", type="expense")

    def test_tc_home_001_budget_card_includes_last_day(self):
        """TC-HOME-001 首页预算卡：月末当天非午夜的支出须计入。"""
        Expense.objects.create(user=self.user, category=self.cat, type="expense", status="confirmed",
                               amount=Decimal("480"), occurred_at=aware(2026, 8, 31, 21, 0, 0))
        with patch("django.utils.timezone.localdate", return_value=date(2026, 8, 15)):
            data = home_data(self.user)
        self.assertEqual(data["spent"], Decimal("480"))


class TimezoneConsistencyTests(TestCase):
    """TC-TZ：两种区间写法的一致性 + 服务端时区为 UTC 时的归属正确性。"""

    def setUp(self):
        self.user = User.objects.create_user("u5", password="p5")
        self.cat = Category.objects.create(name="餐饮", type="expense")
        self.client.login(username="u5", password="p5")

    def test_tc_tz_001_two_boundary_styles_agree(self):
        """TC-TZ-001 一致性：aware 区间与 __date 区间对同一月份结果必须一致。

        代码中两种写法并存（本月用 aware 区间、上月用 __date 查找），
        一旦时区处理不一致就会出现「本月/上月口径不同」的隐蔽错误。
        """
        for when in (aware(2026, 7, 1, 0, 0, 0), aware(2026, 7, 31, 23, 59, 59)):
            Expense.objects.create(user=self.user, category=self.cat, type="expense", status="confirmed",
                                   amount=Decimal("50"), occurred_at=when)
        by_helper = _budget_last_month_total(self.user, date(2026, 8, 15), date(2026, 8, 1))
        by_aware = Expense.objects.filter(
            user=self.user, type="expense", status="confirmed", is_deleted=False,
            occurred_at__gte=aware_day_start(date(2026, 7, 1)),
            occurred_at__lte=aware_day_end(date(2026, 7, 31)),
        ).aggregate(s=Sum("amount"))["s"] or Decimal(0)
        self.assertEqual(by_helper, Decimal("100"))
        self.assertEqual(by_helper, by_aware)

    def test_tc_tz_002_utc_server_last_day_evening_counted(self):
        """TC-TZ-002 配置测试：服务端 TZ=UTC 时，月末当晚支出仍归属当月。

        数据按上海时间 2026-08-31 20:00 记录（库里存为 12:00 UTC）。
        """
        Expense.objects.create(user=self.user, category=self.cat, type="expense", status="confirmed",
                               amount=Decimal("660"), occurred_at=aware(2026, 8, 31, 20, 0, 0))
        with patch("django.utils.timezone.localdate", return_value=date(2026, 8, 31)), \
                timezone.override("UTC"):
            ctx = self.client.get("/budget/").context
        self.assertEqual(ctx["spent_total"], Decimal("660"))

    def test_tc_tz_003_no_naive_datetime_warning(self):
        """TC-TZ-003 负向：统计路径不得再产生 naive datetime 告警。

        告警即意味着 date 被隐式补成 00:00，是本次缺陷的根因。
        """
        import warnings

        Expense.objects.create(user=self.user, category=self.cat, type="expense", status="confirmed",
                               amount=Decimal("10"), occurred_at=aware(2026, 8, 31, 22, 0, 0))
        with patch("django.utils.timezone.localdate", return_value=date(2026, 8, 15)):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                self.client.get("/budget/")
                self.client.get("/dashboard/")
                self.client.get("/review/?period=monthly")
        naive = [w for w in caught if "naive datetime" in str(w.message)]
        self.assertEqual(naive, [], f"仍存在 naive datetime 告警: {[str(w.message) for w in naive]}")


class OwnerIsolationTests(TestCase):
    """TC-SEC：统计口径的越权隔离（负向测试）。"""

    def setUp(self):
        self.user = User.objects.create_user("u6", password="p6")
        self.other = User.objects.create_user("u7", password="p7")
        self.cat = Category.objects.create(name="餐饮", type="expense")
        self.client.login(username="u6", password="p6")

    def test_tc_sec_001_budget_excludes_other_users(self):
        """TC-SEC-001 预算页不得统计他人支出。"""
        Expense.objects.create(user=self.other, category=self.cat, type="expense", status="confirmed",
                               amount=Decimal("9999"), occurred_at=aware(2026, 8, 10, 12, 0, 0))
        Expense.objects.create(user=self.user, category=self.cat, type="expense", status="confirmed",
                               amount=Decimal("10"), occurred_at=aware(2026, 8, 10, 12, 0, 0))
        with patch("django.utils.timezone.localdate", return_value=date(2026, 8, 15)):
            ctx = self.client.get("/budget/").context
        self.assertEqual(ctx["spent_total"], Decimal("10"))

    def test_tc_sec_002_dashboard_excludes_other_users(self):
        """TC-SEC-002 财务看板不得统计他人支出。"""
        Expense.objects.create(user=self.other, category=self.cat, type="expense", status="confirmed",
                               amount=Decimal("8888"), occurred_at=aware(2026, 8, 10, 12, 0, 0))
        Expense.objects.create(user=self.user, category=self.cat, type="expense", status="confirmed",
                               amount=Decimal("20"), occurred_at=aware(2026, 8, 10, 12, 0, 0))
        with patch("django.utils.timezone.localdate", return_value=date(2026, 8, 15)):
            ctx = self.client.get("/dashboard/").context
        self.assertEqual(ctx["total_expense"], Decimal("20"))


class MonthLengthEquivalenceTests(TestCase):
    """TC-LEN：不同月份长度的等价类（28/29/30/31 天）均须完整覆盖。"""

    def setUp(self):
        self.user = User.objects.create_user("u8", password="p8")
        self.cat = Category.objects.create(name="测试", type="expense")
        self.client.login(username="u8", password="p8")

    def _spent_on(self, today):
        with patch("django.utils.timezone.localdate", return_value=today):
            return self.client.get("/budget/").context["spent_total"]

    def test_tc_len_001_all_month_lengths_last_day_counted(self):
        """TC-LEN-001 等价类：28/29/30/31 天的月份，最后一天晚间的支出均须计入。"""
        cases = [
            (2027, 2, 28),   # 平年 2 月
            (2028, 2, 29),   # 闰年 2 月
            (2026, 4, 30),   # 30 天
            (2026, 8, 31),   # 31 天
        ]
        for y, m, last in cases:
            with self.subTest(month=f"{y}-{m:02d}"):
                self.assertEqual(monthrange(y, m)[1], last)
                Expense.objects.create(user=self.user, category=self.cat, type="expense",
                                       status="confirmed", amount=Decimal("42"),
                                       occurred_at=aware(y, m, last, 23, 59, 59))
                self.assertEqual(self._spent_on(date(y, m, 1)), Decimal("42"))
                Expense.objects.all().delete()
