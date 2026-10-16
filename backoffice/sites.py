from django.contrib.admin import AdminSite


class BackofficeAdminSite(AdminSite):
    site_header = "Noter – administration"
    site_title = "Noter admin"
    index_title = "Overblik"
    index_template = "backoffice/index.html"

    def get_dashboard_context(self, request):
        """Tilføj nye nøgletal/widgets til forsiden her; de vises i dashboardet."""
        from notes.models import Note

        return {
            "note_count": Note.objects.count(),
            "latest_notes": Note.objects.all()[:5],
        }

    def index(self, request, extra_context=None):
        context = {**self.get_dashboard_context(request), **(extra_context or {})}
        return super().index(request, context)
