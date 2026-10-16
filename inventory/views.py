import csv
import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from .forms import AttachmentFormSet, ItemForm, LocationForm, ReminderForm
from .models import Attachment, Item, Location, ReminderPreference, soon_cutoff

# Alle visninger filtrerer på ejer. Andres data giver 404 (ikke 403), også for super admins.

# Sortering: ?sorter=<nøgle>. Kun disse (hvidliste) accepteres.
SORTS = {
    "navn": ("Navn", [F("name").asc()]),
    "sted": ("Placering", [F("location__name").asc(nulls_last=True), F("name").asc()]),
    "antal": ("Antal (flest først)", [F("quantity").desc(), F("name").asc()]),
    "koebt": ("Købsdato (nyeste først)", [F("purchased_on").desc(nulls_last=True), F("name").asc()]),
    "garanti": ("Garanti (udløber først)", [F("warranty_expires_on").asc(nulls_last=True), F("name").asc()]),
}
WARRANTY_FILTERS = {
    "udloebet": "Udløbet",
    "snart": "Udløber snart",
    "aktiv": "Garanti OK",
    "ingen": "Ingen garantidato",
}
NO_LOCATION = "__ingen__"


def filter_items(queryset, params):
    """Anvender søgning, placering, garantifilter og sortering fra GET-parametre."""
    query = (params.get("q") or "").strip()
    if query:
        queryset = queryset.filter(
            Q(name__icontains=query) | Q(location__name__icontains=query) | Q(notes__icontains=query)
        )
    location = params.get("sted") or ""
    if location == NO_LOCATION:
        queryset = queryset.filter(location__isnull=True)
    elif location.isdigit() and len(location) < 10:  # længere tal kan ikke være et id (og ville give OverflowError)
        queryset = queryset.filter(location_id=int(location))

    today, cutoff = timezone.localdate(), soon_cutoff()
    warranty = params.get("garanti")
    if warranty == "udloebet":
        queryset = queryset.filter(warranty_expires_on__lt=today)
    elif warranty == "snart":
        queryset = queryset.filter(warranty_expires_on__gte=today, warranty_expires_on__lte=cutoff)
    elif warranty == "aktiv":
        queryset = queryset.filter(warranty_expires_on__gt=cutoff)
    elif warranty == "ingen":
        queryset = queryset.filter(warranty_expires_on__isnull=True)

    sort = params.get("sorter") if params.get("sorter") in SORTS else "navn"
    return queryset.order_by(*SORTS[sort][1])


class OwnItemsMixin(LoginRequiredMixin):
    def get_queryset(self):
        return Item.objects.filter(owner=self.request.user).select_related("location")


class ItemListView(OwnItemsMixin, ListView):
    template_name = "inventory/item_list.html"
    context_object_name = "items"

    def get_queryset(self):
        items = super().get_queryset().annotate(attachment_count=Count("attachments", distinct=True))
        return filter_items(items, self.request.GET)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        mine = Item.objects.filter(owner=self.request.user)
        today, cutoff = timezone.localdate(), soon_cutoff()
        params = self.request.GET
        context.update(
            total_quantity=mine.aggregate(total=Sum("quantity"))["total"] or 0,
            total_items=mine.count(),
            expired_count=mine.filter(warranty_expires_on__lt=today).count(),
            soon_count=mine.filter(warranty_expires_on__gte=today, warranty_expires_on__lte=cutoff).count(),
            locations=Location.objects.filter(owner=self.request.user),
            sorts={key: label for key, (label, _) in SORTS.items()},
            warranty_filters=WARRANTY_FILTERS,
            q=(params.get("q") or "").strip(),
            sel_location=params.get("sted", ""),
            sel_warranty=params.get("garanti", ""),
            sel_sort=params.get("sorter") if params.get("sorter") in SORTS else "navn",
            filtered=any(params.get(k) for k in ("q", "sted", "garanti")),
            querystring=params.urlencode(),
        )
        return context


class ItemDetailView(OwnItemsMixin, DetailView):
    template_name = "inventory/item_detail.html"
    context_object_name = "item"

    def get_queryset(self):
        return super().get_queryset().prefetch_related("attachments")


class ItemEditView(LoginRequiredMixin, View):
    """Opretter (uden pk) eller redigerer (med pk) en ting, inkl. bilag og ny placering."""

    template_name = "inventory/item_form.html"

    def load(self, request, pk):
        self.item = get_object_or_404(Item, pk=pk, owner=request.user) if pk else None

    def render_forms(self, request, form, formset, status=200):
        context = {"form": form, "formset": formset, "object": self.item}
        return render(request, self.template_name, context, status=status)

    def get(self, request, pk=None):
        self.load(request, pk)
        form = ItemForm(instance=self.item, user=request.user)
        formset = AttachmentFormSet(instance=self.item, prefix="files") if self.item else None
        return self.render_forms(request, form, formset)

    def post(self, request, pk=None):
        self.load(request, pk)
        form = ItemForm(request.POST, request.FILES, instance=self.item, user=request.user)
        formset = AttachmentFormSet(request.POST, instance=self.item, prefix="files") if self.item else None
        if not (form.is_valid() and (formset is None or formset.is_valid())):
            return self.render_forms(request, form, formset, status=400)
        with transaction.atomic():
            item = form.save()
            if formset is not None:
                formset.save()
        messages.success(request, f"«{item.name}» er gemt.")
        return redirect("inventory:list")


class ItemDeleteView(OwnItemsMixin, DeleteView):
    template_name = "inventory/confirm_delete.html"
    success_url = reverse_lazy("inventory:list")

    def form_valid(self, form):
        messages.success(self.request, f"«{self.object.name}» er slettet.")
        return super().form_valid(form)


@login_required
def serve_attachment(request, pk):
    """Leverer et bilag til ejeren. PDF'er hentes som fil; billeder vises direkte."""
    attachment = get_object_or_404(Attachment, pk=pk, item__owner=request.user)
    if not attachment.file:
        raise Http404
    content_type = mimetypes.guess_type(attachment.file.name)[0] or "application/octet-stream"
    try:
        handle = attachment.file.open("rb")
    except FileNotFoundError:
        raise Http404
    if attachment.is_pdf:
        name = f"{attachment.label or 'bilag'}.pdf".replace("/", "-")
        response = FileResponse(handle, content_type=content_type, as_attachment=True, filename=name)
    else:
        response = FileResponse(handle, content_type=content_type)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Cache-Control"] = "private, max-age=3600"
    return response


def csv_safe(value):
    """Forhindrer formelinjektion, når filen åbnes i Excel/Sheets."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


class ItemExportView(OwnItemsMixin, View):
    """CSV med de aktuelle filtre. Semikolon og BOM, så dansk Excel åbner den korrekt."""

    def get(self, request):
        items = filter_items(self.get_queryset(), request.GET)
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="udstyr.csv"'
        response.write("﻿")
        writer = csv.writer(response, delimiter=";")
        writer.writerow(["Navn", "Placering", "Antal", "Købsdato", "Garanti udløber", "Garantistatus", "Bemærkninger"])
        for item in items:
            writer.writerow([
                csv_safe(item.name),
                csv_safe(item.location.name if item.location else ""),
                item.quantity,
                item.purchased_on.isoformat() if item.purchased_on else "",
                item.warranty_expires_on.isoformat() if item.warranty_expires_on else "",
                item.warranty_status.label,
                csv_safe(item.notes),
            ])
        return response


# --- Placeringer -----------------------------------------------------------------------------


class OwnLocationsMixin(LoginRequiredMixin):
    def get_queryset(self):
        return Location.objects.filter(owner=self.request.user)


class LocationListView(OwnLocationsMixin, ListView):
    template_name = "inventory/location_list.html"
    context_object_name = "locations"

    def get_queryset(self):
        return super().get_queryset().annotate(item_count=Count("items"))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["unplaced_count"] = Item.objects.filter(owner=self.request.user, location__isnull=True).count()
        return context


class LocationFormMixin:
    form_class = LocationForm
    template_name = "inventory/location_form.html"
    success_url = reverse_lazy("inventory:locations")

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "user": self.request.user}


class LocationCreateView(LocationFormMixin, LoginRequiredMixin, CreateView):
    def form_valid(self, form):
        messages.success(self.request, f"Placeringen «{form.instance.name}» er oprettet.")
        return super().form_valid(form)


class LocationUpdateView(LocationFormMixin, OwnLocationsMixin, UpdateView):
    def form_valid(self, form):
        messages.success(self.request, f"Placeringen er omdøbt til «{form.instance.name}».")
        return super().form_valid(form)


class LocationDeleteView(OwnLocationsMixin, DeleteView):
    template_name = "inventory/location_confirm_delete.html"
    success_url = reverse_lazy("inventory:locations")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["item_count"] = self.object.items.count()
        return context

    def form_valid(self, form):
        messages.success(self.request, f"Placeringen «{self.object.name}» er slettet. Tingene er bevaret.")
        return super().form_valid(form)


# --- E-mailpåmindelser -----------------------------------------------------------------------


class ReminderSettingsView(LoginRequiredMixin, View):
    template_name = "inventory/reminders.html"

    def get_preference(self, request):
        return ReminderPreference.objects.get_or_create(user=request.user)[0]

    def get(self, request):
        form = ReminderForm(instance=self.get_preference(request), user=request.user)
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = ReminderForm(request.POST, instance=self.get_preference(request), user=request.user)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form}, status=400)
        form.save()
        messages.success(request, "Indstillingerne er gemt.")
        return redirect("inventory:reminders")


class ReminderTestView(LoginRequiredMixin, View):
    """Sender en testmail til brugerens egen adresse, så man kan se, at mailopsætningen virker."""

    http_method_names = ["post"]

    def post(self, request):
        if not request.user.email:
            messages.error(request, "Gem først en e-mailadresse.")
        else:
            try:
                EmailMessage(
                    "Testmail fra din udstyrsliste",
                    "Hvis du kan læse denne mail, virker garantipåmindelserne.",
                    to=[request.user.email],
                ).send()
                messages.success(request, f"Testmail sendt til {request.user.email}.")
            except Exception:
                messages.error(request, "Mailen kunne ikke sendes. Tjek mailopsætningen på serveren.")
        return redirect("inventory:reminders")
