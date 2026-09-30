/* Regressions: each test pins a mistake this project has already made, or came close
 * to making, with the reason in the test. The backend's equivalents live in
 * tickets/tests.py and tickets/test_robustness.py; these are the frontend's. */

import { existsSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import BookingForm from '../src/components/BookingForm.vue'
import PayPanel from '../src/components/PayPanel.vue'
import PriceTiers from '../src/components/PriceTiers.vue'
import { event, eventDateParts, ladderFrom } from '../src/event.config.js'

const ROOT = join(import.meta.dirname, '..')

// Only the song-request test below talks to the API; vi.mock is hoisted to the top of
// the file regardless, so it lives here where that is visible.
vi.mock('../src/api.js', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, createBooking: vi.fn(), confirmBooking: vi.fn(), fetchAvailability: vi.fn() }
})

describe('link previews (WhatsApp)', () => {
  // The Worker serves the SPA fallback for unknown paths, so a missing /og.jpg answers
  // 200 text/html instead of 404. The preview silently has no image and WhatsApp
  // caches that result hard.
  it('ships public/og.jpg as a real image', () => {
    const path = join(ROOT, 'public', 'og.jpg')
    expect(existsSync(path)).toBe(true)
    const bytes = readFileSync(path)
    expect(bytes.subarray(0, 3)).toEqual(Buffer.from([0xff, 0xd8, 0xff])) // JPEG magic
    expect(statSync(path).size).toBeGreaterThan(10_000)
  })

  // WhatsApp's crawler does not run JS, so tags set on mount are invisible to it, and
  // it will not resolve a relative og:image.
  it('puts the OG tags in the static index.html with absolute URLs', () => {
    const html = readFileSync(join(ROOT, 'index.html'), 'utf8')
    for (const tag of ['og:title', 'og:description', 'og:image', 'og:url', 'og:type']) {
      expect(html, tag).toContain(`property="${tag}"`)
    }
    expect(html).toContain('name="twitter:card"')
    expect(html).toMatch(/property="og:image" content="https:\/\/[^"]+\/og\.jpg"/)
    expect(html).toMatch(/property="og:url" content="https:\/\/[^"]+"/)
  })
})

describe('event date', () => {
  // new Date('2026-10-03') is UTC midnight, which in Buenos Aires is still the evening
  // of the 2nd. The baked date is parsed at midday -03:00 so the page names the 3rd.
  it('names the 3rd, not the 2nd', () => {
    expect(eventDateParts(null).day).toBe('Saturday 3 October')
  })

  // No start time was ever decided, and a baked "21:00" would be read as fact by the
  // first person to open the link. The time comes from the admin or reads "TBD".
  it('bakes in a date with no invented hour', () => {
    expect(event.dateISO).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(eventDateParts(null).time).toBe('')
  })
})

describe('price ladder', () => {
  // The ladder prices off a high-water mark, not current occupancy. After abandoned
  // bookings the room can be empty while the price has moved on; boxes computed from
  // `taken` would advertise 5.000 while the booking endpoint charges 7.000.
  it('opens the band the server names, not the one occupancy implies', () => {
    const wrapper = mount(PriceTiers, {
      props: {
        availability: {
          taken: 0,
          remaining: 130,
          next_seat: 60,
          tiers: [
            { from_seat: 1, to_seat: 50, price_cents: 500000 },
            { from_seat: 51, to_seat: 100, price_cents: 700000 },
            { from_seat: 101, to_seat: 130, price_cents: 900000 },
          ],
        },
      },
    })
    const states = wrapper.findAll('.tier').map((tier) => tier.classes())
    expect(states[0]).toContain('tier--gone')
    expect(states[1]).toContain('tier--open')
  })

  // A centavo-priced tier used to come through as 5000.5, which renders "5.001" while
  // summing as 5000.5 -- the page contradicting itself.
  it('rounds a fractional-peso price to a whole number', () => {
    const [band] = ladderFrom({ tiers: [{ from_seat: 1, to_seat: 10, price_cents: 500050 }] })
    expect(band.price).toBe(5001)
    expect(Number.isInteger(band.price)).toBe(true)
  })
})

describe('booking form', () => {
  // DRF nests guest errors as { guests: { non_field_errors: [...] } }. With no input to
  // sit beside, the message vanished and the buyer kept tapping Reserve with no
  // feedback at all.
  it('surfaces nested validation errors in the summary', () => {
    const wrapper = mount(BookingForm, {
      props: {
        fieldErrors: {
          guests: { non_field_errors: ['Each guest needs a full name.'] },
          '0': [{ full_name: ['Too long.'] }],
        },
      },
    })
    const summary = wrapper.get('[role="alert"]').text()
    expect(summary).toContain('Each guest needs a full name.')
    expect(summary).toContain('Too long.')
  })
})

describe('Revolut note', () => {
  // The note is organiser-typed text from an admin textarea, rendered into every
  // buyer's browser. It must be interpolated, never v-html.
  it('renders HTML in the note as text', () => {
    const wrapper = mount(PayPanel, {
      props: {
        booking: {
          reference: 'R', quantity: 1, amount_display: '5.000', price_breakdown: [],
          alias: 'a',
          revolut: {
            tag: 'dhruvk', link: 'https://revolut.me/dhruvk',
            note: '<img src=x onerror="alert(1)"><b>bold</b>',
            currency: '', amount_cents: null, amount_display: '',
          },
        },
      },
    })
    const note = wrapper.get('.revolut__note')
    expect(note.find('img').exists()).toBe(false)
    expect(note.find('b').exists()).toBe(false)
    expect(note.text()).toBe('<img src=x onerror="alert(1)"><b>bold</b>')
  })
})

describe('song requests', () => {
  let api
  let flow

  beforeEach(async () => {
    vi.resetModules()
    api = await import('../src/api.js')
    api.fetchAvailability.mockResolvedValue(null)
    flow = (await import('../src/composables/useBooking.js')).useBooking()
  })

  // confirm() replaces `booking` with the server's copy. Songs stored on the booking
  // were wiped by it, so they live beside it instead.
  it('survive confirm replacing the booking', async () => {
    api.createBooking.mockResolvedValue({ reference: 'R', status: 'pending' })
    await flow.submit({
      buyerName: 'Ana', buyerWhatsapp: '1', buyerEmail: '', senderAccountName: '', guests: ['Ana'],
    })
    flow.songs.value = ['Soda Stereo – De música ligera', '', '']
    api.confirmBooking.mockResolvedValue({ reference: 'R', status: 'self_confirmed' })
    await flow.confirm()
    expect(flow.songs.value[0]).toBe('Soda Stereo – De música ligera')
  })
})
