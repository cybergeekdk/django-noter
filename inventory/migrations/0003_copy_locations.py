from django.db import migrations


def text_to_locations(apps, schema_editor):
    """Gør hver brugers tekstplaceringer til faste placeringer og kobler tingene til dem."""
    Item = apps.get_model("inventory", "Item")
    Location = apps.get_model("inventory", "Location")
    cache = {}
    for item in Item.objects.exclude(location="").iterator():
        name = item.location.strip()
        if not name:
            continue
        # Samme sted skrevet med forskellig store/små bogstaver bliver til ét sted.
        key = (item.owner_id, name.lower())
        if key not in cache:
            cache[key] = Location.objects.get_or_create(owner_id=item.owner_id, name=name[:100])[0]
        item.place = cache[key]
        item.save(update_fields=["place"])


def locations_to_text(apps, schema_editor):
    Item = apps.get_model("inventory", "Item")
    for item in Item.objects.exclude(place=None).select_related("place").iterator():
        item.location = item.place.name
        item.save(update_fields=["location"])


class Migration(migrations.Migration):
    dependencies = [("inventory", "0002_locations")]

    operations = [migrations.RunPython(text_to_locations, locations_to_text)]
