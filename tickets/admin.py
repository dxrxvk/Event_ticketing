from datetime import timedelta

from django.contrib import admin
from django.db.models import BooleanField, Case, Count, Q, Value, When
from django.shortcuts import render
from django.urls import path
from django.utils import timezone

from . import exports
from .models import (
    Booking, EventSettings, Guest, SongRequest, seats_remaining, seats_taken,
)
from .money import format_ars

# A self-confirmed booking never expires (the buyer is trusted), so one that stays
# unverified for this long is the realistic way seats and attention get hoarded.
STALE_CONFIRMED_DAYS = 3


@admin.register(EventSettings)
class EventSettingsAdmin(admin.ModelAdmin):
    """The singleton. Add and delete are blocked: the row is created by a data migration
    and save() pins pk=1, so a second one would silently do nothing."""

    readonly_fields = ('capacity_readout',)
    fieldsets = (
        ('Event', {
            'fields': ('event_name', 'event_date', 'venue', 'capacity',
                       'capacity_readout'),
        }),
        ('Payment destination', {
            'fields': ('ticket_price_cents', 'alias', 'cvu', 'account_holder_name'),
            'description': 'Shown to buyers on the pay screen. The API response is '
                           'authoritative -- a stale value baked into the frontend is '
                           'overwritten from here.',
        }),
        ('Revolut (payers abroad)', {
            'fields': ('revolut_tag', 'revolut_currency', 'revolut_price_cents'),
            'description': 'Optional second destination: one flat price per ticket in one '
                           'currency. Leave the tag blank to hide it. These payments land '
                           'in Revolut, so reconciliation means checking two statements.',
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


class GuestInline(admin.TabularInline):
    model = Guest
    extra = 0
    # Guest.clean() blocks adds that would push past capacity.


class SongRequestInline(admin.TabularInline):
    model = SongRequest
    extra = 0
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
        'reference', 'buyer_name', 'party_size', 'status', 'holds_seat',
        'amount_display', 'sender_account_name', 'confirmed_at', 'refund_state_display',
    )
    list_filter = ('status', RefundFilter, StaleConfirmedFilter)
    search_fields = (
        'reference', 'buyer_name', 'buyer_whatsapp', 'buyer_email',
        'sender_account_name', 'guests__full_name',
    )
    ordering = ('-confirmed_at', '-created_at')
    inlines = [GuestInline, SongRequestInline]
    readonly_fields = ('reference', 'created_at')
    actions = ('mark_verified', 'mark_refund_owed', 'mark_refunded', 'expire_stale_pending')

    fieldsets = (
        ('Buyer', {
            'fields': ('reference', 'buyer_name', 'buyer_whatsapp', 'buyer_email',
                       'sender_account_name'),
        }),
        ('Booking', {
            'fields': ('quantity', 'total_amount', 'status', 'created_at',
                       'confirmed_at', 'verified_at'),
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
        """Annotate the seat-holding predicate and guest count in SQL.

        Computing either per row would call EventSettings.load() once per booking. The
        cutoff is read once here and the same expiry predicate as seats_taken() is
        expressed as a CASE, so the whole changelist costs one query.
        """
        cutoff = EventSettings.load().pending_cutoff()
        return super().get_queryset(request).annotate(
            _guest_count=Count('guests', distinct=True),
            _holds_seat=Case(
                When(status__in=Booking.CONFIRMED_STATUSES, then=Value(True)),
                When(
                    Q(status=Booking.Status.PENDING) & Q(created_at__gt=cutoff),
                    then=Value(True),
                ),
                default=Value(False),
                output_field=BooleanField(),
            ),
        )

    @admin.display(description='Party', ordering='_guest_count')
    def party_size(self, obj):
        # Drift means someone is attending who was not paid for, or the reverse.
        if obj._guest_count != obj.quantity:
            return f'{obj._guest_count} (paid for {obj.quantity})'
        return str(obj._guest_count)

    @admin.display(description='Seat', boolean=True, ordering='_holds_seat')
    def holds_seat(self, obj):
        return obj._holds_seat

    @admin.display(description='Amount', ordering='total_amount')
    def amount_display(self, obj):
        return format_ars(obj.total_amount)

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
class GuestAdmin(admin.ModelAdmin):
    """Mostly edited inline on the booking. Registered separately so the venue list can
    be searched and sorted directly when checking a name."""

    list_display = ('full_name', 'booking_reference', 'booking_status')
    list_filter = ('booking__status',)
    search_fields = ('full_name', 'booking__reference', 'booking__buyer_name')
    list_select_related = ('booking',)

    @admin.display(description='Booking', ordering='booking__reference')
    def booking_reference(self, obj):
        return obj.booking.reference

    @admin.display(description='Status', ordering='booking__status')
    def booking_status(self, obj):
        return obj.booking.get_status_display()
