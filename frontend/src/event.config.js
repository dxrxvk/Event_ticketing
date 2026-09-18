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

  // DATE ONLY, deliberately. 03/10 off the poster, read day-first: 3 October 2026. The
  // poster states no time and none has been decided, so no time is baked in -- a
  // made-up "21:00" here would be read as fact by the first person to open the link.
  // The real start time comes from the admin's `event_date` via /api/availability/
  // and overwrites this the moment it answers; until then the page says "TBD".
  dateISO: '2026-10-03',

  // The venue is still being decided, so this is what the page shows until the
  // organiser types one into the admin. It comes from /api/availability/ and
  // overwrites this on arrival -- like the alias and CVU, an edit must not need a
  // redeploy. Blank hides the area line entirely.
  venue: 'TBD',
  venueArea: '',

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

  // Digits only, no + or spaces. Used for wa.me links on every dead end. Same number
  // the API serves as organiser_whatsapp; baked in too so the links work before the
  // backend wakes.
  organiserWhatsapp: '447804472377',

  // Shown under the form. Blank hides the line.
  listDeadlineNote: 'Bookings after the guest list goes to the venue may not make it.',
}

const dateFormatter = new Intl.DateTimeFormat('en-GB', {
  weekday: 'long', day: 'numeric', month: 'long', timeZone: TIMEZONE,
})
const timeFormatter = new Intl.DateTimeFormat('en-GB', {
  hour: '2-digit', minute: '2-digit', hour12: false, timeZone: TIMEZONE,
})

/**
 * When the event is, preferring the admin's `event_date` once /api/availability/ has
 * answered. `time` is '' while no start time is known, and callers render "TBD" for
 * it -- never a guessed hour.
 *
 * The baked fallback is date-only. It is parsed at midday in the event's own offset
 * on purpose: `new Date('2026-10-03')` is UTC midnight, which in Buenos Aires (-03:00)
 * is still the evening of the 2nd, and the page would name the wrong day. Argentina
 * has no DST, so the fixed offset is safe.
 */
export function eventDateParts(availability = null) {
  const fromApi = availability?.event_date
  if (fromApi) {
    const date = new Date(fromApi)
    if (!Number.isNaN(date.valueOf())) {
      return { day: dateFormatter.format(date), time: timeFormatter.format(date) }
    }
  }
  const date = new Date(`${event.dateISO}T12:00:00-03:00`)
  if (Number.isNaN(date.valueOf())) return { day: '', time: '' }
  return { day: dateFormatter.format(date), time: '' }
}

/** Where the event is: the admin's venue once known, else the baked placeholder. */
export function venueDisplay(availability = null) {
  return availability?.venue?.trim() || event.venue
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
    // Cents to whole pesos, rounded rather than left fractional. The server is the
    // authority on money and works in integer cents; this side only ever displays an
    // estimate, and 5.000,50 would render as "5.001" while summing as 5000.5.
    price: Math.round(tier.price_cents / 100),
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

/**
 * Label each band of the ladder against the seat being sold next.
 *
 * Pure, and exported rather than living inside the component, so the boundaries can be
 * tested directly -- "is seat 50 still in the first band" is exactly the kind of
 * off-by-one that renders fine and prices wrong.
 *
 * `nextSeat` is 1-based and comes from the API, which derives it from a high-water
 * mark rather than current occupancy. Recomputing it from seats taken would let the
 * boxes advertise a band the booking endpoint will not honour.
 */
export function tierStates(ladder, nextSeat = 1, soldOut = false) {
  return ladder.map((band) => {
    const size = band.toSeat - band.fromSeat + 1

    let state = 'locked'
    if (soldOut || nextSeat > band.toSeat) state = 'gone'
    else if (nextSeat >= band.fromSeat) state = 'open'

    const sold = state === 'gone' ? size : state === 'open' ? nextSeat - band.fromSeat : 0

    return {
      ...band,
      size,
      state,
      sold,
      left: size - sold,
      percent: Math.round((sold / size) * 100),
    }
  })
}
