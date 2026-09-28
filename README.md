# Multiculture Mixer — Ticketing

A single-event ticketing site for a private party (coworkers, Buenos Aires, 3 Oct 2026).
No payment provider: buyers transfer to a personal bank alias and self-confirm on the
site. The system tracks headcount against a guest-list cap and exports CSVs for the
venue and the organiser.

Full build plan, data model, and business rules: [`event_ticketing.md`](event_ticketing.md).
Day-of-event checklist: [`RUNBOOK.md`](RUNBOOK.md). Instructions for AI coding agents
working in this repo: [`CLAUDE.md`](CLAUDE.md).

## Stack

- **Backend:** Django + Django REST Framework, deployed on Render (free tier)
- **Database:** Postgres on Neon
- **Frontend:** Vue 3 SPA (Vite), deployed as a Cloudflare Worker
- **Dependencies:** managed with [`uv`](https://docs.astral.sh/uv/)

Backend and frontend are separate deployments with no server-side rendering — the
frontend must be readable before the backend (which sleeps after 15 min idle) wakes up.

## Live

- Frontend: https://event-ticketing.dhruxk.workers.dev
- Backend API: https://tickets-6cko.onrender.com

## Setup

```sh
cp .env.example .env      # fill in SECRET_KEY and DATABASE_URL
uv sync                   # install dependencies from uv.lock

uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Frontend:

```sh
cd frontend
npm install
npm run dev                # proxies /api to Django on :8000
```

Leaving `DATABASE_URL` blank falls back to SQLite. **Note:** Neon has two connection
strings — local development must use the **direct** host, not the pooled one, or the
test runner's `CREATE`/`DROP DATABASE` calls fail.

## Commands

```sh
uv run python manage.py test tickets              # business rules
uv run python manage.py test tickets.test_robustness  # bursts, races, hostile input
make race                                         # both race classes x10
uv run python scripts/loadtest.py --base-url <url>    # read-only load test
uv run python manage.py check --deploy            # pre-deploy audit
```

Postgres-only concurrency tests must run against a **local** Postgres, not Neon — the
burst tests make hundreds of sequential requests inside one transaction, and at
~190ms per round trip from Buenos Aires to Oregon a four-test class takes ten minutes
and Neon drops the connection.

## API

| Endpoint | Method |
|---|---|
| `/api/health/` | GET |
| `/api/availability/` | GET |
| `/api/bookings/` | POST |
| `/api/bookings/<reference>/confirm/` | POST |
| `/api/bookings/<reference>/songs/` | POST |

Every state conflict is a `409` with `{"error": <code>}` — `sold_out`, `sales_closed`,
`booking_expired`, `booking_cancelled`, `booking_not_confirmed`. Validation is `400`,
unknown reference is `404`. `POST /api/bookings/` returns everything the pay screen
needs in one response, because there is no second round trip to a cold backend.

## Design decisions

The plan in `event_ticketing.md` was written first and the build then deliberately
overrode parts of it as real constraints showed up. The overrides that shape the code
most:

- **Price is a public ladder, not a flat number or a per-buyer amount.** The original
  plan had one flat price (and, at one point, a scheme where each buyer's total ended
  in unique cents so a bank transfer could be matched automatically). Both were
  dropped: unique-cents amounts are a different price per person, which reads as
  exactly the kind of thing this project exists to avoid, and a flat price stops
  working once the guest cap grows to 130 across three pricing bands (5.000 / 7.000 /
  9.000). Who paid what is now identified by three signals instead — an unguessable
  `reference` token, the `sender_account_name` typed on the form, and time proximity of
  `confirmed_at` — treated as an assisted-manual checklist, not an exact-amount match.
- **Booking logic lives in `tickets/services.py`, not in views.** `create_booking()`
  and `confirm_booking()` hold the lock and the state machine end to end; views only
  translate exceptions into HTTP responses. This is what lets the race tests drive the
  service directly with threads instead of going through HTTP.
- **`self_confirmed` and `verified` are different states, and neither is a boolean.**
  A buyer saying "I paid" and the organiser seeing the deposit land are different
  events that happen at different times (sometimes never, for a no-show). Collapsing
  them into one `paid` flag would lose which one actually happened.
- **Expiry is a query predicate, not a state mutation.** A `pending` booking is
  counted as expired by comparing `created_at` against now at read time, not by a
  background job flipping its status. A sweep that only runs when a request arrives
  can't free seats once "sold out" has already stopped traffic from arriving — so
  there's nothing to wake it up.
- **No Pinia.** The frontend plan called for it, but four screens sharing one booking
  object don't need a store; a single composable (`src/composables/useBooking.js`)
  does the job with less indirection.
- **Frontend and backend are separate deployments, not server-rendered.** The Worker
  serves static assets so the page (and the poster) render before the Render dyno,
  which can be cold for up to a minute, wakes up. `warmUp()` pings `/api/health/` on
  mount and discards the result, so the backend is waking up while the visitor is
  still reading.
- **CSV exports are two separate files**, not one. The venue gets a names-only list;
  the organiser gets one with contact details and status. Contact columns get deleted
  after reconciliation to satisfy Argentina's data protection law (25.326), which
  wouldn't be possible if the venue's copy also carried that data.

## Concurrency

This is the part of the system most likely to fail silently, so it's worth being
explicit about the model.

**The lock.** Capacity is enforced inside `transaction.atomic()` with
`select_for_update()` on the single `EventSettings` row. Every booking attempt
serializes on that one row: two requests racing to take the last seat both try to lock
it, one waits, and by the time it gets the lock it re-reads occupancy under the lock
and sees the seat is gone. There's no separate seat-counter table or optimistic
retry — one row, one lock, one place the count is decided.

**What "taken" means.** Capacity is counted over `Guest` rows (never `quantity` from
the client — that's derived server-side as `len(guests)`), across `pending`,
`self_confirmed`, and `verified` bookings, with `pending` further filtered to
`created_at > now() - TTL`. So the occupancy check and the expiry rule are the same
query, evaluated fresh on every booking attempt, rather than two separate mechanisms
that could disagree.

**Pricing under the same lock, but with its own ratchet.** Which price band a booking
lands in is decided by `EventSettings.price_position()`, computed under the same lock
as the capacity check, using live pending rows — otherwise a launch burst where
everyone submits before anyone confirms would quote the entire room the cheapest band.
But price and capacity are deliberately *not* symmetric: capacity is enforced on
current occupancy, while price is pinned to a high-water mark
(`EventSettings.seats_high_water`) that only ever advances. If it tracked current
occupancy instead, a cancellation would free a seat *and* roll the price back — 50
people fill the form at launch, a chunk abandon before paying, and a later buyer gets
quoted the cheapest band again, long after the first 50 seats were meant to be sold
at that price. The seat is freed; the price it was sold at is not. The one
deliberate way to move the ladder back down is an admin editing
`seats_high_water` by hand.

**Pricing never blocks a sale.** If churn pushes the position past capacity, pricing
just keeps quoting the last band rather than raising an error — only the occupancy
check is allowed to refuse a booking. Price and capacity can disagree in exactly one
direction (price can lag reality), and that's intentional.

**The quote is frozen, not recomputed.** `total_amount`, `price_breakdown`, and
`revolut_amount_cents` are written to the row once, inside the same locked
transaction that counted the seats, because the buyer reads that figure off the pay
screen before typing it into their bank. Editing the ladder afterwards in the admin
never re-prices anyone who already has a booking.

**SQLite silently can't do any of this.** Django omits `FOR UPDATE` from the SQL on
SQLite and raises nothing, even with `nowait=True` — so a concurrency test against
SQLite proves nothing about correctness. The Postgres-only test classes exist for this
reason and must be run against a real Postgres (`tickets/test_robustness.py`, plus a
dedicated race class in `tickets/tests.py`, both driven with real threads, not
`unittest.mock`).
