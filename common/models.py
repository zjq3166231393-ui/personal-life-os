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
