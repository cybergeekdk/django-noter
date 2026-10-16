from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("inventory", "0003_copy_locations")]

    operations = [
        migrations.RemoveField(model_name="item", name="location"),
        migrations.RenameField(model_name="item", old_name="place", new_name="location"),
    ]
