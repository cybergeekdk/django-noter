from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.db import models
from django.utils import timezone

# Garantien markeres som «udløber snart» når der er højst så mange dage tilbage.
WARRANTY_SOON_DAYS = 60


class Item(models.Model):
    class Warranty(models.TextChoices):
        NONE = "none", "Ingen garanti"
        ACTIVE = "active", "Garanti OK"
        SOON = "soon", "Udløber snart"
        EXPIRED = "expired", "Udløbet"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="equipment")
    name = models.CharField("navn", max_length=200)
    location = models.CharField("placering", max_length=200, blank=True, help_text="Fx «Garage – hylde 2»")
    quantity = models.PositiveIntegerField("antal", default=1, validators=[MaxValueValidator(1_000_000)])
    purchased_on = models.DateField("købsdato", null=True, blank=True)
    warranty_expires_on = models.DateField("garanti udløber", null=True, blank=True)
    notes = models.TextField("bemærkninger", blank=True, max_length=2000)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        verbose_name = "udstyr"
        verbose_name_plural = "udstyr"

    def __str__(self):
        return self.name

    def clean(self):
        if self.purchased_on and self.warranty_expires_on and self.warranty_expires_on < self.purchased_on:
            raise ValidationError({"warranty_expires_on": "Garantien kan ikke udløbe før købsdatoen."})

    @property
    def warranty_days_left(self):
        """Dage til garantien udløber (negativt hvis udløbet), eller None uden garantidato."""
        if not self.warranty_expires_on:
            return None
        return (self.warranty_expires_on - timezone.localdate()).days

    @property
    def warranty_status(self):
        days = self.warranty_days_left
        if days is None:
            return self.Warranty.NONE
        if days < 0:
            return self.Warranty.EXPIRED
        if days <= WARRANTY_SOON_DAYS:
            return self.Warranty.SOON
        return self.Warranty.ACTIVE


def soon_cutoff():
    return timezone.localdate() + timedelta(days=WARRANTY_SOON_DAYS)
