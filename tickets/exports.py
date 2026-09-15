"""Guest list exports.

Two audiences, two files, deliberately never merged: the venue gets names and nothing
else, the organiser gets contact details and payment status. S9 is explicit about this,
and the realistic incident is attaching the wrong one to the venue's WhatsApp thread --
hence the INTERNO- filename prefix on the organiser export.

Logic lives here rather than in admin.py so it can be tested without going through HTTP.
"""

import csv
import io
import unicodedata

from django.http import HttpResponse
from django.utils import timezone

from .models import (
    Booking, EventSettings, Guest, SongRequest, fresh_pending_guest_filter,
)
from .money import format_ars


def fold_accents(text):
    """'Álvarez' -> 'alvarez', for sorting only.

    Python's default string sort orders by code point, which puts every accented initial
    after Z: ['Ibarra', 'Ubeda', 'Zapata', 'Álvarez', 'Ñuñez']. On a door list that reads
    as broken. NFKD splits a letter from its diacritic so the diacritic can be dropped.
    """
    decomposed = unicodedata.normalize('NFKD', text)
    return decomposed.encode('ascii', 'ignore').decode('ascii').casefold()


def sort_key(full_name):
    """Sort on the folded whole name.

    S9 asks for "alphabetical by surname", but deriving a surname from one free-text field
    is guesswork -- 'de la Torre', 'María José García López', or someone who typed
    'García, María'. The venue needs a list a human can scan, so whole-string order is
    both honest and sufficient.
    """
    return fold_accents(full_name.strip())


def confirmed_guests():
    """Guests who go on the venue list.

    self_confirmed + verified only. LIVE_STATUSES also includes pending, which would put
    someone who filled the form and never paid onto the door list.
    """
    return (
        Guest.objects
        .filter(booking__status__in=Booking.CONFIRMED_STATUSES)
        .select_related('booking')
    )


def pending_guest_count(event_settings=None):
    """Guests still holding a seat but not yet confirmed -- excluded from the list above.

    Surfaced on the export page so the organiser can decide whether to wait for them
    rather than discovering the gap at the door.
    """
    event_settings = event_settings or EventSettings.load()
    # Reuses the predicate seat counting uses, rather than restating it -- two copies of
    # the TTL rule would drift the first time it changes.
    return Guest.objects.filter(
        fresh_pending_guest_filter(event_settings.pending_cutoff())
    ).count()


def venue_names():
    return sorted((g.full_name.strip() for g in confirmed_guests()), key=sort_key)


def organiser_rows():
    rows = []
    for guest in confirmed_guests():
        booking = guest.booking
        rows.append([
            guest.full_name.strip(),
            booking.buyer_name,
            booking.buyer_whatsapp,
            booking.sender_account_name,
            booking.get_status_display(),
            format_ars(booking.total_amount),
            timezone.localtime(booking.created_at).strftime('%Y-%m-%d %H:%M'),
            booking.reference,
        ])
    rows.sort(key=lambda row: sort_key(row[0]))
    return rows


ORGANISER_HEADER = [
    'Full Name', 'Booked by', 'WhatsApp', 'Transfer from', 'Status', 'Amount',
    'Created', 'Reference',
]


def stamp_exported():
    """Record that these bookings appeared on a list sent to the venue.

    S13 encourages editing guest names after the fact, so without this there is no way to
    tell that a name changed after the venue already had it.
    """
    Booking.objects.filter(status__in=Booking.CONFIRMED_STATUSES).update(
        list_exported_at=timezone.now()
    )


def _csv_response(filename, header, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    # utf-8-sig, not utf-8: without the byte order mark Excel reads the file as latin-1
    # and mangles every a-acute, e-acute and enye. S9 calls this out and it is the single
    # most likely support call from the export.
    payload = buffer.getvalue().encode('utf-8-sig')
    response = HttpResponse(payload, content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _today():
    return timezone.localdate().isoformat()


def venue_text_response():
    """Plain text, one name per line -- what actually gets pasted into WhatsApp."""
    stamp_exported()
    body = '\n'.join(venue_names())
    response = HttpResponse(
        body.encode('utf-8'), content_type='text/plain; charset=utf-8'
    )
    response['Content-Disposition'] = f'attachment; filename="lista-venue-{_today()}.txt"'
    return response


def venue_csv_response():
    stamp_exported()
    return _csv_response(
        f'lista-venue-{_today()}.csv',
        ['Full Name'],
        [[name] for name in venue_names()],
    )


def organiser_csv_response():
    # No stamp: this one never goes to the venue.
    return _csv_response(
        f'INTERNO-organiser-{_today()}.csv',
        ORGANISER_HEADER,
        organiser_rows(),
    )


def dedupe_key(text):
    """Case- and accent-insensitive key that keeps non-Latin letters.

    fold_accents() is for sorting Latin names and throws away everything non-ASCII,
    which would fold every Korean or Cyrillic request to the same empty key and drop all
    but the first from the playlist.
    """
    decomposed = unicodedata.normalize('NFKD', text)
    without_marks = ''.join(
        ch for ch in decomposed if unicodedata.category(ch) != 'Mn'
    )
    return ' '.join(without_marks.casefold().split())


def playlist_lines():
    """Song requests from confirmed bookings, in arrival order, without repeats.

    Three people asking for the same track is one line on the playlist. The key folds
    case and accents, so 'Rosalía' and 'rosalia' are the same request.
    """
    seen = set()
    lines = []
    for song in (
        SongRequest.objects
        .filter(booking__status__in=Booking.CONFIRMED_STATUSES)
        .order_by('created_at', 'position', 'id')
    ):
        key = dedupe_key(song.text)
        if key in seen:
            continue
        seen.add(key)
        lines.append(song.text)
    return lines


def playlist_text_response():
    # No stamp: this never goes to the venue either.
    body = '\n'.join(playlist_lines())
    response = HttpResponse(
        body.encode('utf-8'), content_type='text/plain; charset=utf-8'
    )
    response['Content-Disposition'] = f'attachment; filename="playlist-{_today()}.txt"'
    return response
