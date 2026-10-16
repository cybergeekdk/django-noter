from django.urls import path

from . import views

app_name = "notes"

urlpatterns = [
    path("", views.note_list, name="list"),
    path("<int:pk>/slet/", views.note_delete, name="delete"),
]
