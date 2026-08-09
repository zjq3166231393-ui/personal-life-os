from django.db import migrations


def assign_null_entries_to_first_superuser(apps, schema_editor):
    Entry = apps.get_model("life", "Entry")
    User = apps.get_model("auth", "User")
    orphaned = Entry.objects.filter(user__isnull=True)
    if not orphaned.exists():
        return
    first_admin = User.objects.filter(is_superuser=True).order_by("id").first()
    if first_admin:
        orphaned.update(user=first_admin)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("life", "0002_entry_user")]
    operations = [migrations.RunPython(assign_null_entries_to_first_superuser, reverse_code=noop)]
