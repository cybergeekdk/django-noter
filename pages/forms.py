from django import forms
from django.contrib.auth.forms import AuthenticationForm


class BootstrapAuthenticationForm(AuthenticationForm):
    username = forms.CharField(
        label="Brugernavn",
        widget=forms.TextInput(attrs={"class": "form-control", "autofocus": True, "autocomplete": "username"}),
    )
    password = forms.CharField(
        label="Adgangskode",
        strip=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "current-password"}),
    )
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Forkert brugernavn eller adgangskode.",
    }
