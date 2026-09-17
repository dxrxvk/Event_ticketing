from django.db import connection
from rest_framework import status
from rest_framework.decorators import api_view, throttle_classes, throttle_scope
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from . import services
from .models import Booking, EventSettings
from .serializers import (
    BookingCreateSerializer, SongRequestsSerializer, pay_screen_payload,
    song_requests_payload,
)


class BookingRateThrottle(ScopedRateThrottle):
    """Rate-limits the write endpoints under DEFAULT_THROTTLE_RATES in settings.py.

    ScopedRateThrottle reads its scope from the *view* (`view.throttle_scope`), not from
    the throttle class -- a `scope` attribute here does nothing and the throttle silently
    lets everything through. Hence the @throttle_scope decorator on each write view:
    'booking' for creating, 'confirm' for confirming (a buyer who has already transferred
    must never be the one told to wait), 'song_requests' for the optional extras.
    tickets/test_robustness.py asserts the request past each limit is a 429.
    """


def _error(code, message, http_status=status.HTTP_409_CONFLICT, **extra):
    """409 rather than 400 for every state conflict below: the request was well formed,
    the server just will not accept it in its current state."""
    return Response({'error': code, 'message': message, **extra}, status=http_status)


@api_view(['GET'])
def health(request):
    """Wakes the whole stack, not just Django.

    S7 specifies that this "touches nothing", which defeats its purpose: S13 notes that
    Neon sleeps too, so a ping that skips the database leaves Postgres asleep and the
    first real query lands inside the locked transaction in POST /bookings/ -- the one
    request that must not be slow or fail.
    """
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
    return Response({'ok': True})


@api_view(['GET'])
def availability(request):
    """Seat counts. Performs no writes -- expiry is a query predicate, so nothing needs
    sweeping before the numbers are correct."""
    return Response(services.availability())


@api_view(['POST'])
@throttle_classes([BookingRateThrottle])
@throttle_scope('booking')
def create_booking(request):
    serializer = BookingCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    try:
        booking, created = services.create_booking(
            buyer_name=data['buyer_name'],
            buyer_whatsapp=data['buyer_whatsapp'],
            buyer_email=data.get('buyer_email', ''),
            sender_account_name=data.get('sender_account_name', ''),
            guests=data['guests'],
        )
    except services.SoldOut as exc:
        return _error(exc.code, str(exc), remaining=exc.remaining)
    except services.SalesClosed as exc:
        return _error(
            exc.code, str(exc),
            organiser_whatsapp=EventSettings.load().organiser_whatsapp,
        )

    event_settings = EventSettings.load()
    return Response(
        pay_screen_payload(booking, event_settings),
        # A retry of an in-flight booking returns the original, so it is not a creation.
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@api_view(['POST'])
@throttle_classes([BookingRateThrottle])
@throttle_scope('confirm')
def confirm_booking(request, reference):
    try:
        booking = services.confirm_booking(reference)
    except Booking.DoesNotExist:
        return _error(
            'not_found', 'No booking with that reference.',
            http_status=status.HTTP_404_NOT_FOUND,
        )
    except (services.BookingExpired, services.BookingCancelled) as exc:
        # Both strand a buyer who may be mid-transfer, so hand the page a way to reach a
        # human rather than a dead end.
        return _error(
            exc.code, str(exc),
            organiser_whatsapp=EventSettings.load().organiser_whatsapp,
        )

    return Response(pay_screen_payload(booking, EventSettings.load()))


@api_view(['POST'])
@throttle_classes([BookingRateThrottle])
@throttle_scope('song_requests')
def song_requests(request, reference):
    serializer = SongRequestsSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    try:
        saved = services.set_song_requests(reference, serializer.validated_data['songs'])
    except Booking.DoesNotExist:
        return _error(
            'not_found', 'No booking with that reference.',
            http_status=status.HTTP_404_NOT_FOUND,
        )
    except (services.BookingCancelled, services.BookingNotConfirmed) as exc:
        return _error(
            exc.code, str(exc),
            organiser_whatsapp=EventSettings.load().organiser_whatsapp,
        )

    return Response(song_requests_payload(saved))
