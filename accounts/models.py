from django.db import models
from django.conf import settings


class UserProfile(models.Model):
    """Per-user settings. Phone and change counters added 2026-08-24."""
    MAX_FIELD_CHANGES = 3  # 用户名 / 邮箱 / 手机号 最多各修改 3 次

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    display_name = models.CharField(max_length=50, blank=True)
    phone = models.CharField(max_length=20, blank=True, help_text="手机号（仅自己可见，可选）")
    timezone = models.CharField(max_length=64, default="Asia/Shanghai")
    currency = models.CharField(max_length=8, default="CNY")
    monthly_budget = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    ai_parsing_enabled = models.BooleanField(default=True)
    daily_ai_limit = models.PositiveSmallIntegerField(default=100, help_text="单日 AI 调用上限，0=不限制")
    email_notifications = models.BooleanField(default=False, help_text="启用邮件提醒")
    email_important_only = models.BooleanField(default=True, help_text="仅发送重要提醒（优先级=高）")
    # ── 默认提醒时间（2026-08-24）─
    # 语音/AI 解析时若没识别到具体时刻，就用这个时间作为 due_at/event_at。
    # 默认 10:00（上午 10 点），比旧版 12:00 早，因为很多任务更适合上午完成。
    default_reminder_time = models.TimeField(
        default="10:00", help_text="语音解析任务/提醒未指定时刻时的默认时间（默认 10:00）"
    )
    # ── 头像（2026-08-24）─
    avatar = models.ImageField(
        upload_to="avatars/%Y/%m/", null=True, blank=True,
        help_text="当前头像（最大 2MB，推荐 256×256+ PNG/JPG/WebP）",
    )
    # 历史头像列表：[{[{"url": "...", "uploaded_at": "2026-08-24T..."}], ...]
    # 上限 8 张；新头像取代旧头像时把旧 URL 推入这里，再 trim 到 8。
    avatar_history = models.JSONField(
        default=list, blank=True,
        help_text="历史头像 URL 列表，上限 8 张",
    )
    # 字段修改次数计数（2026-08-24）—— 防止账号属性被频繁变更
    username_change_count = models.PositiveSmallIntegerField(default=0, help_text=f"用户名修改次数（上限 {MAX_FIELD_CHANGES}）")
    email_change_count = models.PositiveSmallIntegerField(default=0, help_text=f"邮箱修改次数（上限 {MAX_FIELD_CHANGES}）")
    phone_change_count = models.PositiveSmallIntegerField(default=0, help_text=f"手机号修改次数（上限 {MAX_FIELD_CHANGES}）")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_user_profile"

    def __str__(self):
        return f"Profile({self.user.username})"

    @property
    def username_changes_left(self):
        return max(0, self.MAX_FIELD_CHANGES - self.username_change_count)

    @property
    def email_changes_left(self):
        return max(0, self.MAX_FIELD_CHANGES - self.email_change_count)

    @property
    def phone_changes_left(self):
        return max(0, self.MAX_FIELD_CHANGES - self.phone_change_count)

    def can_change_username(self):
        return self.username_changes_left > 0

    def can_change_email(self):
        return self.email_changes_left > 0

    def can_change_phone(self):
        return self.phone_changes_left > 0
