from django.db import migrations

DEFAULT_CATEGORIES = [
    {"name": "餐饮", "icon": "🍽️", "kind": "expense"},
    {"name": "交通", "icon": "🚗", "kind": "expense"},
    {"name": "住房", "icon": "🏠", "kind": "expense"},
    {"name": "生活缴费", "icon": "💡", "kind": "expense"},
    {"name": "购物", "icon": "🛒", "kind": "expense"},
    {"name": "其他", "icon": "📦", "kind": "expense"},
    {"name": "工资", "icon": "💰", "kind": "income"},
    {"name": "其他收入", "icon": "💵", "kind": "income"},
]


def seed_categories(apps, schema_editor):
    Category = apps.get_model("life", "Category")
    for cat in DEFAULT_CATEGORIES:
        Category.objects.get_or_create(user=None, name=cat["name"], kind=cat["kind"], defaults={"icon": cat["icon"], "is_default": True})


def copy_entries_to_new_models(apps, schema_editor):
    Entry = apps.get_model("life", "Entry")
    Expense = apps.get_model("life", "Expense")
    Task = apps.get_model("life", "Task")
    Note = apps.get_model("life", "Note")
    Category = apps.get_model("life", "Category")

    cat_by_name = {c.name: c for c in Category.objects.filter(user__isnull=True)}

    for entry in Entry.objects.filter(kind="expense").iterator():
        Expense.objects.create(user_id=entry.user_id, title=entry.title, raw_text=entry.raw_text,
            category=cat_by_name.get(entry.category), amount=entry.amount or 0,
            occurred_on=entry.occurred_on or entry.created_at.date(), created_at=entry.created_at)

    for entry in Entry.objects.filter(kind="task").iterator():
        Task.objects.create(user_id=entry.user_id, title=entry.title, raw_text=entry.raw_text,
            due_at=entry.due_at, priority=entry.priority, completed=entry.completed, created_at=entry.created_at)

    for entry in Entry.objects.filter(kind="note").iterator():
        Note.objects.create(user_id=entry.user_id, title=entry.title, raw_text=entry.raw_text,
            occurred_on=entry.occurred_on, created_at=entry.created_at)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("life", "0004_category_expense_note_task_and_more")]
    operations = [
        migrations.RunPython(seed_categories, reverse_code=noop),
        migrations.RunPython(copy_entries_to_new_models, reverse_code=noop),
    ]
