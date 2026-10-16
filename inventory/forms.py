from django import forms
from django.forms import inlineformset_factory

from .models import Attachment, Item, Location, ReminderPreference
from .validators import validate_attachment

MAX_ATTACHMENTS_PER_ITEM = 10


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleAttachmentField(forms.FileField):
    """Flere filer i ét felt. Hver fil valideres for størrelse og rigtigt indhold (billede/PDF)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault(
            "widget",
            MultipleFileInput(attrs={"class": "form-control", "accept": "image/*,application/pdf"}),
        )
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        files = data if isinstance(data, (list, tuple)) else [data] if data else []
        cleaned = [single_clean(f, initial) for f in files]
        for f in cleaned:
            validate_attachment(f)
        return cleaned


class ItemForm(forms.ModelForm):
    new_location = forms.CharField(
        label="…eller opret ny placering",
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Fx Garage – hylde 3"}),
    )
    new_files = MultipleAttachmentField(
        label="Tilføj bilag",
        required=False,
        help_text="Kvittering, manual eller foto. JPEG, PNG, GIF, WEBP eller PDF, op til 8 MB pr. fil.",
    )

    class Meta:
        model = Item
        fields = ["name", "location", "quantity", "purchased_on", "warranty_expires_on", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "autofocus": True}),
            "location": forms.Select(attrs={"class": "form-select"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "purchased_on": forms.DateInput(format="%Y-%m-%d", attrs={"class": "form-control", "type": "date"}),
            "warranty_expires_on": forms.DateInput(format="%Y-%m-%d", attrs={"class": "form-control", "type": "date"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        # Man kan kun vælge sine egne placeringer.
        self.fields["location"].queryset = Location.objects.filter(owner=user)
        self.fields["location"].empty_label = "Ingen placering"

    def clean_new_location(self):
        return " ".join(self.cleaned_data["new_location"].split())

    def clean_new_files(self):
        files = self.cleaned_data["new_files"]
        existing = self.instance.attachments.count() if self.instance.pk else 0
        if existing + len(files) > MAX_ATTACHMENTS_PER_ITEM:
            raise forms.ValidationError(f"Et udstyr kan højst have {MAX_ATTACHMENTS_PER_ITEM} bilag.")
        return files

    def save(self, commit=True):
        item = super().save(commit=False)
        if not item.pk:
            item.owner = self.user
        name = self.cleaned_data.get("new_location")
        if name:
            location = Location.objects.filter(owner=self.user, name__iexact=name).first()
            item.location = location or Location.objects.create(owner=self.user, name=name)
        if commit:
            item.save()
            for upload in self.cleaned_data["new_files"]:
                Attachment.objects.create(item=item, file=upload)
        return item


AttachmentFormSet = inlineformset_factory(
    Item,
    Attachment,
    fields=["label"],
    extra=0,
    can_delete=True,
    widgets={"label": forms.TextInput(attrs={"class": "form-control form-control-sm", "placeholder": "Navn"})},
)


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = ["name"]
        widgets = {"name": forms.TextInput(attrs={"class": "form-control", "autofocus": True})}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        clash = Location.objects.filter(owner=self.user, name__iexact=name).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("Du har allerede en placering med det navn.")
        return name

    def save(self, commit=True):
        location = super().save(commit=False)
        location.owner = self.user
        if commit:
            location.save()
        return location


class ReminderForm(forms.ModelForm):
    email = forms.EmailField(
        label="E-mailadresse",
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-control"}),
        help_text="Påmindelserne sendes hertil.",
    )

    class Meta:
        model = ReminderPreference
        fields = ["enabled", "days_before"]
        widgets = {
            "enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "days_before": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["email"].initial = user.email

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("enabled") and not cleaned.get("email"):
            self.add_error("email", "Angiv en e-mailadresse for at få påmindelser.")
        return cleaned

    def save(self, commit=True):
        preference = super().save(commit=False)
        preference.user = self.user
        self.user.email = self.cleaned_data["email"]
        if commit:
            self.user.save(update_fields=["email"])
            preference.save()
        return preference
