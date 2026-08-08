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
