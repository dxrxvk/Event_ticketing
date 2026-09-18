from rest_framework import serializers

from .models import MAX_SONG_REQUESTS, MAX_TICKETS_PER_BOOKING
from .money import format_ars, format_minor_units


class GuestInputSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=200)


class BookingCreateSerializer(serializers.Serializer):
    """Input for POST /api/bookings/.

    Deliberately has no `quantity` field, contra event_ticketing.md S7. Accepting both a
    quantity and a guest list lets them disagree: a body with quantity 2 and three names
    owes for two seats and occupies three. Quantity is derived from len(guests), and the
    1..MAX bound lives here so a fat-fingered 11 is a clean 400 rather than eleven sold
    seats.
    """

    buyer_name = serializers.CharField(max_length=200)
    buyer_whatsapp = serializers.CharField(max_length=30)
    buyer_email = serializers.EmailField(required=False, allow_blank=True, default='')
    sender_account_name = serializers.CharField(
        max_length=200, required=False, allow_blank=True, default='',
    )
    guests = GuestInputSerializer(
        many=True,
        min_length=1,
        max_length=MAX_TICKETS_PER_BOOKING,
    )

    def validate_guests(self, value):
        for guest in value:
            if not guest['full_name'].strip():
                raise serializers.ValidationError('Every guest needs a name.')
        return value


class SongRequestsSerializer(serializers.Serializer):
    """Input for POST /api/bookings/<reference>/songs/. Replaces the whole list."""

    songs = serializers.ListField(
        child=serializers.CharField(max_length=120, allow_blank=True),
        max_length=MAX_SONG_REQUESTS,
        allow_empty=True,
    )

    def validate_songs(self, value):
        # Blank slots are the empty inputs on the form, not requests.
        return [song.strip() for song in value if song.strip()]


def song_requests_payload(song_requests):
    return {'songs': [{'text': song.text} for song in song_requests]}


def revolut_amount_for(booking, event_settings):
    """What this booking owes on the Revolut rail, in minor units.

    Three cases, in order, and the order is the whole point:

    1. A figure was frozen when the booking was priced -- use it, exactly as the peso
       `total_amount` is used. This is the normal path.
    2. No frozen figure, but the booking has a peso breakdown. This is a booking made
       while Revolut was switched off and priced after the organiser turned it on.
       Rebuild it from the bands the booking was actually quoted in, so a top-tier
       booking is not charged the first-tier figure -- the naive `price * quantity`
       would quote a 9.000-band pair EUR 10 instead of EUR 18.
    3. No breakdown at all: a row from before tiers existed, when one flat price was
       the truth. The flat product is then the right answer.
    """
    if booking.revolut_amount_cents is not None:
        return booking.revolut_amount_cents
    if booking.price_breakdown:
        return sum(
            event_settings.revolut_price_for(line['unit_price_cents']) * line['quantity']
            for line in booking.price_breakdown
        )
    return event_settings.revolut_price_cents * booking.quantity


def revolut_payload(booking, event_settings):
    """The second destination, or None until tag, currency and price are all set.

    Gating on the tag alone would publish "EUR 0.00" to every buyer the moment the
    organiser saves a half-filled admin form.
    """
    if not (
        event_settings.revolut_tag
        and event_settings.revolut_currency
        and event_settings.revolut_price_cents
    ):
        return None
    amount = revolut_amount_for(booking, event_settings)
    return {
        'tag': event_settings.revolut_tag,
        'link': f'https://revolut.me/{event_settings.revolut_tag}',
        'currency': event_settings.revolut_currency,
        'amount_cents': amount,
        'amount_display': format_minor_units(amount, event_settings.revolut_currency),
    }


def pay_screen_payload(booking, event_settings):
    """Everything the pay screen needs, in one response.

    S7 is explicit that the create response must be self-contained: a second round trip
    would hit a backend that may have gone back to sleep. The payment destination is
    served from here rather than from the frontend's baked-in build, so correcting a
    mistyped alias is an admin edit rather than a redeploy.
    """
    return {
        'reference': booking.reference,
        'status': booking.status,
        'quantity': booking.quantity,
        'total_amount': booking.total_amount,
        # Argentine format, deliberately: this number is read off the screen and typed
        # into an Argentine banking app.
        'amount_display': format_ars(booking.total_amount),
        # The quote, line by line. A party that straddles a tier boundary owes two
        # different unit prices, and "24.000" with no explanation reads like a mistake
        # to someone who was told tickets cost 5.000.
        'price_breakdown': [
            {
                'from_seat': line['from_seat'],
                'to_seat': line['to_seat'],
                'quantity': line['quantity'],
                'unit_price_cents': line['unit_price_cents'],
                'unit_price_display': format_ars(line['unit_price_cents']),
            }
            for line in (booking.price_breakdown or [])
        ],
        'pricing_display': booking.pricing_display,
        'alias': event_settings.alias,
        'cvu': event_settings.cvu,
        'account_holder_name': event_settings.account_holder_name,
        'organiser_whatsapp': event_settings.organiser_whatsapp,
        'list_deadline': event_settings.list_deadline,
        'revolut': revolut_payload(booking, event_settings),
    }
