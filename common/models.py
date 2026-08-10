from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Lightweight audit trail. Records who did what, never stores secrets."""

    class Action(models.TextChoices):
        EXPENSE_CREATE = "expense.create", "创建支出"
        EXPENSE_UPDATE = "expense.update", "修改支出"
        EXPENSE_DELETE = "expense.delete", "删除支出"
        TASK_CREATE = "task.create", "创建待办"
        TASK_UPDATE = "task.update", "修改待办"
        TASK_COMPLETE = "task.complete", "完成待办"
        TASK_DELETE = "task.delete", "删除待办"
        NOTE_CREATE = "note.create", "创建随心记"
        NOTE_UPDATE = "note.update", "修改随心记"
        NOTE_DELETE = "note.delete", "删除随心记"
        LOGIN_FAILED = "login.failed", "登录失败"
        AI_SAVE = "ai.save", "AI 解析确认保存"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs")
    action = models.CharField(max_length=40, choices=Action.choices)
    target_id = models.PositiveBigIntegerField(null=True, blank=True)
    summary = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        who = self.user.username if self.user else "?"
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {who} {self.get_action_display()} — {self.summary}"


class NotificationLog(models.Model):
    """Notification: scan_reminders generates, user marks read/ignored. Idempotent."""

    class Type(models.TextChoices):
        REMINDER = "reminder", "提醒"
        BILL = "bill", "账单"
        TASK = "task", "任务"

    class Status(models.TextChoices):
        PENDING = "pending", "待推送"
        DELIVERED = "delivered", "已推送"
        READ = "read", "已读"
        IGNORED = "ignored", "已忽略"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    notification_type = models.CharField(max_length=20, choices=Type.choices, default="reminder")
    source_type = models.CharField(max_length=40, blank=True, help_text="Source model, e.g. Reminder/RecurringExpense/Task")
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default="pending")
    idempotency_key = models.CharField(max_length=200, unique=True, null=True, blank=True, help_text="Prevent duplicates, e.g. reminder-42-2026-08-10")
    email_retry_count = models.PositiveSmallIntegerField(default=0)
    email_last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["idempotency_key"]),
        ]

    def __str__(self):
        return f"🔔 [{self.get_status_display()}] {self.title}"


class PushSubscription(models.Model):
    """Browser push subscription — user opts in, stored for push delivery."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint = models.TextField(unique=True)
    p256dh = models.TextField()
    auth = models.TextField()
    user_agent = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"PushSubscription({self.user.username})"
