from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html

from .models import Category, Note


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "color_badge", "note_count"]
    search_fields = ["name"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_note_count=Count("notes"))

    @admin.display(description="farve")
    def color_badge(self, obj):
        return format_html(
            '<span style="background:var(--bs-{c},#6c757d);color:#fff;padding:2px 8px;border-radius:4px">{l}</span>',
            c=obj.color,
            l=obj.get_color_display(),
        )

    @admin.display(description="noter", ordering="_note_count")
    def note_count(self, obj):
        return obj._note_count


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ["title", "category", "created"]
    list_filter = ["category", "created"]
    list_select_related = ["category"]
    search_fields = ["title", "body"]
    date_hierarchy = "created"
    readonly_fields = ["created"]
    fields = ["title", "category", "body", "created"]
