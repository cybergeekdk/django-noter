from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, ListView

from .forms import SignUpForm
from .permissions import (
    ROLE_SUPERADMIN,
    ROLE_USER,
    SuperAdminRequiredMixin,
    is_last_superadmin,
)

User = get_user_model()


class SignUpView(CreateView):
    form_class = SignUpForm
    template_name = "accounts/signup.html"
    success_url = reverse_lazy("pages:home")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(self.success_url)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        login(self.request, self.object)
        messages.success(self.request, f"Velkommen, {self.object.get_username()}! Din konto er oprettet.")
        return response


class UserListView(SuperAdminRequiredMixin, ListView):
    template_name = "accounts/user_list.html"
    context_object_name = "users"

    def get_queryset(self):
        return User.objects.order_by("-is_superuser", "username")


class UserDeleteView(SuperAdminRequiredMixin, DeleteView):
    model = User
    template_name = "accounts/user_confirm_delete.html"
    context_object_name = "target"
    success_url = reverse_lazy("accounts:user_list")

    def _blocked_reason(self, target):
        if target.pk == self.request.user.pk:
            return "Du kan ikke slette din egen konto."
        if is_last_superadmin(target):
            return "Den sidste super admin kan ikke slettes."
        return None

    def get(self, request, *args, **kwargs):
        reason = self._blocked_reason(self.get_object())
        if reason:
            messages.error(request, reason)
            return redirect(self.success_url)
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        target = self.get_object()
        reason = self._blocked_reason(target)
        if reason:
            messages.error(request, reason)
            return redirect(self.success_url)
        name = target.get_username()
        target.delete()
        messages.success(request, f"Brugeren «{name}» er slettet.")
        return redirect(self.success_url)


class UserRoleView(SuperAdminRequiredMixin, View):
    """Skifter en brugers rolle. Super admin = is_superuser + is_staff (adgang til admin)."""

    http_method_names = ["post"]

    def post(self, request, pk):
        target = get_object_or_404(User, pk=pk)
        role = request.POST.get("role")
        if role not in (ROLE_USER, ROLE_SUPERADMIN):
            messages.error(request, "Ukendt rolle.")
        elif role == ROLE_USER and is_last_superadmin(target):
            messages.error(request, "Den sidste super admin kan ikke gøres til almindelig bruger.")
        else:
            make_admin = role == ROLE_SUPERADMIN
            target.is_superuser = target.is_staff = make_admin
            target.save(update_fields=["is_superuser", "is_staff"])
            label = "super admin" if make_admin else "almindelig bruger"
            messages.success(request, f"«{target.get_username()}» er nu {label}.")
        return redirect("accounts:user_list")
