from django import forms

from .alerts import forget_pending_days
from .models import CheapPowerAlert


class AlertForm(forms.ModelForm):
    email = forms.EmailField(
        label="E-mailadresse",
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-control"}),
        help_text="Beskederne sendes hertil.",
    )

    class Meta:
        model = CheapPowerAlert
        fields = ["enabled", "area", "threshold_ore", "incl_vat", "min_hours"]
        widgets = {
            "enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "area": forms.Select(attrs={"class": "form-select"}),
            "threshold_ore": forms.NumberInput(attrs={"class": "form-control", "step": "0.5"}),
            "incl_vat": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "min_hours": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["email"].initial = user.email

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("enabled") and not cleaned.get("email"):
            self.add_error("email", "Angiv en e-mailadresse for at få beskeder.")
        return cleaned

    def save(self, commit=True):
        alert = super().save(commit=False)
        alert.user = self.user
        self.user.email = self.cleaned_data["email"]
        if commit:
            self.user.save(update_fields=["email"])
            alert.save()
            forget_pending_days(self.user)  # nye indstillinger: vurdér i dag og i morgen igen
        return alert
