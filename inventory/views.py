import csv

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import F, Q, Sum
from django.http import HttpResponse
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from .forms import ItemForm
from .models import Item, soon_cutoff

# Sortering: ?sorter=<nøgle>. Kun disse (hvidliste) accepteres.
SORTS = {
    "navn": ("Navn", [F("name").asc()]),
    "sted": ("Placering", [F("location").asc(), F("name").asc()]),
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


def filter_items(queryset, params):
    """Anvender søgning, placering, garantifilter og sortering fra GET-parametre."""
    query = (params.get("q") or "").strip()
    if query:
        queryset = queryset.filter(
            Q(name__icontains=query) | Q(location__icontains=query) | Q(notes__icontains=query)
        )
    location = params.get("sted")
    if location == "__ingen__":
        queryset = queryset.filter(location="")
    elif location:
        queryset = queryset.filter(location=location)

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
    """Alle visninger filtrerer på ejer; andres udstyr giver 404."""

    def get_queryset(self):
        return Item.objects.filter(owner=self.request.user)


class ItemListView(OwnItemsMixin, ListView):
    template_name = "inventory/item_list.html"
    context_object_name = "items"

    def get_queryset(self):
        return filter_items(super().get_queryset(), self.request.GET)

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
            locations=mine.exclude(location="").order_by("location").values_list("location", flat=True).distinct(),
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


class ItemFormMixin:
    form_class = ItemForm
    template_name = "inventory/item_form.html"
    success_url = reverse_lazy("inventory:list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        mine = Item.objects.filter(owner=self.request.user)
        context["locations"] = mine.exclude(location="").order_by("location").values_list("location", flat=True).distinct()
        return context


class ItemCreateView(ItemFormMixin, LoginRequiredMixin, CreateView):
    def form_valid(self, form):
        form.instance.owner = self.request.user
        messages.success(self.request, f"«{form.instance.name}» er tilføjet.")
        return super().form_valid(form)


class ItemUpdateView(ItemFormMixin, OwnItemsMixin, UpdateView):
    def form_valid(self, form):
        messages.success(self.request, f"«{form.instance.name}» er gemt.")
        return super().form_valid(form)


class ItemDeleteView(OwnItemsMixin, DeleteView):
    template_name = "inventory/confirm_delete.html"
    success_url = reverse_lazy("inventory:list")

    def form_valid(self, form):
        messages.success(self.request, f"«{self.object.name}» er slettet.")
        return super().form_valid(form)


def csv_safe(value):
    """Forhindrer formelinjektion, når filen åbnes i Excel/Sheets."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


class ItemExportView(OwnItemsMixin, View):
    """CSV med de aktuelle filtre. Semikolon og BOM, så dansk Excel åbner den korrekt."""

    def get(self, request):
        items = filter_items(Item.objects.filter(owner=request.user), request.GET)
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="udstyr.csv"'
        response.write("﻿")
        writer = csv.writer(response, delimiter=";")
        writer.writerow(["Navn", "Placering", "Antal", "Købsdato", "Garanti udløber", "Garantistatus", "Bemærkninger"])
        for item in items:
            writer.writerow([
                csv_safe(item.name),
                csv_safe(item.location),
                item.quantity,
                item.purchased_on.isoformat() if item.purchased_on else "",
                item.warranty_expires_on.isoformat() if item.warranty_expires_on else "",
                item.warranty_status.label,
                csv_safe(item.notes),
            ])
        return response
