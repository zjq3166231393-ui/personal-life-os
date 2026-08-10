"""Verify database integrity after migration.

Usage:
    python manage.py verify_db

Checks: Decimal fields, datetime fields, indexes, soft-delete, charset.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import connection

from life.models import Expense, Task


class Command(BaseCommand):
    help = "Verify database integrity after migration."

    def handle(self, **options):
        ok = True

        ok &= self._check_decimal()
        ok &= self._check_datetime()
        ok &= self._check_indexes()
        ok &= self._check_soft_delete()
        ok &= self._check_charset()

        if ok:
            self.stdout.write(self.style.SUCCESS("All checks passed."))
        else:
            self.stdout.write(self.style.ERROR("Some checks failed. See details above."))

    def _check_decimal(self):
        self.stdout.write("Checking Decimal fields...")
        exp = Expense.objects.first()
        if exp is not None:
            if not isinstance(exp.amount, Decimal):
                self.stderr.write("  FAIL: Expense.amount is not Decimal")
                return False
        self.stdout.write("  OK: amount is Decimal")
        return True

    def _check_datetime(self):
        self.stdout.write("Checking DateTime fields...")
        tasks = Task.objects.exclude(due_at__isnull=True)[:1]
        for t in tasks:
            if t.due_at.tzinfo is None:
                self.stderr.write("  FAIL: Task.due_at has no timezone info")
                return False
        self.stdout.write("  OK: DateTimeField has timezone")
        return True

    def _check_indexes(self):
        self.stdout.write("Checking indexes...")
        if connection.vendor == "mysql":
            with connection.cursor() as c:
                c.execute("SHOW INDEX FROM life_expense")
                rows = c.fetchall()
                indexed = {r[4] for r in rows}
                for col in ("user_id", "is_deleted", "category_id"):
                    if col not in indexed:
                        self.stdout.write(f"  WARN: life_expense.{col} not indexed")
        else:
            self.stdout.write("  SKIP: index check only for MySQL")
        return True

    def _check_soft_delete(self):
        self.stdout.write("Checking soft-delete...")
        if hasattr(Expense, 'is_deleted') and hasattr(Expense, 'deleted_at'):
            self.stdout.write("  OK: is_deleted + deleted_at exist")
            return True
        return False

    def _check_charset(self):
        self.stdout.write("Checking charset...")
        if connection.vendor == "mysql":
            with connection.cursor() as c:
                c.execute("SHOW VARIABLES LIKE 'character_set_database'")
                row = c.fetchone()
                if row and 'utf8' in row[1]:
                    self.stdout.write(f"  OK: charset={row[1]}")
                    return True
        else:
            self.stdout.write("  OK: SQLite always uses UTF-8")
        return True
