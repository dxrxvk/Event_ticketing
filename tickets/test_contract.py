"""The backend half of the frontend contract.

The Vue app copies a handful of backend facts by hand -- the price ladder, how pesos are
formatted, the per-booking cap, the quote arithmetic, the names of the keys it reads and
the error codes it titles -- because the page has to render before a sleeping backend
answers. Nothing linked the copies, so either side could change and the page would
quietly under-quote or show "Something went wrong" for a code it had never heard of.

`contract/frontend_contract.json` now holds those facts once. This file checks the
backend still says them; `frontend/tests/contract.test.js` checks the frontend still
agrees. Change one side and one of the two goes red until the JSON and the other side
are brought along.
"""

import json
from pathlib import Path

from django.conf import settings
from django.test import TestCase

from . import services
from .models import MAX_TICKETS_PER_BOOKING, EventSettings
from .money import format_ars
from .serializers import pay_screen_payload, revolut_payload

CONTRACT = json.loads(
    (Path(settings.BASE_DIR) / 'contract' / 'frontend_contract.json').read_text()
)


def _codes(exception_class):
    """Every error code raised by services, found by walking the exception tree."""
    found = set()
    for subclass in exception_class.__subclasses__():
        found.add(subclass.code)
        found |= _codes(subclass)
    return found


class FrontendContractTests(TestCase):
    """Deliberately uses the event as the migrations leave it, not configure_event():
    the thing under test is that the seeded production ladder matches the ladder the
    frontend bakes in."""

    def setUp(self):
        EventSettings.objects.filter(pk=1).update(sales_close_at=None)
        self.event = EventSettings.load()

    def test_max_guests_matches(self):
        self.assertEqual(CONTRACT['max_guests'], MAX_TICKETS_PER_BOOKING)

    def test_seeded_ladder_matches(self):
        ladder = [
            {key: band[key] for key in ('from_seat', 'to_seat', 'price_cents')}
            for band in self.event.price_ladder()
        ]
        self.assertEqual(ladder, CONTRACT['seeded_ladder'])

    def test_format_ars_matches(self):
        for cents, expected in CONTRACT['format_ars']:
            with self.subTest(cents=cents):
                self.assertEqual(format_ars(cents), expected)

    def test_quotes_match_price_seats(self):
        for case in CONTRACT['quotes']:
            with self.subTest(case['name']):
                breakdown, total, _ = self.event.price_seats(
                    case['next_seat'] - 1, case['quantity'],
                )
                lines = [[line['quantity'], line['unit_price_cents']] for line in breakdown]
                self.assertEqual(lines, case['lines'])
                self.assertEqual(total, case['total_cents'])

    def test_availability_sends_every_key_the_frontend_reads(self):
        payload = services.availability(self.event)
        self.assertLessEqual(set(CONTRACT['availability_keys']), set(payload))
        for tier in payload['tiers']:
            self.assertLessEqual(set(CONTRACT['tier_keys']), set(tier))

    def test_pay_screen_sends_every_key_the_frontend_reads(self):
        booking, _ = services.create_booking(
            buyer_name='Ana', buyer_whatsapp='+5491100000001',
            guests=[{'full_name': 'Ana López'}],
        )
        payload = pay_screen_payload(booking, self.event)
        self.assertLessEqual(set(CONTRACT['booking_keys']), set(payload))
        for line in payload['price_breakdown']:
            self.assertLessEqual(set(CONTRACT['price_breakdown_keys']), set(line))

    def test_revolut_block_sends_every_key_the_frontend_reads(self):
        self.event.revolut_tag = 'dhruvk'
        self.event.revolut_note = 'Send the equivalent of {total} ARS.'
        self.event.save()
        booking, _ = services.create_booking(
            buyer_name='Ana', buyer_whatsapp='+5491100000001',
            guests=[{'full_name': 'Ana López'}],
        )
        payload = revolut_payload(booking, self.event)
        self.assertLessEqual(set(CONTRACT['revolut_keys']), set(payload))

    def test_error_codes_match(self):
        # The service exceptions, plus the 404 the views raise for an unknown reference.
        codes = _codes(services.BookingError) | {'not_found'}
        self.assertEqual(codes, set(CONTRACT['error_codes']))
