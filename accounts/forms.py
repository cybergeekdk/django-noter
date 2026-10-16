from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm


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


class SignUpForm(UserCreationForm):
    """Tilmelding. Nye brugere er altid almindelige brugere (ingen staff/superuser)."""

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ["username", "email"]
        labels = {"email": "E-mail (valgfri)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
        self.fields["username"].widget.attrs["autofocus"] = True
