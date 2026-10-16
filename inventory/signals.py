"""Fjerner filer fra disken, når bilag slettes (også når ting slettes og bilagene følger med)."""

from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Attachment


@receiver(post_delete, sender=Attachment)
def delete_attachment_file(sender, instance, **kwargs):
    if instance.file and instance.file.name:
        instance.file.storage.delete(instance.file.name)
