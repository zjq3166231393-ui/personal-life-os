from django.db import migrations, models
from django.utils import timezone


def migrate_occurred_at(apps, schema_editor):
    Expense = apps.get_model("life", "Expense")
    for e in Expense.objects.iterator():
        if e.occurred_at is None and hasattr(e, 'occurred_on'):
            e.occurred_at = timezone.make_aware(
                timezone.datetime.combine(e.occurred_on, timezone.datetime.min.time())
            )
        if not e.note and hasattr(e, 'title'):
            e.note = getattr(e, 'title', '')[:500]
        e.save(update_fields=['occurred_at', 'note'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("life", "0008_seed_category_colors"),
    ]

    operations = [
        # 1. Add new fields (nullable first)
        migrations.AddField(
            model_name="expense",
            name="occurred_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="expense",
            name="merchant",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="expense",
            name="source",
            field=models.CharField(choices=[("voice", "语音"), ("text", "文本"), ("manual", "手动"), ("recurring", "周期"), ("ai", "AI")], default="manual", max_length=20),
        ),
        migrations.AddField(
            model_name="expense",
            name="status",
            field=models.CharField(choices=[("confirmed", "已确认"), ("pending", "待确认"), ("voided", "已作废")], default="confirmed", max_length=20),
        ),
        migrations.AddField(
            model_name="expense",
            name="type",
            field=models.CharField(choices=[("expense", "支出"), ("income", "收入"), ("transfer", "转账")], default="expense", max_length=20),
        ),
        # 2. Rename title -> note
        migrations.RenameField(
            model_name="expense",
            old_name="title",
            new_name="note",
        ),
        # 3. Populate occurred_at from occurred_on, copy title data to note
        migrations.RunPython(migrate_occurred_at, reverse_code=noop),
        # 4. Make occurred_at non-nullable, remove occurred_on
        migrations.AlterField(
            model_name="expense",
            name="occurred_at",
            field=models.DateTimeField(),
        ),
        migrations.RemoveField(
            model_name="expense",
            name="occurred_on",
        ),
        # 5. Update ordering
        migrations.AlterModelOptions(
            name="expense",
            options={"ordering": ["-occurred_at", "-created_at"]},
        ),
    ]
