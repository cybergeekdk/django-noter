from django import forms
from django.forms import inlineformset_factory

from .models import Guide, Section, SectionImage
from .validators import validate_image_file

MAX_IMAGES_PER_SECTION = 20


class GuideForm(forms.ModelForm):
    class Meta:
        model = Guide
        fields = ["title", "summary", "cover"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "summary": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "cover": forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
        }


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.FileField):
    """Flere billeder i ét felt. Hver fil valideres for størrelse og rigtigt billedformat."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"class": "form-control", "accept": "image/*"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        files = data if isinstance(data, (list, tuple)) else [data] if data else []
        cleaned = [single_clean(f, initial) for f in files]
        for f in cleaned:
            validate_image_file(f)
        return cleaned


class SectionForm(forms.ModelForm):
    new_images = MultipleImageField(
        label="Tilføj billeder",
        required=False,
        help_text="JPEG, PNG, GIF eller WEBP, op til 8 MB pr. billede. Du kan vælge flere ad gangen.",
    )

    class Meta:
        model = Section
        fields = ["title", "body"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "body": forms.Textarea(attrs={"class": "form-control font-monospace", "rows": 14}),
        }

    def clean_new_images(self):
        files = self.cleaned_data["new_images"]
        existing = self.instance.images.count() if self.instance.pk else 0
        if existing + len(files) > MAX_IMAGES_PER_SECTION:
            raise forms.ValidationError(f"Et afsnit kan højst have {MAX_IMAGES_PER_SECTION} billeder.")
        return files


ImageFormSet = inlineformset_factory(
    Section,
    SectionImage,
    fields=["caption"],
    extra=0,
    can_delete=True,
    widgets={"caption": forms.TextInput(attrs={"class": "form-control form-control-sm", "placeholder": "Billedtekst"})},
)
