import os
import uuid

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.urls import reverse

from .storage import PrivateStorage
from .validators import ALLOWED_EXTENSIONS, validate_image_file


def upload_path(instance, filename):
    """Tilfældigt filnavn, så brugerens eget filnavn aldrig bruges på disken."""
    ext = os.path.splitext(filename)[1].lower()
    return f"guides/{uuid.uuid4().hex}{ext}"


def image_field(**kwargs):
    return models.ImageField(
        upload_to=upload_path,
        storage=PrivateStorage(),
        validators=[FileExtensionValidator(ALLOWED_EXTENSIONS), validate_image_file],
        max_length=200,
        **kwargs,
    )


class Guide(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="guides")
    title = models.CharField("titel", max_length=200)
    summary = models.TextField("beskrivelse", blank=True, max_length=1000)
    cover = image_field(verbose_name="forsidebillede", blank=True)
    created = models.DateTimeField("oprettet", auto_now_add=True)
    updated = models.DateTimeField("opdateret", auto_now=True)

    class Meta:
        ordering = ["-updated"]
        verbose_name = "guide"
        verbose_name_plural = "guider"

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("guides:detail", args=[self.pk])


class Section(models.Model):
    guide = models.ForeignKey(Guide, on_delete=models.CASCADE, related_name="sections")
    title = models.CharField("titel", max_length=200)
    body = models.TextField("tekst", blank=True)
    position = models.PositiveIntegerField("placering", default=0)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "id"]
        verbose_name = "afsnit"
        verbose_name_plural = "afsnit"

    def __str__(self):
        return self.title


class SectionImage(models.Model):
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="images")
    image = image_field(verbose_name="billede")
    caption = models.CharField("billedtekst", max_length=200, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "billede"
        verbose_name_plural = "billeder"

    def __str__(self):
        return self.caption or os.path.basename(self.image.name)
