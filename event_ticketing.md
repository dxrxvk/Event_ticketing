# Event Ticketing App — Build Plan

Context document for Claude Code. Read this fully before writing code.

---

## 1. Goal

A small ticketing site for a single private event. Attendees are coworkers. There is
no payment integration: people transfer money to a personal alias (Argentina) and
self-confirm on the site. The system tracks who is coming, enforces a guest list cap,
and exports CSVs.

Two audiences:
- **Buyers**: tap a link shared in WhatsApp/Slack, fill a short form, see the exact
  amount to transfer, confirm they paid.
- **Organiser (me)**: see bookings, verify payments against a bank statement at
  leisure, export a guest list and send it to the venue on WhatsApp.

The venue has no access to this app. They receive a plain list of names by
`LIST_DEADLINE`. Door check-in is their problem, on paper.

## 2. Non-goals

Do not build these. If they seem like a good idea, they are not, for this event.

- No payment provider integration, webhooks, or Mercado Pago API.
- No ticket tiers or early bird pricing. One fixed price.
- No QR codes, scannable tickets, or door check-in view. The venue works off a
  name list sent over WhatsApp.
- No transactional email or SMS. Confirmation is handled manually over WhatsApp.
- No user accounts, signup, or password reset for buyers.
- No admin UI beyond Django admin plus two small custom views.
- No Docker, no Celery, no Redis, no message queue.

## 3. Stack

| Layer | Choice | Notes |
|---|---|---|
| Backend | Django + Django REST Framework | |
| Database | Postgres on Neon (free tier) | Free tier persists indefinitely. Do **not** use Render's free Postgres: it is deleted 30 days after creation. |
| Backend hosting | Render free web service | Spins down after 15 min idle; cold start ~50-60s. |
| Frontend | Vue 3 + Pinia, static SPA | |
| Frontend hosting | Cloudflare Pages | Free, instant, HTTPS, custom domain. |

Config via environment variables and `dj-database-url`. Never commit secrets.

**Do not serve the frontend from Django templates.** The static page must load instantly
even when the backend is asleep. That separation is deliberate.

## 4. Constants to fill in

```
EVENT_NAME       = "TBD"
EVENT_DATE       = "TBD"
EVENT_VENUE      = "TBD"
TICKET_PRICE_ARS = 5000        # store as integer cents: 500000
CAPACITY         = TBD         # hard cap on total guests
ALIAS            = "TBD"       # shown to buyers, not stored in repo if avoidable
PENDING_TTL      = 45 minutes
LIST_DEADLINE    = "TBD"       # when the name list goes to the venue; show it on the page
```

## 5. Data model

Two models. Money stored as **integer cents**, never float.

### Booking
- `buyer_name` — char
- `buyer_whatsapp` — char
- `buyer_email` — email, optional
- `quantity` — positive int
- `cents_code` — int, 1-99, unique among non-expired bookings
- `total_amount` — int cents = (TICKET_PRICE * quantity) + cents_code
- `status` — choices: `pending`, `self_confirmed`, `verified`, `expired`, `cancelled`
- `created_at`, `confirmed_at`, `verified_at`
- `notes` — text, blank, organiser use

### Guest
- `booking` — FK to Booking, related_name `guests`
- `full_name` — char

One Guest row per attending human. A booking of 3 creates 3 Guest rows. Guest rows
are what the CSV is built from, so headcount and export always agree. No DNI, no
arrival tracking: the venue confirmed they only need names.

### Status semantics — important

- `pending` — form submitted, buyer has not yet tapped "Ya transferí". Lasts ~90
  seconds in normal use. **Only this status ever expires.**
- `self_confirmed` — buyer says they transferred. Trusted. **Never auto-expires.**
- `verified` — organiser matched the deposit in their bank statement. Set manually
  in admin, possibly never for some rows.

Do not collapse `self_confirmed` and `verified` into a boolean. The distinction between
"they say they paid" and "I saw the money" is the whole point of the audit trail.

## 6. Business rules

### Unique cents
Each booking gets `cents_code` 1-99. Buyer 23 buying one ticket owes `5.000,23`.
This is **not** a payment gate — buyers self-confirm and are trusted. It exists so that
after the event the organiser can match deposits to buyers, because the sender name on
an Argentine transfer is the account holder and frequently differs from the buyer.

- Must be unique across all bookings not in `expired`/`cancelled` status.
- If no code is free (>99 live bookings), **raise loudly**. Do not silently reuse.
  Fallback if it ever triggers: add a second base price, e.g. 5.001,XX.

### Capacity
Cap is on total Guest rows across bookings in `pending`, `self_confirmed`, `verified`.

Enforce with a count-on-read inside a transaction, guarded by a row lock:

```python
with transaction.atomic():
    settings = EventSettings.objects.select_for_update().get(pk=1)
    taken = Guest.objects.filter(
        booking__status__in=LIVE_STATUSES
    ).count()
    if taken + quantity > settings.capacity:
        raise CapacityError
    # ... create booking + guests
```

The `select_for_update` is required. Two coworkers grabbing the last two seats at the
same instant is a real scenario. Write a test that fires two concurrent requests and
asserts exactly one succeeds.

### Expiry
Sweep `pending` bookings older than `PENDING_TTL` to `expired`, freeing their seats.

Render's free tier has no cron. Implement **lazy expiry**: run the sweep at the start
of any request that reads or consumes capacity. Also expose it as a Django admin action
for manual runs. Do not add an external scheduler.

If a buyer returns to a dead booking and resubmits: if capacity allows, let them through
and expire the old row. If the event is full, return a clear "sold out" response.

## 7. API

Small and flat. All JSON.

- `GET /api/health/` — returns `{"ok": true}`. Touches nothing. Exists solely so the
  frontend can wake the sleeping dyno on page load.
- `GET /api/availability/` — returns `{"capacity": n, "taken": n, "remaining": n}`.
  Runs the lazy expiry sweep first.
- `POST /api/bookings/` — body: buyer details, quantity, list of `{full_name}`.
  Returns `{reference, total_amount, amount_display, alias, status}`. The response must
  contain **everything** the pay screen needs — no second round trip to a cold backend.
- `POST /api/bookings/<reference>/confirm/` — flips `pending` → `self_confirmed`, sets
  `confirmed_at`. Idempotent.

CORS: use `django-cors-headers`, whitelist the exact frontend origin. Not `*`.

`reference` should be an unguessable short token, not a sequential PK.

## 8. Organiser tooling

### Django admin
Register both models with useful `list_display`, `list_filter` on status, and search on
buyer name / guest name. Inline Guests on Booking. Admin is the back office —
the whole event could be run by hand from here if the frontend broke.

### Reconciliation view
A page with a large textarea. Organiser pastes deposit amounts from home banking,
submits. It parses amounts, matches each to a booking by exact `total_amount`, flips
matched ones to `verified`. It then **shows both lists**: what matched, and what did
not. Unmatched deposits are normal (people round the cents) and get resolved by hand.

## 9. CSV exports

Two separate exports. Do not merge them — the venue should not receive contact details
and internal status.

**Venue list**: `Nombre y Apellido`. Alphabetical by surname. Nothing else. This is
what gets pasted into WhatsApp, so also offer it as plain text, one name per line.

**Organiser list**: guest name, buyer name, buyer WhatsApp, status, amount owed,
created date.

One implementation detail that will otherwise cause a support call:
- Write **UTF-8 with BOM** (`utf-8-sig`) or Excel mangles every á, é and ñ.

Add `*.csv` to `.gitignore`.

## 10. Frontend

Single page, mobile-first, designed at 375px. Most visitors arrive via WhatsApp's
in-app browser.

1. **Static content is static.** Event name, date, venue, price and alias are hardcoded
   or baked in at build time. The page must be fully readable and useful before any API
   response arrives.
2. **Warm-up on mount.** First action in the app's mounted hook: call `/api/health/` and
   ignore the result. The visitor reads and types for 30-60s while the dyno wakes, so
   it is awake by the time they submit.
3. **Availability loads async** and fills in after. Never block rendering on it.
4. **Form**: buyer contact details once, then a repeatable `full_name` field, one per
   attendee. Prefill the first from the buyer's own name. Show `LIST_DEADLINE` here:
   bookings after it may not make the venue list.
5. **Pay screen** — the most important screen. Shows:
   - alias, with a copy button
   - the exact amount including cents, large and bold: `5.000,23`
   - a copy button for the amount (retyping is where people round it off)
   - one loud line: *"Transferí el monto exacto, incluidos los centavos."*
   - a prominent **"Ya transferí"** button
   - the booking reference
6. **Confirmed screen**: short, warm, tells them they're on the list.

### OG tags — get these right before sharing the link anywhere
Put `og:title`, `og:description`, `og:image`, `og:url`, `og:type` and `twitter:card`
**directly in `index.html`**. WhatsApp's crawler fetches raw HTML and does not run
JavaScript, so a head-manager library or anything set on mount will not work. The image
must be an absolute URL, ~1200x630. WhatsApp caches previews aggressively, so test
against a throwaway path first.

Page must be Spanish (Argentina). Use `vos`, not `tú`.

## 11. Build order

Do not reorder. Each step assumes the previous one works.

1. Django project + `tickets` app, DRF, CORS, dotenv. Confirm it runs.
2. `Booking`, `Guest`, `EventSettings` models. Migrate. Inspect the tables.
3. Register in Django admin. **Checkpoint: the event could now be run entirely by hand.**
4. `cents_code` assignment + uniqueness + loud failure past 99. Unit test it.
5. `POST /api/bookings/` with the capacity lock. **Test with two concurrent requests.**
6. `POST /confirm/`, `GET /availability/`, `GET /health/`.
7. Lazy expiry sweep + admin action.
8. Reconciliation view.
9. Both CSV exports and the plain-text name list. Open the CSVs in Excel and verify accents.
10. Move DB to Neon. Migrate. Local Django now talks to cloud Postgres.
11. Deploy backend to Render. Hit `/admin` on the live URL and log in.
12. Vue app: static shell → warm-up ping → OG tags → form → pay screen.
13. Deploy to Cloudflare Pages. Fix CORS.
14. **End-to-end test with real money**: send yourself 10 pesos with a cents pattern,
    run reconciliation, watch it match.
15. Build a Google Form fallback and leave it unused. Ten minutes. Insurance.

## 12. Personal data

The venue confirmed they need names only, so no DNI is collected. What remains is
names, WhatsApp numbers and optional emails of coworkers.

- Never commit production data or CSV exports to the repo.
- Environment variables only for anything sensitive.
- **After the event, delete the buyer contact columns** or the whole database once
  reconciliation is done. There is no reason to hold coworkers' phone numbers
  indefinitely, and Argentina's personal data law (25.326) still applies.

## 13. Known rough edges

Accept these, do not engineer around them.

- ~5-10% of transfers will have rounded cents and need manual matching. The
  reconciliation view surfaces them; that is the whole mitigation.
- Group buyers often do not know their guests' full names at purchase time. Allow
  editing Guest rows in admin after the fact.
- Render cold start is real. The warm-up ping mitigates it. If it still bites during the
  sales window, pay $7 for one month of an always-on instance.
- Free Neon Postgres sleeps when idle and wakes in a second or two. Fine.

## 14. Open question

Capacity vs audience size. If the cap is well below the number of people who can see the
link, the concurrency locking and expiry logic matter a lot and there should also be a
clean "sold out" state and a waitlist. If the cap is close to the audience size, both are
a nicety. Confirm the two numbers before spending time on step 5's edge cases.
