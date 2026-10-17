from django.contrib import admin

from .models import FetchRun, SpotPrice


@admin.register(SpotPrice)
class SpotPriceAdmin(admin.ModelAdmin):
    list_display = ["time", "area", "price_dkk_mwh", "price_eur_mwh"]
    list_filter = ["area"]
    date_hierarchy = "time"
    ordering = ["-time"]


@admin.register(FetchRun)
class FetchRunAdmin(admin.ModelAdmin):
    list_display = ["started", "ok", "rows_new", "rows_updated", "message"]
    list_filter = ["ok"]
    readonly_fields = ["started", "finished", "ok", "rows_new", "rows_updated", "message"]

    def has_add_permission(self, request):
        return False
