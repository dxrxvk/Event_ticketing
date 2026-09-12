from rest_framework import serializers

from .models import MAX_TICKETS_PER_BOOKING
from .money import format_ars


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
        'alias': event_settings.alias,
        'cvu': event_settings.cvu,
        'account_holder_name': event_settings.account_holder_name,
        'organiser_whatsapp': event_settings.organiser_whatsapp,
        'list_deadline': event_settings.list_deadline,
    }
