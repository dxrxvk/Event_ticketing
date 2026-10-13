import datetime

from django.db import migrations


# The Multiculture Mixer (9 Oct 2026) was cancelled and its buyers carried over to
# Haunted Halloween on the same row, so this re-points the singleton rather than
# creating a second event. Values are off the poster.
EVENT_NAME = 'Haunted Halloween'
# The area ("Caballito") is baked into event.config.js as venueArea and rendered under
# the venue, so it is left out here or the page would print it twice.
VENUE = 'Valentín Virasoro 814'
# 23:00 in Buenos Aires (-03:00, no DST) on the 31st is 02:00 UTC on 1 November.
EVENT_DATE = datetime.datetime(2026, 11, 1, 2, 0, tzinfo=datetime.timezone.utc)
CAPACITY = 120


def point_at_halloween(apps, schema_editor):
    """Rename, re-date and re-place the event, and raise capacity to 120.

    Capacity only goes up, like 0004: lowering it could take seats away from people
    already holding them. Nothing else moves -- the price, the high-water mark and
    every booking's frozen quote stay exactly as they are.
    """
    EventSettings = apps.get_model('tickets', 'EventSettings')
    EventSettings.objects.filter(pk=1).update(
        event_name=EVENT_NAME,
        venue=VENUE,
        event_date=EVENT_DATE,
    )
    EventSettings.objects.filter(pk=1, capacity__lt=CAPACITY).update(capacity=CAPACITY)


def leave_as_is(apps, schema_editor):
    """Deliberately a no-op: the Mixer's details are gone, and restoring them would
    mean guessing at what the admin held before."""


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0008_eventsettings_flat_price_cents_and_more'),
    ]

    operations = [
        migrations.RunPython(point_at_halloween, leave_as_is),
    ]
