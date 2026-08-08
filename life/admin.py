from django.contrib import admin
from .models import Entry


@admin.register(Entry)
class EntryAdmin(admin.ModelAdmin):
    list_display = ("kind", "title", "category", "amount", "occurred_on", "due_at", "completed")
    list_filter = ("kind", "category", "completed")
    search_fields = ("title", "raw_text")

