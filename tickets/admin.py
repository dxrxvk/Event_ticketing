from datetime import timedelta

from django.contrib import admin
from django.db.models import Case, Count, IntegerField, Value, When
from django.shortcuts import render
from django.urls import path
from django.utils import timezone
from django.utils.html import format_html

from . import exports
from .models import (
    MAX_SONG_REQUESTS, Booking, EventSettings, Guest, PriceTier, SongRequest,
    seats_remaining, seats_taken,
    MAX_SONG_REQUESTS, Booking, EventSettings, Guest, SongRequest,
    fresh_pending_filter, seats_remaining, seats_taken,
)
from .money import format_ars

# A self-confirmed booking never expires (the buyer is trusted), so one that stays
# unverified for this long is the realistic way seats and attention get hoarded.
STALE_CONFIRMED_DAYS = 3

# The seat traffic light.
#
# Rank, colour and label all come from this one table, and every admin that shows a
# booking's state renders it through render_seat_light(). Three readings, in the
# organiser's own words: green means the buyer says the money is sent and the seat is
# held, orange means we are still waiting for them to say it, red means the row holds
# nothing.
#
# Ranks are integers rather than an enum because the same ladder is what the Seat column
# sorts on -- annotated in SQL by seat_rank_case(), so a sorted changelist is still one
# query.
#
# Colour never carries the meaning alone: every dot is followed by its label. That is an
# accessibility rule rather than decoration, and the mid-tone hues below are picked to
# stay legible on both the light and the dark admin theme.
SEAT_RANK_VERIFIED = 4
SEAT_RANK_SELF_CONFIRMED = 3
SEAT_RANK_AWAITING = 2
SEAT_RANK_LAPSED = 1
SEAT_RANK_NONE = 0

SEAT_LIGHTS = {
    SEAT_RANK_VERIFIED: ('#2c9c3f', 'Verified'),
    SEAT_RANK_SELF_CONFIRMED: ('#2c9c3f', 'Seat held'),
    SEAT_RANK_AWAITING: ('#c98a00', 'Awaiting payment'),
    SEAT_RANK_LAPSED: ('#d6332b', 'Lapsed'),
    SEAT_RANK_NONE: ('#d6332b', 'No seat'),
}


def seat_rank(booking, cutoff):
    """How firmly this booking holds a seat, as one of the SEAT_RANK_* values.

    The Python twin of seat_rank_case(). Both read the same rule as seats_taken():
    CONFIRMED_STATUSES hold a seat outright, a pending booking holds one only while it is
    fresher than the TTL cutoff, and cancelled, expired and lapsed pending hold nothing.
    Lapsed is red rather than orange on purpose -- that seat is back on sale, and a
    colour implying otherwise would be the expensive direction to get wrong.
    """
    if booking.status == Booking.Status.VERIFIED:
        return SEAT_RANK_VERIFIED
    if booking.status in Booking.CONFIRMED_STATUSES:
        return SEAT_RANK_SELF_CONFIRMED
    if booking.status == Booking.Status.PENDING:
        return SEAT_RANK_AWAITING if booking.created_at > cutoff else SEAT_RANK_LAPSED
    return SEAT_RANK_NONE


def seat_rank_case(prefix=''):
    """seat_rank() as a SQL CASE, so a changelist ranks every row in its own query.

    `prefix` walks a relation: the Guest and SongRequest lists pass 'booking__'. The
    cutoff is resolved once per request by the caller, never once per row.
    """
    cutoff = EventSettings.load().pending_cutoff()
    status = f'{prefix}status'
    return Case(
        When(**{status: Booking.Status.VERIFIED}, then=Value(SEAT_RANK_VERIFIED)),
        When(
            **{f'{status}__in': Booking.CONFIRMED_STATUSES},
            then=Value(SEAT_RANK_SELF_CONFIRMED),
        ),
        # Orange is exactly what seats_taken() charges for beyond the confirmed rows, so
        # it reuses that predicate rather than restating "pending and inside the TTL".
        When(fresh_pending_filter(cutoff, prefix), then=Value(SEAT_RANK_AWAITING)),
        When(**{status: Booking.Status.PENDING}, then=Value(SEAT_RANK_LAPSED)),
        default=Value(SEAT_RANK_NONE),
        output_field=IntegerField(),
    )


def render_seat_light(rank):
    """A coloured dot and its label.

    Styled inline deliberately: an admin stylesheet would be one more asset that has to
    survive collectstatic and the hashed manifest storage to avoid rendering a list with
    no lights at all.
    """
    colour, label = SEAT_LIGHTS[rank]
    return format_html(
        '<span style="color: {}; font-size: 1.25em; line-height: 1" '
        'aria-hidden="true">●</span> {}',
        colour,
        label,
    )


class SeatLightMixin:
    """The Seat column for the admins that hang off a booking (Guest, SongRequest).

    Shared rather than copied, so a fourth list cannot invent a fifth reading of the same
    five statuses.
    """

    seat_light_prefix = 'booking__'

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _seat_rank=seat_rank_case(self.seat_light_prefix),
        )

    @admin.display(description='Seat', ordering='_seat_rank')
    def seat_light(self, obj):
        return render_seat_light(obj._seat_rank)


class PriceTierInline(admin.TabularInline):
    """The steps in the ladder. The first band is the base price on this same page, so
    two rows describe a three-tier event."""

    model = PriceTier
    extra = 0
    fields = ('starts_after_seats', 'price_cents')


@admin.register(EventSettings)
class EventSettingsAdmin(admin.ModelAdmin):
    """The singleton. Add and delete are blocked: the row is created by a data migration
    and save() pins pk=1, so a second one would silently do nothing."""

    readonly_fields = ('capacity_readout', 'price_ladder_readout')
    inlines = [PriceTierInline]
    fieldsets = (
        ('Event', {
            'fields': ('event_name', 'event_date', 'venue', 'capacity',
                       'capacity_readout'),
        }),
        ('Price ladder', {
            'fields': ('ticket_price_cents', 'seats_high_water', 'price_ladder_readout'),
            'description': 'The price below is what the FIRST tier costs. Each row in '
                           '"Price tiers" at the bottom of this page raises it from a '
                           'given seat on. Changing the ladder never re-prices a booking '
                           'that already exists -- its quote was frozen when it was made. '
                           'The ladder only ever climbs: if bookings lapse, their seats '
                           'go back on sale at the price reached, not the price they '
                           'were first offered at. Lower "seats high water" by hand to '
                           're-open a cheaper band on purpose.',
        }),
        ('Payment destination', {
            'fields': ('alias', 'cvu', 'account_holder_name'),
            'description': 'Shown to buyers on the pay screen. The API response is '
                           'authoritative -- a stale value baked into the frontend is '
                           'overwritten from here.',
        }),
        ('Revolut (payers abroad)', {
            'fields': ('revolut_tag', 'revolut_note', 'revolut_currency',
                       'revolut_price_cents'),
            'description': 'Optional second destination. Leave the tag blank to hide it '
                           'entirely. The note is what payers abroad read: write {total} '
                           "for this booking's peso total or {price} for its per-ticket "
                           'price and the sentence follows the price ladder on its own, '
                           'so you never retype it when the band steps up. Currency and '
                           'price are optional -- set both to also show a fixed figure '
                           '(scaled per band), or leave them blank, since ARS moves '
                           'daily and a pinned figure goes stale. A tag needs at least '
                           'one of the two. These payments land in Revolut, so '
                           'reconciliation means checking two statements.',
        }),
        ('Deadlines', {
            'fields': ('pending_ttl_minutes', 'list_deadline', 'sales_close_at',
                       'organiser_whatsapp'),
            'description': 'After sales_close_at, new bookings are refused. Leave blank '
                           'to never close.',
        }),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Seats')
    def capacity_readout(self, obj):
        if not obj.pk:
            return '-'
        return f'{seats_taken(obj)} taken, {seats_remaining(obj)} remaining'

    @admin.display(description='Ladder')
    def price_ladder_readout(self, obj):
        """The thresholds rendered as the ranges a human said out loud.

        Tiers are stored as "after N seats", which is the form with nothing to drift,
        but nobody plans an event that way -- they say "the first 50 are 5.000". This
        turns the stored form back into the spoken one, and names the seat being sold
        next, so a mis-entered threshold is visible before anyone buys at the wrong
        price rather than after.
        """
        if not obj.pk:
            return '-'
        bands = ' · '.join(
            f"{band['from_seat']}-{band['to_seat']}: {format_ars(band['price_cents'])}"
            for band in obj.price_ladder()
        )
        taken = seats_taken(obj)
        position = obj.price_position(taken)
        if taken >= obj.capacity:
            return f'{bands} — sold out'
        line = (
            f'{bands} — next ticket is seat {position + 1} at '
            f'{format_ars(obj.current_price_cents())}'
        )
        if position > taken:
            # The gap is abandoned bookings: seats back on sale, price not rolled back.
            # Worth naming, because otherwise "20 seats free but priced as seat 51"
            # looks like a bug rather than the rule it is.
            line += (
                f' — the room holds {taken}, but the ladder has reached {position}, '
                f'so {position - taken} freed seat(s) are back on sale at the price '
                f'the event has climbed to'
            )
        return line


class GuestInline(admin.TabularInline):
    model = Guest
    extra = 0
    # Guest.clean() blocks adds that would push past capacity.


class SongRequestInline(admin.TabularInline):
    model = SongRequest
    extra = 0
    # The API enforces three; without this the admin could save a fourth that the
    # buyer's next save silently wipes.
    max_num = MAX_SONG_REQUESTS
    fields = ('position', 'text')


class RefundFilter(admin.SimpleListFilter):
    """Refund state is derived from two nullable fields rather than stored as a status,
    so it needs a filter of its own."""

    title = 'refund'
    parameter_name = 'refund'

    def lookups(self, request, model_admin):
        return (('owed', 'Owed'), ('sent', 'Sent'), ('none', 'Nothing owed'))

    def queryset(self, request, queryset):
        if self.value() == 'owed':
            return queryset.filter(refund_owed_cents__isnull=False, refunded_at__isnull=True)
        if self.value() == 'sent':
            return queryset.filter(refund_owed_cents__isnull=False, refunded_at__isnull=False)
        if self.value() == 'none':
            return queryset.filter(refund_owed_cents__isnull=True)
        return queryset


class StaleConfirmedFilter(admin.SimpleListFilter):
    title = 'stale self-confirmed'
    parameter_name = 'stale'

    def lookups(self, request, model_admin):
        return (('yes', f'Unverified for {STALE_CONFIRMED_DAYS}+ days'),)

    def queryset(self, request, queryset):
        if self.value() != 'yes':
            return queryset
        cutoff = timezone.now() - timedelta(days=STALE_CONFIRMED_DAYS)
        return queryset.filter(
            status=Booking.Status.SELF_CONFIRMED,
            confirmed_at__lt=cutoff,
        )


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    """The back office, and -- until a dedicated view exists -- the reconciliation tool.

    Sort by confirmed_at, read the bank statement, tick people off with the
    "mark verified" action. sender_account_name is the column that identifies who a
    deposit came from, since the account holder is frequently not the buyer.
    """

    list_display = (
        'reference', 'buyer_name', 'party_size', 'status', 'seat_light',
        'amount_display', 'priced_at', 'sender_account_name', 'confirmed_at',
        'refund_state_display',
    )
    list_filter = ('status', RefundFilter, StaleConfirmedFilter)
    search_fields = (
        'reference', 'buyer_name', 'buyer_whatsapp', 'buyer_email',
        'sender_account_name', 'guests__full_name',
    )
    ordering = ('-confirmed_at', '-created_at')
    inlines = [GuestInline, SongRequestInline]
    # price_breakdown is evidence of what the buyer was quoted, not a field to edit:
    # an editable JSON blob invites a typo that disagrees with total_amount, and the
    # two are what a refund is computed from.
    readonly_fields = ('reference', 'created_at', 'price_breakdown')
    actions = ('mark_verified', 'mark_refund_owed', 'mark_refunded', 'expire_stale_pending')

    fieldsets = (
        ('Buyer', {
            'fields': ('reference', 'buyer_name', 'buyer_whatsapp', 'buyer_email',
                       'sender_account_name'),
        }),
        ('Booking', {
            'fields': ('quantity', 'total_amount', 'price_breakdown',
                       'revolut_amount_cents', 'status', 'created_at',
                       'confirmed_at', 'verified_at'),
            'description': 'The quote was frozen when the booking was made. Editing the '
                           'ladder later does not change it, which is the point: the '
                           'buyer already read this number off the pay screen.',
        }),
        ('Money received', {
            'fields': ('amount_received_cents', 'verified_source'),
            'description': 'What actually arrived, and what verified it. Recorded so a '
                           'wrong match can be undone.',
        }),
        ('Refund', {
            'fields': ('refund_owed_cents', 'refunded_at'),
            'description': 'Refund state is derived: nothing owed / owed / sent. '
                           'Cancelling a booking frees its seats automatically.',
        }),
        ('Organiser', {
            'fields': ('list_exported_at', 'notes'),
        }),
    )

    def get_urls(self):
        """Guest list exports, mounted under the booking changelist.

        Custom routes must come BEFORE super().get_urls(): the default set ends in a
        catch-all <path:object_id>/ that would otherwise match 'exports' and try to look
        up a booking with that primary key.

        admin_view() wraps each one in the staff-permission check, so these are never
        reachable by an anonymous visitor who guesses the URL -- which matters, because
        the organiser export carries every coworker's phone number.
        """
        custom = [
            path('exports/', self.admin_site.admin_view(self.exports_index),
                 name='tickets_exports'),
            path('exports/venue.txt', self.admin_site.admin_view(self.export_venue_text),
                 name='tickets_export_venue_txt'),
            path('exports/venue.csv', self.admin_site.admin_view(self.export_venue_csv),
                 name='tickets_export_venue_csv'),
            path('exports/organiser.csv',
                 self.admin_site.admin_view(self.export_organiser_csv),
                 name='tickets_export_organiser_csv'),
            path('exports/playlist.txt',
                 self.admin_site.admin_view(self.export_playlist_text),
                 name='tickets_export_playlist_txt'),
        ]
        return custom + super().get_urls()

    def exports_index(self, request):
        return render(request, 'admin/tickets/exports.html', {
            **self.admin_site.each_context(request),
            'title': 'Guest list exports',
            'confirmed_count': exports.confirmed_guests().count(),
            'pending_count': exports.pending_guest_count(),
            'song_count': len(exports.playlist_lines()),
        })

    def export_playlist_text(self, request):
        return exports.playlist_text_response()

    def export_venue_text(self, request):
        return exports.venue_text_response()

    def export_venue_csv(self, request):
        return exports.venue_csv_response()

    def export_organiser_csv(self, request):
        return exports.organiser_csv_response()

    def get_queryset(self, request):
        """Annotate the seat rank and guest count in SQL.

        Computing either per row would call EventSettings.load() once per booking.
        seat_rank_case() reads the cutoff once and expresses the same expiry predicate as
        seats_taken() as a CASE, so the whole changelist costs one query.
        """
        return super().get_queryset(request).annotate(
            _guest_count=Count('guests', distinct=True),
            _seat_rank=seat_rank_case(),
        )

    @admin.display(description='Party', ordering='_guest_count')
    def party_size(self, obj):
        # Drift means someone is attending who was not paid for, or the reverse.
        if obj._guest_count != obj.quantity:
            return f'{obj._guest_count} (paid for {obj.quantity})'
        return str(obj._guest_count)

    @admin.display(description='Seat', ordering='_seat_rank')
    def seat_light(self, obj):
        # Not a repeat of the status column. Status is the enum the state machine writes;
        # this is what it means for the room -- which of the five is costing a seat.
        return render_seat_light(obj._seat_rank)

    @admin.display(description='Amount', ordering='total_amount')
    def amount_display(self, obj):
        return format_ars(obj.total_amount)

    @admin.display(description='Priced at')
    def priced_at(self, obj):
        """Which tier(s) this booking bought in.

        The column that answers "are we actually selling at the ladder we published?"
        A run of 5.000 rows appearing after seat 50 means late confirms are still
        landing on old quotes, which is allowed but worth seeing.
        """
        return obj.pricing_display

    @admin.display(description='Refund')
    def refund_state_display(self, obj):
        return {'none': '-', 'owed': 'OWED', 'sent': 'sent'}[obj.refund_state]

    @admin.action(description='Mark verified (payment seen in statement)')
    def mark_verified(self, request, queryset):
        now = timezone.now()
        updated = 0
        for booking in queryset:
            booking.status = Booking.Status.VERIFIED
            booking.verified_at = booking.verified_at or now
            # Record what arrived. Assume the full amount unless it is already known --
            # edit the booking if the transfer came up short.
            if booking.amount_received_cents is None:
                booking.amount_received_cents = booking.total_amount
            if not booking.verified_source:
                booking.verified_source = f'manual (admin, {now:%Y-%m-%d %H:%M})'
            booking.save()
            updated += 1
        self.message_user(request, f'{updated} booking(s) marked verified.')

    @admin.action(description='Mark refund owed (full amount received)')
    def mark_refund_owed(self, request, queryset):
        updated = 0
        for booking in queryset:
            booking.refund_owed_cents = (
                booking.amount_received_cents
                if booking.amount_received_cents is not None
                else booking.total_amount
            )
            booking.save()
            updated += 1
        self.message_user(
            request,
            f'{updated} booking(s) flagged as owed a refund. Cancel them separately to '
            f'free the seats.',
        )

    @admin.action(description='Mark refund sent')
    def mark_refunded(self, request, queryset):
        updated = queryset.filter(refund_owed_cents__isnull=False).update(
            refunded_at=timezone.now()
        )
        self.message_user(request, f'{updated} refund(s) marked sent.')

    @admin.action(description='Expire stale pending bookings (tidy-up only)')
    def expire_stale_pending(self, request, queryset):
        """Cosmetic. Seat counting uses a query predicate, so a stale pending booking
        already stops holding its seat at the TTL whether or not this ever runs. This
        only makes the status column agree with reality."""
        cutoff = EventSettings.load().pending_cutoff()
        updated = queryset.filter(
            status=Booking.Status.PENDING, created_at__lte=cutoff
        ).update(status=Booking.Status.EXPIRED)
        self.message_user(
            request,
            f'{updated} booking(s) marked expired. Their seats were already free.',
        )


@admin.register(Guest)
class GuestAdmin(SeatLightMixin, admin.ModelAdmin):
    """Mostly edited inline on the booking. Registered separately so the venue list can
    be searched and sorted directly when checking a name."""

    list_display = ('full_name', 'booking_reference', 'booking_status', 'seat_light')
    list_filter = ('booking__status',)
    search_fields = ('full_name', 'booking__reference', 'booking__buyer_name')
    list_select_related = ('booking',)

    @admin.display(description='Booking', ordering='booking__reference')
    def booking_reference(self, obj):
        return obj.booking.reference

    @admin.display(description='Status', ordering='booking__status')
    def booking_status(self, obj):
        return obj.booking.get_status_display()


@admin.register(SongRequest)
class SongRequestAdmin(SeatLightMixin, admin.ModelAdmin):
    """Edited inline on the booking; registered on its own so the whole list can be
    scanned and searched without opening bookings one by one."""

    list_display = (
        'text', 'position', 'booking_reference', 'buyer', 'booking_status', 'seat_light',
    )
    list_filter = ('booking__status',)
    search_fields = ('text', 'booking__reference', 'booking__buyer_name')
    list_select_related = ('booking',)
    ordering = ('-created_at', 'position')

    @admin.display(description='Booking', ordering='booking__reference')
    def booking_reference(self, obj):
        return obj.booking.reference

    @admin.display(description='Buyer', ordering='booking__buyer_name')
    def buyer(self, obj):
        return obj.booking.buyer_name

    @admin.display(description='Status', ordering='booking__status')
    def booking_status(self, obj):
        return obj.booking.get_status_display()
