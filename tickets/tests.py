from collections import Counter
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
from .serializers import pay_screen_payload
from .models import (
    MAX_TICKETS_PER_BOOKING, Booking, EventSettings, Guest, PriceTier, SongRequest,
    seats_taken,
)
from . import admin, exports, services
from .models import Booking, EventSettings, Guest, SongRequest, seats_taken
from .money import format_ars, format_minor_units

CAPACITY = 10
PRICE = 500_000

# A test ladder scaled to CAPACITY=10, shaped like the real one (three bands, the last
# one shortest): seats 1-4 at 5.000, seats 5-8 at 7.000, seats 9-10 at 9.000. A party of
# five can therefore straddle all three bands, which the real 50/50/30 split also allows.
TIER_TWO_AT = 4
TIER_TWO_PRICE = 700_000
TIER_THREE_AT = 8
TIER_THREE_PRICE = 900_000


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
    # The 50/100 thresholds seeded by the data migration survive inside a TestCase, so
    # without this every test would silently inherit the production ladder. The default
    # event here is flat-priced; tier tests opt in with configure_tiers().
    PriceTier.objects.all().delete()
    return settings_row


def configure_tiers(*pairs):
    """Give the event a price ladder. `configure_tiers((4, 700_000), (8, 900_000))`.

    Called after configure_event(), which recreates pk=1 and clears any existing tiers.
    """
    return [
        PriceTier.objects.create(
            event_id=1, starts_after_seats=starts_after, price_cents=price,
        )
        for starts_after, price in pairs
    ]


def default_tiers():
    return configure_tiers(
        (TIER_TWO_AT, TIER_TWO_PRICE), (TIER_THREE_AT, TIER_THREE_PRICE)
    )


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


# Same reason as ExportTests below: admin pages use {% static %}, and the manifest
# storage refuses files collectstatic has not hashed.
@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
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

    def test_song_requests_have_their_own_list(self):
        # The organiser reads the playlist from the sidebar, not booking by booking.
        booking = make_booking(status=Booking.Status.SELF_CONFIRMED)
        SongRequest.objects.create(booking=booking, position=1, text='Drake - One Dance')
        User.objects.create_superuser('org', 'o@example.com', 'pw')
        self.client.login(username='org', password='pw')
        response = self.client.get('/admin/tickets/songrequest/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Drake - One Dance')
        self.assertContains(response, booking.reference)


# Renders admin changelists, so the same manifest-storage escape as AdminAccessTests.
@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class SeatLightTests(TestCase):
    """The Seat column's traffic light.

    Green means the seat is held, orange means we are waiting for the buyer to say they
    transferred, red means the row holds nothing. The risk this guards is the light
    disagreeing with seats_taken(): a row shown green that the capacity count treats as
    free oversells the room, and one shown red that it counts sells a seat twice.
    """

    def setUp(self):
        self.settings_row = configure_event()
        self.cutoff = self.settings_row.pending_cutoff()
        User.objects.create_superuser('org', 'o@example.com', 'pw')
        self.client.login(username='org', password='pw')

    def test_every_status_has_a_light(self):
        # A sixth status added without a colour would otherwise KeyError on the
        # changelist -- the one page the organiser cannot run the event without.
        for status in Booking.Status:
            booking = make_booking(status=status)
            self.assertIn(admin.seat_rank(booking, self.cutoff), admin.SEAT_LIGHTS)

    def test_the_light_agrees_with_the_capacity_count(self):
        """Orange and green are exactly the rows seats_taken() charges for."""
        holding = {
            admin.SEAT_RANK_VERIFIED,
            admin.SEAT_RANK_SELF_CONFIRMED,
            admin.SEAT_RANK_AWAITING,
        }
        for status in Booking.Status:
            for minutes_old in (0, 999):
                Booking.objects.all().delete()
                booking = make_booking(status=status, minutes_old=minutes_old)
                lit = admin.seat_rank(booking, EventSettings.load().pending_cutoff())
                self.assertEqual(
                    lit in holding,
                    seats_taken() == 1,
                    f'{status} aged {minutes_old}m lights as {admin.SEAT_LIGHTS[lit][1]} '
                    f'but seats_taken() says {seats_taken()}',
                )

    def test_lapsed_pending_goes_red_not_orange(self):
        # Its seat is back on sale. Orange would tell the organiser to keep waiting for
        # money that no longer buys anything.
        fresh = make_booking(name='Fresh', status=Booking.Status.PENDING)
        lapsed = make_booking(name='Lapsed', status=Booking.Status.PENDING,
                              minutes_old=self.settings_row.pending_ttl_minutes + 1)
        self.assertEqual(admin.seat_rank(fresh, self.cutoff), admin.SEAT_RANK_AWAITING)
        self.assertEqual(admin.seat_rank(lapsed, self.cutoff), admin.SEAT_RANK_LAPSED)

    def test_sql_ranking_matches_the_python_one(self):
        """seat_rank_case() and seat_rank() are two spellings of one rule."""
        for status in Booking.Status:
            make_booking(name=f'B {status}', status=status)
        make_booking(name='Lapsed', status=Booking.Status.PENDING,
                     minutes_old=self.settings_row.pending_ttl_minutes + 1)
        ranked = Booking.objects.annotate(_seat_rank=admin.seat_rank_case())
        for booking in ranked:
            self.assertEqual(booking._seat_rank,
                             admin.seat_rank(booking, self.cutoff),
                             f'{booking.buyer_name} ({booking.status})')

    def test_relation_prefix_ranks_through_the_booking(self):
        # What the Guest and SongRequest lists rely on.
        make_booking(name='Green', status=Booking.Status.VERIFIED)
        ranked = Guest.objects.annotate(_seat_rank=admin.seat_rank_case('booking__'))
        self.assertEqual(ranked.first()._seat_rank, admin.SEAT_RANK_VERIFIED)

    def test_labels_render_on_every_changelist(self):
        booking = make_booking(status=Booking.Status.VERIFIED)
        SongRequest.objects.create(booking=booking, position=1, text='Soda Stereo')
        make_booking(name='Waiting', status=Booking.Status.PENDING)
        make_booking(name='Gone', status=Booking.Status.CANCELLED)
        for url in ('/admin/tickets/booking/', '/admin/tickets/guest/',
                    '/admin/tickets/songrequest/'):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertContains(response, 'Verified', msg_prefix=url)
        response = self.client.get('/admin/tickets/booking/')
        self.assertContains(response, 'Awaiting payment')
        self.assertContains(response, 'No seat')

    def test_the_label_is_there_without_the_colour(self):
        # Colour alone must not carry the meaning: a red/green reader sees two identical
        # dots, and the dot is decorative to a screen reader.
        make_booking(status=Booking.Status.PENDING)
        response = self.client.get('/admin/tickets/booking/')
        self.assertContains(response, 'aria-hidden="true"')
        self.assertContains(response, 'Awaiting payment')

    def test_the_seat_column_sorts_by_colour_band(self):
        make_booking(name='Red', status=Booking.Status.CANCELLED)
        make_booking(name='Green', status=Booking.Status.VERIFIED)
        make_booking(name='Orange', status=Booking.Status.PENDING)
        ranked = (Booking.objects
                  .annotate(_seat_rank=admin.seat_rank_case())
                  .order_by('-_seat_rank'))
        self.assertEqual([b.buyer_name for b in ranked], ['Green', 'Orange', 'Red'])


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



class PriceLadderTests(TestCase):
    """The arithmetic, with no HTTP and no threads.

    Split from the booking tests on purpose: if a boundary is off by one, the failure
    should name the band, not a status code.
    """

    def setUp(self):
        self.event = configure_event()
        default_tiers()

    def quote(self, position, quantity=1):
        _, total, _ = self.event.price_seats(position, quantity)
        return total

    def test_ladder_reads_as_inclusive_seat_ranges(self):
        # Thresholds are stored as "after N seats"; humans say "the first four".
        self.assertEqual(
            [(b['from_seat'], b['to_seat'], b['price_cents'])
             for b in self.event.price_ladder()],
            [(1, 4, PRICE), (5, 8, TIER_TWO_PRICE), (9, 10, TIER_THREE_PRICE)],
        )

    def test_first_tier_covers_its_whole_band_including_the_last_seat(self):
        # Seat 1 and seat 4 both cost the base price. The off-by-one that matters.
        self.assertEqual(self.quote(position=0), PRICE)
        self.assertEqual(self.quote(position=TIER_TWO_AT - 1), PRICE)

    def test_second_tier_starts_at_the_seat_after_the_threshold(self):
        self.assertEqual(self.quote(position=TIER_TWO_AT), TIER_TWO_PRICE)
        self.assertEqual(self.quote(position=TIER_THREE_AT - 1), TIER_TWO_PRICE)

    def test_third_tier_starts_at_the_seat_after_its_threshold(self):
        self.assertEqual(self.quote(position=TIER_THREE_AT), TIER_THREE_PRICE)
        self.assertEqual(self.quote(position=CAPACITY - 1), TIER_THREE_PRICE)

    def test_seats_past_capacity_keep_the_last_price_rather_than_raising(self):
        """Pricing must never be the thing that fails.

        A position past the end is reachable -- capacity lowered after sales opened, or
        a sold-out event still being quoted for the availability display. Capacity is
        what refuses a booking; the ladder just answers.
        """
        self.assertEqual(self.quote(position=CAPACITY + 5), TIER_THREE_PRICE)

    def test_a_party_straddling_one_boundary_is_split(self):
        # Two seats left in the first band, four wanted: 2 x 5.000 + 2 x 7.000.
        breakdown, total, _ = self.event.price_seats(TIER_TWO_AT - 2, 4)
        self.assertEqual(
            [(line['from_seat'], line['to_seat'], line['unit_price_cents'],
              line['quantity']) for line in breakdown],
            [(3, 4, PRICE, 2), (5, 6, TIER_TWO_PRICE, 2)],
        )
        self.assertEqual(total, 2 * PRICE + 2 * TIER_TWO_PRICE)

    def test_a_party_can_straddle_every_band_at_once(self):
        breakdown, total, _ = self.event.price_seats(TIER_TWO_AT - 1, 5)
        self.assertEqual([line['quantity'] for line in breakdown], [1, 4])
        self.assertEqual(total, PRICE + 4 * TIER_TWO_PRICE)

        breakdown, total, _ = self.event.price_seats(TIER_TWO_AT - 1, 6)
        self.assertEqual(
            [(line['unit_price_cents'], line['quantity']) for line in breakdown],
            [(PRICE, 1), (TIER_TWO_PRICE, 4), (TIER_THREE_PRICE, 1)],
        )
        self.assertEqual(total, PRICE + 4 * TIER_TWO_PRICE + TIER_THREE_PRICE)

    def test_no_tiers_is_the_old_flat_price(self):
        """The regression guard for every other test in this file.

        An event with no PriceTier rows must behave exactly as it did when the price was
        a single number, or this change quietly re-prices the flat-price path.
        """
        PriceTier.objects.all().delete()
        event = EventSettings.load()
        self.assertEqual(
            [(b['from_seat'], b['to_seat'], b['price_cents'])
             for b in event.price_ladder()],
            [(1, CAPACITY, PRICE)],
        )
        _, total, _ = event.price_seats(0, 3)
        self.assertEqual(total, PRICE * 3)

    def test_a_threshold_past_capacity_is_not_a_band(self):
        """A tier nobody can reach must not appear on the page as if they could.

        Capacity 6 against thresholds at 4 and 8 is the shape that matters: the first
        threshold is reachable and the second is not, so the ladder must keep one step
        and drop the other. An earlier version of this test used capacity 4, which put
        *both* thresholds past the end and so proved only the easy half -- and hid the
        fact that seeding tiers below the current capacity re-prices seats immediately.
        """
        configure_event(capacity=6)
        default_tiers()
        event = EventSettings.load()
        self.assertEqual(
            [(b['from_seat'], b['to_seat'], b['price_cents'])
             for b in event.price_ladder()],
            [(1, 4, PRICE), (5, 6, TIER_TWO_PRICE)],
        )

    def test_price_position_tracks_occupancy_until_the_ladder_has_climbed_higher(self):
        """Position is occupancy or the high-water mark, whichever is further up.

        With nothing abandoned the two are the same number, which is the ordinary case
        and the one worth pinning: the Nth ticket sold is the Nth ticket charged for.
        """
        make_booking(quantity=3, status=Booking.Status.SELF_CONFIRMED)
        event = EventSettings.load()
        self.assertEqual(event.next_seat_position(), seats_taken(event))
        self.assertEqual(event.next_seat_position(), 3)

    def test_price_position_never_drops_below_the_high_water_mark(self):
        event = EventSettings.load()
        event.seats_high_water = 7
        event.save()
        # The room is empty, but the ladder has already reached seat 8.
        self.assertEqual(event.price_position(0), 7)
        # Occupancy overtakes it again once real bookings pass that point.
        self.assertEqual(event.price_position(9), 9)

    def test_advancing_the_high_water_mark_only_goes_up(self):
        event = EventSettings.load()
        self.assertTrue(event.advance_price_position(5))
        self.assertFalse(event.advance_price_position(3))
        event.refresh_from_db()
        self.assertEqual(event.seats_high_water, 5)


class TierPricingTests(TestCase):
    """Pricing through the API, one test per tier and per way the tier can move."""

    def setUp(self):
        self.event = configure_event()
        default_tiers()
        self.client = APIClient()

    def post(self, count=1, whatsapp='+5491111111111'):
        return self.client.post('/api/bookings/', {
            'buyer_name': 'Ana Perez',
            'buyer_whatsapp': whatsapp,
            'guests': guest_payload(count),
        }, format='json')

    def test_a_booking_in_the_first_tier_pays_the_base_price(self):
        body = self.post().json()
        self.assertEqual(body['total_amount'], PRICE)
        self.assertEqual(body['amount_display'], '5.000')
        self.assertEqual(body['pricing_display'], '5.000')

    def test_a_booking_in_the_second_tier_pays_the_second_price(self):
        make_booking(quantity=TIER_TWO_AT, status=Booking.Status.SELF_CONFIRMED)
        body = self.post().json()
        self.assertEqual(body['total_amount'], TIER_TWO_PRICE)
        self.assertEqual(body['amount_display'], '7.000')

    def test_a_booking_in_the_third_tier_pays_the_third_price(self):
        make_booking(quantity=TIER_THREE_AT, status=Booking.Status.SELF_CONFIRMED)
        body = self.post().json()
        self.assertEqual(body['total_amount'], TIER_THREE_PRICE)
        self.assertEqual(body['amount_display'], '9.000')

    def test_the_pay_screen_explains_a_straddled_quote(self):
        """"24.000" with no explanation reads as a mistake to someone told 5.000."""
        make_booking(quantity=TIER_TWO_AT - 2, status=Booking.Status.SELF_CONFIRMED)
        body = self.post(count=4).json()
        self.assertEqual(body['total_amount'], 2 * PRICE + 2 * TIER_TWO_PRICE)
        self.assertEqual(
            [(line['quantity'], line['unit_price_display'])
             for line in body['price_breakdown']],
            [(2, '5.000'), (2, '7.000')],
        )
        self.assertEqual(body['pricing_display'], '2 x 5.000 + 2 x 7.000')

    def test_a_single_tier_quote_carries_one_breakdown_line(self):
        body = self.post(count=2).json()
        self.assertEqual(len(body['price_breakdown']), 1)
        self.assertEqual(body['price_breakdown'][0]['quantity'], 2)
        self.assertEqual(body['total_amount'], 2 * PRICE)

    def test_a_pending_booking_inside_its_ttl_moves_the_price_on(self):
        """A pending booking holds its seats, so it holds its place in the ladder too.

        Otherwise a launch burst -- everyone submitting before anyone confirms -- would
        quote the whole room the first-tier price and the ladder would never advance.
        """
        make_booking(quantity=TIER_TWO_AT)  # pending, fresh
        self.assertEqual(self.post().json()['total_amount'], TIER_TWO_PRICE)

    def test_the_quote_is_frozen_and_survives_the_ladder_moving(self):
        """The buyer read this number off the pay screen and typed it into a bank.

        Re-pricing at confirm would demand a different amount from someone who has
        already sent the money.
        """
        created = self.post().json()
        self.assertEqual(created['total_amount'], PRICE)
        # The rest of the cheap tier goes while this buyer is in their banking app.
        make_booking(name='Beto', quantity=TIER_THREE_AT,
                     status=Booking.Status.SELF_CONFIRMED)
        confirmed = self.client.post(
            f"/api/bookings/{created['reference']}/confirm/", format='json'
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()['total_amount'], PRICE)
        booking = Booking.objects.get(reference=created['reference'])
        self.assertEqual(booking.status, Booking.Status.SELF_CONFIRMED)
        self.assertEqual(booking.total_amount, PRICE)

    def test_editing_the_ladder_never_reprices_an_existing_booking(self):
        created = self.post().json()
        PriceTier.objects.all().delete()
        configure_tiers((1, 900_000))
        booking = Booking.objects.get(reference=created['reference'])
        self.assertEqual(booking.total_amount, PRICE)

    def test_a_retry_is_quoted_the_original_price_not_a_new_one(self):
        """A double-tap through a cold start must not come back more expensive."""
        first = self.post().json()
        make_booking(name='Beto', quantity=TIER_TWO_AT,
                     status=Booking.Status.SELF_CONFIRMED)
        second = self.post()
        self.assertEqual(second.status_code, 200)  # the existing booking, not a new one
        self.assertEqual(second.json()['reference'], first['reference'])
        self.assertEqual(second.json()['total_amount'], PRICE)

    def test_the_breakdown_is_stored_on_the_booking_as_evidence(self):
        self.post(count=2)
        booking = Booking.objects.get()
        self.assertEqual(len(booking.price_breakdown), 1)
        self.assertEqual(booking.price_breakdown[0]['unit_price_cents'], PRICE)
        self.assertEqual(
            booking.total_amount,
            sum(line['unit_price_cents'] * line['quantity']
                for line in booking.price_breakdown),
        )

    def test_a_booking_made_before_tiers_existed_still_describes_its_price(self):
        # price_breakdown is empty on rows created by the old flat-price code.
        booking = make_booking(quantity=2)
        self.assertEqual(booking.price_breakdown, [])
        self.assertEqual(booking.pricing_display, '5.000')

    def test_revolut_is_charged_in_the_same_tier_as_the_peso_price(self):
        """Otherwise the cheapest ticket at the door is a Revolut one in the top tier."""
        configure_event(revolut_tag='dhruvk', revolut_currency='EUR',
                        revolut_price_cents=500)
        default_tiers()
        make_booking(quantity=TIER_TWO_AT, status=Booking.Status.SELF_CONFIRMED)
        revolut = self.post().json()['revolut']
        # 7.000/5.000 of EUR 5.00.
        self.assertEqual(revolut['amount_cents'], 700)
        self.assertEqual(revolut['amount_display'], 'EUR 7.00')

    def test_revolut_stays_hidden_when_it_is_not_priced(self):
        self.assertIsNone(self.post().json()['revolut'])

    def test_revolut_switched_on_later_charges_the_band_the_booking_bought_in(self):
        """The regression for a real undercharge.

        A booking priced while Revolut was off stores no Revolut figure. Turning
        Revolut on later must not fall back to base-price x quantity: that would quote
        a top-band pair the first-band figure and hand two people a cheap ticket.
        """
        make_booking(quantity=TIER_THREE_AT, status=Booking.Status.SELF_CONFIRMED)
        created = self.post(count=2).json()  # priced in the 9.000 band, Revolut off
        self.assertIsNone(created['revolut'])
        booking = Booking.objects.get(reference=created['reference'])
        self.assertIsNone(booking.revolut_amount_cents)

        configure_event(revolut_tag='dhruvk', revolut_currency='EUR',
                        revolut_price_cents=500)
        default_tiers()
        payload = pay_screen_payload(booking, EventSettings.load())
        # 9.000/5.000 of EUR 5.00, twice -- not 2 x EUR 5.00.
        self.assertEqual(payload['revolut']['amount_cents'], 1800)

    def test_revolut_scaling_rounds_half_up_in_integer_arithmetic(self):
        """Money is integer cents. round() is banker's rounding and would give 2."""
        event = configure_event(revolut_tag='dhruvk', revolut_currency='EUR',
                                revolut_price_cents=5, ticket_price_cents=200_000)
        self.assertEqual(event.revolut_price_for(100_000), 3)

    def test_a_lapsed_booking_frees_its_seat_but_not_its_price(self):
        """The core of a monotonic ladder, and the reason capacity and price are read
        from two different numbers.

        A booking that lapses genuinely frees its seat -- the room is empty again and
        someone must be able to book into it. What it does not do is roll the price
        back. Otherwise a launch burst that mostly abandons would hand the cheap band
        out twice, and the event would sell far more than 50 tickets at 5.000.
        """
        first = self.post(count=TIER_TWO_AT, whatsapp='+5491100000011')
        self.assertEqual(first.status_code, 201)
        # The cheap band is gone, so the next buyer is in the second.
        self.assertEqual(self.post(whatsapp='+5491100000022').json()['total_amount'],
                         TIER_TWO_PRICE)

        Booking.objects.all().update(
            created_at=timezone.now() - timedelta(minutes=46)
        )

        # The seats really are back: occupancy is zero and the room is bookable.
        self.assertEqual(seats_taken(EventSettings.load()), 0)
        self.assertEqual(
            self.client.get('/api/availability/').json()['remaining'], CAPACITY
        )
        # The price is not back.
        self.assertEqual(self.post(whatsapp='+5491100000033').json()['total_amount'],
                         TIER_TWO_PRICE)

    def test_a_cancelled_booking_frees_its_seat_but_not_its_price(self):
        self.post(count=TIER_TWO_AT, whatsapp='+5491100000044')
        Booking.objects.all().update(status=Booking.Status.CANCELLED)
        self.assertEqual(seats_taken(EventSettings.load()), 0)
        self.assertEqual(self.post(whatsapp='+5491100000055').json()['total_amount'],
                         TIER_TWO_PRICE)

    def test_the_quoted_price_never_falls_across_a_sale_with_churn(self):
        """The property stated as a property, rather than as one scenario.

        Walks a sale where every booking is abandoned immediately, which is the worst
        case for a ladder keyed on occupancy: the room is empty the whole way through,
        so a non-monotonic ladder would quote 5.000 every single time.
        """
        quotes = []
        for i in range(CAPACITY):
            quotes.append(self.post(whatsapp=f'+54911000{i:05d}').json()['total_amount'])
            Booking.objects.all().update(
                created_at=timezone.now() - timedelta(minutes=46)
            )
        self.assertEqual(quotes, sorted(quotes), 'the ladder walked backwards')
        # And it really did climb rather than just not falling.
        self.assertEqual(quotes[0], PRICE)
        self.assertEqual(quotes[-1], TIER_THREE_PRICE)

    def test_abandoned_bookings_never_cost_the_event_a_seat(self):
        """The safety property that makes the trade acceptable.

        Pricing climbing past the room must never be what refuses a booking. The event
        can still fill every seat after any amount of churn -- those buyers just pay
        the price the ladder has reached.
        """
        for i in range(CAPACITY):
            self.post(whatsapp=f'+54911100{i:05d}')
            Booking.objects.all().update(
                created_at=timezone.now() - timedelta(minutes=46)
            )
        # The ladder is now past the end of the room; the room is still empty.
        event = EventSettings.load()
        self.assertGreaterEqual(event.seats_high_water, CAPACITY)
        self.assertEqual(seats_taken(event), 0)

        # Every remaining seat is still sellable.
        response = self.post(count=MAX_TICKETS_PER_BOOKING, whatsapp='+5491199999999')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['total_amount'],
                         MAX_TICKETS_PER_BOOKING * TIER_THREE_PRICE)

    def test_the_admin_can_re_open_a_cheaper_band_by_hand(self):
        """The escape hatch. A bulk cancellation should be forgivable without SQL."""
        self.post(count=TIER_TWO_AT, whatsapp='+5491100000066')
        Booking.objects.all().update(status=Booking.Status.CANCELLED)
        self.assertEqual(self.post(whatsapp='+5491100000077').json()['total_amount'],
                         TIER_TWO_PRICE)

        event = EventSettings.load()
        event.seats_high_water = 0
        event.save()
        Booking.objects.all().update(status=Booking.Status.CANCELLED)
        self.assertEqual(self.post(whatsapp='+5491100000088').json()['total_amount'],
                         PRICE)

    def test_availability_advertises_the_price_the_booking_endpoint_will_charge(self):
        """The two must not disagree, or the page quotes a price it cannot honour."""
        self.post(count=TIER_TWO_AT, whatsapp='+5491100000099')
        Booking.objects.all().update(
            created_at=timezone.now() - timedelta(minutes=46)
        )
        body = self.client.get('/api/availability/').json()
        self.assertEqual(body['current_price_cents'], TIER_TWO_PRICE)
        self.assertEqual(body['next_seat'], TIER_TWO_AT + 1)
        # Seats are free again even though the price has moved on.
        self.assertEqual(body['remaining'], CAPACITY)
        self.assertEqual(self.post(whatsapp='+5491100001111').json()['total_amount'],
                         body['current_price_cents'])


class AvailabilityLadderTests(TestCase):
    """What the page needs in order to show a price before anyone books anything."""

    def setUp(self):
        self.event = configure_event()
        default_tiers()
        self.client = APIClient()

    def body(self):
        return self.client.get('/api/availability/').json()

    def test_the_whole_ladder_is_published(self):
        # Baking prices into the frontend would make a correction a redeploy.
        self.assertEqual(
            [(t['from_seat'], t['to_seat'], t['price_display'])
             for t in self.body()['tiers']],
            [(1, 4, '5.000'), (5, 8, '7.000'), (9, 10, '9.000')],
        )

    def test_the_current_price_and_seat_advance_with_sales(self):
        first = self.body()
        self.assertEqual(first['next_seat'], 1)
        self.assertEqual(first['current_price_display'], '5.000')

        make_booking(quantity=TIER_TWO_AT, status=Booking.Status.SELF_CONFIRMED)
        second = self.body()
        self.assertEqual(second['next_seat'], TIER_TWO_AT + 1)
        self.assertEqual(second['current_price_cents'], TIER_TWO_PRICE)
        self.assertEqual(second['current_price_display'], '7.000')

    def test_a_flat_priced_event_publishes_one_band(self):
        PriceTier.objects.all().delete()
        self.assertEqual(len(self.body()['tiers']), 1)
        self.assertEqual(self.body()['current_price_cents'], PRICE)

    def test_a_sold_out_event_still_answers_with_a_price(self):
        # The badge renders from this payload; a missing key would blank the page.
        make_booking(quantity=CAPACITY, status=Booking.Status.SELF_CONFIRMED)
        body = self.body()
        self.assertTrue(body['sold_out'])
        self.assertEqual(body['current_price_cents'], TIER_THREE_PRICE)


@skipUnless(
    connection.features.has_select_for_update,
    'select_for_update is a silent no-op on this backend, so this test would pass '
    'without proving anything. Run it against Postgres.',
)
class TierRaceTest(TransactionTestCase):
    """Does the ladder survive a launch burst?

    Capacity has its own race test. This one asks a different question: with everyone
    submitting at once, does exactly the published number of tickets sell at each price?
    Without the lock the reads interleave, several threads see the same position, and
    the event sells more cheap tickets than it has cheap seats -- a revenue bug no
    capacity assertion would catch, because the headcount stays correct.
    """

    def setUp(self):
        self.event = configure_event()
        default_tiers()

    @staticmethod
    def _attempt(index, quantity):
        try:
            booking, _ = services.create_booking(
                buyer_name=f'Buyer {index}',
                # Distinct numbers, or the retry dedupe collapses these into one
                # booking and the race never happens.
                buyer_whatsapp=f'+549110000{index:04d}',
                guests=guest_payload(quantity),
            )
            return booking.total_amount
        except services.SoldOut:
            return None
        finally:
            # Django opens a connection per thread; leaking them hangs the suite.
            connection.close()

    def _run(self, attempts, quantity):
        with ThreadPoolExecutor(max_workers=attempts) as pool:
            results = list(
                pool.map(lambda i: self._attempt(i, quantity), range(attempts))
            )
        return [total for total in results if total is not None]

    def test_each_tier_sells_exactly_its_own_number_of_seats(self):
        totals = self._run(attempts=20, quantity=1)
        self.assertEqual(len(totals), CAPACITY)
        sold = Counter(totals)
        self.assertEqual(sold[PRICE], TIER_TWO_AT)
        self.assertEqual(sold[TIER_TWO_PRICE], TIER_THREE_AT - TIER_TWO_AT)
        self.assertEqual(sold[TIER_THREE_PRICE], CAPACITY - TIER_THREE_AT)

    def test_the_takings_are_exactly_the_published_ladder(self):
        """One number that catches both a double-quoted seat and a skipped one."""
        self._run(attempts=20, quantity=1)
        expected = (
            TIER_TWO_AT * PRICE
            + (TIER_THREE_AT - TIER_TWO_AT) * TIER_TWO_PRICE
            + (CAPACITY - TIER_THREE_AT) * TIER_THREE_PRICE
        )
        self.assertEqual(
            sum(Booking.objects.values_list('total_amount', flat=True)), expected
        )

    def test_multi_seat_parties_split_across_tiers_without_double_selling_a_seat(self):
        """Every seat is priced once and only once, even when parties straddle bands."""
        self._run(attempts=10, quantity=3)
        seats = [
            seat
            for breakdown in Booking.objects.values_list('price_breakdown', flat=True)
            for line in breakdown
            for seat in range(line['from_seat'], line['to_seat'] + 1)
        ]
        self.assertEqual(sorted(seats), list(range(1, Guest.objects.count() + 1)))
        self.assertLessEqual(Guest.objects.count(), CAPACITY)

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
