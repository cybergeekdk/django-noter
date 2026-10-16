from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("", views.ItemListView.as_view(), name="list"),
    path("nyt/", views.ItemEditView.as_view(), name="create"),
    path("eksport/", views.ItemExportView.as_view(), name="export"),
    path("<int:pk>/", views.ItemDetailView.as_view(), name="detail"),
    path("<int:pk>/rediger/", views.ItemEditView.as_view(), name="update"),
    path("<int:pk>/slet/", views.ItemDeleteView.as_view(), name="delete"),
    path("bilag/<int:pk>/", views.serve_attachment, name="attachment"),
    path("placeringer/", views.LocationListView.as_view(), name="locations"),
    path("placeringer/ny/", views.LocationCreateView.as_view(), name="location_create"),
    path("placeringer/<int:pk>/rediger/", views.LocationUpdateView.as_view(), name="location_update"),
    path("placeringer/<int:pk>/slet/", views.LocationDeleteView.as_view(), name="location_delete"),
    path("paamindelser/", views.ReminderSettingsView.as_view(), name="reminders"),
    path("paamindelser/test/", views.ReminderTestView.as_view(), name="reminder_test"),
]
