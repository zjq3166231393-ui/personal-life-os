"""Data quality checker for financial records.

Usage:
    python manage.py data_check              # all checks
    python manage.py data_check --check=amount  # single check
    python manage.py data_check --fix          # auto-fix safe issues

Checks:
  1. Negative or abnormal amounts
  2. Missing categories
  3. Unreasonable dates (future, too old)
  4. Duplicate entries (same user+amount+date+note)
  5. Duplicate recurring expense generations
  6. Soft-deleted records in statistics
"""
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from life.models import Expense, RecurringExpense


class Command(BaseCommand):
    help = "Check financial data quality."

    def add_arguments(self, parser):
        parser.add_argument("--check", choices=["amount", "category", "date", "duplicate", "recurring", "deleted", "all"], default="all")
        parser.add_argument("--fix", action="store_true", help="Auto-fix safe issues")
        parser.add_argument("--user-id", type=int, help="Check only this user")

    def handle(self, check, fix, user_id, **options):
        issues = 0
        users = Expense.objects.values_list("user_id", flat=True).distinct()
        if user_id:
            users = users.filter(user_id=user_id)

        if check in ("all", "amount"):
            issues += self._check_amounts(users, fix)
        if check in ("all", "category"):
            issues += self._check_categories(users, fix)
        if check in ("all", "date"):
            issues += self._check_dates(users, fix)
        if check in ("all", "duplicate"):
            issues += self._check_duplicates(users, fix)
        if check in ("all", "recurring"):
            issues += self._check_recurring(users, fix)
        if check in ("all", "deleted"):
            issues += self._check_deleted_in_stats(users, fix)

        if issues == 0:
            self.stdout.write(self.style.SUCCESS("All checks passed. No issues found."))
        else:
            self.stdout.write(self.style.WARNING(f"Found {issues} issue(s). Run with --fix to auto-correct safe issues."))

    # ── checkers ─────────────────────────────────────────────────

    def _check_amounts(self, users, fix):
        issues = 0
        for uid in users:
            abnormal = Expense.objects.filter(
                user_id=uid, is_deleted=False,
            ).filter(amount__lte=0) | Expense.objects.filter(
                user_id=uid, is_deleted=False, amount__gt=Decimal("10000000"),
            )
            for e in abnormal:
                reason = "负/零金额" if e.amount <= 0 else "金额异常大"
                self.stdout.write(f"  [amount] user={uid} id={e.pk} ¥{e.amount} — {reason}")
                if fix and e.amount <= 0:
                    e.status = "voided"
                    e.save(update_fields=["status"])
            issues += abnormal.count()
        return issues

    def _check_categories(self, users, fix):
        issues = 0
        for uid in users:
            missing = Expense.objects.filter(user_id=uid, is_deleted=False, category__isnull=True, type="expense")
            for e in missing:
                self.stdout.write(f"  [category] user={uid} id={e.pk} '{e.note}' — 无分类")
            issues += missing.count()
        return issues

    def _check_dates(self, users, fix):
        issues = 0
        today = timezone.localdate()
        future_limit = today + timedelta(days=365)
        past_limit = today - timedelta(days=365 * 10)
        for uid in users:
            bad = Expense.objects.filter(user_id=uid, is_deleted=False).filter(
                occurred_at__gt=future_limit,
            ) | Expense.objects.filter(user_id=uid, is_deleted=False).filter(
                occurred_at__lt=past_limit,
            )
            for e in bad:
                label = "未来日期" if e.occurred_at and e.occurred_at.date() > today else "过于久远"
                self.stdout.write(f"  [date] user={uid} id={e.pk} {e.occurred_at.date() if e.occurred_at else 'NULL'} — {label}")
                if fix and label == "未来日期":
                    e.occurred_at = today
                    e.status = "pending"
                    e.save(update_fields=["occurred_at", "status"])
            issues += bad.count()
        return issues

    def _check_duplicates(self, users, fix):
        issues = 0
        for uid in users:
            dups = Expense.objects.filter(user_id=uid, is_deleted=False).values(
                "amount", "occurred_at", "note", "type",
            ).annotate(cnt=Count("id")).filter(cnt__gt=1)
            for d in dups:
                rows = Expense.objects.filter(
                    user_id=uid, amount=d["amount"], occurred_at=d["occurred_at"],
                    note=d["note"], type=d["type"], is_deleted=False,
                ).order_by("pk")
                self.stdout.write(f"  [duplicate] user={uid} '{d['note']}' ¥{d['amount']} x{d['cnt']}")
                if fix and rows.count() > 1:
                    # Keep the first, void the rest
                    for dup in rows[1:]:
                        dup.status = "voided"
                        dup.save(update_fields=["status"])
            issues += len(dups)
        return issues

    def _check_recurring(self, users, fix):
        issues = 0
        for uid in users:
            recs = RecurringExpense.objects.filter(user_id=uid, is_active=True)
            for r in recs:
                # Check if there are multiple expenses with the same note this month
                today = timezone.localdate()
                month_start = date(today.year, today.month, 1)
                count = Expense.objects.filter(
                    user_id=uid, is_deleted=False, status="confirmed",
                    note__icontains=r.name, occurred_at__gte=month_start,
                ).count()
                if count > 1:
                    self.stdout.write(f"  [recurring] user={uid} '{r.name}' — 本月 {count} 条匹配记录(suspected dup)")
                    issues += 1
        return issues

    def _check_deleted_in_stats(self, users, fix):
        issues = 0
        for uid in users:
            # Check: any expense with is_deleted=True that's still confirmed?
            stale = Expense.objects.filter(user_id=uid, is_deleted=True, status="confirmed")
            for e in stale:
                self.stdout.write(f"  [deleted] user={uid} id={e.pk} '{e.note}' — deleted but still confirmed")
                if fix:
                    e.status = "voided"
                    e.save(update_fields=["status"])
            issues += stale.count()
        return issues
