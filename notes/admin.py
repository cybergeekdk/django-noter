from django.contrib import admin

from .models import Note


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ["title", "created"]
    list_filter = ["created"]
    search_fields = ["title", "body"]
    date_hierarchy = "created"
    readonly_fields = ["created"]
    fields = ["title", "body", "created"]
