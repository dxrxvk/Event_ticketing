from django.db import migrations


def seed_event_settings(apps, schema_editor):
    """Create the singleton settings row.

    EventSettings.load() does .get(pk=1), so without this row every booking attempt
    raises DoesNotExist and returns a 500. Seeding here rather than from a shell command
    means a fresh database -- including the production one on first deploy -- always has
    it.

    Note apps.get_model() returns the historical model, which has none of the custom
    methods defined on the real class: the save() override that pins pk=1 does not exist
    here, so the primary key is set explicitly.
    """
    EventSettings = apps.get_model('tickets', 'EventSettings')
    EventSettings.objects.get_or_create(pk=1)


def drop_event_settings(apps, schema_editor):
    EventSettings = apps.get_model('tickets', 'EventSettings')
    EventSettings.objects.filter(pk=1).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_event_settings, drop_event_settings),
    ]
