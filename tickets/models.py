import secrets
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

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
        help_text='Integer cents. 500000 = 5.000 pesos. Flat price for everyone.',
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
        help_text='Integer cents. ticket_price_cents * quantity, nothing added.',
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
