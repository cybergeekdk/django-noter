from django.urls import path

from . import views

app_name = "elpriser"

urlpatterns = [
    path("", views.index, name="index"),
    path("api/dag/", views.api_day, name="api_day"),
    path("api/dage/", views.api_days, name="api_days"),
    path("api/profil/", views.api_profile, name="api_profile"),
    path("opdater/", views.refresh, name="refresh"),
    path("besked/", views.alerts, name="alerts"),
    path("besked/test/", views.alerts_test, name="alerts_test"),
    path("besked/test-telefon/", views.alerts_test_phone, name="alerts_test_phone"),
    path("besked/nyt-emne/", views.alerts_new_topic, name="alerts_new_topic"),
]
