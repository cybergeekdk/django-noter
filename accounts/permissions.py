from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.exceptions import PermissionDenied

ROLE_USER = "user"
ROLE_SUPERADMIN = "superadmin"
ROLES = {ROLE_USER: "Bruger", ROLE_SUPERADMIN: "Super admin"}


def role_of(user):
    return ROLE_SUPERADMIN if user.is_superuser else ROLE_USER


def is_last_superadmin(user):
    """Sand hvis `user` er den eneste super admin (må hverken slettes eller degraderes)."""
    others = get_user_model().objects.filter(is_superuser=True).exclude(pk=user.pk)
    return user.is_superuser and not others.exists()


class SuperAdminRequiredMixin(UserPassesTestMixin):
    """Kun super admins. Anonyme sendes til login, andre loggede brugere får 403."""

    def test_func(self):
        return self.request.user.is_superuser

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied
        return super().handle_no_permission()
