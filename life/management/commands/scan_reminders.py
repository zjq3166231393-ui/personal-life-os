"""Scan due items and generate in-app notifications.

Usage:
    python manage.py scan_reminders              # scan all types
    python manage.py scan_reminders --dry-run    # preview, no writes
    python manage.py scan_reminders --type=task  # scan only tasks

Covers: Reminder, Task (due today), RecurringExpense (due this month).
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from common.email_util import send_notification_email
from common.models import NotificationLog
from life.models import RecurringExpense, Reminder, Task
from life.services import aware_day_start


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
        # remind_at 是 DateTimeField：today/tomorrow 是 date，须显式转感知时区的当日 00:00
        for r in Reminder.objects.filter(is_enabled=True, remind_at__gte=aware_day_start(today),
                                         remind_at__lt=aware_day_start(tomorrow)).select_related("user"):
            key = f"reminder-{r.pk}-{today.isoformat()}"
            if NotificationLog.objects.filter(idempotency_key=key).exists():
                continue
            if not dry_run:
                n = NotificationLog.objects.create(
                    user=r.user, title=r.title, body=f"提醒: {r.get_reminder_type_display()} · {r.event_at.date()}",
                    notification_type="reminder", source_type="Reminder", source_id=r.pk,
                    scheduled_at=now, status="pending", idempotency_key=key,
                )
                Reminder.objects.filter(pk=r.pk).update(last_triggered_at=now)
                self._try_email(n, r.user, r.title)
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
                n = NotificationLog.objects.create(
                    user=t.user, title=title, body=f"任务截止: {t.due_at.date() if t.due_at else '无截止'} · 优先级: {t.priority}",
                    notification_type="task", source_type="Task", source_id=t.pk,
                    scheduled_at=now, status="pending", idempotency_key=key,
                )
                self._try_email(n, t.user, title, important_only=(t.priority == 1))
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
                n = NotificationLog.objects.create(
                    user=r.user, title=f"账单到期: {r.name}",
                    body=f"每月{r.due_day}日 · {r.category.name if r.category else '未分类'}",
                    notification_type="bill", source_type="RecurringExpense", source_id=r.pk,
                    scheduled_at=now, status="pending", idempotency_key=key,
                )
                self._try_email(n, r.user, f"账单到期: {r.name}")
            created += 1
        return created

    # 邮件正文固定使用泛化文案：docs/privacy-and-data.md 承诺「邮件通知不发送
    # 金额、分类细节」。站内通知正文（notification.body）可能含分类名
    # （如「每月5日 · 住房」），直接外发会违背该承诺。
    EMAIL_BODY = "你有一条新的提醒，请登录 Personal Life OS 查看。"

    def _try_email(self, notification, user, title, important_only=False):
        """Try sending email. Update notification with retry/error on failure.

        隐私契约：邮件正文使用固定泛化文案，**不使用** ``notification.body``。
        失败只累加 ``email_retry_count``、记录截断后的错误信息，不抛异常。
        """
        if not hasattr(user, 'profile') or not user.profile.email_notifications:
            return
        if user.profile.email_important_only and not important_only:
            return
        ok, err = send_notification_email(user, title, self.EMAIL_BODY)
        if ok:
            notification.status = "delivered"
            notification.delivered_at = timezone.now()
            notification.save(update_fields=["status", "delivered_at"])
        else:
            notification.email_retry_count += 1
            notification.email_last_error = err[:500]
            notification.save(update_fields=["email_retry_count", "email_last_error"])
