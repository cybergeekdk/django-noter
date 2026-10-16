from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from guides.storage import PrivateStorage

from .validators import ALLOWED_EXTENSIONS, validate_attachment

# Garantien markeres som «udløber snart» når der er højst så mange dage tilbage.
WARRANTY_SOON_DAYS = 60


class Location(models.Model):
    """En fast placering (fx «Garage – hylde 2»). Omdøbes ét sted og følger alle ting med."""

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="locations")
    name = models.CharField("navn", max_length=100)

    class Meta:
        ordering = ["name"]
        verbose_name = "placering"
        verbose_name_plural = "placeringer"
        constraints = [
            models.UniqueConstraint(Lower("name"), "owner", name="unique_location_name_per_owner"),
        ]

    def __str__(self):
        return self.name


class Item(models.Model):
    class Warranty(models.TextChoices):
        NONE = "none", "Ingen garanti"
        ACTIVE = "active", "Garanti OK"
        SOON = "soon", "Udløber snart"
        EXPIRED = "expired", "Udløbet"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="equipment")
    name = models.CharField("navn", max_length=200)
    location = models.ForeignKey(
        Location,
        verbose_name="placering",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="items",
    )
    quantity = models.PositiveIntegerField("antal", default=1, validators=[MaxValueValidator(1_000_000)])
    purchased_on = models.DateField("købsdato", null=True, blank=True)
    warranty_expires_on = models.DateField("garanti udløber", null=True, blank=True)
    notes = models.TextField("bemærkninger", blank=True, max_length=2000)
    # Dagen, hvor der sidst blev sendt en påmindelse om denne garanti (så der kun sendes én).
    reminded_at = models.DateField(null=True, blank=True, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        verbose_name = "udstyr"
        verbose_name_plural = "udstyr"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Ny garantidato = ny påmindelse. Nulstil, hvis datoen er ændret.
        if self.pk and self.reminded_at:
            old = Item.objects.filter(pk=self.pk).values_list("warranty_expires_on", flat=True).first()
            if old != self.warranty_expires_on:
                self.reminded_at = None
        super().save(*args, **kwargs)

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


def attachment_path(instance, filename):
    """Tilfældigt filnavn, så brugerens eget filnavn aldrig bruges på disken."""
    import os
    import uuid

    return f"inventory/{uuid.uuid4().hex}{os.path.splitext(filename)[1].lower()}"


class Attachment(models.Model):
    """Kvittering, manual eller foto til en ting. Privat fil, leveres kun til ejeren."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(
        "fil",
        upload_to=attachment_path,
        storage=PrivateStorage(),
        validators=[FileExtensionValidator(ALLOWED_EXTENSIONS), validate_attachment],
        max_length=200,
    )
    label = models.CharField("navn", max_length=200, blank=True, help_text="Fx «Kvittering» eller «Manual»")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "bilag"
        verbose_name_plural = "bilag"

    def __str__(self):
        return self.label or "Bilag"

    @property
    def is_pdf(self):
        return self.file.name.lower().endswith(".pdf")


class ReminderPreference(models.Model):
    """Brugerens valg til e-mailpåmindelser om garantier, der udløber."""

    DAYS_CHOICES = [(7, "1 uge før"), (14, "2 uger før"), (30, "1 måned før"), (60, "2 måneder før"), (90, "3 måneder før")]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reminder_pref")
    enabled = models.BooleanField("send mig påmindelser", default=False)
    days_before = models.PositiveSmallIntegerField("varsel", choices=DAYS_CHOICES, default=30)

    class Meta:
        verbose_name = "påmindelsesindstilling"
        verbose_name_plural = "påmindelsesindstillinger"

    def __str__(self):
        return f"{self.user} ({'til' if self.enabled else 'fra'})"
