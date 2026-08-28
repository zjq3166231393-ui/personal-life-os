import uuid as _uuid

from django.conf import settings
from django.db import models


class Category(models.Model):
    """Expense category with system defaults and per-user customization."""

    class Type(models.TextChoices):
        EXPENSE = "expense", "支出"
        INCOME = "income", "收入"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="categories", null=True, blank=True, help_text="NULL = system default category")
    name = models.CharField(max_length=50)
    icon = models.CharField(max_length=8, blank=True)
    type = models.CharField(max_length=20, choices=Type.choices, default="expense")
    color = models.CharField(max_length=20, blank=True, help_text="Tailwind or hex color, e.g. #f97316 or orange-500")
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["type", "name"]
        constraints = [models.UniqueConstraint(fields=["user", "name", "type"], name="unique_category_per_user")]

    def __str__(self):
        return f"{self.icon or ''} {self.name}"


class Expense(models.Model):
    class TransactionType(models.TextChoices):
        EXPENSE = "expense", "支出"
        INCOME = "income", "收入"
        TRANSFER = "transfer", "转账"

    class Source(models.TextChoices):
        VOICE = "voice", "语音"
        TEXT = "text", "文本"
        MANUAL = "manual", "手动"
        RECURRING = "recurring", "周期"
        AI = "ai", "AI"

    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "已确认"
        PENDING = "pending", "待确认"
        VOIDED = "voided", "已作废"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="expenses")
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="expenses")
    type = models.CharField(max_length=20, choices=TransactionType.choices, default="expense")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    occurred_at = models.DateTimeField()
    merchant = models.CharField(max_length=200, blank=True)
    note = models.CharField(max_length=500, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default="manual")
    status = models.CharField(max_length=20, choices=Status.choices, default="confirmed")
    raw_text = models.TextField(blank=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-occurred_at", "-created_at"]
        indexes = [
            # 列表/看板高频：filter(user, is_deleted=False, occurred_at__range)
            models.Index(fields=["user", "is_deleted", "occurred_at"]),
            # 类型/状态筛选 + 时间排序
            models.Index(fields=["user", "is_deleted", "type", "status"]),
            # 按分类聚合（预算/分析页 values("category").annotate(Sum)）
            models.Index(fields=["user", "is_deleted", "category"]),
        ]

    def __str__(self):
        sign = "+" if self.type == "income" else "-"
        return f"{'收入' if self.type == 'income' else '支出'}：{self.note or self.merchant or '未命名'} {sign}¥{self.amount}"


class Task(models.Model):
    class Status(models.TextChoices):
        TODO = "todo", "待办"
        IN_PROGRESS = "in_progress", "进行中"
        COMPLETED = "completed", "已完成"
        CANCELLED = "cancelled", "已取消"
        ARCHIVED = "archived", "已归档"

    class Source(models.TextChoices):
        VOICE = "voice", "语音"
        TEXT = "text", "文本"
        MANUAL = "manual", "手动"
        AI = "ai", "AI"
        RULE = "rule", "规则"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default="todo")
    priority = models.PositiveSmallIntegerField(default=2, help_text="1 高，2 中，3 低")
    due_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default="manual")
    parent_task = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="subtasks")
    recurrence_rule = models.CharField(max_length=20, default="none", help_text="none/daily/weekly/monthly/yearly")
    recurrence_day = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Day of month (1-31) for monthly/yearly")
    recurrence_days_before = models.PositiveSmallIntegerField(default=0, help_text="Remind N days before due date")
    raw_text = models.TextField(blank=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "-priority", "due_at"]
        indexes = [
            # 今日/本周/逾期任务：filter(user, is_deleted=False, status__in=[...], due_at__date...)
            models.Index(fields=["user", "is_deleted", "due_at"]),
            models.Index(fields=["user", "is_deleted", "status"]),
        ]

    def __str__(self):
        return f"[{self.get_status_display()}] {self.title}"

    def next_occurrence(self):
        """Return the next due_at after completing this recurring task."""
        from calendar import monthrange
        from datetime import datetime, timedelta
        if not self.recurrence_rule or self.recurrence_rule == "none" or not self.due_at:
            return None
        base = self.due_at
        if isinstance(base, str):
            from django.utils import timezone
            base = datetime.fromisoformat(base.replace("Z", "+00:00"))
            if timezone.is_naive(base):
                base = timezone.make_aware(base)
        rule = self.recurrence_rule
        rday = self.recurrence_day or base.day
        if rule == "daily":
            return base + timedelta(days=1)
        if rule == "weekly":
            return base + timedelta(days=7)
        if rule == "monthly":
            y, m = base.year, base.month + 1
            if m > 12:
                y += 1
                m = 1
            last = monthrange(y, m)[1]
            return base.replace(year=y, month=m, day=min(rday, last))
        if rule == "yearly":
            return base.replace(year=base.year + 1)
        return None


class Note(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notes")
    title = models.CharField(max_length=200)
    raw_text = models.TextField(blank=True)
    occurred_on = models.DateField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "is_deleted", "created_at"]),
            models.Index(fields=["user", "is_deleted", "occurred_on"]),
        ]

    def __str__(self):
        return f"随心记：{self.title}"


class Budget(models.Model):
    """Monthly budget — total or per-category. NULL category = total budget."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="budgets")
    category = models.ForeignKey(Category, on_delete=models.CASCADE, null=True, blank=True, related_name="budgets")
    month = models.DateField(help_text="First day of month, e.g. 2026-08-01")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-month", "category"]
        constraints = [
            models.UniqueConstraint(fields=["user", "category", "month"], name="unique_budget_per_user_category_month"),
        ]
        indexes = [
            # 预算/分析页按月拉取：filter(user, month...) 及按分类聚合
            models.Index(fields=["user", "month"]),
            models.Index(fields=["user", "category"]),
        ]

    def __str__(self):
        scope = self.category.name if self.category else "总预算"
        return f"{self.month:%Y-%m} {scope} ¥{self.amount}"


class RecurringExpense(models.Model):
    """Recurring bill: rent, phone, subscriptions, insurance, etc."""

    class Frequency(models.TextChoices):
        WEEKLY = "weekly", "每周"
        MONTHLY = "monthly", "每月"
        QUARTERLY = "quarterly", "每季度"
        YEARLY = "yearly", "每年"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recurring_expenses")
    name = models.CharField(max_length=200)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="recurring_expenses")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    frequency = models.CharField(max_length=20, choices=Frequency.choices, default="monthly")
    due_day = models.PositiveSmallIntegerField(help_text="Day of month (1-31)")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text="留空表示无截止日期")
    remind_days_before = models.PositiveSmallIntegerField(default=3)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["is_active", "due_day"]
        indexes = [
            # scan_reminders / 固定支出列表：filter(user, is_active=True/False)
            models.Index(fields=["user", "is_active"]),
        ]

    def __str__(self):
        freq = dict(self.Frequency.choices).get(self.frequency, self.frequency)
        status = "" if self.is_active else " (已停用)"
        return f"{freq}{self.due_day}日 {self.name} ¥{self.amount}{status}"


class InstallmentPlan(models.Model):
    """Installment plan: track multi-period payments like loans, large purchases."""

    class Status(models.TextChoices):
        ACTIVE = "active", "进行中"
        COMPLETED = "completed", "已还清"
        CANCELLED = "cancelled", "已取消"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="installment_plans")
    name = models.CharField(max_length=200)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="installment_plans")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    installment_amount = models.DecimalField(max_digits=12, decimal_places=2)
    total_periods = models.PositiveSmallIntegerField()
    paid_periods = models.PositiveSmallIntegerField(default=0)
    next_due_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-status", "next_due_date"]
        indexes = [
            models.Index(fields=["user", "status"]),
        ]

    def remaining_amount(self):
        return self.total_amount - (self.installment_amount * self.paid_periods)

    def remaining_periods(self):
        return max(0, self.total_periods - self.paid_periods)

    def __str__(self):
        return f"{self.name} — {self.paid_periods}/{self.total_periods}期 ¥{self.installment_amount}/期"


class Reminder(models.Model):
    """Reminders: birthdays, bills, anniversaries, custom events."""

    class Type(models.TextChoices):
        BIRTHDAY = "birthday", "生日"
        BILL = "bill", "账单"
        CUSTOM = "custom", "自定义"
        TASK = "task", "任务"

    class Recurrence(models.TextChoices):
        NONE = "none", "不重复"
        DAILY = "daily", "每天"
        WEEKLY = "weekly", "每周"
        MONTHLY = "monthly", "每月"
        YEARLY = "yearly", "每年"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reminders")
    title = models.CharField(max_length=200)
    reminder_type = models.CharField(max_length=20, choices=Type.choices, default="custom")
    event_at = models.DateTimeField(help_text="事件发生的日期时间")
    remind_at = models.DateTimeField(help_text="提醒触发时间 = event_at - remind_days")
    remind_days_before = models.CharField(max_length=50, default="1", help_text="Comma-separated days, e.g. 1,7,15")
    recurrence_rule = models.CharField(max_length=20, choices=Recurrence.choices, default="none")
    is_enabled = models.BooleanField(default=True)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["remind_at"]
        indexes = [
            # 首页/扫描提醒高频：filter(user, is_enabled=True, remind_at__range)
            models.Index(fields=["user", "is_enabled", "remind_at"]),
            models.Index(fields=["user", "is_enabled", "event_at"]),
        ]

    def __str__(self):
        return f"🔔 {self.title} ({self.get_reminder_type_display()})"


class Countdown(models.Model):
    """倒计时 / 纪念日 — iOS Day Matters 风格模块。

    关键差异（vs Reminder）：
    - 用户视角：「生日还有 86 天」「考研还有 213 天」——主要是 **距离** 时间
    - 可选自动同步到 Reminder（提前 N 天在首页高亮）
    - 隐私：默认每个用户独立，没参与日历通用事件共享
    """

    class Direction(models.TextChoices):
        DOWN = "down", "倒计时（向目标日期倒数）"
        UP = "up", "纪念日（从过去日期数已经多少天）"

    class Recurrence(models.TextChoices):
        NONE = "none", "不重复"
        YEARLY = "yearly", "每年"
        MONTHLY = "monthly", "每月"
        WEEKLY = "weekly", "每周"
        DAILY = "daily", "每天"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="countdowns")
    title = models.CharField(max_length=80)
    target_date = models.DateField(help_text="目标日期")
    direction = models.CharField(max_length=8, choices=Direction.choices, default=Direction.DOWN)
    recurrence = models.CharField(max_length=10, choices=Recurrence.choices, default=Recurrence.NONE)

    # 显示 / 个性化
    emoji = models.CharField(max_length=8, blank=True, default="", help_text="1-4 chars 可含 emoji")
    color = models.CharField(max_length=16, blank=True, default="", help_text="Hex color e.g. #5b8def")
    note = models.TextField(blank=True, max_length=500)
    show_on_home = models.BooleanField(default=True, help_text="是否在首页小板块显示")

    # 联动日历提醒（可选）
    sync_to_reminder = models.BooleanField(default=False, help_text="同步为日历提醒，提前 N 天提醒")
    reminder = models.OneToOneField(
        "Reminder", on_delete=models.SET_NULL, null=True, blank=True, related_name="countdown",
        help_text="已同步的日历提醒（删除 Countdown 时不会级联删除 Reminder）",
    )

    pinned = models.BooleanField(default=False, help_text="首页置顶")
    is_active = models.BooleanField(default=True, help_text="软删除开关")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["pinned", "-created_at"]
        indexes = [
            models.Index(fields=["user", "is_active", "target_date"]),
        ]

    def __str__(self):
        arrow = "⏳" if self.direction == self.Direction.DOWN else "🎉"
        return f"{arrow} {self.title} → {self.target_date.isoformat()}"

    # ── 展示 helper ──────────────────────────────────────────────────
    def next_occurrence(self, today=None):
        """Return the next / current occurrence date based on recurrence.

        For DOWN direction + YEARLY recurrence, if today is past target_date,
        roll forward to next year (so the countdown never shows negative days).
        """
        from datetime import timedelta
        today = today or timezone_now_localdate()
        d = self.target_date
        if self.recurrence == self.Recurrence.NONE:
            return d
        if self.recurrence == self.Recurrence.YEARLY:
            # jump forward year-by-year until >= today
            try:
                while d < today:
                    d = d.replace(year=d.year + 1)
            except ValueError:  # 2/29 in non-leap year
                d = d.replace(year=d.year + 1, day=28)
            return d
        if self.recurrence == self.Recurrence.MONTHLY:
            while d < today:
                y, m = (d.year, d.month + 1) if d.month < 12 else (d.year + 1, 1)
                try:
                    d = d.replace(year=y, month=m)
                except ValueError:
                    d = d.replace(year=y, month=m, day=28)
            return d
        if self.recurrence == self.Recurrence.WEEKLY:
            while d < today:
                d = d + timedelta(days=7)
            return d
        if self.recurrence == self.Recurrence.DAILY:
            # for daily, target_date is just the start day; next = today
            return today
        return d

    def days_diff(self, today=None):
        """Return signed day count (negative = past)."""
        today = today or timezone_now_localdate()
        target = self.next_occurrence(today) if self.direction == self.Direction.DOWN else self.target_date
        return (target - today).days

    @property
    def accent_color(self):
        return self.color or "#5b8def"  # default brand blue


def timezone_now_localdate():
    """Small helper — avoid importing timezone at module load time."""
    from django.utils import timezone
    return timezone.localdate()


# ── AI Conversation models ──────────────────────────────────────────


class ConversationLog(models.Model):
    """Raw AI input — never stores API keys."""

    class InputType(models.TextChoices):
        VOICE = "voice", "语音"
        TEXT = "text", "文本"

    class Status(models.TextChoices):
        PENDING = "pending", "待处理"
        CONFIRMED = "confirmed", "已确认"
        CANCELLED = "cancelled", "已取消"
        ERROR = "error", "解析失败"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversations")
    raw_text = models.TextField()
    input_type = models.CharField(max_length=20, choices=InputType.choices, default="text")
    model = models.CharField(max_length=100, blank=True, help_text="AI model name, e.g. deepseek-v3")
    token_count = models.PositiveIntegerField(null=True, blank=True, help_text="Total tokens used")
    cost = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True, help_text="Estimated cost in USD")
    status = models.CharField(max_length=20, choices=Status.choices, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        snippet = self.raw_text[:60] + "…" if len(self.raw_text) > 60 else self.raw_text
        return f"[{self.get_status_display()}] {snippet}"


class ParseResult(models.Model):
    """AI parse output — draft only, never auto-saved to business tables."""

    conversation = models.ForeignKey(ConversationLog, on_delete=models.CASCADE, related_name="parse_results")
    confidence = models.FloatField(default=0.0, help_text="0.0–1.0")
    draft_json = models.JSONField(default=dict, help_text="Raw AI output as JSON")
    is_confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"ParseResult(confidence={self.confidence:.2f}, confirmed={self.is_confirmed})"


class ProposedAction(models.Model):
    """One pending action from AI parse — user confirms before saving to real models."""

    class ActionType(models.TextChoices):
        CREATE_EXPENSE = "create_expense", "新建支出"
        CREATE_TASK = "create_task", "新建任务"
        CREATE_REMINDER = "create_reminder", "新建提醒"
        CREATE_NOTE = "create_note", "新建记事"
        CREATE_RECURRING_EXPENSE = "create_recurring_expense", "新建固定账单"
        CREATE_DAILY_REMINDER = "create_daily_reminder", "新建每日提醒"

    parse_result = models.ForeignKey(ParseResult, on_delete=models.CASCADE, related_name="proposed_actions")
    action_type = models.CharField(max_length=30, choices=ActionType.choices)
    title = models.CharField(max_length=200)
    category = models.CharField(max_length=50, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    event_at = models.DateTimeField(null=True, blank=True)
    is_confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.get_action_type_display()}：{self.title}"


class Review(models.Model):
    """Weekly or monthly review — draft generated, user confirms."""

    class Period(models.TextChoices):
        WEEKLY = "weekly", "每周"
        MONTHLY = "monthly", "每月"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews")
    period = models.CharField(max_length=20, choices=Period.choices)
    period_start = models.DateField()
    period_end = models.DateField()
    content = models.TextField(help_text="Markdown content of the review")
    is_confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_start"]
        constraints = [
            models.UniqueConstraint(fields=["user", "period", "period_start"], name="unique_review_per_user_period"),
        ]
        indexes = [
            models.Index(fields=["user", "period_start"]),
        ]

    def __str__(self):
        return f"{self.get_period_display()}复盘 {self.period_start}"


class Suggestion(models.Model):
    """Data-backed suggestion — every suggestion must cite evidence."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="suggestions")
    title = models.CharField(max_length=300)
    evidence = models.TextField(help_text="Data basis for this suggestion, e.g. '餐饮本月 ¥820，比过去3月均值 ¥670 高 22%'")
    category = models.CharField(max_length=40, blank=True, help_text="e.g. spending/task/reminder/budget")
    feedback = models.CharField(max_length=20, blank=True, help_text="useful/not_useful/dismissed")
    generated_at = models.DateField(auto_now_add=True)

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self):
        return f"💡 {self.title}"


class ParseJob(models.Model):
    """AI 解析异步任务表。

    解析文本时，规则解析同步返回；需要 AI 时改为后台线程执行并写入本表，
    前端通过 ``/api/parse-status/<uuid>/`` 轮询结果，避免 AI 调用（最长 ~30s）
    阻塞 Web worker。仅单用户量级的本地辅助表，不做复杂约束。
    """

    STATUS = [
        ("pending", "等待中"),
        ("running", "解析中"),
        ("done", "已完成"),
        ("error", "失败"),
    ]

    uuid = models.CharField(max_length=32, unique=True, db_index=True, default=_uuid.uuid4().hex,
                            help_text="对外暴露的任务标识，用于轮询，无业务含义")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="parse_jobs")
    raw_text = models.TextField(help_text="待解析的原始文本")
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    result = models.JSONField(null=True, blank=True, help_text="解析结果（与 route_parse 返回结构一致）")
    error = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "status", "-created_at"], name="parsejob_user_status_idx")]

    def __str__(self):
        return f"ParseJob[{self.uuid}] {self.status}"
