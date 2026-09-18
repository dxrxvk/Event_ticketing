/* ============================================================================
   EVENT DETAILS -- fill these in.

   Baked into the build on purpose. event_ticketing.md S10.1 requires the page to be
   readable and useful before any API response arrives, so these cannot come from the
   backend: the Render dyno may be asleep when someone opens the link.

   The PAYMENT destination (alias, CVU, account holder) deliberately does NOT live here.
   It comes from the API, so a mistyped alias is an admin edit rather than a redeploy.
   ============================================================================ */

export const TIMEZONE = 'America/Argentina/Buenos_Aires'

export const event = {
  name: 'Multiculture Mixer',
  tagline: 'A night of global genres, sounds and vibes. Rep your flag, share your culture, make new friends.',

  // ISO 8601 with the Buenos Aires offset (-03:00).
  // Date is 03/10 off the poster, read day-first: 3 October 2026.
  // THE TIME IS A PLACEHOLDER -- the poster does not state one. Confirm 21:00.
  dateISO: '2026-10-03T21:00:00-03:00',

  venue: 'Venue Name',
  venueArea: 'Neighbourhood, Buenos Aires',

  // Display only, and only until /api/availability/ answers with the real ladder --
  // the API is the single source of truth for money, and an admin edit must not need a
  // redeploy. This copy exists because S10.1 requires a price on screen before any API
  // call returns, and a page that says nothing about price reads as a scam.
  //
  // `upTo` is the last seat at that price. Keep it in step with the PriceTier rows.
  priceTiers: [
    { upTo: 50, price: 5000 },
    { upTo: 100, price: 7000 },
    { upTo: 130, price: 9000 },
  ],

  // '' means no poster panel renders and the hero stands on its own -- the page is
  // designed to look finished without it.
  // public/poster.webp is the 6.1MB source PNG at 1000px wide, ~145KB. The separate
  // public/og.jpg is the WhatsApp preview crop and is referenced from index.html only.
  posterUrl: '/poster.webp',

  // Digits only, no + or spaces. Used for wa.me links on every dead end.
  organiserWhatsapp: '',

  // Shown under the form. Blank hides the line.
  listDeadlineNote: 'Bookings after the guest list goes to the venue may not make it.',
}

const dateFormatter = new Intl.DateTimeFormat('en-GB', {
  weekday: 'long', day: 'numeric', month: 'long', timeZone: TIMEZONE,
})
const timeFormatter = new Intl.DateTimeFormat('en-GB', {
  hour: '2-digit', minute: '2-digit', hour12: false, timeZone: TIMEZONE,
})

/** One stored date, formatted on the fly -- no second field to drift out of sync. */
export function eventDateParts() {
  const date = new Date(event.dateISO)
  if (Number.isNaN(date.valueOf())) return { day: '', time: '' }
  return { day: dateFormatter.format(date), time: timeFormatter.format(date) }
}

export function whatsappLink(message = '') {
  if (!event.organiserWhatsapp) return null
  const base = `https://wa.me/${event.organiserWhatsapp}`
  return message ? `${base}?text=${encodeURIComponent(message)}` : base
}

/** 5000 -> '5.000'. Argentine grouping, because the number is typed into an
 *  Argentine banking app. Mirrors format_ars() in tickets/money.py. */
export function formatPesos(amount) {
  return new Intl.NumberFormat('es-AR', { maximumFractionDigits: 0 }).format(amount)
}

/**
 * The baked ladder in the shape the API sends, so one quote function serves both.
 * Whole pesos here, integer cents there -- converted at this boundary and nowhere else.
 */
export function bakedLadder() {
  let from = 1
  return event.priceTiers.map((tier) => {
    const band = { fromSeat: from, toSeat: tier.upTo, price: tier.price }
    from = tier.upTo + 1
    return band
  })
}

/** The API's ladder, in the same shape. Returns null until availability arrives. */
export function ladderFrom(availability) {
  if (!availability?.tiers?.length) return null
  return availability.tiers.map((tier) => ({
    fromSeat: tier.from_seat,
    toSeat: tier.to_seat,
    price: tier.price_cents / 100,
  }))
}

/**
 * Price `quantity` seats starting at 1-based `nextSeat`, splitting across bands.
 *
 * Mirrors EventSettings.price_seats() in tickets/models.py -- deliberately, because the
 * form has to show a total before the server has quoted one. The server's number is the
 * one that counts; this is why the form calls its figure an estimate.
 */
export function quoteFor(quantity, ladder, nextSeat = 1) {
  const bands = ladder?.length ? ladder : bakedLadder()
  const lines = []
  let seat = nextSeat
  let left = quantity

  for (const band of bands) {
    if (left <= 0) break
    if (seat > band.toSeat) continue
    const take = Math.min(left, band.toSeat - seat + 1)
    lines.push({ quantity: take, price: band.price })
    seat += take
    left -= take
  }
  if (left > 0) {
    // Past the end of the ladder: the last price continues, same as the server.
    lines.push({ quantity: left, price: bands[bands.length - 1].price })
  }

  return {
    lines,
    total: lines.reduce((sum, line) => sum + line.quantity * line.price, 0),
  }
}
