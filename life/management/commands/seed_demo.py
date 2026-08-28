"""Seed demo data for a complete month of usage.

Usage:
    python manage.py seed_demo                 # Create demo user + data
    python manage.py seed_demo --username=demo  # Custom username
    python manage.py seed_demo --clean          # Remove demo data only
"""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from random import choice, randint, uniform

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from life.models import (
    Budget,
    Category,
    Expense,
    InstallmentPlan,
    Note,
    RecurringExpense,
    Reminder,
    Review,
    Task,
)

DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo123456"


class Command(BaseCommand):
    help = "Seed demo data for a complete month."

    def add_arguments(self, parser):
        parser.add_argument("--username", default=DEMO_USERNAME)
        parser.add_argument("--clean", action="store_true", help="Remove demo data")

    def handle(self, username, clean, **options):
        if clean:
            self._clean(username)
            return

        user, created = User.objects.get_or_create(username=username, defaults={"email": f"{username}@example.com"})
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save()
        elif Expense.objects.filter(user=user).exists():
            self.stdout.write(f"User '{username}' already has data. Use --clean first.")
            return

        today = timezone.localdate()
        month_start = date(today.year, today.month, 1)
        _, last_day = monthrange(today.year, today.month)
        days = [(month_start + timedelta(days=i)) for i in range(today.day)]

        # ── Categories ──────────────────────────────────────────
        cats = {}
        for name, icon, color in [
            ("餐饮", "🍽️", "#f97316"), ("交通", "🚗", "#3b82f6"),
            ("住房", "🏠", "#8b5cf6"), ("生活缴费", "💡", "#06b6d4"),
            ("购物", "🛒", "#ec4899"),
        ]:
            c, _ = Category.objects.get_or_create(user=None, name=name, defaults={"icon": icon, "color": color, "type": "expense", "is_system": True})
            cats[name] = c

        # ── Expenses (20-30 random across the month) ────────────
        items = [
            ("午餐", "餐饮", 15, 40), ("晚餐", "餐饮", 20, 60),
            ("打车", "交通", 10, 35), ("地铁", "交通", 3, 8),
            ("超市购物", "购物", 30, 150), ("奶茶", "餐饮", 12, 28),
            ("水果", "餐饮", 10, 45), ("外卖", "餐饮", 25, 55),
        ]
        for _ in range(randint(20, 30)):
            name, cat, lo, hi = choice(items)
            amt = round(uniform(lo, hi), 2)
            d = choice(days)
            dt = timezone.make_aware(
                timezone.datetime(d.year, d.month, d.day, randint(7, 22), randint(0, 59))
            )
            Expense.objects.create(user=user, type="expense", category=cats.get(cat),
                                   amount=Decimal(str(amt)), occurred_at=dt,
                                   note=name, merchant=choice(["美团", "滴滴", "大润发", "", ""]),
                                   source=choice(["text", "manual"]), status="confirmed")

        # Income
        Expense.objects.create(user=user, type="income", amount=Decimal("8000"),
                               occurred_at=days[0] if days else today, note="工资",
                               source="manual", status="confirmed")

        # ── Budget ──────────────────────────────────────────────
        Budget.objects.create(user=user, month=month_start, amount=Decimal("5000"))

        # ── Recurring ──────────────────────────────────────────
        for name, cat, amt, day in [("房租", "住房", 2500, 5), ("话费", "生活缴费", 59, 15), ("视频会员", "购物", 25, 20)]:
            RecurringExpense.objects.create(user=user, name=name, category=cats[cat], amount=Decimal(str(amt)),
                                            frequency="monthly", due_day=day, start_date=month_start.replace(day=1))

        # ── Installment ────────────────────────────────────────
        InstallmentPlan.objects.create(user=user, name="MacBook 分期", category=cats["购物"],
                                       total_amount=Decimal("12000"), installment_amount=Decimal("1000"),
                                       total_periods=12, paid_periods=randint(0, 3),
                                       next_due_date=today + timedelta(days=15))

        # ── Tasks ──────────────────────────────────────────────
        for title, pri, due, status in [
            ("提交月报", 1, today + timedelta(days=1), "todo"),
            ("买生日礼物", 1, today + timedelta(days=3), "todo"),
            ("健身房续费", 2, today - timedelta(days=1), "in_progress"),
            ("整理笔记", 3, None, "todo"),
            ("读完一本书", 3, today + timedelta(days=7), "completed"),
            ("修手机屏幕", 2, today - timedelta(days=3), "completed"),
        ]:
            t = Task.objects.create(user=user, title=title, priority=pri, status=status,
                                    due_at=timezone.now().replace(hour=18) + timedelta(days=(due - today).days) if due else None)
            if status == "completed":
                t.completed_at = timezone.now()

        # ── Reminders ──────────────────────────────────────────
        for title, rtype, event_days, rec in [
            ("姐姐生日", "birthday", 140, "yearly"),
            ("结婚纪念日", "custom", 60, "yearly"),
            ("车险续保", "bill", 30, "yearly"),
        ]:
            Reminder.objects.create(user=user, title=title, reminder_type=rtype,
                                    event_at=timezone.now() + timedelta(days=event_days),
                                    remind_at=timezone.now() + timedelta(days=event_days - 3),
                                    recurrence_rule=rec)

        # ── Notes ──────────────────────────────────────────────
        Note.objects.create(user=user, title="V1 上线前想优化的几个点", occurred_on=today - timedelta(days=5))
        Note.objects.create(user=user, title="好的记账习惯从每天一次开始", occurred_on=today - timedelta(days=2))

        # ── Review ─────────────────────────────────────────────
        Review.objects.create(user=user, period="weekly", period_start=today - timedelta(days=7),
                              period_end=today, content="本周完成健身 3 次，餐饮控制在预算内。",
                              is_confirmed=True)

        self.stdout.write(self.style.SUCCESS(
            f"Demo data seeded for '{username}' (password: {DEMO_PASSWORD}). "
            f"Run 'python manage.py seed_demo --clean' to remove."
        ))

    def _clean(self, username):
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            self.stdout.write(f"User '{username}' not found.")
            return
        Expense.objects.filter(user=user).delete()
        Task.objects.filter(user=user).delete()
        Note.objects.filter(user=user).delete()
        Reminder.objects.filter(user=user).delete()
        RecurringExpense.objects.filter(user=user).delete()
        InstallmentPlan.objects.filter(user=user).delete()
        Budget.objects.filter(user=user).delete()
        Review.objects.filter(user=user).delete()
        user.delete()
        self.stdout.write(self.style.SUCCESS(f"Demo user '{username}' cleaned."))
