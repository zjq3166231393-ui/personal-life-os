"""Management command to scan due reminders and generate in-app notifications.

Usage:
    python manage.py scan_reminders

No duplicates: if a notification for the same (user, reference_id, notification_type)
exists from today, it is skipped.
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
        created = 0

        reminders = Reminder.objects.filter(
            is_enabled=True,
            remind_at__gte=today,
            remind_at__lt=tomorrow,
        ).select_related("user")

        for r in reminders:
            # Prevent duplicate: same user + same reference + same type today
            already = NotificationLog.objects.filter(
                user=r.user,
                reference_id=r.pk,
                notification_type="reminder",
                created_at__date=today,
            ).exists()
            if already:
                continue

            NotificationLog.objects.create(
                user=r.user,
                title=r.title,
                body=f"提醒类型: {r.get_reminder_type_display()} · 事件日期: {r.event_at.date()}",
                notification_type="reminder",
                reference_id=r.pk,
            )
            created += 1
            # Update last_triggered_at
            Reminder.objects.filter(pk=r.pk).update(last_triggered_at=timezone.now())

        self.stdout.write(f"Scan complete. {created} new notification(s) created.")
