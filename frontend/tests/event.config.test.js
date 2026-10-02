import { describe, expect, it } from 'vitest'

import {
  bakedLadder,
  event,
  eventDateParts,
  formatPesos,
  ladderFrom,
  quoteFor,
  tierStates,
  venueDisplay,
  whatsappLink,
} from '../src/event.config.js'

// The seeded three-step ladder in the shape ladderFrom() produces: 1-50 at 5.000,
// 51-100 at 7.000, 101-130 at 9.000. A fixture, not the baked copy, which follows the
// admin and may be a single flat band.
const LADDER = [
  { fromSeat: 1, toSeat: 50, price: 5000 },
  { fromSeat: 51, toSeat: 100, price: 7000 },
  { fromSeat: 101, toSeat: 130, price: 9000 },
]

describe('formatPesos', () => {
  it('groups thousands with a dot, Argentine style', () => {
    expect(formatPesos(5000)).toBe('5.000')
    expect(formatPesos(24000)).toBe('24.000')
    expect(formatPesos(1170000)).toBe('1.170.000')
  })

  it('leaves small amounts ungrouped', () => {
    expect(formatPesos(0)).toBe('0')
    expect(formatPesos(900)).toBe('900')
  })
})

describe('bakedLadder', () => {
  it('turns upTo thresholds into inclusive 1-based ranges', () => {
    const saved = event.priceTiers
    event.priceTiers = [{ upTo: 50, price: 5000 }, { upTo: 100, price: 7000 }, { upTo: 130, price: 9000 }]
    try {
      expect(bakedLadder()).toEqual(LADDER)
    } finally {
      event.priceTiers = saved
    }
  })
})

describe('ladderFrom', () => {
  it('is null until availability carries tiers', () => {
    expect(ladderFrom(null)).toBeNull()
    expect(ladderFrom({})).toBeNull()
    expect(ladderFrom({ tiers: [] })).toBeNull()
  })

  it('converts the API shape from cents to whole pesos', () => {
    const availability = {
      tiers: [
        { from_seat: 1, to_seat: 50, price_cents: 500000 },
        { from_seat: 51, to_seat: 130, price_cents: 700000 },
      ],
    }
    expect(ladderFrom(availability)).toEqual([
      { fromSeat: 1, toSeat: 50, price: 5000 },
      { fromSeat: 51, toSeat: 130, price: 7000 },
    ])
  })
})

describe('quoteFor', () => {
  it('prices a party inside one band as a single line', () => {
    expect(quoteFor(3, LADDER, 1)).toEqual({
      lines: [{ quantity: 3, price: 5000 }],
      total: 15000,
    })
  })

  it('keeps seat 50 in the first band', () => {
    expect(quoteFor(1, LADDER, 50).total).toBe(5000)
  })

  it('prices seat 51 in the second band', () => {
    expect(quoteFor(1, LADDER, 51).total).toBe(7000)
  })

  it('splits a party that straddles a step', () => {
    // Seats 49-52: two at 5.000, two at 7.000 -- the "24.000 for four" case.
    expect(quoteFor(4, LADDER, 49)).toEqual({
      lines: [{ quantity: 2, price: 5000 }, { quantity: 2, price: 7000 }],
      total: 24000,
    })
  })

  it('splits across all three bands when a party is wide enough', () => {
    const tiny = [
      { fromSeat: 1, toSeat: 2, price: 5000 },
      { fromSeat: 3, toSeat: 4, price: 7000 },
      { fromSeat: 5, toSeat: 6, price: 9000 },
    ]
    expect(quoteFor(5, tiny, 2).lines).toEqual([
      { quantity: 1, price: 5000 },
      { quantity: 2, price: 7000 },
      { quantity: 2, price: 9000 },
    ])
  })

  it('keeps quoting the last price past the end of the ladder, never refusing', () => {
    expect(quoteFor(2, LADDER, 130)).toEqual({
      lines: [{ quantity: 1, price: 9000 }, { quantity: 1, price: 9000 }],
      total: 18000,
    })
    expect(quoteFor(3, LADDER, 200).total).toBe(27000)
  })

  it('falls back to the baked ladder when given none', () => {
    expect(quoteFor(2, null, 1)).toEqual(quoteFor(2, bakedLadder(), 1))
    expect(quoteFor(2, [], 30)).toEqual(quoteFor(2, bakedLadder(), 30))
  })

  it('defaults to the first seat', () => {
    expect(quoteFor(1, LADDER).total).toBe(5000)
  })
})

describe('tierStates', () => {
  const states = (nextSeat, soldOut = false) =>
    tierStates(LADDER, nextSeat, soldOut).map((tier) => tier.state)

  it('opens the first band and locks the rest at launch', () => {
    expect(states(1)).toEqual(['open', 'locked', 'locked'])
  })

  it('keeps the first band open on its last seat', () => {
    expect(states(50)).toEqual(['open', 'locked', 'locked'])
  })

  it('moves to the second band at seat 51', () => {
    expect(states(51)).toEqual(['gone', 'open', 'locked'])
  })

  it('marks every band gone when sold out, whatever the seat', () => {
    expect(states(10, true)).toEqual(['gone', 'gone', 'gone'])
  })

  it('marks every band gone once the position passes the ladder', () => {
    expect(states(131)).toEqual(['gone', 'gone', 'gone'])
  })

  it('counts sold and left within the open band', () => {
    const [first, second] = tierStates(LADDER, 26)
    expect(first).toMatchObject({ size: 50, sold: 25, left: 25, percent: 50 })
    expect(second).toMatchObject({ size: 50, sold: 0, left: 50, percent: 0 })
  })

  it('counts a gone band as fully sold', () => {
    const [first] = tierStates(LADDER, 60)
    expect(first).toMatchObject({ sold: 50, left: 0, percent: 100 })
  })
})

describe('eventDateParts', () => {
  it('uses the baked date with no time until the API answers', () => {
    expect(eventDateParts(null)).toEqual({ day: 'Friday 9 October', time: '' })
  })

  it('prefers the admin date and time, shown in Buenos Aires time', () => {
    // 01:30 UTC on the 10th is 22:30 on the 9th in Buenos Aires.
    expect(eventDateParts({ event_date: '2026-10-10T01:30:00Z' })).toEqual({
      day: 'Friday 9 October',
      time: '22:30',
    })
  })

  it('falls back to the baked date when the API date is blank or garbage', () => {
    expect(eventDateParts({ event_date: null }).time).toBe('')
    expect(eventDateParts({ event_date: 'not a date' })).toEqual({
      day: 'Friday 9 October',
      time: '',
    })
  })
})

describe('venueDisplay', () => {
  it('shows the baked placeholder until a venue is set', () => {
    expect(venueDisplay(null)).toBe(event.venue)
    expect(venueDisplay({ venue: '' })).toBe(event.venue)
    expect(venueDisplay({ venue: '   ' })).toBe(event.venue)
  })

  it('shows the admin venue, trimmed', () => {
    expect(venueDisplay({ venue: '  Niceto Club ' })).toBe('Niceto Club')
  })
})

describe('whatsappLink', () => {
  it('links to the organiser, URL-encoding any message', () => {
    expect(whatsappLink()).toBe(`https://wa.me/${event.organiserWhatsapp}`)
    expect(whatsappLink('Hi & bye?')).toBe(
      `https://wa.me/${event.organiserWhatsapp}?text=Hi%20%26%20bye%3F`,
    )
  })
})
