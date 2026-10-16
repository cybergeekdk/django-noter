from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import BootstrapAuthenticationForm

app_name = "pages"

urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("om/", views.AboutView.as_view(), name="about"),
    path(
        "login/",
        auth_views.LoginView.as_view(authentication_form=BootstrapAuthenticationForm),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
