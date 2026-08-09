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

    class Kind(models.TextChoices):
        EXPENSE = "expense", "支出"
        INCOME = "income", "收入"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="categories", null=True, blank=True, help_text="NULL = system default category")
    name = models.CharField(max_length=50)
    icon = models.CharField(max_length=8, blank=True)
    kind = models.CharField(max_length=20, choices=Kind.choices, default="expense")
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "name"]
        constraints = [models.UniqueConstraint(fields=["user", "name", "kind"], name="unique_category_per_user")]

    def __str__(self):
        return f"{self.icon or ''} {self.name}"


class Expense(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="expenses")
    title = models.CharField(max_length=200)
    raw_text = models.TextField(blank=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="expenses")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    occurred_on = models.DateField()
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-occurred_on", "-created_at"]

    def __str__(self):
        return f"支出：{self.title} ¥{self.amount}"


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
