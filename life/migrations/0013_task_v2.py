from django.db import migrations, models


def migrate_completed_to_status(apps, schema_editor):
    Task = apps.get_model("life", "Task")
    Task.objects.filter(completed=True).update(status="completed")
    Task.objects.filter(completed=False).update(status="todo")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("life", "0012_installmentplan"),
    ]

    operations = [
        # 1. Add new fields (nullable first)
        migrations.AddField(
            model_name="task",
            name="description",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="task",
            name="source",
            field=models.CharField(choices=[("voice", "语音"), ("text", "文本"), ("manual", "手动"), ("ai", "AI")], default="manual", max_length=20),
        ),
        migrations.AddField(
            model_name="task",
            name="status",
            field=models.CharField(choices=[("todo", "待办"), ("in_progress", "进行中"), ("completed", "已完成"), ("cancelled", "已取消"), ("archived", "已归档")], default="todo", max_length=20),
        ),
        migrations.AddField(
            model_name="task",
            name="parent_task",
            field=models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="subtasks", to="life.task"),
        ),
        # 2. Migrate existing completed data → status
        migrations.RunPython(migrate_completed_to_status, reverse_code=noop),
        # 3. Remove old completed boolean field
        migrations.RemoveField(
            model_name="task",
            name="completed",
        ),
        # 4. Update ordering
        migrations.AlterModelOptions(
            name="task",
            options={"ordering": ["status", "-priority", "due_at"]},
        ),
    ]
