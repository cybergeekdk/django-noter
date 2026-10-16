from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string

from .forms import NoteForm
from .models import Category, Note


def is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def search_notes(query, category_id=None):
    notes = Note.objects.select_related("category")
    if category_id:
        notes = notes.filter(category_id=category_id)
    if query:
        notes = notes.filter(Q(title__icontains=query) | Q(body__icontains=query))
    return notes


def parse_category_id(request):
    """Kategori-id fra ?kategori=, eller None hvis det mangler/er ugyldigt."""
    value = request.GET.get("kategori", "")
    return int(value) if value.isdigit() else None


def note_list(request):
    query = request.GET.get("q", "").strip()
    category_id = parse_category_id(request)

    if request.method == "POST":
        form = NoteForm(request.POST)
        if form.is_valid():
            form.save()
            if is_ajax(request):
                html = render_to_string(
                    "notes/_note_list.html",
                    {"notes": search_notes(query, category_id)},
                    request=request,
                )
                return JsonResponse({"html": html})
            return redirect("notes:list")
        if is_ajax(request):
            return JsonResponse({"errors": form.errors}, status=400)
    else:
        form = NoteForm()

    context = {
        "notes": search_notes(query, category_id),
        "form": form,
        "query": query,
        "categories": Category.objects.all(),
        "selected_category": category_id,
    }
    if is_ajax(request):
        return render(request, "notes/_note_list.html", context)
    return render(request, "notes/note_list.html", context)


def note_delete(request, pk):
    note = get_object_or_404(Note, pk=pk)
    if request.method == "POST":
        note.delete()
        if is_ajax(request):
            return HttpResponse(status=204)
    return redirect("notes:list")
