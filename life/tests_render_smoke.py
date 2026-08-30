"""核心页面「有数据」渲染冒烟测试。

背景（2026-08-30）：新增回收站测试时首次让账目列表页在「列表非空」状态下
被渲染，立刻暴露 `expense_list.html` 用了 `expense_title` 过滤器却漏了
``{% load life_extras %}``——只要列表有数据就 500，而既有 511 条测试全部
只覆盖到空列表或重定向，从未真正渲染过该页面。

教训：光测「视图逻辑正确」不够，必须测「页面真的渲染得出来」。
本文件因此建立最低限度的渲染基线：造一批真实数据，逐个 GET 核心页面，
确保它们返回 200 且不抛模板错误。

新增页面时请顺手把 URL name 加进 CORE_PAGES。
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Account, Budget, Category, Countdown, Expense, InstallmentPlan,
    Note, RecurringExpense, Reminder, SavingsGoal, Tag, Task,
)
from .models_daily import DailyCheckin

# (url name, 说明) —— 覆盖用户日常真正会打开的核心页面
CORE_PAGES = [
    ("home", "首页"),
    ("expense_list", "账目列表"),
    ("task_list", "任务列表"),
    ("task_quadrant", "四象限"),
    ("note_list", "随心记列表"),
    ("daily_list", "打卡列表"),
    ("dashboard", "看板"),
    ("reports", "报表"),
    ("calendar", "日历"),
    ("budget", "预算"),
    ("savings_goals", "储蓄目标"),
    ("envelopes", "信封预算"),
    ("net_worth", "净值"),
    ("cashflow_forecast", "现金流预测"),
    ("gamification", "成就"),
    ("search", "搜索"),
    ("account_list", "账户列表"),
    ("category_list", "分类列表"),
    ("tag_list", "标签列表"),
    ("countdown_list", "倒计时列表"),
    ("recurring_list", "固定支出列表"),
    ("installment_list", "分期列表"),
    ("reminder_list", "提醒列表"),
    ("export_index", "数据导出"),
    ("import_index", "数据导入"),
    ("trash", "回收站"),
]


def _mkuser(name):
    return User.objects.create_user(name, password="TestPass123!")


class CorePageRenderTests(TestCase):
    """造一批真实数据，确保每个核心页面都能真的渲染出来（200）。"""

    @classmethod
    def setUpTestData(cls):
        u = cls.u = _mkuser("smoke_u1")
        today = timezone.now()

        cat_exp = Category.objects.create(user=u, name="餐饮", type="expense", icon="🍜")
        cat_inc = Category.objects.create(user=u, name="工资", type="income", icon="💰")

        cls.expense = Expense.objects.create(
            user=u, amount=Decimal("38.50"), note="午饭", type="expense",
            status="confirmed", category=cat_exp, occurred_at=today,
        )
        Expense.objects.create(
            user=u, amount=Decimal("12000"), note="月薪", type="income",
            status="confirmed", category=cat_inc, occurred_at=today,
        )

        cls.task = Task.objects.create(user=u, title="写周报", due_at=today + timedelta(days=1))
        cls.note = Note.objects.create(user=u, title="灵感", raw_text="记一笔", occurred_on=date.today())
        DailyCheckin.objects.create(user=u, title="背单词")
        Countdown.objects.create(user=u, title="发薪日", target_date=date.today() + timedelta(days=10))
        RecurringExpense.objects.create(
            user=u, name="房租", amount=Decimal("3500"), frequency="monthly",
            due_day=5, start_date=date.today().replace(day=1),
        )
        InstallmentPlan.objects.create(
            user=u, name="手机分期", total_amount=Decimal("6000"),
            installment_amount=Decimal("1000"), total_periods=6, paid_periods=1,
            next_due_date=date.today() + timedelta(days=15),
        )
        Reminder.objects.create(
            user=u, title="交电费", event_at=today + timedelta(days=3),
            remind_at=today + timedelta(days=2),
        )
        cls.account = Account.objects.create(
            user=u, name="招行卡", type="bank", initial_balance=Decimal("5000"),
        )
        Budget.objects.create(user=u, amount=Decimal("4000"), month=date.today().replace(day=1))
        SavingsGoal.objects.create(
            user=u, name="旅行基金", target_amount=Decimal("20000"),
            current_amount=Decimal("3000"), deadline=date.today() + timedelta(days=180),
        )
        Tag.objects.create(user=u, name="日常")

    def setUp(self):
        self.client.login(username="smoke_u1", password="TestPass123!")

    def test_core_pages_render_200(self):
        for name, label in CORE_PAGES:
            with self.subTest(page=label, url=name):
                res = self.client.get(reverse(name))
                self.assertEqual(res.status_code, 200, f"{label}({name}) 渲染失败")

    def test_detail_pages_render_200(self):
        """详情页同样要能真的渲染（列表页 bug 的同类盲区）。"""
        cases = [
            ("expense_detail", {"pk": self.expense.pk}, "账目详情"),
            ("expense_edit", {"pk": self.expense.pk}, "账目编辑"),
            ("task_detail", {"pk": self.task.pk}, "任务详情"),
            ("note_detail", {"pk": self.note.pk}, "随心记详情"),
            ("account_detail", {"pk": self.account.pk}, "账户详情"),
            ("home", {}, "首页"),
        ]
        for name, kwargs, label in cases:
            with self.subTest(page=label, url=name):
                res = self.client.get(reverse(name, kwargs=kwargs))
                self.assertEqual(res.status_code, 200, f"{label}({name}) 渲染失败")

    def test_expense_list_shows_the_expense(self):
        """针对本次 bug 的定点回归：列表非空时页面须正常渲染并含该笔账。"""
        res = self.client.get(reverse("expense_list"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "午饭")

    def test_pages_require_login(self):
        """未登录访问核心页面应跳登录，而不是报错。"""
        self.client.logout()
        for name, label in CORE_PAGES:
            with self.subTest(page=label, url=name):
                res = self.client.get(reverse(name))
                self.assertIn(res.status_code, (301, 302), f"{label}({name}) 未登录未跳转")
