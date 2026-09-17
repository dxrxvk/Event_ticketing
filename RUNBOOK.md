# Event-day runbook

The organiser's checklist for running ticket sales by hand. Everything here happens in
the Django admin at `https://tickets-6cko.onrender.com/admin/` unless it says otherwise.
The public page is `https://event-ticketing.dhruxk.workers.dev`.

## The day before

- Set the real **capacity** and check **sales close at** in *Event settings*. Bookings
  are refused after that moment; pending ones from before it can still confirm.
- Make sure the seat count starts clean: *Bookings* should hold no test entries.
- If you want Revolut on the pay screen, fill **all three** fields in *Event settings →
  Revolut*: tag, currency, price in minor units (`500` = 5.00). The card stays hidden
  until all three are set.
- Send yourself `https://event-ticketing.dhruxk.workers.dev/?v=2` on WhatsApp and check
  the preview card (title, text, poster crop). Use the `?v=2` the first time; WhatsApp
  caches previews hard.

## Ten minutes before sharing the link

- Open the site once on your phone and wait until the availability badge appears. That
  wakes the backend (about 45 seconds) and the database, so the first guest does not
  pay the cold start.
- For the two or three busiest days only, a free uptime pinger (for example UptimeRobot)
  hitting `https://tickets-6cko.onrender.com/api/health/` every five minutes keeps both
  awake. Do not leave it on for the whole month: the database's free plan is 100
  compute-hours and an always-on database burns about six a day.

## While bookings come in (*Bookings*)

- A new booking arrives as **pending** and flips to **self-confirmed** when the buyer
  taps "I've sent the transfer". A pending booking that never confirms stops holding its
  seat after 45 minutes by itself; there is nothing to do.
- Reconcile against your bank app using the **Transfer from** column (the sender's name,
  which is often not the buyer) and the reference the buyer pasted into the note field.
  Tick people off with the **Mark verified** action. If Revolut is on, check that
  statement too.
- Seats can never exceed capacity. A load test with 40 simultaneous buyers landed on
  exactly 60 of 60.
- Song requests appear on each booking and under *Song requests* in the sidebar.

## What to expect if it gets busy

- Everyone sees the page instantly. Booking takes about a second, confirming under a
  second; under a real pile-on, about two seconds at worst.
- "Too many attempts from your network right now": the rate limit doing its job on a
  shared office or phone-network address. A minute's wait clears it. Confirming is never
  blocked by it.
- "Could not reach the server": the backend was asleep. The page's own wake-up ping has
  already fired, so "try again in a minute" is the honest answer.

## After the deadline (*Bookings → Exports*)

- **Venue list**: names only, as plain text (paste into WhatsApp) or CSV (opens in Excel
  with accents intact). Downloading it stamps the bookings as exported.
- **Organiser CSV**: contact details and status, named `INTERNO-` so it is hard to send
  to the venue by mistake.
- **Playlist**: one song per line, repeats removed, from confirmed bookings only.
- Delete the organiser CSV once reconciliation is done. It is personal data under
  Argentina's law 25.326.
