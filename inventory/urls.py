from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("", views.ItemListView.as_view(), name="list"),
    path("nyt/", views.ItemCreateView.as_view(), name="create"),
    path("eksport/", views.ItemExportView.as_view(), name="export"),
    path("<int:pk>/rediger/", views.ItemUpdateView.as_view(), name="update"),
    path("<int:pk>/slet/", views.ItemDeleteView.as_view(), name="delete"),
]
