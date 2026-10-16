from django.views.generic import TemplateView

from notes.models import Note


class HomeView(TemplateView):
    template_name = "pages/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["note_count"] = Note.objects.count()
        context["latest_notes"] = Note.objects.all()[:3]
        return context


class AboutView(TemplateView):
    template_name = "pages/about.html"
