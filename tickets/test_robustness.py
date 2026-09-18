"""Robustness tests: bursts, hostile input, admin edits mid-sale, and real contention.

tickets/tests.py proves the business rules one request at a time. This module asks what
happens when the link drops into a WhatsApp group: a hundred requests in minutes, some
of them retries, some of them garbage, some of them landing on the same seat in the
same millisecond. Every test here is an invariant that, if broken, sells a seat that
does not exist or turns a buyer away from one that does.

Postgres-only classes are marked with skipUnless(has_select_for_update): SQLite silently
drops FOR UPDATE, so a race test there would pass without proving anything.
"""

import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import mock, skipUnless

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, OperationalError, connection
from django.test import LiveServerTestCase, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from . import services
from .models import Booking, EventSettings, Guest, seats_taken
from .tests import CAPACITY, PRICE, configure_event, guest_payload, make_booking

BOOKINGS_URL = '/api/bookings/'
AVAILABILITY_URL = '/api/availability/'
HEALTH_URL = '/api/health/'


def rate_limit(scope):
    """The N in settings.py's 'N/hour' for a throttle scope, so these tests follow it."""
    return int(settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'][scope].split('/')[0])


THROTTLE_LIMIT = rate_limit('booking')


def confirm_url(reference):
    return f'/api/bookings/{reference}/confirm/'


def booking_body(index, quantity=1):
    """A distinct buyer per index, so the retry check never collapses two of them."""
    return {
        'buyer_name': f'Buyer {index}',
        'buyer_whatsapp': f'+549115{index:06d}',
        'guests': guest_payload(quantity),
    }


def ip_for(index):
    """A distinct client address per index, so the throttle sees a crowd, not a script."""
    return f'10.{(index >> 16) & 255}.{(index >> 8) & 255}.{index & 255}'


class ThrottleResetMixin:
    """Throttle history lives in the process-wide LocMemCache. Without clearing it, the
    hundreds of requests these tests fire from 127.0.0.1 would leak into every test that
    runs after them and surface as unexplained 429s."""

    def setUp(self):
        super().setUp()
        cache.clear()

    def tearDown(self):
        cache.clear()
        super().tearDown()


class ApiBurstTests(ThrottleResetMixin, TestCase):
    """A WhatsApp link drops and a hundred coworkers tap it inside ten minutes."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def test_burst_of_buyers_never_oversells_and_never_errors(self):
        capacity = 50
        configure_event(capacity=capacity)
        attempts = 120
        sold = 0
        sold_out_seen = False

        for i in range(attempts):
            quantity = (i % 3) + 1
            response = self.client.post(
                BOOKINGS_URL, booking_body(i, quantity), format='json',
                REMOTE_ADDR=ip_for(i),
            )
            self.assertIn(
                response.status_code, (201, 409),
                f'request {i}: {response.status_code} {response.content[:200]}',
            )
            if response.status_code == 201:
                sold += quantity
                self.assertEqual(response.data['total_amount'], PRICE * quantity)
            else:
                sold_out_seen = True
                self.assertEqual(response.data['error'], 'sold_out')
                # The remaining count the buyer is told must be the real one.
                self.assertEqual(response.data['remaining'], capacity - sold)
                self.assertLess(response.data['remaining'], quantity)

        self.assertTrue(sold_out_seen, 'burst was not large enough to fill the event')
        self.assertLessEqual(Guest.objects.count(), capacity)
        self.assertEqual(Guest.objects.count(), sold)
        self.assertEqual(seats_taken(), sold)

    def test_read_endpoints_are_not_throttled(self):
        # A sold-out page being refreshed by a whole office must never turn into 429s.
        configure_event()
        for _ in range(THROTTLE_LIMIT + 80):
            self.assertEqual(self.client.get(AVAILABILITY_URL).status_code, 200)
        self.assertEqual(self.client.get(HEALTH_URL).status_code, 200)

    def _seed_many_bookings(self, count, quantity=3):
        bookings = Booking.objects.bulk_create(
            Booking(
                buyer_name=f'Seed {i}',
                buyer_whatsapp=f'+549116{i:06d}',
                quantity=quantity,
                total_amount=PRICE * quantity,
                status=Booking.Status.SELF_CONFIRMED,
                confirmed_at=timezone.now(),
            )
            for i in range(count)
        )
        Guest.objects.bulk_create(
            Guest(booking=b, full_name=f'{b.buyer_name} guest {g}')
            for b in bookings
            for g in range(quantity)
        )

    def test_availability_query_count_does_not_grow_with_the_guest_list(self):
        configure_event(capacity=10_000)
        # Settings row, live-guest count, price tiers. Three constant queries: the
        # point of this test is that none of them is per-booking or per-guest.
        with self.assertNumQueries(3):
            self.client.get(AVAILABILITY_URL)
        self._seed_many_bookings(300)
        with self.assertNumQueries(3):
            response = self.client.get(AVAILABILITY_URL)
        self.assertEqual(response.data['taken'], 900)

    def test_create_booking_query_count_does_not_grow_with_the_guest_list(self):
        configure_event(capacity=10_000)
        with CaptureQueriesContext(connection) as small_table:
            self.client.post(BOOKINGS_URL, booking_body(1), format='json')
        self._seed_many_bookings(300)
        with CaptureQueriesContext(connection) as big_table:
            self.client.post(BOOKINGS_URL, booking_body(2), format='json')
        self.assertEqual(
            len(small_table.captured_queries), len(big_table.captured_queries),
            'create_booking runs more queries as the table grows (N+1?)',
        )


class ThrottleTests(ThrottleResetMixin, TestCase):
    """The rate limit exists to stop a runaway script, never a crowd. Coworkers share
    office wifi and carrier NAT, so dozens of real buyers arrive from one address."""

    def setUp(self):
        super().setUp()
        configure_event(capacity=10_000)
        self.client = APIClient()

    def _post_from_one_ip(self, count):
        return [
            self.client.post(BOOKINGS_URL, booking_body(i), format='json')
            for i in range(count)
        ]

    def test_a_whole_office_behind_one_ip_gets_through(self):
        responses = self._post_from_one_ip(THROTTLE_LIMIT)
        self.assertEqual({r.status_code for r in responses}, {201})

    def test_a_runaway_client_is_cut_off_past_the_limit(self):
        self._post_from_one_ip(THROTTLE_LIMIT)
        response = self.client.post(
            BOOKINGS_URL, booking_body(THROTTLE_LIMIT), format='json',
        )
        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response.headers)
        self.assertEqual(Booking.objects.count(), THROTTLE_LIMIT)

    def test_confirm_has_its_own_budget(self):
        # Confirm is the one request a buyer who has already transferred must be able to
        # make, so an office that has exhausted the booking budget can still confirm.
        responses = self._post_from_one_ip(THROTTLE_LIMIT)
        reference = responses[0].data['reference']
        self.assertEqual(
            self.client.post(BOOKINGS_URL, booking_body(THROTTLE_LIMIT), format='json').status_code,
            429,
        )
        self.assertEqual(self.client.post(confirm_url(reference)).status_code, 200)

    def test_song_saves_do_not_spend_the_booking_budget(self):
        booking = make_booking(status=Booking.Status.SELF_CONFIRMED)
        songs_url = f'/api/bookings/{booking.reference}/songs/'
        for _ in range(rate_limit('song_requests')):
            response = self.client.post(songs_url, {'songs': ['Song']}, format='json')
            self.assertEqual(response.status_code, 200)
        # The songs bucket is empty; booking from the same address is untouched, and the
        # next save is the one that gets told to wait.
        self.assertEqual(
            self.client.post(BOOKINGS_URL, booking_body(1), format='json').status_code, 201,
        )
        self.assertEqual(
            self.client.post(songs_url, {'songs': ['Song']}, format='json').status_code, 429,
        )


class HostileInputTests(ThrottleResetMixin, TestCase):
    """Anything a browser extension, a bored coworker or a bot might send. Every case
    must be a clean 4xx with a JSON body, never a 500, and must create nothing."""

    def setUp(self):
        super().setUp()
        configure_event()
        self.client = APIClient()

    def assert_rejected(self, response, expected_status=400):
        self.assertEqual(
            response.status_code, expected_status, response.content[:300],
        )
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(Guest.objects.count(), 0)

    def post_raw(self, body, content_type='application/json'):
        return self.client.generic('POST', BOOKINGS_URL, body, content_type=content_type)

    def test_garbage_bytes_are_a_400(self):
        self.assert_rejected(self.post_raw(b'\xff\xfe{not json'))

    def test_wrong_json_shapes_are_a_400(self):
        for body in (b'[1, 2, 3]', b'"just a string"', b'null', b'42', b'{}'):
            with self.subTest(body=body):
                self.assert_rejected(self.post_raw(body))

    def test_wrong_content_type_is_rejected(self):
        self.assert_rejected(
            self.post_raw(b'<xml/>', content_type='text/xml'), expected_status=415,
        )

    def test_malformed_guest_lists_are_a_400(self):
        base = {'buyer_name': 'Ana', 'buyer_whatsapp': '+5491100000001'}
        for guests in (
            'Ana, Beto',
            {'full_name': 'Ana'},
            [1, 2],
            [None],
            [{}],
            [{'name': 'wrong key'}],
            [{'full_name': None}],
            [{'full_name': ['a', 'list']}],
            [],
        ):
            with self.subTest(guests=guests):
                response = self.client.post(
                    BOOKINGS_URL, {**base, 'guests': guests}, format='json',
                )
                self.assert_rejected(response)

    def test_oversized_guest_list_is_a_400_not_a_stall(self):
        response = self.client.post(
            BOOKINGS_URL, booking_body(1, quantity=5_000), format='json',
        )
        self.assert_rejected(response)

    def test_blank_and_oversized_names_are_a_400(self):
        for name in ('', '   ', '\t\n', 'x' * 201, 'x' * 100_000):
            with self.subTest(name=repr(name)[:20]):
                body = {**booking_body(1), 'guests': [{'full_name': name}]}
                self.assert_rejected(self.client.post(BOOKINGS_URL, body, format='json'))
                body = {**booking_body(1), 'buyer_name': name}
                self.assert_rejected(self.client.post(BOOKINGS_URL, body, format='json'))

    def test_oversized_whatsapp_is_a_400(self):
        body = {**booking_body(1), 'buyer_whatsapp': '+' + '5' * 40}
        self.assert_rejected(self.client.post(BOOKINGS_URL, body, format='json'))

    def test_accents_and_emoji_survive_intact(self):
        name = 'Ñuñez Álvarez 🎉'
        body = {**booking_body(1), 'guests': [{'full_name': f'  {name}  '}]}
        response = self.client.post(BOOKINGS_URL, body, format='json')
        self.assertEqual(response.status_code, 201)
        # Stored stripped, otherwise byte-for-byte.
        self.assertEqual(Guest.objects.get().full_name, name)

    def test_unknown_fields_and_client_quantity_are_ignored(self):
        body = {
            **booking_body(1, quantity=2),
            'quantity': 8,
            'total_amount': 1,
            'status': 'verified',
            'reference': 'chosen-by-me',
            'is_admin': True,
        }
        response = self.client.post(BOOKINGS_URL, body, format='json')
        self.assertEqual(response.status_code, 201)
        booking = Booking.objects.get()
        self.assertEqual(booking.quantity, 2)
        self.assertEqual(booking.total_amount, PRICE * 2)
        self.assertEqual(booking.status, Booking.Status.PENDING)
        self.assertNotEqual(booking.reference, 'chosen-by-me')

    def test_hostile_references_are_a_404(self):
        for reference in (
            'x' * 500,
            "' OR 1=1 --",
            'ñandú-🎉',
            '..%2F..%2Fetc%2Fpasswd',
            '<script>alert(1)</script>',
            ' ',
        ):
            with self.subTest(reference=reference[:30]):
                response = self.client.post(confirm_url(reference))
                self.assertEqual(response.status_code, 404, response.content[:200])
                # A reference that decodes to extra path segments (the %2F and </script>
                # cases) never matches a route, so Django answers with its own 404 rather
                # than the view's JSON one. Either is fine; a 500 is not.
                if response['Content-Type'] == 'application/json':
                    self.assertEqual(response.data['error'], 'not_found')

    def test_wrong_methods_are_a_405(self):
        booking = make_booking()
        self.assertEqual(self.client.get(BOOKINGS_URL).status_code, 405)
        self.assertEqual(self.client.delete(BOOKINGS_URL).status_code, 405)
        self.assertEqual(self.client.get(confirm_url(booking.reference)).status_code, 405)
        self.assertEqual(self.client.put(confirm_url(booking.reference)).status_code, 405)
        self.assertEqual(self.client.delete(confirm_url(booking.reference)).status_code, 405)
        self.assertEqual(self.client.post(AVAILABILITY_URL).status_code, 405)
        self.assertEqual(booking.status, Booking.Status.PENDING)


class StateMachineAbuseTests(ThrottleResetMixin, TestCase):
    """Repeated taps, stale tabs, and the organiser changing settings mid-sale."""

    def setUp(self):
        super().setUp()
        self.event = configure_event()
        self.client = APIClient()

    def test_confirm_hammered_fifty_times_records_one_confirmation(self):
        booking = make_booking()
        first = self.client.post(confirm_url(booking.reference))
        self.assertEqual(first.status_code, 200)
        booking.refresh_from_db()
        confirmed_at = booking.confirmed_at

        for _ in range(49):
            self.assertEqual(self.client.post(confirm_url(booking.reference)).status_code, 200)

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.SELF_CONFIRMED)
        self.assertEqual(booking.confirmed_at, confirmed_at)
        self.assertEqual(seats_taken(), 1)

    def test_retry_after_the_original_aged_out_makes_a_fresh_booking(self):
        stale = make_booking(minutes_old=46)  # +5491100000001, quantity 1
        body = {
            'buyer_name': 'Ana',
            'buyer_whatsapp': stale.buyer_whatsapp,
            'guests': guest_payload(1),
        }
        response = self.client.post(BOOKINGS_URL, body, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertNotEqual(response.data['reference'], stale.reference)
        self.assertEqual(Booking.objects.count(), 2)
        self.assertEqual(seats_taken(), 1)

    def test_admin_lowering_capacity_below_taken_closes_sales_cleanly(self):
        make_booking(quantity=8, status=Booking.Status.SELF_CONFIRMED)
        pending = make_booking(quantity=1)
        configure_event(capacity=5)

        availability = self.client.get(AVAILABILITY_URL).data
        self.assertEqual(availability['taken'], 9)
        self.assertEqual(availability['remaining'], 0)
        self.assertTrue(availability['sold_out'])

        response = self.client.post(BOOKINGS_URL, booking_body(1), format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['error'], 'sold_out')
        self.assertEqual(response.data['remaining'], 0)

        # Already holding a seat inside the TTL: the admin's oversell is the admin's
        # call, the buyer who followed the rules is not thrown out.
        self.assertEqual(self.client.post(confirm_url(pending.reference)).status_code, 200)

    def test_changing_the_ttl_applies_to_existing_bookings_instantly(self):
        make_booking(quantity=2, minutes_old=30)
        self.assertEqual(seats_taken(configure_event(pending_ttl_minutes=45)), 2)
        self.assertEqual(seats_taken(configure_event(pending_ttl_minutes=20)), 0)
        self.assertEqual(seats_taken(configure_event(pending_ttl_minutes=45)), 2)
        # No sweep ran, so the row itself was never touched.
        self.assertEqual(Booking.objects.get().status, Booking.Status.PENDING)

    def test_pending_booking_from_before_the_close_can_still_confirm(self):
        pending = make_booking()
        configure_event(sales_close_at=timezone.now() - timedelta(hours=1))

        self.assertEqual(
            self.client.post(BOOKINGS_URL, booking_body(1), format='json').data['error'],
            'sales_closed',
        )
        self.assertEqual(self.client.post(confirm_url(pending.reference)).status_code, 200)

    def test_expired_party_cannot_squeeze_into_partial_room_but_resurrects_later(self):
        make_booking(name='Confirmed', quantity=8, status=Booking.Status.SELF_CONFIRMED)
        stale = make_booking(name='Late', quantity=3, minutes_old=50)
        self.assertEqual(seats_taken(), 8)  # two free, the party needs three

        response = self.client.post(confirm_url(stale.reference))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['error'], 'booking_expired')
        self.assertEqual(response.data['organiser_whatsapp'], self.event.organiser_whatsapp)
        stale.refresh_from_db()
        self.assertEqual(stale.status, Booking.Status.EXPIRED)
        self.assertEqual(seats_taken(), 8)

        # The organiser finds one more seat. The same link now works.
        configure_event(capacity=CAPACITY + 1)
        response = self.client.post(confirm_url(stale.reference))
        self.assertEqual(response.status_code, 200)
        stale.refresh_from_db()
        self.assertEqual(stale.status, Booking.Status.SELF_CONFIRMED)
        self.assertEqual(seats_taken(), 11)


class TransactionIntegrityTests(ThrottleResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        configure_event()

    def test_failure_after_the_booking_row_rolls_everything_back(self):
        with mock.patch.object(
            Guest.objects, 'bulk_create', side_effect=DatabaseError('connection reset'),
        ):
            with self.assertRaises(DatabaseError):
                services.create_booking(
                    buyer_name='Ana', buyer_whatsapp='+5491100000001',
                    guests=guest_payload(2),
                )
        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(seats_taken(), 0)

        # The lock was released and the connection is healthy: the next buyer is fine.
        booking, created = services.create_booking(
            buyer_name='Beto', buyer_whatsapp='+5491100000002', guests=guest_payload(2),
        )
        self.assertTrue(created)
        self.assertEqual(booking.guests.count(), 2)

    def test_health_fails_loudly_when_the_database_is_down(self):
        client = APIClient(raise_request_exception=False)
        with mock.patch('tickets.views.connection') as db:
            db.cursor.side_effect = OperationalError('could not connect')
            response = client.get(HEALTH_URL)
        # An uptime check that gets {"ok": true} from a dead database is worse than none.
        self.assertEqual(response.status_code, 500)


REQUIRES_ROW_LOCKS = skipUnless(
    connection.features.has_select_for_update,
    'select_for_update is a silent no-op on this backend, so this test would pass '
    'without proving anything. Run it against Postgres.',
)


def _close_connection_after(fn):
    """Django opens a connection per thread; leaking them hangs the suite."""

    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        finally:
            connection.close()

    return wrapper


@REQUIRES_ROW_LOCKS
class ContentionTests(TransactionTestCase):
    """Requests that land inside the same few milliseconds, driven straight at the
    service layer with real threads and real connections. Complements CapacityRaceTest
    in tests.py, which covers plain single- and multi-seat creates."""

    def setUp(self):
        self.event = configure_event()

    @staticmethod
    def _run(fn, args_list):
        with ThreadPoolExecutor(max_workers=len(args_list)) as pool:
            return list(pool.map(lambda a: _close_connection_after(fn)(*a), args_list))

    @staticmethod
    def _create(index, quantity, whatsapp=None):
        try:
            booking, created = services.create_booking(
                buyer_name=f'Buyer {index}',
                buyer_whatsapp=whatsapp or f'+549117{index:06d}',
                guests=guest_payload(quantity),
            )
            return {'ok': True, 'created': created, 'reference': booking.reference,
                    'quantity': quantity}
        except services.SoldOut as exc:
            return {'ok': False, 'remaining': exc.remaining, 'quantity': quantity}

    @staticmethod
    def _confirm(reference):
        try:
            booking = services.confirm_booking(reference)
            return {'ok': True, 'status': booking.status, 'confirmed_at': booking.confirmed_at}
        except services.BookingExpired:
            return {'ok': False}

    def test_simultaneous_double_taps_collapse_into_one_booking(self):
        # Ten taps on a frozen button, all arriving together. The retry check runs under
        # the lock, so the second and later ones must see the first.
        results = self._run(self._create, [(i, 2, '+5491100009999') for i in range(10)])
        self.assertTrue(all(r['ok'] for r in results))
        self.assertEqual(len({r['reference'] for r in results}), 1)
        self.assertEqual(sum(r['created'] for r in results), 1)
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(Guest.objects.count(), 2)

    def test_mixed_party_sizes_never_oversell(self):
        capacity = 20
        configure_event(capacity=capacity)
        results = self._run(self._create, [(i, (i % 4) + 1) for i in range(24)])

        winners = [r for r in results if r['ok']]
        losers = [r for r in results if not r['ok']]
        self.assertLessEqual(Guest.objects.count(), capacity)
        self.assertEqual(Guest.objects.count(), sum(r['quantity'] for r in winners))
        self.assertTrue(losers, 'not enough demand to fill the event')
        for r in losers:
            # Every refusal was honest: the party really did not fit at that moment.
            self.assertLess(r['remaining'], r['quantity'])
            self.assertGreaterEqual(r['remaining'], 0)

    def test_resurrections_race_new_buyers_for_the_same_seats(self):
        # Ten stale bookings whose seats have lapsed, ten buyers who want those seats,
        # ten stale buyers tapping confirm -- all at once. Exactly ten seats exist.
        stale = [
            Booking.objects.create(
                buyer_name=f'Stale {i}', buyer_whatsapp=f'+549118{i:06d}',
                quantity=1, total_amount=PRICE,
            )
            for i in range(CAPACITY)
        ]
        Guest.objects.bulk_create(Guest(booking=b, full_name=b.buyer_name) for b in stale)
        Booking.objects.filter(pk__in=[b.pk for b in stale]).update(
            created_at=timezone.now() - timedelta(minutes=50),
        )
        self.assertEqual(seats_taken(), 0)

        def contender(kind, arg):
            return self._confirm(arg) if kind == 'confirm' else self._create(arg, 1)

        work = [('confirm', b.reference) for b in stale] + [('create', i) for i in range(CAPACITY)]
        results = self._run(contender, work)

        self.assertEqual(sum(r['ok'] for r in results), CAPACITY)
        self.assertEqual(seats_taken(), CAPACITY)
        self.assertLessEqual(seats_taken(), self.event.capacity)
        # Every stale booking that lost is marked, so the admin does not see it as live.
        for b in stale:
            b.refresh_from_db()
            self.assertIn(b.status, (Booking.Status.SELF_CONFIRMED, Booking.Status.EXPIRED))

    def test_concurrent_confirms_of_one_booking_are_idempotent(self):
        booking = make_booking()
        results = self._run(self._confirm, [(booking.reference,)] * 10)
        self.assertTrue(all(r['ok'] for r in results))
        self.assertEqual({r['status'] for r in results}, {Booking.Status.SELF_CONFIRMED})
        self.assertEqual(len({r['confirmed_at'] for r in results}), 1)
        self.assertEqual(seats_taken(), 1)


@REQUIRES_ROW_LOCKS
class HttpContentionTests(LiveServerTestCase):
    """The only test that puts serializer, view, throttle and service under true
    parallelism together, over real HTTP against Django's threaded test server."""

    def setUp(self):
        cache.clear()
        configure_event()

    def tearDown(self):
        cache.clear()

    def _post(self, path, body=None):
        request = urllib.request.Request(
            self.live_server_url + path,
            data=json.dumps(body).encode() if body is not None else b'',
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_parallel_buyers_over_http(self):
        attempts = 16
        with ThreadPoolExecutor(max_workers=attempts) as pool:
            creates = list(pool.map(
                lambda i: self._post(BOOKINGS_URL, booking_body(i)), range(attempts),
            ))

        statuses = [status for status, _ in creates]
        self.assertTrue(all(s in (201, 409) for s in statuses), statuses)
        self.assertEqual(statuses.count(201), CAPACITY)
        self.assertEqual(Guest.objects.count(), CAPACITY)
        for status, body in creates:
            if status == 409:
                self.assertEqual(body['error'], 'sold_out')

        references = [body['reference'] for status, body in creates if status == 201]
        with ThreadPoolExecutor(max_workers=len(references)) as pool:
            confirms = list(pool.map(lambda r: self._post(confirm_url(r)), references))

        self.assertEqual({status for status, _ in confirms}, {200})
        self.assertEqual({body['status'] for _, body in confirms}, {'self_confirmed'})
        self.assertEqual(
            Booking.objects.filter(status=Booking.Status.SELF_CONFIRMED).count(), CAPACITY,
        )
        self.assertEqual(seats_taken(), CAPACITY)
