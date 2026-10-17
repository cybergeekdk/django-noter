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
            "enabled", "area", "notify_cheap", "threshold_ore", "incl_vat", "include_grid", "min_hours",
            "warn_expensive", "expensive_ore", "notify_email", "notify_phone",
        ]
        widgets = {
            "enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "area": forms.Select(attrs={"class": "form-select"}),
            "notify_cheap": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "threshold_ore": forms.NumberInput(attrs={"class": "form-control", "step": "0.5"}),
            "incl_vat": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "include_grid": forms.CheckboxInput(attrs={"class": "form-check-input"}),
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


# --- Nettarif ------------------------------------------------------------------------------------

from decimal import Decimal  # noqa: E402

from .models import GridCompany, GridSettings, GridTariff  # noqa: E402

HOURS = range(24)


def tariff_choices(gln):
    """[(kode, navn)] for netselskabets tariffer (nyeste navn pr. kode)."""
    names = {}
    for tariff in GridTariff.objects.filter(gln=gln).order_by("code", "valid_from"):
        names[tariff.code] = tariff.name or tariff.code  # sidste (nyeste) række pr. kode vinder
    return [(code, f"{name} ({code})" if name != code else code) for code, name in sorted(names.items(), key=lambda kv: kv[1].lower())]


def money_field(label, help_text=""):
    return forms.DecimalField(
        label=label,
        required=False,
        min_value=Decimal("0"),
        max_value=Decimal("1000"),
        max_digits=7,
        decimal_places=3,
        localize=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001", "min": "0"}),
        help_text=help_text,
    )


class GridForm(forms.Form):
    source = forms.ChoiceField(choices=GridSettings.Source.choices, widget=forms.RadioSelect, initial=GridSettings.Source.DATAHUB)
    company = forms.ModelChoiceField(
        queryset=GridCompany.objects.all(),
        required=False,
        empty_label="Vælg netselskab…",
        label="Netselskab",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    tariff_code = forms.ChoiceField(required=False, label="Tarif", widget=forms.Select(attrs={"class": "form-select"}))
    elafgift_ore = money_field("Elafgift")
    energinet_ore = money_field("Energinets tariffer (system og transmission)")
    supplier_ore = money_field("Leverandørtillæg")

    def __init__(self, *args, user, instance, selected_company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.instance = instance
        for hour in HOURS:
            self.fields[f"hour_{hour}"] = forms.DecimalField(
                required=False,
                min_value=Decimal("0"),
                max_value=Decimal("1000"),
                max_digits=7,
                decimal_places=3,
                localize=False,
                label=f"{hour:02d}–{(hour + 1) % 24:02d}",
                widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.001", "min": "0"}),
            )
        # Tarifvalg afhænger af det valgte netselskab (fra POST, ellers de gemte indstillinger).
        company = None if selected_company is False else selected_company  # False = «intet netselskab valgt»
        if selected_company is None and company is None and self.is_bound:
            company = GridCompany.objects.filter(pk=self.data.get("company") or None).first() if str(self.data.get("company", "")).isdigit() else None
        if selected_company is None and company is None and not self.is_bound:
            company = instance.company
        self.company = company
        self.fields["tariff_code"].choices = [("", "Vælg tarif…")] + (tariff_choices(company.gln) if company else [])
        if not self.is_bound:
            # De gemte værdier er kun standard: det, der allerede er givet (fx kilde og netselskab, når man
            # skifter netselskab og trykker «Vis tariffer»), må ikke overskrives.
            hours = instance.manual_hours if len(instance.manual_hours) == 24 else [None] * 24
            defaults = dict(
                source=instance.source,
                company=company,
                tariff_code=instance.tariff_code,
                elafgift_ore=instance.elafgift_ore,
                energinet_ore=instance.energinet_ore,
                supplier_ore=instance.supplier_ore,
                **{f"hour_{h}": hours[h] for h in HOURS},
            )
            for name, value in defaults.items():
                self.initial.setdefault(name, value)

    def hour_fields(self):
        return [self[f"hour_{hour}"] for hour in HOURS]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("source") == GridSettings.Source.MANUAL:
            hours = [cleaned.get(f"hour_{h}") for h in HOURS]
            if any(value is None for value in hours):
                self.add_error(None, "Udfyld prisen for alle 24 timer, eller vælg at hente fra Energinet.")
        elif cleaned.get("source") == GridSettings.Source.DATAHUB:
            if not cleaned.get("company"):
                self.add_error("company", "Vælg dit netselskab.")
            elif not cleaned.get("tariff_code"):
                if self.fields["tariff_code"].choices[1:]:
                    self.add_error("tariff_code", "Vælg din tarif.")
                else:
                    self.add_error("tariff_code", "Der er ikke hentet tariffer for dette netselskab endnu.")
        return cleaned

    def save(self):
        data, instance = self.cleaned_data, self.instance
        instance.user = self.user
        instance.source = data["source"]
        instance.elafgift_ore = data.get("elafgift_ore") or Decimal("0")
        instance.energinet_ore = data.get("energinet_ore") or Decimal("0")
        instance.supplier_ore = data.get("supplier_ore") or Decimal("0")
        if instance.source == GridSettings.Source.DATAHUB:
            instance.company, instance.tariff_code = data["company"], data["tariff_code"]
        else:
            instance.manual_hours = [float(data[f"hour_{h}"]) for h in HOURS]
        instance.save()
        return instance
