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
  name: 'Event Name',
  tagline: 'An evening with the people you already like.',

  // ISO 8601 with the Buenos Aires offset (-03:00).
  dateISO: '2026-09-26T21:00:00-03:00',

  venue: 'Venue Name',
  venueArea: 'Neighbourhood, Buenos Aires',

  // Display only. The amount actually charged comes from the API, which is the single
  // source of truth for money.
  pricePerTicket: 5000,

  // '' means no poster panel renders and the hero stands on its own -- the page is
  // designed to look finished without it. Drop the file into public/ and set this to
  // '/poster.jpg' when it arrives.
  posterUrl: '',

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
