from django.conf import settings
from django.db import models


class Entry(models.Model):
    class Kind(models.TextChoices):
        EXPENSE = "expense", "支出"
        TASK = "task", "待办"
        NOTE = "note", "随心记"

    class Category(models.TextChoices):
        FOOD = "餐饮", "餐饮"
        TRANSPORT = "交通", "交通"
        HOUSING = "住房", "住房"
        UTILITIES = "生活缴费", "生活缴费"
        SHOPPING = "购物", "购物"
        OTHER = "其他", "其他"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="entries", null=True, blank=True)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    title = models.CharField(max_length=200)
    raw_text = models.TextField(blank=True)
    category = models.CharField(max_length=20, choices=Category.choices, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    occurred_on = models.DateField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    priority = models.PositiveSmallIntegerField(default=2, help_text="1 高，2 中，3 低")
    completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()}：{self.title}"


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

    def __str__(self):
        sign = "+" if self.type == "income" else "-"
        return f"{'收入' if self.type == 'income' else '支出'}：{self.note or self.merchant or '未命名'} {sign}¥{self.amount}"


class Task(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=200)
    raw_text = models.TextField(blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    priority = models.PositiveSmallIntegerField(default=2, help_text="1 高，2 中，3 低")
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["completed", "-priority", "due_at"]

    def __str__(self):
        return f"待办：{self.title}"


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

    def __str__(self):
        freq = dict(self.Frequency.choices).get(self.frequency, self.frequency)
        status = "" if self.is_active else " (已停用)"
        return f"{freq}{self.due_day}日 {self.name} ¥{self.amount}{status}"
