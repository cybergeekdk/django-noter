from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import BootstrapAuthenticationForm

app_name = "accounts"

urlpatterns = [
    path(
        "login/",
        auth_views.LoginView.as_view(
            authentication_form=BootstrapAuthenticationForm,
            template_name="accounts/login.html",
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("tilmeld/", views.SignUpView.as_view(), name="signup"),
    path("brugere/", views.UserListView.as_view(), name="user_list"),
    path("brugere/<int:pk>/slet/", views.UserDeleteView.as_view(), name="user_delete"),
    path("brugere/<int:pk>/rolle/", views.UserRoleView.as_view(), name="user_role"),
]
