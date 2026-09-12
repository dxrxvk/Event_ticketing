---
name: ticketing-review
description: Review the current diff, or a target (PR/branch/path), against this repo's specific invariants from CLAUDE.md and event_ticketing.md — money-as-cents, capacity locking, status semantics, the error contract, CSV encoding, and the plan overrides. Use after implementing a booking/model/API/frontend change and before committing, or whenever asked to check code against the build plan. Complements the generic /code-review skill; run both for anything touching tickets/services.py, tickets/models.py, tickets/views.py, or the booking flow in frontend/.
---

# Ticketing invariants review

This project has a build plan (`event_ticketing.md`) plus a set of decisions in
`CLAUDE.md` that **overrule parts of it**. Generic review catches bugs; this skill
catches the violations that are specific to this event and invisible to a linter —
things that look correct in isolation but contradict a decision made for this repo.

Read `CLAUDE.md` and `event_ticketing.md` fresh if they are not already in context.
**`CLAUDE.md` wins on every conflict.**

Scope the review to whatever changed: the current diff (`git diff`, or against the
target's base branch), or the specific files named. Don't re-review unrelated code.

## 0. Plan overrides — check these first

`event_ticketing.md` still contains designs that were explicitly rejected. Flag any
new or touched code that reintroduces:

- **`cents_code` / unique-cents / per-buyer amount variation** (plan §5, §6, §9,
  §10.5). `total_amount` must equal `TICKET_PRICE * quantity` and nothing else — no
  per-booking cents tag, no `5.001,XX` second-base-price fallback. Reconciliation
  identity comes from `reference` + `sender_account_name` + `confirmed_at` proximity,
  not from a unique amount.
- **Spanish UI copy** (plan §10's "use `vos`, not `tú`"). All copy is English. The
  exception: the transfer amount is still displayed Argentine-style (`5.000`, dot as
  thousands separator — don't "fix" it to `5,000`), and `concepto` stays Spanish
  because that's the bank's own field label.
- **Pinia** (plan §3). State lives in one composable
  (`frontend/src/composables/useBooking.js`), not a store.
- **Refunds settled purely out-of-band.** Cancellation should be tracked in-app and
  free the seat; money moving by hand is separate from state tracking in the app.

## 1. Money
- Any arithmetic on `total_amount`, price, or amounts is integer cents. Grep for
  `float(`, decimals, or division that could introduce fractional cents.
- `total_amount` has no per-buyer/per-booking variable component — flat price only.

## 2. Status semantics
- `pending`, `self_confirmed`, `verified` stay three distinct states, never collapsed
  into a boolean (e.g. no `is_paid` field standing in for the pair).
- Only `pending` ever expires. Nothing sets `self_confirmed` or `verified` back to
  `pending`, and nothing auto-expires a `self_confirmed`/`verified` row.
- `verified` is only ever set by a human action (admin), never by booking-flow code.

## 3. Capacity & concurrency
- `create_booking()`/`confirm_booking()` (in `tickets/services.py`, not in views) hold
  the lock and the state transition; views only translate exceptions to responses.
- Capacity check runs inside `transaction.atomic()` with
  `EventSettings.objects.select_for_update()`.
- Taken count is computed live over `Guest` rows filtered to
  `verified`/`self_confirmed`/(`pending` AND not expired) — never a stored/cached
  counter, and never `Booking.quantity` summed instead of `Guest` rows.
- `quantity` is never read from the request body — it's derived from `len(guests)`.
- If a race/concurrency test was added or touched, confirm it's noted as
  SQLite-meaningless (`select_for_update` is a silent no-op there) rather than treated
  as proof the lock works.

## 4. Expiry
- Expiry is a query predicate (`created_at > now() - TTL`) evaluated at read time, not
  a mutation that must have already run for capacity math to be correct. A sweep that
  only fires on request traffic must not be load-bearing for correctness once traffic
  stops.
- The admin sweep action, if present, is cosmetic tidying only — nothing depends on it
  having run.

## 5. API & error contract
- `reference` is generated as an unguessable token (≥64 bits), never the sequential PK
  or derived from it.
- Every state conflict returns `409` with `{"error": <code>}`. `sold_out` includes
  `remaining`; `sales_closed`, `booking_expired`, `booking_cancelled` include
  `organiser_whatsapp`. Validation errors are `400`; unknown reference is `404`.
- `POST /api/bookings/` response contains everything the pay screen needs — no code
  path makes a second request to the backend before rendering the pay screen.
- `GET /api/health/` actually runs a query (`SELECT 1`) — don't "simplify" it to a
  bare 200, that's what keeps Neon warm ahead of the locked transaction.

## 6. Access control
- Any new organiser/back-office view is `@staff_member_required` — this project's DRF
  default is `AllowAny`, so an unguarded view is open by default, not by mistake.
- CORS config lists exact frontend origin(s), never `*`.

## 7. CSV & personal data
- CSV writer opens with `utf-8-sig`, not plain `utf-8`.
- Sort key is accent-folded (so Álvarez/Ñuñez sort correctly), not a raw string sort.
- Venue export is names-only; organiser export carries contact + status. These stay
  two separate exports — check nothing merges them.
- No `*.csv` and no production data added to the repo; check `.gitignore` still covers
  it if export code changed paths.

## 8. Frontend specifics (if `frontend/` touched)
- Baked-in-at-build constants (`src/event.config.js`): name, date, venue, price,
  `posterUrl`, WhatsApp number. Payment destination (alias, CVU, account holder) comes
  from the API response, never hardcoded — a wrong alias must be fixable without a
  redeploy.
- No copy button on the amount itself (Argentine banking apps require it typed, not
  pasted). Copy buttons are fine on alias, CVU, reference.
- `posterUrl: ''` must still render a complete-looking hero with no poster block —
  don't leave a broken image or empty gap.
- OG tags (`og:title`, `og:description`, `og:image`, `og:url`, `og:type`,
  `twitter:card`) are hardcoded directly in `index.html`, not set from a mounted hook
  or a head-manager library — WhatsApp's crawler doesn't run JS.
- The warm-up ping to `/api/health/` on mount discards its result and never blocks
  render.

## 9. Non-goals
Reject, don't just flag, any new code implementing: a payment provider/webhook
integration, QR codes or a door check-in view, transactional email/SMS, buyer
accounts/login, or Docker/Celery/Redis. These are explicit non-goals in
`event_ticketing.md` §2 — if the diff adds one, say so and ask before proceeding
rather than reviewing it as if it belonged.

## Output

Report findings grouped by the numbered sections above, each with `file:line` and a
one-line fix. For a section with nothing to flag, say so in one line rather than
omitting it — omission reads as "not checked," not "clean."
