"""Scan due items and generate in-app notifications.

Usage:
    python manage.py scan_reminders              # scan all types
    python manage.py scan_reminders --dry-run    # preview, no writes
    python manage.py scan_reminders --type=task  # scan only tasks

Covers: Reminder, Task (due today), RecurringExpense (due this month).
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from common.models import NotificationLog
from life.models import RecurringExpense, Reminder, Task


class Command(BaseCommand):
    help = "Scan due reminders, tasks, and recurring expenses — generate notifications."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Preview only, no DB writes")
        parser.add_argument("--type", choices=["reminder", "task", "recurring", "all"], default="all")

    def handle(self, dry_run, type, **options):
        today = timezone.localdate()
        tomorrow = today + timedelta(days=1)
        now = timezone.now()
        created = 0

        # ── Reminders ──────────────────────────────────────────
        if type in ("all", "reminder"):
            created += self._scan_reminders(today, tomorrow, now, dry_run)

        # ── Tasks due today ────────────────────────────────────
        if type in ("all", "task"):
            created += self._scan_tasks(today, tomorrow, now, dry_run)

        # ── Recurring expenses due this month ──────────────────
        if type in ("all", "recurring"):
            created += self._scan_recurring(today, now, dry_run)

        label = " (dry-run, not saved)" if dry_run else ""
        self.stdout.write(f"Scan complete. {created} new notification(s){label}.")

    # ── scanners ──────────────────────────────────────────────

    def _scan_reminders(self, today, tomorrow, now, dry_run):
        created = 0
        for r in Reminder.objects.filter(is_enabled=True, remind_at__gte=today, remind_at__lt=tomorrow).select_related("user"):
            key = f"reminder-{r.pk}-{today.isoformat()}"
            if NotificationLog.objects.filter(idempotency_key=key).exists():
                continue
            if not dry_run:
                NotificationLog.objects.create(
                    user=r.user, title=r.title, body=f"提醒: {r.get_reminder_type_display()} · {r.event_at.date()}",
                    notification_type="reminder", source_type="Reminder", source_id=r.pk,
                    scheduled_at=now, status="pending", idempotency_key=key,
                )
                Reminder.objects.filter(pk=r.pk).update(last_triggered_at=now)
            created += 1
        return created

    def _scan_tasks(self, today, tomorrow, now, dry_run):
        created = 0
        qs = Task.objects.filter(is_deleted=False, status__in=["todo", "in_progress"], due_at__date__gte=today, due_at__date__lt=tomorrow)
        for t in qs.select_related("user"):
            key = f"task-{t.pk}-{today.isoformat()}"
            if NotificationLog.objects.filter(idempotency_key=key).exists():
                continue
            overdue = t.due_at and t.due_at.date() < today
            title = f"{'⚠ 逾期: ' if overdue else ''}{t.title}"
            if not dry_run:
                NotificationLog.objects.create(
                    user=t.user, title=title, body=f"任务截止: {t.due_at.date() if t.due_at else '无截止'} · 优先级: {t.priority}",
                    notification_type="task", source_type="Task", source_id=t.pk,
                    scheduled_at=now, status="pending", idempotency_key=key,
                )
            created += 1
        return created

    def _scan_recurring(self, today, now, dry_run):
        created = 0
        for r in RecurringExpense.objects.filter(is_active=True).select_related("user"):
            # Due if today.day >= due_day
            if today.day < r.due_day:
                continue
            key = f"recurring-{r.pk}-{today.year}-{today.month}"
            if NotificationLog.objects.filter(idempotency_key=key).exists():
                continue
            if not dry_run:
                NotificationLog.objects.create(
                    user=r.user, title=f"账单到期: {r.name}",
                    body=f"金额: ¥{r.amount} · 每月{r.due_day}日 · 分类: {r.category.name if r.category else '未分类'}",
                    notification_type="bill", source_type="RecurringExpense", source_id=r.pk,
                    scheduled_at=now, status="pending", idempotency_key=key,
                )
            created += 1
        return created
