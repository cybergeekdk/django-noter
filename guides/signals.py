"""Fjerner billedfiler fra disken, når billeder, afsnit eller guider slettes."""

from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

from .models import Guide, SectionImage


def _delete_file(field_file):
    if field_file and field_file.name:
        field_file.storage.delete(field_file.name)


@receiver(post_delete, sender=SectionImage)
def delete_image_file(sender, instance, **kwargs):
    _delete_file(instance.image)


@receiver(post_delete, sender=Guide)
def delete_cover_file(sender, instance, **kwargs):
    _delete_file(instance.cover)


@receiver(pre_save, sender=Guide)
def delete_replaced_cover(sender, instance, **kwargs):
    """Når forsidebilledet udskiftes eller fjernes, slettes den gamle fil."""
    if not instance.pk:
        return
    old = Guide.objects.filter(pk=instance.pk).values_list("cover", flat=True).first()
    if old and old != instance.cover.name:
        instance.cover.storage.delete(old)
