# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-event ticketing site for a private party (coworkers, Buenos Aires). No payment
integration: buyers transfer money to a personal bank alias and self-confirm on the site.
The system tracks headcount, enforces a guest-list cap, and exports CSVs.

`event_ticketing.md` is the authoritative build plan — goals, non-goals, data model,
business rules, API surface and a numbered build order. **Read it before writing code.**
It also lists explicit non-goals (no payment provider, no QR codes, no email/SMS, no
Docker/Celery/Redis, no buyer accounts). Don't add those.

## Current state

Backend is deployed on Render at `tickets-6cko.onrender.com` against Neon Postgres; the
frontend is deployed at `https://event-ticketing.dhruxk.workers.dev`. The page carries the
real event details and poster ("Multiculture Mixer", 3 Oct 2026); the start time in
`event.config.js` is a placeholder until confirmed.

- **Backend done:** models + migrations (incl. the seeded `EventSettings` singleton),
  admin for all three models, the four API endpoints, the venue and organiser exports,
  and 123 tests (`uv run python manage.py test tickets`). `tickets/tests.py` holds the
  business rules (including the price ladder and its own race class);
  `tickets/test_robustness.py` holds bursts, throttling, hostile input,
  admin edits mid-sale, rollback and contention. 11 tests skip on SQLite by design — see
  the capacity invariant below — and run for real on Postgres. **Run the Postgres-only
  classes against a local Postgres, not against Neon from afar:** the burst tests make
  hundreds of sequential requests inside one transaction, and at ~190ms per round trip
  from Buenos Aires to Oregon a four-test class takes ten minutes and Neon drops the
  connection (`server closed the connection unexpectedly`). That is the environment, not
  a bug. A killed run leaves `test_neondb` behind; `--noinput` replaces it.
- **Load script:** `scripts/loadtest.py` (stdlib only) fires concurrent requests at a
  running server and exits non-zero on any 5xx or oversell. Reads are safe against any
  URL; `--write` refuses non-local hosts unless `--allow-remote-writes` is passed.
- **Frontend done:** `frontend/` holds a Vite + Vue 3 SPA covering hero, form, pay screen,
  confirmed screen and the sold-out / closed / expired states. `npm run dev` proxies
  `/api` to Django on :8000, so CORS does not exist in development.
- **Deployed:** Render Blueprint from `render.yaml` (free plan, `build.sh`, WhiteNoise,
  gunicorn). The health check is `/admin/login/`; Render sends its own hostname as the
  `Host` header and `settings.py` reads it from `RENDER_EXTERNAL_HOSTNAME`, so
  `ALLOWED_HOSTS` on Render is only needed for a custom domain. `DATABASE_URL` on Render
  is Neon's **pooled** host (`-pooler` in the hostname). Render (Oregon) and Neon
  (`us-west-2`) are deliberately in the same region: with the database in São Paulo every
  query cost ~180ms and admin pages run dozens. gunicorn runs `--threads 4`; one sync
  worker made simultaneous requests queue into 502s.
- **Neon has two connection strings; use the right one.** Local `.env` must use the
  **direct** host: the test runner's `CREATE`/`DROP DATABASE` fails through the pooler
  ("being accessed by other users") and leaves a stray `test_neondb` behind. Leaving
  `DATABASE_URL` blank still gives SQLite for a fresh clone.
- **Frontend deployed** as a Cloudflare Worker with static assets (`event-ticketing`),
  Git-connected to `main`: root directory `frontend`, build `npm run build`, output
  `dist`, build-time env `VITE_API_BASE=https://tickets-6cko.onrender.com`. With the root
  directory unset the build finds `pyproject.toml`, runs `uv sync` and deploys nothing.
- `main.py` is an unused leftover from `uv init`.
- **CORS is already set** on Render: `CORS_ALLOWED_ORIGINS` =
  `https://event-ticketing.dhruxk.workers.dev`. A preflight from that origin comes back
  with a matching `Access-Control-Allow-Origin`, so the API is reachable from the Worker.
- **Still to do (pricing).** Migration `0004` seeds the two `PriceTier` rows (50 →
  7.000, 100 → 9.000) **and raises `capacity` to 130**, because the ladder only
  describes a 130-seat event. It raises capacity, never lowers it. Seeding the tiers
  without the capacity was tried and rejected: `price_ladder()` drops only a threshold
  at or *past* capacity, so at the old capacity of 60 the event would have sold seats
  51-60 at 7.000 with no third band and no extra seats — a half-applied ladder nobody
  asked for. After deploying, check the ladder readout on the event settings page reads
  `1-50 · 51-100 · 101-130`. If Revolut is in use, its price is the *first-band* figure
  and higher bands scale from it, so check that too.
- **Still to do:** `EventSettings` is filled (capacity 60, alias, holder, WhatsApp);
  Revolut is optional and off until its three fields are set. Confirm the start time
  (`event.config.js` says 21:00; the admin's `event_date` reads 12:00 local).
  `RUNBOOK.md` is the organiser's event-day checklist. The poster (`public/poster.webp`)
  and preview crop (`public/og.jpg`, 1200x630) are in. Keep `og.jpg` present: the Worker serves the SPA fallback for unknown paths, so a
  missing `/og.jpg` returns `200 text/html` instead of `404`, the preview silently has no
  image, and WhatsApp caches that result hard.

## Commands

Dependencies are managed with `uv` (lockfile: `uv.lock`). Prefix Django commands with
`uv run` — there is no need to activate `.venv` manually.

```sh
cp .env.example .env                  # first run only, then fill in SECRET_KEY
uv sync                               # install/refresh deps from the lockfile
uv add <package>                      # add a dependency (updates pyproject + lock)

uv run python manage.py runserver
uv run python manage.py makemigrations
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py shell

uv run python manage.py test                                  # all tests
uv run python manage.py test tickets                          # one app
uv run python manage.py test tickets.tests.BookingTests        # one class
uv run python manage.py test tickets.tests.BookingTests.test_x # one test
uv run python manage.py test tickets.test_robustness          # bursts, races, hostile input
uv run python scripts/loadtest.py --base-url https://tickets-6cko.onrender.com  # read-only load

uv run python manage.py check --deploy                        # pre-deploy audit (step 11)

make race                                                     # both race classes x10
```

There is no linter or formatter configured.

## Invariants that are easy to get wrong

These are the decisions the plan is emphatic about; breaking them silently breaks the
event.

- **Money is integer cents.** Never float.
- **The price is a published ladder, not a flat number and never a per-buyer amount.**
  Decided 2026-09-18, overriding §2's single price and §1's "no ticket tiers": 5.000 for
  the first 50 seats, 7.000 for the next 50, 9.000 for the last 30, with capacity 130.
  The rules that make a ladder safe here:
  - **The first band is `EventSettings.ticket_price_cents`; each further step is a
    `PriceTier` row** holding `starts_after_seats` (a threshold, so there is one number
    per step and nothing to reconcile) and `price_cents`. No tiers configured is exactly
    the old flat behaviour, and `PriceLadderTests.test_no_tiers_is_the_old_flat_price`
    guards that.
  - **Which band a booking gets is decided by `EventSettings.next_seat_position()`,** and
    that is deliberately the same count capacity is enforced on (`seats_taken()`). The Nth
    ticket sold must be the Nth ticket charged for. Nothing else computes a position.
    It is one method precisely so the seat rule can change without hunting: **if a
    `pending` booking ever stops holding a seat, position must still count live pending
    rows here**, or a launch burst (everyone submits before anyone confirms) quotes the
    whole room the cheapest band and the ladder never advances.
  - **The quote is frozen on the row at create** (`total_amount`, `price_breakdown`,
    `revolut_amount_cents`), inside the same lock that counted the seats. It is never
    re-derived at confirm: the buyer already read that figure off the pay screen and
    typed it into a bank. Editing the ladder later does not re-price anyone.
  - **A party straddling a boundary is split** — 2 × 5.000 + 2 × 7.000 — and both the form
    and the pay screen show the split, because "24.000" unexplained reads as a bug to
    someone who was told tickets cost 5.000.
  - **Still never per-buyer variation.** Two people buying the 51st and 52nd tickets pay
    the same. §5's `cents_code` and §6's "Unique cents" scheme stay **dropped**, including
    §6's `5.001,XX` fallback. A ladder is public and readable off the page; a centavo tag
    is a different amount for each person, which is what this project refuses.
- **Who paid is identified by three signals**, not by the amount: `reference` (shown to
  the buyer to paste into the transfer's *concepto* field, if their bank has one),
  `sender_account_name` (asked on the form, because the account holder is often not the
  buyer), and `confirmed_at` (time proximity to the credit). Reconciliation is an
  assisted-manual checklist, not an exact-amount matcher.
- **`self_confirmed` and `verified` are different states**, and neither is a boolean.
  `pending` is the only status that ever expires. `verified` is set by hand once the
  organiser sees the deposit in their statement.
- **Capacity is counted over `Guest` rows**, not bookings, across `pending`/`self_confirmed`/`verified`.
  Enforce inside `transaction.atomic()` with `select_for_update()` on the `EventSettings`
  row. **That lock is a silent no-op on SQLite** — Django omits `FOR UPDATE` from the SQL
  and raises nothing, even with `nowait=True` — so any concurrency test is meaningless
  until the database is Postgres.
- **Expiry is a query predicate, not a state mutation.** Count taken as
  `verified + self_confirmed + (pending AND created_at > now() - TTL)`. A sweep that only
  runs when requests arrive cannot free seats once "sold out" has stopped the traffic.
  The admin sweep action stays, but only as cosmetic status tidying.
- **Never accept `quantity` from the client** — derive it from `len(guests)`. Capacity is
  counted over Guest rows, so drift between the two sells seats that aren't paid for.
- **CSVs are written `utf-8-sig`** or Excel mangles every á, é and ñ. Sort on an
  accent-folded key too, or Álvarez and Ñuñez land after Zapata. Two separate exports: the
  venue gets names only, the organiser gets contact details and status.
- **`reference` is an unguessable short token** (≥64 bits), never the sequential PK.
- **CORS whitelists the exact frontend origin**, not `*`. Organiser views are
  `@staff_member_required` — the DRF default in this project is `AllowAny`.
- **Throttling needs `@throttle_scope(...)` on the view.** `ScopedRateThrottle` reads the
  scope from the view, not from the throttle class; without the decorator it lets
  everything through and raises nothing (production ran that way until 2026-09-17).
  Three buckets, per IP: `booking` 300/hour, `confirm` 300/hour (a buyer who has already
  transferred must never be the one told to wait) and `song_requests` 120/hour.
  `ThrottleTests` derives the limits from settings and asserts the request past each is
  a 429, so a regression fails loudly. The frontend maps 429 to a plain "too many
  attempts from your network" notice.

## Architecture

Backend and frontend are deliberately separate deployments:

- **Django + DRF on Render free tier.** Spins down after 15 min idle, ~50-60s cold start.
  The API is small and flat (`/api/health/`, `/api/availability/`, `POST /api/bookings/`,
  `POST /api/bookings/<reference>/confirm/`, `POST /api/bookings/<reference>/songs/`).
  `POST /api/bookings/` must return
  *everything* the pay screen needs in one response — no second round trip to a cold
  backend (`pay_screen_payload()` in `tickets/serializers.py`).
- **Booking rules live in `tickets/services.py`, not in views.** `create_booking()` and
  `confirm_booking()` hold the lock and the state machine; views only translate exceptions
  into responses. The race test drives the service directly with threads.
- **Error contract** — every state conflict is `409` with `{"error": <code>}`:
  `sold_out` (carries `remaining`), `sales_closed`, `booking_expired`,
  `booking_cancelled`, `booking_not_confirmed` (song requests before payment is
  confirmed). All but `sold_out` also carry `organiser_whatsapp` so the page can offer a
  human. Validation is `400`, unknown reference `404`.
- **Revolut rides the same ladder, on a second rail.** `EventSettings` holds
  `revolut_tag`, `revolut_currency` and `revolut_price_cents` (the price for the *first*
  band); the pay payload's `revolut` key is `null` until tag, currency and price are all
  set. Higher bands are scaled from the base pair with integer arithmetic, so a Revolut
  payer in the 9.000 band owes 9/5 of the base Revolut figure — otherwise the cheapest
  ticket at the door would be a foreign one bought last. The quote is frozen on
  `Booking.revolut_amount_cents` at create, like the peso one. The peso `total_amount` is
  separate and untouched. Reconciliation then means two statements; `verified_source` can
  say "revolut".
- **Song requests** (`SongRequest`, max three per booking) are stored as typed and only
  accepted for confirmed bookings. The admin shows them inline and exports a deduplicated
  playlist text under the guest-list exports. Any real-playlist sync is a layer on top of
  the stored row, never a replacement for it.
- **`/api/health/` runs `SELECT 1` on purpose.** §7 says it touches nothing, but Neon
  sleeps too — a ping that skips the database leaves Postgres cold for the first real
  query, which happens inside the locked transaction in `POST /bookings/`.
- **Vue 3 static SPA on Cloudflare** (`frontend/`), as a Worker with static assets, which
  is what Cloudflare's Create flow now defaults to; Pages would behave the same. Do **not**
  serve it from Django templates: the page must be readable before the backend wakes. The first thing the
  mounted hook does is ping `/api/health/` and discard the result, so the dyno wakes while
  the visitor types.
  - **No Pinia**, contra §3 — one composable (`src/composables/useBooking.js`) holds the
    whole flow. Four screens and one booking object do not need a store.
  - **Poster-first cover.** With `posterUrl` set, the page opens on the poster alone and
    a tap flips it (`EventHero.vue`) to the details (`EventDetails.vue`); the badge and
    form fade in below only then. `warmUp()` still fires on mount, so the backend wakes
    while people look at the poster. The cover is a local `ref` in `App.vue`, not booking
    state. With no poster there is no cover and no card.
  - **Event details are baked in** via `src/event.config.js` (name, date, venue,
    `priceTiers`, `posterUrl`, WhatsApp), because §10.1 requires the page to render before
    any API call. The baked ladder is a placeholder only: `/api/availability/` sends
    `tiers`, `next_seat` and `current_price_display`, and every component prefers those.
    Keep the two in step anyway — the baked one is what a visitor sees for the first
    second, and it under-quotes once the cheap band is gone. That is why the form says
    **"Estimated total"** and the server's `amount_display` is authoritative on the pay
    screen. `quoteFor()` in `event.config.js` mirrors `price_seats()` in `models.py`.
    The **payment destination is not** baked in — alias, CVU and account holder come from
    the API so a mistyped alias is an admin edit, not a redeploy.
  - **All colour and type live in `src/styles/tokens.css`.** Restyling to match the event
    poster means editing that one file. Tokens are named semantically (`--accent`,
    `--surface`), never literally, so a swap does not leave lying names.
  - **The poster is optional by design.** `posterUrl: ''` renders no poster block at all
    and the hero stands alone — the page must look finished without it.
  - **No copy button on the amount.** Argentine banking apps require the amount to be
    typed, so there is nothing to paste into. Copy buttons go on alias, CVU and reference.
- **Postgres on Neon**, via `dj-database-url`. Not Render's free Postgres, which is
  deleted after 30 days.
- **Django admin is the real back office** — the event should be runnable entirely by hand
  from it. Only two custom organiser views on top: reconciliation (a checklist of
  unverified bookings with sender name, party size and confirm time, ticked off against a
  bank statement) and the CSV/plain-text exports.

## Conventions

- **All UI copy is English.** This overrides §10's "Page must be Spanish (Argentina) —
  use `vos`, not `tú`" and the Spanish strings in §9 and §10.5. The **amount** is still
  displayed Argentine-style (`5.000`, dot as thousands separator) because it goes into an
  Argentine bank's amount field — do not "fix" it to `5,000` to match the English copy.
  `concepto` stays Spanish when referring to the bank field, since that is what the
  banking apps label it.
- OG tags (`og:title`, `og:description`, `og:image`, `og:url`, `og:type`, `twitter:card`)
  go directly in `index.html`. WhatsApp's crawler does not run JS, so anything set on
  mount is invisible to it.
- Personal data: names, WhatsApp numbers, optional emails. No DNI. Never commit
  production data or CSV exports (`*.csv` belongs in `.gitignore`). Argentina's law 25.326
  applies — contact columns get deleted after reconciliation.
