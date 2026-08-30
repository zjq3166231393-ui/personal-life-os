"""游戏化（P2）测试：连续记账 streak、月度达成度、徽章评估与成就页渲染。"""

from datetime import date, datetime

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .gamification import (
    current_streak,
    evaluate_badges,
    home_gamification,
    longest_streak,
    month_progress,
)
from .models import Badge, Expense, Tag


def _mkuser(name):
    return User.objects.create_user(name, password="TestPass123!")


def _mk(user, local_date, amount="10.00", tags=None):
    """在指定本地日期创建一笔已确认账目。"""
    tz = timezone.get_current_timezone()
    occurred_at = timezone.make_aware(
        datetime(local_date.year, local_date.month, local_date.day, 12, 0), tz
    )
    e = Expense.objects.create(
        user=user,
        type="expense",
        amount=__import__("decimal").Decimal(amount),
        occurred_at=occurred_at,
        status="confirmed",
        source="manual",
    )
    if tags:
        e.tags.set(tags)
    return e


class StreakTests(TestCase):
    def setUp(self):
        self.u = _mkuser("streak")

    def test_current_streak_counts_consecutive(self):
        for d in range(25, 28):
            _mk(self.u, date(2026, 8, d))
        self.assertEqual(current_streak(self.u, date(2026, 8, 27)), 3)
        # 今天还没记、但昨天记了 → 视为连续中
        self.assertEqual(current_streak(self.u, date(2026, 8, 28)), 3)
        # 昨天和今天都没记 → 中断
        self.assertEqual(current_streak(self.u, date(2026, 8, 29)), 0)

    def test_current_streak_empty(self):
        self.assertEqual(current_streak(self.u, date(2026, 8, 1)), 0)

    def test_longest_streak(self):
        for d in (1, 2, 3, 10, 11, 12):
            _mk(self.u, date(2026, 8, d))
        self.assertEqual(longest_streak(self.u), 3)

    def test_streak_crosses_month_boundary(self):
        _mk(self.u, date(2026, 7, 30))
        _mk(self.u, date(2026, 7, 31))
        _mk(self.u, date(2026, 8, 1))
        _mk(self.u, date(2026, 8, 2))
        self.assertEqual(longest_streak(self.u), 4)
        self.assertEqual(current_streak(self.u, date(2026, 8, 2)), 4)


class MonthProgressTests(TestCase):
    def setUp(self):
        self.u = _mkuser("month")

    def test_month_progress_partial(self):
        _mk(self.u, date(2026, 8, 1))
        _mk(self.u, date(2026, 8, 2))
        mp = month_progress(self.u, date(2026, 8, 10))
        self.assertEqual(mp["logged"], 2)
        self.assertEqual(mp["elapsed"], 10)
        self.assertEqual(mp["pct"], 20)
        self.assertFalse(mp["is_full"])

    def test_month_progress_full_short_month(self):
        for d in range(1, 29):
            _mk(self.u, date(2026, 2, d))
        mp = month_progress(self.u, date(2026, 2, 28))
        self.assertEqual(mp["logged"], 28)
        self.assertEqual(mp["days_in_month"], 28)
        self.assertTrue(mp["is_full"])


class BadgeTests(TestCase):
    def setUp(self):
        self.u = _mkuser("badge")

    def test_first_log_earned_and_persisted(self):
        _mk(self.u, date(2026, 8, 1))
        badges = evaluate_badges(self.u, date(2026, 8, 1), persist=True)
        first = next(b for b in badges if b["key"] == "first_log")
        self.assertTrue(first["earned"])
        self.assertTrue(Badge.objects.filter(user=self.u, key="first_log").exists())
        # 其他未达成徽章不点亮
        log100 = next(b for b in badges if b["key"] == "log_100")
        self.assertFalse(log100["earned"])
        self.assertEqual(log100["current"], 1)

    def test_streak_7_badge(self):
        for d in range(1, 8):
            _mk(self.u, date(2026, 8, d))
        badges = evaluate_badges(self.u, date(2026, 8, 7), persist=True)
        s7 = next(b for b in badges if b["key"] == "streak_7")
        self.assertTrue(s7["earned"])
        s3 = next(b for b in badges if b["key"] == "streak_3")
        self.assertTrue(s3["earned"])
        s30 = next(b for b in badges if b["key"] == "streak_30")
        self.assertFalse(s30["earned"])

    def test_month_full_badge(self):
        for d in range(1, 29):
            _mk(self.u, date(2026, 2, d))
        badges = evaluate_badges(self.u, date(2026, 2, 28), persist=True)
        mf = next(b for b in badges if b["key"] == "month_full")
        self.assertTrue(mf["earned"])

    def test_cat_and_tag_badges(self):
        from .models import Category

        for i in range(8):
            c = Category.objects.create(user=self.u, name=f"c{i}", type="expense")
            e = _mk(self.u, date(2026, 8, 1))
            e.category = c
            e.save()
        t1, t2, t3 = (Tag.objects.create(user=self.u, name=f"t{i}") for i in range(3))
        for i, t in enumerate((t1, t2, t3)):
            _mk(self.u, date(2026, 8, 2), tags=[t])
        badges = evaluate_badges(self.u, date(2026, 8, 2), persist=True)
        self.assertTrue(next(b for b in badges if b["key"] == "cat_8")["earned"])
        self.assertTrue(next(b for b in badges if b["key"] == "tags_3")["earned"])

    def test_home_gamification_keys(self):
        _mk(self.u, date(2026, 8, 1))
        _mk(self.u, date(2026, 8, 2))
        data = home_gamification(self.u)
        for k in ("streak", "streak_longest", "month_logged", "month_elapsed",
                  "month_pct", "badge_earned", "badge_total"):
            self.assertIn(k, data)
        self.assertGreaterEqual(data["badge_earned"], 1)
        self.assertEqual(data["badge_total"], 9)


class GamificationViewTests(TestCase):
    def setUp(self):
        self.u = _mkuser("viewer")
        self.client.force_login(self.u)

    def test_page_requires_login(self):
        self.client.logout()
        r = self.client.get(reverse("gamification"))
        self.assertEqual(r.status_code, 302)

    def test_page_renders_and_persists_badge(self):
        _mk(self.u, date(2026, 8, 1))
        r = self.client.get(reverse("gamification"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "我的成就")
        self.assertContains(r, "天连续记账")
        # 访问成就页应持久化已点亮徽章
        self.assertTrue(Badge.objects.filter(user=self.u, key="first_log").exists())

    def test_home_renders_with_gamification_widget(self):
        _mk(self.u, date(2026, 8, 1))
        r = self.client.get(reverse("home"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "成就 →")
