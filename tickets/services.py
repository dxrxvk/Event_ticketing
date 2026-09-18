"""Booking logic.

Kept out of views.py so the views stay thin, the rules are directly testable, and the
concurrency test can fire real threads at create_booking() without going through HTTP.
"""

from django.db import transaction
from django.utils import timezone

from .models import Booking, EventSettings, Guest, SongRequest, seats_taken
from .money import format_ars


class BookingError(Exception):
    """Base for the conditions the API turns into a specific error response."""

    code = 'booking_error'


class SoldOut(BookingError):
    code = 'sold_out'

    def __init__(self, remaining):
        self.remaining = remaining
        super().__init__(f'Only {remaining} seat(s) left.')


class SalesClosed(BookingError):
    code = 'sales_closed'


class BookingExpired(BookingError):
    code = 'booking_expired'


class BookingCancelled(BookingError):
    code = 'booking_cancelled'


class BookingNotConfirmed(BookingError):
    code = 'booking_not_confirmed'


def _find_live_duplicate(event_settings, buyer_whatsapp, quantity):
    """An existing pending booking that this request is probably a retry of.

    Render cold starts run 50-60s. A buyer in WhatsApp's in-app browser sees nothing
    happen and taps again, which without this creates a second booking: double the seats
    consumed, one transfer against two reservations. In an oversubscribed event that is
    the likeliest way capacity quietly evaporates.
    """
    cutoff = event_settings.pending_cutoff()
    return (
        Booking.objects.filter(
            buyer_whatsapp=buyer_whatsapp,
            quantity=quantity,
            status=Booking.Status.PENDING,
            created_at__gt=cutoff,
        )
        .order_by('-created_at')
        .first()
    )


def create_booking(*, buyer_name, buyer_whatsapp, guests, buyer_email='',
                   sender_account_name=''):
    """Create a booking and its guests, or raise.

    Returns (booking, created). created=False means this was a retry and the existing
    booking is being returned unchanged.
    """
    quantity = len(guests)

    with transaction.atomic():
        # The serialization point. EventSettings is a single row, so locking it turns
        # every concurrent attempt to take a seat into one queue: the count below and the
        # create that follows cannot interleave with another request's. Locking booking
        # rows instead would not help -- the rows being counted are not the rows being
        # created.
        event_settings = EventSettings.objects.select_for_update().get(pk=1)

        if not event_settings.sales_are_open():
            raise SalesClosed(
                'Sales are closed. The guest list has gone to the venue.'
            )

        existing = _find_live_duplicate(event_settings, buyer_whatsapp, quantity)
        if existing is not None:
            return existing, False

        # One count, used for both decisions, so the seat a buyer is charged for is
        # literally the seat capacity admitted them to. Calling next_seat_position()
        # rather than seats_taken() directly is the point: it is the single definition
        # of "which seat is next", and a change there has to reach this path or the
        # ladder and the guest list start describing different events.
        taken = event_settings.next_seat_position()
        if taken + quantity > event_settings.capacity:
            raise SoldOut(max(event_settings.capacity - taken, 0))

        # Priced inside the same lock that just counted, then frozen on the row and
        # never re-derived: the buyer is about to read this figure off the pay screen
        # and type it into a bank, so the ladder must not move under them at confirm.
        breakdown, total, revolut_total = event_settings.price_seats(taken, quantity)

        booking = Booking.objects.create(
            buyer_name=buyer_name,
            buyer_whatsapp=buyer_whatsapp,
            buyer_email=buyer_email,
            sender_account_name=sender_account_name,
            quantity=quantity,
            total_amount=total,
            price_breakdown=breakdown,
            revolut_amount_cents=revolut_total or None,
        )
        # bulk_create skips Guest.clean(), which is correct here: the capacity check
        # above already ran under the lock, and clean() is the guard for the admin path.
        Guest.objects.bulk_create(
            Guest(booking=booking, full_name=g['full_name'].strip()) for g in guests
        )

    return booking, True


def confirm_booking(reference):
    """Flip a booking to self_confirmed. Resurrect-or-fail, never a bare 200.

    A buyer can sit on the pay screen past the TTL -- asking a friend for a surname,
    arguing with their bank about a transfer limit -- and tap confirm on a booking whose
    seats have already been released. Returning 200 there would show them the warm
    "you're on the list" screen for a booking with no seat, and they would transfer
    anyway. So an aged-out booking re-acquires capacity under the same lock, and fails
    loudly if it cannot.
    """
    with transaction.atomic():
        event_settings = EventSettings.objects.select_for_update().get(pk=1)
        booking = Booking.objects.select_for_update().get(reference=reference)

        # Idempotent: confirming twice is a retry, not an error.
        if booking.status in (Booking.Status.SELF_CONFIRMED, Booking.Status.VERIFIED):
            return booking

        if booking.status == Booking.Status.CANCELLED:
            raise BookingCancelled('This booking was cancelled.')

        aged_out = (
            booking.status == Booking.Status.EXPIRED
            or booking.created_at <= event_settings.pending_cutoff()
        )
        # seats_taken() already excludes this booking, since it is expired or a pending
        # row past the cutoff -- so this asks whether there is room to put it back, not
        # whether it fits alongside itself.
        too_late = (
            aged_out
            and seats_taken(event_settings) + booking.quantity > event_settings.capacity
        )

        if not too_late:
            booking.status = Booking.Status.SELF_CONFIRMED
            booking.confirmed_at = timezone.now()
            booking.save(update_fields=['status', 'confirmed_at'])
            return booking

    # Reached only when the booking aged out and there is no room left. Marking it
    # expired has to happen outside the atomic block above: raising inside a transaction
    # rolls it back, so a status written there would be discarded along with the raise,
    # and the row would sit in the admin looking live forever.
    Booking.objects.filter(pk=booking.pk, status=Booking.Status.PENDING).update(
        status=Booking.Status.EXPIRED
    )
    raise BookingExpired('This booking expired and the event is now full.')


def set_song_requests(reference, songs):
    """Replace a booking's song requests with `songs` (already stripped, at most three).

    Only confirmed bookings may add songs: the form appears once payment is confirmed,
    and a form-filler who never paid should not steer the playlist.
    """
    with transaction.atomic():
        # Lock the booking row, not for capacity but for the replace itself: two saves
        # for the same reference overlapping during a cold start would both delete and
        # then both insert position 1, and the unique constraint would surface as a 500.
        booking = Booking.objects.select_for_update().get(reference=reference)
        if booking.status == Booking.Status.CANCELLED:
            raise BookingCancelled('This booking was cancelled.')
        if booking.status not in Booking.CONFIRMED_STATUSES:
            raise BookingNotConfirmed('Confirm your payment first, then add songs.')

        booking.song_requests.all().delete()
        SongRequest.objects.bulk_create(
            SongRequest(booking=booking, position=position, text=text)
            for position, text in enumerate(songs, start=1)
        )
    return list(booking.song_requests.all())


def availability(event_settings=None):
    event_settings = event_settings or EventSettings.load()
    taken = seats_taken(event_settings)
    remaining = max(event_settings.capacity - taken, 0)
    # One read of the tier table, reused for both the ladder and the current price.
    # price_seats() would fetch it again, and this endpoint is polled.
    ladder = event_settings.price_ladder()
    current_price = next(
        (band['price_cents'] for band in ladder if taken + 1 <= band['to_seat']),
        ladder[-1]['price_cents'],
    )
    return {
        'capacity': event_settings.capacity,
        'taken': taken,
        'remaining': remaining,
        # The ladder, so the page can show what the next ticket costs and what the rest
        # will cost, without the frontend hardcoding prices that an admin edit changes.
        # `next_seat` is 1-based because that is how the ladder reads to a human.
        'tiers': [
            {
                'from_seat': band['from_seat'],
                'to_seat': band['to_seat'],
                'price_cents': band['price_cents'],
                'price_display': format_ars(band['price_cents']),
            }
            for band in ladder
        ],
        'next_seat': taken + 1,
        'current_price_cents': current_price,
        'current_price_display': format_ars(current_price),
        'sold_out': remaining == 0,
        'sales_open': event_settings.sales_are_open(),
        'list_deadline': event_settings.list_deadline,
        'organiser_whatsapp': event_settings.organiser_whatsapp,
    }
