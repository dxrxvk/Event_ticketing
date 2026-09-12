from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import skipUnless

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import services
from .models import Booking, EventSettings, Guest, seats_taken
from .money import format_ars

CAPACITY = 10
PRICE = 500_000


def configure_event(**overrides):
    """EventSettings is seeded by a data migration, but TransactionTestCase flushes it.
    update_or_create works under both test classes."""
    defaults = {
        'capacity': CAPACITY,
        'ticket_price_cents': PRICE,
        'alias': 'test.alias.mp',
        'cvu': '0000003100000000000000',
        'account_holder_name': 'Test Holder',
        'organiser_whatsapp': '+5491100000000',
        'pending_ttl_minutes': 45,
        'sales_close_at': None,
    }
    defaults.update(overrides)
    settings_row, _ = EventSettings.objects.update_or_create(pk=1, defaults=defaults)
    return settings_row


def make_booking(name='Ana', quantity=1, status=Booking.Status.PENDING, minutes_old=0):
    booking = Booking.objects.create(
        buyer_name=name,
        buyer_whatsapp='+5491100000001',
        quantity=quantity,
        total_amount=PRICE * quantity,
        status=status,
        confirmed_at=timezone.now() if status != Booking.Status.PENDING else None,
    )
    Guest.objects.bulk_create(
        Guest(booking=booking, full_name=f'{name} {i}') for i in range(quantity)
    )
    if minutes_old:
        Booking.objects.filter(pk=booking.pk).update(
            created_at=timezone.now() - timedelta(minutes=minutes_old)
        )
        booking.refresh_from_db()
    return booking


def guest_payload(count):
    return [{'full_name': f'Guest {i}'} for i in range(count)]


class MoneyTests(TestCase):
    def test_argentine_thousands_separator(self):
        # Dot for thousands, not comma: this is read off the screen and typed into an
        # Argentine banking app.
        self.assertEqual(format_ars(500_000), '5.000')
        self.assertEqual(format_ars(1_500_000), '15.000')
        self.assertEqual(format_ars(100_000_000), '1.000.000')

    def test_cents_rendered_with_comma(self):
        self.assertEqual(format_ars(500_023, with_cents=True), '5.000,23')
        self.assertEqual(format_ars(500_007, with_cents=True), '5.000,07')


class SeatCountingTests(TestCase):
    def setUp(self):
        self.event = configure_event()

    def test_pending_inside_ttl_holds_seats(self):
        make_booking(quantity=3)
        self.assertEqual(seats_taken(self.event), 3)

    def test_confirmed_statuses_never_expire(self):
        make_booking(quantity=2, status=Booking.Status.SELF_CONFIRMED, minutes_old=600)
        make_booking(quantity=1, status=Booking.Status.VERIFIED, minutes_old=600)
        self.assertEqual(seats_taken(self.event), 3)

    def test_cancelled_frees_seats(self):
        booking = make_booking(quantity=4, status=Booking.Status.VERIFIED)
        self.assertEqual(seats_taken(self.event), 4)
        booking.status = Booking.Status.CANCELLED
        booking.save()
        self.assertEqual(seats_taken(self.event), 0)

    def test_stale_pending_frees_seats_with_no_sweep(self):
        """The starvation case.

        A sold-out event stops receiving requests, so a sweep triggered by incoming
        requests can never run -- the seats would stay dead for the rest of the sale.
        Expiry is a query predicate instead, so this passes with nothing having run and
        the row still marked pending.
        """
        stale = make_booking(quantity=CAPACITY, minutes_old=46)
        self.assertEqual(seats_taken(self.event), 0)
        stale.refresh_from_db()
        self.assertEqual(stale.status, Booking.Status.PENDING)


class CreateBookingTests(TestCase):
    def setUp(self):
        self.event = configure_event()
        self.client = APIClient()

    def post(self, **overrides):
        body = {
            'buyer_name': 'Ana Perez',
            'buyer_whatsapp': '+5491111111111',
            'guests': guest_payload(2),
        }
        body.update(overrides)
        return self.client.post('/api/bookings/', body, format='json')

    def test_creates_booking_and_guests(self):
        response = self.post()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(Guest.objects.count(), 2)

    def test_response_carries_everything_the_pay_screen_needs(self):
        # S7: the create response must be self-contained, because a second round trip
        # would hit a backend that may have gone back to sleep.
        body = self.post().json()
        for field in ('reference', 'total_amount', 'amount_display', 'alias', 'cvu',
                      'account_holder_name', 'organiser_whatsapp', 'status'):
            self.assertIn(field, body)
        self.assertEqual(body['amount_display'], '10.000')

    def test_quantity_is_derived_not_accepted(self):
        """A client-supplied quantity must not be able to disagree with the guest list."""
        response = self.post(quantity=99, guests=guest_payload(3))
        self.assertEqual(response.status_code, 201)
        booking = Booking.objects.get()
        self.assertEqual(booking.quantity, 3)
        self.assertEqual(booking.total_amount, PRICE * 3)

    def test_rejects_empty_and_oversized_guest_lists(self):
        self.assertEqual(self.post(guests=[]).status_code, 400)
        self.assertEqual(self.post(guests=guest_payload(9)).status_code, 400)

    def test_sold_out_returns_409_with_remaining(self):
        make_booking(quantity=9, status=Booking.Status.SELF_CONFIRMED)
        response = self.post(guests=guest_payload(2))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'sold_out')
        self.assertEqual(response.json()['remaining'], 1)

    def test_exact_fit_is_allowed(self):
        make_booking(quantity=8, status=Booking.Status.SELF_CONFIRMED)
        self.assertEqual(self.post(guests=guest_payload(2)).status_code, 201)

    def test_sales_closed_after_deadline(self):
        configure_event(sales_close_at=timezone.now() - timedelta(minutes=1))
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'sales_closed')
        # The page needs somewhere to send them.
        self.assertTrue(response.json()['organiser_whatsapp'])

    def test_retry_returns_the_same_booking(self):
        """A double-tap during a 50-60s cold start must not consume seats twice."""
        first = self.post()
        second = self.post()
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()['reference'], second.json()['reference'])
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(seats_taken(self.event), 2)

    def test_different_party_size_is_not_a_retry(self):
        self.post(guests=guest_payload(2))
        self.post(guests=guest_payload(3))
        self.assertEqual(Booking.objects.count(), 2)


class ConfirmBookingTests(TestCase):
    def setUp(self):
        self.event = configure_event()
        self.client = APIClient()

    def confirm(self, booking):
        return self.client.post(f'/api/bookings/{booking.reference}/confirm/', format='json')

    def test_confirms_pending(self):
        booking = make_booking(quantity=2)
        self.assertEqual(self.confirm(booking).status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.SELF_CONFIRMED)
        self.assertIsNotNone(booking.confirmed_at)

    def test_confirming_twice_is_idempotent(self):
        booking = make_booking()
        self.confirm(booking)
        booking.refresh_from_db()
        first_time = booking.confirmed_at
        self.assertEqual(self.confirm(booking).status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.confirmed_at, first_time)

    def test_expired_booking_resurrects_when_there_is_room(self):
        booking = make_booking(quantity=2, minutes_old=46)
        self.assertEqual(self.confirm(booking).status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.SELF_CONFIRMED)
        self.assertEqual(seats_taken(self.event), 2)

    def test_expired_booking_fails_loudly_when_full(self):
        """Never a bare 200 for a booking with no seat: the buyer would see the warm
        confirmation screen and then transfer money for nothing."""
        booking = make_booking(quantity=2, minutes_old=46)
        make_booking(name='Beto', quantity=CAPACITY, status=Booking.Status.SELF_CONFIRMED)
        response = self.confirm(booking)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'booking_expired')
        self.assertTrue(response.json()['organiser_whatsapp'])
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.EXPIRED)

    def test_cancelled_booking_cannot_confirm(self):
        booking = make_booking(status=Booking.Status.CANCELLED)
        response = self.confirm(booking)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'booking_cancelled')

    def test_unknown_reference_is_404(self):
        response = self.client.post('/api/bookings/nope/confirm/', format='json')
        self.assertEqual(response.status_code, 404)


class ReadEndpointTests(TestCase):
    def setUp(self):
        self.event = configure_event()
        self.client = APIClient()

    def test_health_touches_the_database(self):
        # S7 says this touches nothing, which would leave Neon asleep for the first real
        # query. Assert a query actually runs.
        with self.assertNumQueries(1):
            response = self.client.get('/api/health/')
        self.assertEqual(response.json(), {'ok': True})

    def test_availability_reports_counts(self):
        make_booking(quantity=4, status=Booking.Status.SELF_CONFIRMED)
        body = self.client.get('/api/availability/').json()
        self.assertEqual(body['capacity'], CAPACITY)
        self.assertEqual(body['taken'], 4)
        self.assertEqual(body['remaining'], 6)
        self.assertFalse(body['sold_out'])

    def test_availability_performs_no_writes(self):
        stale = make_booking(quantity=3, minutes_old=46)
        self.client.get('/api/availability/')
        stale.refresh_from_db()
        self.assertEqual(stale.status, Booking.Status.PENDING)


class AdminAccessTests(TestCase):
    def setUp(self):
        configure_event()

    def test_anonymous_is_redirected_from_booking_list(self):
        self.assertEqual(self.client.get('/admin/tickets/booking/').status_code, 302)

    def test_event_settings_cannot_be_added(self):
        User.objects.create_superuser('org', 'o@example.com', 'pw')
        self.client.login(username='org', password='pw')
        # A second row is impossible (save pins pk=1), so adding one would silently
        # overwrite the live configuration.
        self.assertEqual(self.client.get('/admin/tickets/eventsettings/add/').status_code, 403)


@skipUnless(
    connection.features.has_select_for_update,
    'select_for_update is a silent no-op on this backend, so this test would pass '
    'without proving anything. Run it against Postgres.',
)
class CapacityRaceTest(TransactionTestCase):
    """The test event_ticketing.md S6 asks for, with a corrected assertion.

    S6 says "two coworkers grabbing the last two seats... assert exactly one succeeds".
    With two seats free and two single-ticket requests, both *should* succeed -- written
    to the letter the test fails on correct code, and the natural fix is to make the code
    over-reject real buyers. The invariants that actually matter are that capacity is
    never exceeded and that everyone who fits gets in.
    """

    def setUp(self):
        self.event = configure_event()

    @staticmethod
    def _attempt(index, quantity):
        try:
            services.create_booking(
                buyer_name=f'Buyer {index}',
                # Distinct numbers, or the idempotency check would collapse these into
                # one booking and the race would never happen.
                buyer_whatsapp=f'+549110000{index:04d}',
                guests=guest_payload(quantity),
            )
            return True
        except services.SoldOut:
            return False
        finally:
            # Django opens a connection per thread; leaking them hangs the suite.
            connection.close()

    def _run(self, attempts, quantity):
        with ThreadPoolExecutor(max_workers=attempts) as pool:
            return list(pool.map(lambda i: self._attempt(i, quantity), range(attempts)))

    def test_capacity_is_never_exceeded_under_contention(self):
        results = self._run(attempts=20, quantity=1)
        self.assertLessEqual(Guest.objects.count(), CAPACITY)
        self.assertEqual(sum(results), CAPACITY)

    def test_everyone_who_fits_gets_in(self):
        make_booking(quantity=8, status=Booking.Status.SELF_CONFIRMED)
        # Two seats left, two single-ticket buyers: both must succeed.
        results = self._run(attempts=2, quantity=1)
        self.assertEqual(sum(results), 2)
        self.assertEqual(Guest.objects.count(), CAPACITY)

    def test_multi_seat_bookings_do_not_oversell(self):
        results = self._run(attempts=10, quantity=3)
        self.assertLessEqual(Guest.objects.count(), CAPACITY)
        self.assertEqual(sum(results), CAPACITY // 3)
