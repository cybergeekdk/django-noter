from django import forms

from . import push
from .alerts import forget_pending_days
from .models import CheapPowerAlert


class AlertForm(forms.ModelForm):
    email = forms.EmailField(
        label="E-mailadresse",
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-control"}),
        help_text="E-mails sendes hertil.",
    )

    class Meta:
        model = CheapPowerAlert
        fields = [
            "enabled", "area", "notify_cheap", "threshold_ore", "incl_vat", "min_hours",
            "warn_expensive", "expensive_ore", "notify_email", "notify_phone",
        ]
        widgets = {
            "enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "area": forms.Select(attrs={"class": "form-select"}),
            "notify_cheap": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "threshold_ore": forms.NumberInput(attrs={"class": "form-control", "step": "0.5"}),
            "incl_vat": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "min_hours": forms.Select(attrs={"class": "form-select"}),
            "warn_expensive": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "expensive_ore": forms.NumberInput(attrs={"class": "form-control", "step": "0.5"}),
            "notify_email": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "notify_phone": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["email"].initial = user.email

    def clean(self):
        cleaned = super().clean()
        cheap, expensive = cleaned.get("notify_cheap"), cleaned.get("warn_expensive")
        low, high = cleaned.get("threshold_ore"), cleaned.get("expensive_ore")
        if cheap and expensive and low is not None and high is not None and high <= low:
            self.add_error("expensive_ore", "Grænsen for dyr skal være højere end grænsen for billig.")
        if cleaned.get("enabled"):
            if not (cheap or expensive):
                self.add_error(None, "Vælg mindst én slags besked: billig eller dyr strøm.")
            if not (cleaned.get("notify_email") or cleaned.get("notify_phone")):
                self.add_error(None, "Vælg mindst én måde at få besked på: e-mail eller telefon.")
            if cleaned.get("notify_email") and not cleaned.get("email"):
                self.add_error("email", "Angiv en e-mailadresse for at få e-mails.")
        return cleaned

    def save(self, commit=True):
        alert = super().save(commit=False)
        alert.user = self.user
        self.user.email = self.cleaned_data["email"]
        if alert.notify_phone and not alert.ntfy_topic:
            alert.ntfy_topic = push.new_topic()  # tilfældigt og hemmeligt; brugeren vælger det aldrig selv
        if commit:
            self.user.save(update_fields=["email"])
            alert.save()
            forget_pending_days(self.user)  # nye indstillinger: vurdér i dag og i morgen igen
        return alert
