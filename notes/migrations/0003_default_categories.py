from django.db import migrations

DEFAULTS = [("Privat", "success"), ("Arbejde", "primary"), ("Idéer", "warning")]


def create_defaults(apps, schema_editor):
    Category = apps.get_model("notes", "Category")
    for name, color in DEFAULTS:
        Category.objects.get_or_create(name=name, defaults={"color": color})


class Migration(migrations.Migration):
    dependencies = [("notes", "0002_category")]

    operations = [migrations.RunPython(create_defaults, migrations.RunPython.noop)]
