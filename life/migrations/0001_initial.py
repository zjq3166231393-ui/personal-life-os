# Generated manually for the initial Personal Life OS schema.
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Entry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("expense", "支出"), ("task", "待办"), ("note", "随心记")], max_length=20)),
                ("title", models.CharField(max_length=200)),
                ("raw_text", models.TextField(blank=True)),
                ("category", models.CharField(blank=True, choices=[("餐饮", "餐饮"), ("交通", "交通"), ("住房", "住房"), ("生活缴费", "生活缴费"), ("购物", "购物"), ("其他", "其他")], max_length=20)),
                ("amount", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ("occurred_on", models.DateField(blank=True, null=True)),
                ("due_at", models.DateTimeField(blank=True, null=True)),
                ("priority", models.PositiveSmallIntegerField(default=2, help_text="1 高，2 中，3 低")),
                ("completed", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
