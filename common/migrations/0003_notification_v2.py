from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0002_notificationlog"),
    ]

    operations = [
        migrations.RenameField(
            model_name="notificationlog",
            old_name="reference_id",
            new_name="source_id",
        ),
        migrations.RenameField(
            model_name="notificationlog",
            old_name="is_read",
            new_name="read_at",
        ),
        migrations.AlterField(
            model_name="notificationlog",
            name="read_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="notificationlog",
            name="source_type",
            field=models.CharField(blank=True, help_text="Source model, e.g. Reminder/RecurringExpense/Task", max_length=40),
        ),
        migrations.AddField(
            model_name="notificationlog",
            name="scheduled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="notificationlog",
            name="delivered_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="notificationlog",
            name="status",
            field=models.CharField(choices=[("pending", "待推送"), ("delivered", "已推送"), ("read", "已读"), ("ignored", "已忽略")], default="pending", max_length=20),
        ),
        migrations.AddField(
            model_name="notificationlog",
            name="idempotency_key",
            field=models.CharField(blank=True, help_text="Prevent duplicates, e.g. reminder-42-2026-08-10", max_length=200, null=True, unique=True),
        ),
        migrations.AddIndex(
            model_name="notificationlog",
            index=models.Index(fields=["user", "status"], name="common_noti_user_st_ix"),
        ),
        migrations.AddIndex(
            model_name="notificationlog",
            index=models.Index(fields=["idempotency_key"], name="common_noti_idem_ix"),
        ),
    ]
