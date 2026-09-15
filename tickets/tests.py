from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import skipUnless

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import exports, services
from .models import Booking, EventSettings, Guest, SongRequest, seats_taken
from .money import format_ars, format_minor_units

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

    def test_foreign_amounts_use_dot_decimal(self):
        # Typed into Revolut, not an Argentine bank, so the separators flip.
        self.assertEqual(format_minor_units(500, 'EUR'), 'EUR 5.00')
        self.assertEqual(format_minor_units(150_000, 'gbp'), 'GBP 1,500.00')


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
                      'account_holder_name', 'organiser_whatsapp', 'status', 'revolut'):
            self.assertIn(field, body)
        self.assertEqual(body['amount_display'], '10.000')

    def test_revolut_block_is_absent_until_configured(self):
        self.assertIsNone(self.post().json()['revolut'])

    def test_revolut_block_is_absent_while_half_configured(self):
        # A tag with no price would otherwise publish "EUR 0.00" to every buyer.
        configure_event(revolut_tag='dhruvk')
        self.assertIsNone(self.post().json()['revolut'])
        configure_event(revolut_tag='dhruvk', revolut_currency='EUR', revolut_price_cents=0)
        self.assertIsNone(self.post(guests=guest_payload(3)).json()['revolut'])

    def test_half_configured_revolut_is_rejected_in_the_admin_form(self):
        row = configure_event()
        row.revolut_tag = 'dhruvk'
        with self.assertRaises(ValidationError):
            row.full_clean()
        row.revolut_currency, row.revolut_price_cents = 'EUR', 500
        row.full_clean()

    def test_revolut_block_multiplies_the_flat_price_by_party_size(self):
        # Pasted straight from Revolut, @ and trailing space included, lower-case currency.
        configure_event(revolut_tag=' @dhruvk ', revolut_currency='eur', revolut_price_cents=500)
        body = self.post(guests=guest_payload(3)).json()
        self.assertEqual(body['revolut'], {
            'tag': 'dhruvk',
            'link': 'https://revolut.me/dhruvk',
            'currency': 'EUR',
            'amount_cents': 1500,
            'amount_display': 'EUR 15.00',
        })
        # The peso amount is untouched: Revolut is a second flat figure, not a discount.
        self.assertEqual(body['total_amount'], PRICE * 3)

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


class SongRequestTests(TestCase):
    def setUp(self):
        configure_event()
        self.client = APIClient()

    def save(self, booking, songs):
        return self.client.post(
            f'/api/bookings/{booking.reference}/songs/', {'songs': songs}, format='json',
        )

    def test_saves_up_to_three_for_a_confirmed_booking(self):
        booking = make_booking(status=Booking.Status.SELF_CONFIRMED)
        songs = ['Drake - One Dance', 'Bad Bunny - Tití Me Preguntó', 'Rosalía - Despechá']
        response = self.save(booking, songs)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([s['text'] for s in response.json()['songs']], songs)
        self.assertEqual(
            list(booking.song_requests.values_list('position', 'text')),
            [(1, songs[0]), (2, songs[1]), (3, songs[2])],
        )

    def test_blank_slots_are_dropped_and_saving_again_replaces(self):
        booking = make_booking(status=Booking.Status.VERIFIED)
        self.save(booking, ['  First  ', '', 'Third'])
        self.assertEqual(
            list(booking.song_requests.values_list('text', flat=True)), ['First', 'Third'],
        )
        self.save(booking, ['Only one'])
        self.assertEqual(
            list(booking.song_requests.values_list('text', flat=True)), ['Only one'],
        )
        self.assertEqual(self.save(booking, []).status_code, 200)
        self.assertEqual(booking.song_requests.count(), 0)

    def test_more_than_three_is_rejected(self):
        booking = make_booking(status=Booking.Status.SELF_CONFIRMED)
        self.assertEqual(self.save(booking, ['a', 'b', 'c', 'd']).status_code, 400)
        self.assertEqual(booking.song_requests.count(), 0)

    def test_pending_booking_cannot_add_songs(self):
        # The form only appears after confirmation; a form-filler who never paid should
        # not steer the playlist.
        booking = make_booking()
        response = self.save(booking, ['Song'])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'booking_not_confirmed')
        self.assertTrue(response.json()['organiser_whatsapp'])

    def test_cancelled_booking_cannot_add_songs(self):
        booking = make_booking(status=Booking.Status.CANCELLED)
        response = self.save(booking, ['Song'])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'booking_cancelled')

    def test_unknown_reference_is_404(self):
        response = self.client.post('/api/bookings/nope/songs/', {'songs': ['x']}, format='json')
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


# The exports index renders an admin template. WhiteNoise's manifest storage refuses to
# serve a file that collectstatic has not hashed, so without this a fresh clone fails
# here while a checkout where collectstatic once ran passes.
@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class ExportTests(TestCase):
    """The venue list is the one artefact that leaves this system and reaches strangers,
    so encoding, sorting and the status filter all get asserted."""

    def setUp(self):
        configure_event(capacity=50)
        self.client = Client()
        User.objects.create_superuser('org', 'o@example.com', 'pw')
        self.client.login(username='org', password='pw')

    @staticmethod
    def _booking_with(names, status=Booking.Status.SELF_CONFIRMED, minutes_old=0):
        booking = Booking.objects.create(
            buyer_name=names[0], buyer_whatsapp='+5491100000002',
            sender_account_name='M. Perez',
            quantity=len(names), total_amount=PRICE * len(names), status=status,
            confirmed_at=timezone.now() if status != Booking.Status.PENDING else None,
        )
        Guest.objects.bulk_create(Guest(booking=booking, full_name=n) for n in names)
        if minutes_old:
            Booking.objects.filter(pk=booking.pk).update(
                created_at=timezone.now() - timedelta(minutes=minutes_old)
            )
        return booking

    def test_accented_names_sort_in_place_not_after_z(self):
        self._booking_with(['Zapata Ana', 'Álvarez Luis', 'Ñuñez Mia', 'Ibarra Bo'])
        self.assertEqual(
            exports.venue_names(),
            ['Álvarez Luis', 'Ibarra Bo', 'Ñuñez Mia', 'Zapata Ana'],
        )

    def test_venue_list_excludes_pending(self):
        self._booking_with(['Paid Person'])
        self._booking_with(['Never Paid'], status=Booking.Status.PENDING)
        self.assertEqual(exports.venue_names(), ['Paid Person'])

    def test_venue_list_excludes_cancelled_and_expired(self):
        self._booking_with(['Gone One'], status=Booking.Status.CANCELLED)
        self._booking_with(['Gone Two'], status=Booking.Status.EXPIRED)
        self._booking_with(['Still Here'], status=Booking.Status.VERIFIED)
        self.assertEqual(exports.venue_names(), ['Still Here'])

    def test_pending_count_is_surfaced(self):
        self._booking_with(['Paid'])
        self._booking_with(['Waiting One', 'Waiting Two'], status=Booking.Status.PENDING)
        # A pending booking past the TTL no longer holds a seat, so it is not "waiting".
        self._booking_with(['Long Gone'], status=Booking.Status.PENDING, minutes_old=90)
        self.assertEqual(exports.pending_guest_count(), 2)

    def test_venue_text_is_one_name_per_line(self):
        self._booking_with(['Ana Perez', 'Beto Ñuñez'])
        response = self.client.get('/admin/tickets/booking/exports/venue.txt')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content.decode('utf-8').split('\n'),
            ['Ana Perez', 'Beto Ñuñez'],
        )
        self.assertIn('lista-venue-', response['Content-Disposition'])

    def test_venue_csv_carries_the_bom_excel_needs(self):
        self._booking_with(['José Ñuñez'])
        response = self.client.get('/admin/tickets/booking/exports/venue.csv')
        # Without the BOM Excel reads the file as latin-1 and mangles every accent.
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        self.assertIn('José Ñuñez', response.content.decode('utf-8-sig'))

    def test_venue_csv_carries_names_only(self):
        self._booking_with(['Ana Perez'])
        body = self.client.get(
            '/admin/tickets/booking/exports/venue.csv'
        ).content.decode('utf-8-sig')
        # The venue must never receive contact details or payment status.
        self.assertNotIn('+549', body)
        self.assertNotIn('M. Perez', body)
        self.assertIn('Full Name', body)

    def test_organiser_csv_carries_contact_details_and_is_flagged_internal(self):
        self._booking_with(['Ana Perez'])
        response = self.client.get('/admin/tickets/booking/exports/organiser.csv')
        body = response.content.decode('utf-8-sig')
        self.assertIn('+549', body)
        self.assertIn('M. Perez', body)
        # Filename prefix makes it hard to attach to the venue's thread by mistake.
        self.assertIn('INTERNO-', response['Content-Disposition'])

    def test_venue_exports_stamp_list_exported_at(self):
        booking = self._booking_with(['Ana Perez'])
        self.assertIsNone(booking.list_exported_at)
        self.client.get('/admin/tickets/booking/exports/venue.txt')
        booking.refresh_from_db()
        self.assertIsNotNone(booking.list_exported_at)

    def test_organiser_export_does_not_stamp(self):
        booking = self._booking_with(['Ana Perez'])
        self.client.get('/admin/tickets/booking/exports/organiser.csv')
        booking.refresh_from_db()
        self.assertIsNone(booking.list_exported_at)

    def test_index_reports_both_counts(self):
        self._booking_with(['Paid One', 'Paid Two'])
        self._booking_with(['Waiting'], status=Booking.Status.PENDING)
        response = self.client.get('/admin/tickets/booking/exports/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['confirmed_count'], 2)
        self.assertEqual(response.context['pending_count'], 1)

    def test_playlist_lists_confirmed_requests_once_each(self):
        paid = self._booking_with(['Paid Person'])
        other = self._booking_with(['Other Paid'], status=Booking.Status.VERIFIED)
        unpaid = self._booking_with(['Never Paid'], status=Booking.Status.PENDING)
        services.set_song_requests(paid.reference, ['Drake - One Dance', 'Rosalía - Despechá'])
        services.set_song_requests(other.reference, ['drake - one dance', 'Bad Bunny - Tití'])
        SongRequest.objects.create(booking=unpaid, position=1, text='Should Not Appear')
        self.assertEqual(
            exports.playlist_lines(),
            ['Drake - One Dance', 'Rosalía - Despechá', 'Bad Bunny - Tití'],
        )

    def test_playlist_keeps_distinct_non_latin_requests(self):
        # An ASCII-only fold would turn every Korean or Cyrillic title into the same
        # empty key and drop all but the first.
        paid = self._booking_with(['Paid Person'])
        services.set_song_requests(
            paid.reference, ['방탄소년단 - 봄날', '아이유 - 밤편지', 'Земфира - Искала'],
        )
        self.assertEqual(len(exports.playlist_lines()), 3)
        self.assertEqual(exports.dedupe_key('Rosalía - DESPECHÁ'), 'rosalia - despecha')

    def test_playlist_text_download(self):
        paid = self._booking_with(['Paid Person'])
        services.set_song_requests(paid.reference, ['Song A', 'Song B'])
        response = self.client.get('/admin/tickets/booking/exports/playlist.txt')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode('utf-8').split('\n'), ['Song A', 'Song B'])
        self.assertIn('playlist-', response['Content-Disposition'])

    def test_exports_are_staff_only(self):
        anon = Client()
        for path in ('', 'venue.txt', 'venue.csv', 'organiser.csv', 'playlist.txt'):
            response = anon.get(f'/admin/tickets/booking/exports/{path}')
            # The organiser export carries every coworker's phone number.
            self.assertEqual(response.status_code, 302, path)
            self.assertIn('/admin/login/', response['Location'])
