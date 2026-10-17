from django.urls import path

from . import views

app_name = "elpriser"

urlpatterns = [
    path("", views.index, name="index"),
    path("api/dag/", views.api_day, name="api_day"),
    path("api/dage/", views.api_days, name="api_days"),
    path("api/profil/", views.api_profile, name="api_profile"),
    path("opdater/", views.refresh, name="refresh"),
]
