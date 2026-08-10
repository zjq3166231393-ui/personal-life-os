"""Management command to scan due reminders and generate in-app notifications.

Usage:
    python manage.py scan_reminders

Idempotent: uses idempotency_key to prevent duplicate notifications.
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from common.models import NotificationLog
from life.models import Reminder


class Command(BaseCommand):
    help = "Scan due reminders and generate in-app notifications."

    def handle(self, **options):
        today = timezone.localdate()
        tomorrow = today + timedelta(days=1)
        now = timezone.now()
        created = 0

        reminders = Reminder.objects.filter(
            is_enabled=True,
            remind_at__gte=today,
            remind_at__lt=tomorrow,
        ).select_related("user")

        for r in reminders:
            key = f"reminder-{r.pk}-{today.isoformat()}"
            if NotificationLog.objects.filter(idempotency_key=key).exists():
                continue

            NotificationLog.objects.create(
                user=r.user,
                title=r.title,
                body=f"提醒类型: {r.get_reminder_type_display()} · 事件日期: {r.event_at.date()}",
                notification_type="reminder",
                source_type="Reminder",
                source_id=r.pk,
                scheduled_at=now,
                status="pending",
                idempotency_key=key,
            )
            created += 1
            Reminder.objects.filter(pk=r.pk).update(last_triggered_at=now)

        self.stdout.write(f"Scan complete. {created} new notification(s) created.")
