import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from .forms import GuideForm, ImageFormSet, SectionForm
from .models import Guide, Section, SectionImage

# Alle visninger filtrerer på ejer. Andres guider/billeder giver 404 (ikke 403),
# så man heller ikke kan se, at de findes. Det gælder også super admins.


class OwnGuidesMixin(LoginRequiredMixin):
    def get_queryset(self):
        return Guide.objects.filter(owner=self.request.user)


class GuideListView(OwnGuidesMixin, ListView):
    template_name = "guides/guide_list.html"
    context_object_name = "guides"

    def get_queryset(self):
        guides = super().get_queryset().annotate(section_count=Count("sections", distinct=True))
        query = self.request.GET.get("q", "").strip()
        if query:
            guides = guides.filter(
                Q(title__icontains=query)
                | Q(summary__icontains=query)
                | Q(sections__title__icontains=query)
                | Q(sections__body__icontains=query)
            ).distinct()
        return guides

    def get_context_data(self, **kwargs):
        return super().get_context_data(query=self.request.GET.get("q", "").strip(), **kwargs)


class GuideDetailView(OwnGuidesMixin, DetailView):
    template_name = "guides/guide_detail.html"
    context_object_name = "guide"

    def get_queryset(self):
        return super().get_queryset().prefetch_related("sections__images")


class GuideCreateView(LoginRequiredMixin, CreateView):
    model = Guide
    form_class = GuideForm
    template_name = "guides/guide_form.html"

    def form_valid(self, form):
        form.instance.owner = self.request.user
        messages.success(self.request, "Guiden er oprettet. Tilføj det første afsnit.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("guides:section_create", args=[self.object.pk])


class GuideUpdateView(OwnGuidesMixin, UpdateView):
    form_class = GuideForm
    template_name = "guides/guide_form.html"

    def form_valid(self, form):
        messages.success(self.request, "Guiden er gemt.")
        return super().form_valid(form)


class GuideDeleteView(OwnGuidesMixin, DeleteView):
    template_name = "guides/confirm_delete.html"
    success_url = reverse_lazy("guides:list")
    extra_context = {"what": "guiden", "details": "Alle afsnit og billeder i guiden slettes også."}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["cancel_url"] = self.object.get_absolute_url()
        return context

    def form_valid(self, form):
        messages.success(self.request, f"Guiden «{self.object.title}» er slettet.")
        return super().form_valid(form)


class SectionEditView(LoginRequiredMixin, View):
    """Opretter (guide_pk) eller redigerer (pk) et afsnit, inkl. billeder og billedtekster."""

    template_name = "guides/section_form.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.section = None

    def load(self, request, pk=None, guide_pk=None):
        if pk is not None:
            self.section = get_object_or_404(Section, pk=pk, guide__owner=request.user)
            self.guide = self.section.guide
        else:
            self.guide = get_object_or_404(Guide, pk=guide_pk, owner=request.user)

    def render_forms(self, request, form, formset, status=200):
        context = {"guide": self.guide, "section": self.section, "form": form, "formset": formset}
        return render(request, self.template_name, context, status=status)

    def get(self, request, pk=None, guide_pk=None):
        self.load(request, pk, guide_pk)
        form = SectionForm(instance=self.section)
        formset = ImageFormSet(instance=self.section) if self.section else None
        return self.render_forms(request, form, formset)

    def post(self, request, pk=None, guide_pk=None):
        self.load(request, pk, guide_pk)
        form = SectionForm(request.POST, request.FILES, instance=self.section)
        formset = ImageFormSet(request.POST, instance=self.section) if self.section else None
        if not (form.is_valid() and (formset is None or formset.is_valid())):
            return self.render_forms(request, form, formset, status=400)

        with transaction.atomic():
            section = form.save(commit=False)
            section.guide = self.guide
            if section.pk is None:
                last = self.guide.sections.aggregate(last=Max("position"))["last"]
                section.position = 0 if last is None else last + 1
            section.save()
            if formset is not None:
                formset.save()
            for upload in form.cleaned_data["new_images"]:
                SectionImage.objects.create(section=section, image=upload)
            self.guide.save(update_fields=["updated"])  # opdaterer «opdateret»

        messages.success(request, "Afsnittet er gemt.")
        if "save_and_add" in request.POST:
            return redirect("guides:section_create", guide_pk=self.guide.pk)
        if "save_and_continue" in request.POST:
            return redirect("guides:section_update", pk=section.pk)
        return redirect(f"{self.guide.get_absolute_url()}#afsnit-{section.pk}")


class SectionDeleteView(LoginRequiredMixin, DeleteView):
    template_name = "guides/confirm_delete.html"
    extra_context = {"what": "afsnittet", "details": "Billederne i afsnittet slettes også."}

    def get_queryset(self):
        return Section.objects.filter(guide__owner=self.request.user).select_related("guide")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["cancel_url"] = self.object.guide.get_absolute_url()
        return context

    def get_success_url(self):
        return self.object.guide.get_absolute_url()

    def form_valid(self, form):
        messages.success(self.request, f"Afsnittet «{self.object.title}» er slettet.")
        return super().form_valid(form)


class SectionMoveView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        section = get_object_or_404(Section, pk=pk, guide__owner=request.user)
        direction = request.POST.get("direction")
        sections = list(section.guide.sections.all())  # allerede sorteret
        index = sections.index(section)
        target = index - 1 if direction == "up" else index + 1 if direction == "down" else None
        if target is not None and 0 <= target < len(sections):
            sections[index], sections[target] = sections[target], sections[index]
            for position, item in enumerate(sections):
                if item.position != position:
                    Section.objects.filter(pk=item.pk).update(position=position)
        return redirect(f"{section.guide.get_absolute_url()}#afsnit-{section.pk}")


def _file_response(field_file):
    if not field_file or not field_file.name:
        raise Http404
    content_type = mimetypes.guess_type(field_file.name)[0] or "application/octet-stream"
    try:
        response = FileResponse(field_file.open("rb"), content_type=content_type)
    except FileNotFoundError:
        raise Http404
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Cache-Control"] = "private, max-age=3600"
    return response


@login_required
def serve_cover(request, pk):
    guide = get_object_or_404(Guide, pk=pk, owner=request.user)
    return _file_response(guide.cover)


@login_required
def serve_image(request, pk):
    image = get_object_or_404(SectionImage, pk=pk, section__guide__owner=request.user)
    return _file_response(image.image)
