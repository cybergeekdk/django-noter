from django import forms

from .models import Item


class ItemForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = ["name", "location", "quantity", "purchased_on", "warranty_expires_on", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "autofocus": True}),
            "location": forms.TextInput(attrs={"class": "form-control", "list": "locations", "autocomplete": "off"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "purchased_on": forms.DateInput(format="%Y-%m-%d", attrs={"class": "form-control", "type": "date"}),
            "warranty_expires_on": forms.DateInput(format="%Y-%m-%d", attrs={"class": "form-control", "type": "date"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }
