import secrets
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

# money.py imports nothing from this app, so this cannot be circular.
from .money import format_ars

# 9 bytes -> 72 bits of entropy -> a 12-character url-safe string. The reference is shown
# to the buyer and may appear in a URL, so it must not be guessable: a sequential pk would
# let anyone enumerate their coworkers' bookings.
REFERENCE_BYTES = 9

MAX_TICKETS_PER_BOOKING = 8
MAX_SONG_REQUESTS = 3


def generate_reference():
    return secrets.token_urlsafe(REFERENCE_BYTES)


class EventSettings(models.Model):
    """Single row (pk=1) holding everything the organiser may need to change live.

    This is a table rather than constants in settings.py for two reasons: capacity can be
    changed from the admin without a redeploy, and a row can be locked with
    select_for_update() while seats are counted. A constant cannot be locked.
    """

    event_name = models.CharField(max_length=200, default='TBD')
    event_date = models.DateTimeField(null=True, blank=True)
    venue = models.CharField(max_length=200, blank=True)

    capacity = models.PositiveIntegerField(
        default=0,
        help_text='Hard cap on total guests across live bookings.',
    )
    ticket_price_cents = models.PositiveIntegerField(
        default=500_000,
        # Zero would make every Revolut band scale to the same figure (see
        # revolut_price_for) and is never a real price, so the admin form refuses it.
        validators=[MinValueValidator(1)],
        help_text='Integer cents. 500000 = 5.000 pesos. The price of the FIRST tier -- '
                  'what a ticket costs until the first PriceTier threshold is passed. '
                  'With no tiers configured this is a flat price for everyone.',
    )

    # Payment destination, shown on the pay screen. The API response is authoritative:
    # the frontend may bake these in for instant render, but must overwrite from the API.
    alias = models.CharField(max_length=100, blank=True)
    cvu = models.CharField(max_length=30, blank=True)
    account_holder_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Name the buyer's banking app will show as the destination.",
    )
    organiser_whatsapp = models.CharField(
        max_length=30,
        blank=True,
        help_text='Used for wa.me links on the sold-out and error screens.',
    )

    # Second destination for guests paying from outside Argentina: one flat figure per
    # ticket in one currency, on a second rail. Still never per-person variation. A blank
    # tag hides the whole block on the pay screen.
    revolut_tag = models.CharField(
        max_length=50,
        blank=True,
        help_text='Revtag without the @. Blank hides Revolut on the pay screen.',
    )
    revolut_currency = models.CharField(
        max_length=3,
        blank=True,
        help_text='ISO code the Revolut price is in, e.g. EUR.',
    )
    revolut_price_cents = models.PositiveIntegerField(
        default=0,
        help_text='Integer minor units in that currency: 500 = 5.00. Flat for everyone '
                  'paying via Revolut.',
    )

    seats_high_water = models.PositiveIntegerField(
        default=0,
        help_text='How far up the price ladder the event has climbed: the most seats '
                  'ever occupied at once. The ladder reads this, not current '
                  'occupancy, so the price never falls when a booking lapses or is '
                  'cancelled -- those seats come back for sale, but at the price the '
                  'event has reached. Lower it by hand only to deliberately re-open a '
                  'cheaper band, e.g. after a bulk cancellation.',
    )

    pending_ttl_minutes = models.PositiveIntegerField(
        default=45,
        help_text='How long a pending booking holds its seats.',
    )
    list_deadline = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the name list goes to the venue. Shown to buyers.',
    )
    sales_close_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='After this, new bookings are refused. Blank means never close.',
    )

    class Meta:
        verbose_name = 'event settings'
        verbose_name_plural = 'event settings'

    def __str__(self):
        return self.event_name

    def clean(self):
        super().clean()
        self._normalise_revolut()
        if self.revolut_tag and not (self.revolut_currency and self.revolut_price_cents):
            raise ValidationError(
                'Revolut needs a currency and a price above zero. Leave the tag blank to '
                'hide Revolut instead.'
            )

    def _normalise_revolut(self):
        # Revolut shows the tag as "@name"; accept it pasted that way, and with the
        # trailing space a paste often carries. A config slip must stay an admin edit.
        self.revolut_tag = self.revolut_tag.strip().lstrip('@')
        self.revolut_currency = self.revolut_currency.strip().upper()

    def save(self, *args, **kwargs):
        # Enforce the singleton: there is only ever one event.
        self.pk = 1
        self._normalise_revolut()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Event settings cannot be deleted.')

    @classmethod
    def load(cls):
        """The settings row. Seeded by a data migration, so it always exists."""
        return cls.objects.get(pk=1)

    def pending_cutoff(self, now=None):
        """Pending bookings created at or before this moment no longer hold a seat."""
        now = now or timezone.now()
        return now - timedelta(minutes=self.pending_ttl_minutes)

    def sales_are_open(self, now=None):
        if self.sales_close_at is None:
            return True
        return (now or timezone.now()) < self.sales_close_at

    # ------------------------------------------------------------------ pricing

    def price_position(self, taken):
        """The 0-based index of the seat the next ticket will be priced as.

        **The one place that decides which tier a booking is quoted in.** Everything
        that needs a position -- `create_booking()`, `/api/availability/`, the admin
        readout -- comes through here, so the price a buyer is shown and the price they
        are charged cannot come apart.

        The ladder is **monotonic**: it is the high-water mark of occupancy, never
        current occupancy alone. Seats that come back (a pending booking lapsing, a
        cancellation) restore *capacity* but not *price*. Without that, the ladder walks
        backwards -- 50 people fill the form at launch, the next buyer is quoted 7.000,
        thirty abandon, and a buyer arriving later is quoted 5.000 again. Over a sale
        with ordinary churn the event would sell far more than 50 tickets at 5.000,
        which is the whole thing the ladder exists to prevent.

        The accepted cost is the other way round: seats abandoned in the cheap band are
        not re-offered at the cheap price. That is a deliberate trade, and
        `seats_high_water` is editable in the admin so a bulk cancellation can be
        forgiven by hand.

        Takes the occupancy count rather than querying, because every caller has just
        counted -- and asking twice inside one lock invites two answers.
        """
        return max(taken, self.seats_high_water)

    def next_seat_position(self, now=None):
        """price_position() for a caller that has not already counted seats."""
        return self.price_position(seats_taken(self, now))

    def advance_price_position(self, seats_through):
        """Move the ladder's high-water mark up to `seats_through`. Never down.

        Called under the same lock that priced the booking, so two concurrent creates
        cannot both claim the same rung. Writes a single column: the caller's copy of
        this row was read at the top of the transaction and may be stale in fields an
        admin edited meanwhile.
        """
        if seats_through <= self.seats_high_water:
            return False
        self.seats_high_water = seats_through
        self.save(update_fields=['seats_high_water'])
        return True

    def price_ladder(self):
        """The ladder as inclusive 1-based seat ranges, cheapest first.

        `[{'from_seat': 1, 'to_seat': 50, 'price_cents': 500000}, ...]` -- the shape the
        organiser and the buyer both think in ("the first 50 are 5.000"), derived from
        the thresholds actually stored. Storing ranges instead would mean storing the
        same boundary twice and watching them drift.

        The last band runs to `capacity`; with no tiers it is the whole event at the
        base price.
        """
        tiers = list(self.price_tiers.all())
        bands = []
        price = self.ticket_price_cents
        start = 1
        for tier in tiers:
            # A threshold at or past capacity can never be reached, so it is not a band.
            if tier.starts_after_seats >= self.capacity:
                break
            bands.append({
                'from_seat': start,
                'to_seat': tier.starts_after_seats,
                'price_cents': price,
                'revolut_price_cents': self.revolut_price_for(price),
            })
            price = tier.price_cents
            start = tier.starts_after_seats + 1
        bands.append({
            'from_seat': start,
            'to_seat': max(self.capacity, start),
            'price_cents': price,
            'revolut_price_cents': self.revolut_price_for(price),
        })
        return bands

    def revolut_price_for(self, ars_price_cents):
        """The Revolut figure for a band priced at `ars_price_cents`.

        Scaled from the base pair so the two rails stay in step without asking the
        organiser to maintain a second ladder by hand: at 2x the peso price a Revolut
        payer owes 2x the Revolut price. Integer arithmetic, rounded to the nearest
        minor unit -- never float, like every other amount here.

        Returns 0 when Revolut is unpriced, which hides the block entirely.
        """
        if not self.revolut_price_cents or not self.ticket_price_cents:
            return self.revolut_price_cents
        # Integer arithmetic with explicit half-up rounding. Not `round(a * b / c)`:
        # that is float division in a money path, which this project forbids, and
        # Python's round() is banker's rounding, so an exact .5 would go to even.
        numerator = self.revolut_price_cents * ars_price_cents
        return (numerator + self.ticket_price_cents // 2) // self.ticket_price_cents

    def price_seats(self, position, quantity):
        """Price `quantity` consecutive seats starting at 0-based seat `position`.

        Returns `(breakdown, total_cents, revolut_total_cents)`. A party that straddles
        a boundary is split across bands -- two seats at 5.000 and two at 7.000 -- which
        is the literal meaning of "the first 50 tickets are 5.000". Charging the whole
        party at either end's price would mean the 50th ticket's price depended on who
        happened to be standing next to it.

        Seats past the last band (possible when a refused confirm leaves pending rows
        inflating the position, or when capacity is lowered) take the last band's price
        rather than raising: pricing must never be the thing that fails. Capacity is
        enforced separately, and it is what refuses the booking.
        """
        bands = self.price_ladder()
        breakdown = []
        seat = position + 1  # 1-based, the way the ladder reads
        left = quantity
        for band in bands:
            if left <= 0:
                break
            if seat > band['to_seat']:
                continue
            take = min(left, band['to_seat'] - seat + 1)
            breakdown.append({
                'from_seat': seat,
                'to_seat': seat + take - 1,
                'unit_price_cents': band['price_cents'],
                'unit_revolut_cents': band['revolut_price_cents'],
                'quantity': take,
            })
            seat += take
            left -= take
        if left > 0:
            # Past the end of the ladder: the last band's price continues.
            last = bands[-1]
            breakdown.append({
                'from_seat': seat,
                'to_seat': seat + left - 1,
                'unit_price_cents': last['price_cents'],
                'unit_revolut_cents': last['revolut_price_cents'],
                'quantity': left,
            })
        total = sum(line['unit_price_cents'] * line['quantity'] for line in breakdown)
        revolut_total = sum(
            line['unit_revolut_cents'] * line['quantity'] for line in breakdown
        )
        return breakdown, total, revolut_total

    def current_price_cents(self, now=None):
        """What one ticket costs right now. Display only -- a real booking is priced by
        price_seats() under the lock, because the answer moves."""
        _, total, _ = self.price_seats(self.next_seat_position(now), 1)
        return total


class PriceTier(models.Model):
    """A step in the price ladder: "after N seats are sold, a ticket costs X".

    Stored as a threshold rather than a band size so there is exactly one number per
    step and nothing to reconcile: the band a tier opens runs until the next threshold,
    or until capacity. The first band's price is EventSettings.ticket_price_cents, so an
    event with no tiers behaves exactly as it did when the price was flat.

    This is a ladder every buyer can read off the page, not per-buyer variation -- the
    thing this project refuses. Two people buying the 51st and 52nd tickets pay the same.
    """

    event = models.ForeignKey(
        EventSettings,
        related_name='price_tiers',
        on_delete=models.CASCADE,
    )
    starts_after_seats = models.PositiveIntegerField(
        unique=True,
        validators=[MinValueValidator(1)],
        help_text='This price applies from the NEXT seat on. 50 means seats 51 and up.',
    )
    price_cents = models.PositiveIntegerField(
        help_text='Integer cents. 700000 = 7.000 pesos.',
    )

    class Meta:
        ordering = ('starts_after_seats',)
        verbose_name = 'price tier'
        verbose_name_plural = 'price tiers'

    def __str__(self):
        return f'from seat {self.starts_after_seats + 1}: {format_ars(self.price_cents)}'


class Booking(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SELF_CONFIRMED = 'self_confirmed', 'Self-confirmed'
        VERIFIED = 'verified', 'Verified'
        EXPIRED = 'expired', 'Expired'
        CANCELLED = 'cancelled', 'Cancelled'

    # Statuses whose guests occupy a seat. PENDING only counts while it is inside the
    # TTL -- see seats_taken(), which applies the age check rather than trusting a sweep.
    LIVE_STATUSES = (Status.PENDING, Status.SELF_CONFIRMED, Status.VERIFIED)
    # Statuses that go on the list sent to the venue. Deliberately excludes PENDING: a
    # form-filler who never paid should not take a slot on the door list.
    CONFIRMED_STATUSES = (Status.SELF_CONFIRMED, Status.VERIFIED)

    reference = models.CharField(
        max_length=32,
        unique=True,
        default=generate_reference,
        editable=False,
    )

    buyer_name = models.CharField(max_length=200)
    buyer_whatsapp = models.CharField(max_length=30)
    buyer_email = models.EmailField(blank=True)
    sender_account_name = models.CharField(
        max_length=200,
        blank=True,
        help_text=(
            'Name the transfer will arrive under. Often not the buyer -- a partner, a '
            'parent, a Mercado Pago handle. Primary key for reconciliation.'
        ),
    )

    quantity = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(MAX_TICKETS_PER_BOOKING)],
        help_text='Derived from the guest list; never accepted from the client.',
    )
    total_amount = models.PositiveIntegerField(
        help_text='Integer cents. The sum of the tier prices for the seats this booking '
                  'was quoted, nothing added. Frozen at creation.',
    )
    price_breakdown = models.JSONField(
        default=list,
        blank=True,
        help_text='The quote, per tier band: seats, unit price, how many. A party that '
                  'straddles a boundary has more than one line. Read-only evidence of '
                  'what the buyer was shown.',
    )
    revolut_amount_cents = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text='The Revolut figure quoted at the same moment, in minor units. Null '
                  'for bookings made before Revolut was priced.',
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    # Evidence of money actually moving. The plan recorded only that a booking was
    # verified, never what arrived -- which is fine inbound-only, but not once a refund is
    # owed, because a rounded transfer means received != total_amount.
    amount_received_cents = models.PositiveIntegerField(null=True, blank=True)
    verified_source = models.CharField(
        max_length=500,
        blank=True,
        help_text='The statement line that verified this, or "manual". Makes a wrong '
                  'match reversible.',
    )
    refund_owed_cents = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text='Null means nothing is owed. Refund state is derived from this and '
                  'refunded_at -- it is deliberately not a status.',
    )
    refunded_at = models.DateTimeField(null=True, blank=True)

    list_exported_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When this booking last appeared on a list sent to the venue.',
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            # seats_taken() filters on exactly this pair on every availability check.
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        return f'{self.reference} - {self.buyer_name} ({self.quantity})'

    @property
    def is_live(self):
        """Does this booking hold its seats right now?"""
        if self.status in Booking.CONFIRMED_STATUSES:
            return True
        if self.status != Booking.Status.PENDING:
            return False
        return self.created_at > EventSettings.load().pending_cutoff()

    @property
    def pricing_display(self):
        """'2 x 5.000 + 2 x 7.000', or just '5.000' when it is all one tier.

        The organiser's answer to "why does this booking owe 24.000?" without opening
        the JSON. Falls back to the flat division for rows created before tiers existed.
        """
        if not self.price_breakdown:
            if not self.quantity:
                return format_ars(self.total_amount)
            return format_ars(self.total_amount // self.quantity)
        parts = []
        for line in self.price_breakdown:
            price = format_ars(line['unit_price_cents'])
            count = line['quantity']
            parts.append(f'{count} x {price}' if count > 1 else price)
        return ' + '.join(parts)

    @property
    def spans_multiple_tiers(self):
        return len(self.price_breakdown or []) > 1

    @property
    def refund_state(self):
        if self.refund_owed_cents is None:
            return 'none'
        return 'sent' if self.refunded_at else 'owed'

    @property
    def guests_differ_from_quantity(self):
        """Drift between what was paid for and who is attending.

        Surfaced on the reconciliation page: editing guests in admin (which the plan
        encourages) can change headcount without changing the amount owed.
        """
        return self.guests.count() != self.quantity


class Guest(models.Model):
    """One row per attending human. Capacity is counted over these, not over bookings,
    so headcount and the venue export can never disagree."""

    booking = models.ForeignKey(
        Booking,
        related_name='guests',
        on_delete=models.CASCADE,
    )
    full_name = models.CharField(max_length=200)

    class Meta:
        ordering = ('full_name',)

    def __str__(self):
        return self.full_name

    def clean(self):
        """Block admin adds that would push the event past capacity.

        Django calls this from ModelForm validation, so it covers the admin. The API
        path does its own check inside a locked transaction -- this is the second door,
        not the main one.
        """
        super().clean()
        if self.pk or not self.booking_id:
            return
        if self.booking.status not in Booking.LIVE_STATUSES:
            return
        event_settings = EventSettings.load()
        if seats_taken(event_settings) >= event_settings.capacity:
            raise ValidationError(
                f'Event is at capacity ({event_settings.capacity}). Cancel or expire a '
                f'booking before adding another guest.'
            )


class SongRequest(models.Model):
    """A buyer's optional song request, at most MAX_SONG_REQUESTS per booking.

    Stored exactly as typed. Anything that ends up on a real playlist is a layer on top
    of this row, so a request is never lost to a flaky third party.
    """

    booking = models.ForeignKey(
        Booking,
        related_name='song_requests',
        on_delete=models.CASCADE,
    )
    position = models.PositiveSmallIntegerField()
    text = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('position',)
        constraints = [
            models.UniqueConstraint(
                fields=['booking', 'position'], name='songrequest_unique_position',
            ),
        ]

    def __str__(self):
        return self.text


def fresh_pending_filter(cutoff, prefix=''):
    """Pending bookings still inside their TTL.

    The single definition of "pending but not yet expired". Anything that needs this --
    seat counting, the export page's excluded count, the admin's Seat column -- must use
    it rather than restating the condition, or the two drift the day the TTL rule
    changes.

    `prefix` walks a relation, so the same rule can be applied to a queryset of Bookings
    ('') or of anything hanging off one ('booking__').
    """
    return Q(**{
        f'{prefix}status': Booking.Status.PENDING,
        f'{prefix}created_at__gt': cutoff,
    })


def fresh_pending_guest_filter(cutoff):
    """fresh_pending_filter() from a Guest queryset."""
    return fresh_pending_filter(cutoff, 'booking__')


def live_filter(cutoff, prefix=''):
    """Bookings that currently hold a seat.

    Expiry is expressed as a query predicate, not as a stored status. A stale pending
    booking stops counting the moment it ages past the cutoff, whether or not any sweep
    has run -- which matters because a sold-out event stops receiving the requests that
    would trigger a sweep, and the seats would otherwise stay dead for the rest of the
    sale.
    """
    return (
        Q(**{f'{prefix}status__in': Booking.CONFIRMED_STATUSES})
        | fresh_pending_filter(cutoff, prefix)
    )


def live_guest_filter(cutoff):
    """live_filter() from a Guest queryset."""
    return live_filter(cutoff, 'booking__')


def seats_taken(event_settings=None, now=None):
    """How many seats are occupied right now."""
    event_settings = event_settings or EventSettings.load()
    return Guest.objects.filter(
        live_guest_filter(event_settings.pending_cutoff(now))
    ).count()


def seats_remaining(event_settings=None, now=None):
    event_settings = event_settings or EventSettings.load()
    taken = seats_taken(event_settings, now)
    return max(event_settings.capacity - taken, 0)
