import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api.js'

// The composable keeps its state at module level on purpose (one flow per page), so
// every test re-imports it after vi.resetModules() to start from a clean slate. The API
// is mocked at the module boundary: these tests are about the state machine, not fetch.
vi.mock('../src/api.js', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ApiError: actual.ApiError,
    fetchAvailability: vi.fn(),
    createBooking: vi.fn(),
    confirmBooking: vi.fn(),
    saveSongRequests: vi.fn(),
    warmUp: vi.fn(),
  }
})

let api
let flow

const FORM = {
  buyerName: '  Ana López ',
  buyerWhatsapp: ' +54 9 11 5555 5555 ',
  buyerEmail: '',
  senderAccountName: ' Juan López ',
  guests: [' Ana López ', 'Bea Núñez'],
}

const BOOKING = { reference: 'REF123', status: 'pending', quantity: 2 }

beforeEach(async () => {
  vi.resetModules()
  api = await import('../src/api.js')
  vi.mocked(api.fetchAvailability).mockReset().mockResolvedValue({ sold_out: false, sales_open: true })
  vi.mocked(api.createBooking).mockReset()
  vi.mocked(api.confirmBooking).mockReset()
  vi.mocked(api.saveSongRequests).mockReset()
  const { useBooking } = await import('../src/composables/useBooking.js')
  flow = useBooking()
})

describe('availability', () => {
  it('derives soldOut, salesClosed and canBook', async () => {
    expect(flow.canBook.value).toBe(true) // nothing loaded yet never blocks the form

    api.fetchAvailability.mockResolvedValue({ sold_out: true, sales_open: true })
    await flow.loadAvailability()
    expect(flow.soldOut.value).toBe(true)
    expect(flow.canBook.value).toBe(false)

    api.fetchAvailability.mockResolvedValue({ sold_out: false, sales_open: false })
    await flow.loadAvailability()
    expect(flow.salesClosed.value).toBe(true)
    expect(flow.canBook.value).toBe(false)
  })

  it('is silent when availability fails to load', async () => {
    api.fetchAvailability.mockRejectedValue(new ApiError('network', 'down'))
    await flow.loadAvailability()
    expect(flow.availability.value).toBeNull()
    expect(flow.notice.value).toBeNull()
    expect(flow.canBook.value).toBe(true)
  })
})

describe('availability retry', () => {
  let delays

  beforeEach(async () => {
    vi.useFakeTimers()
    ;({ AVAILABILITY_RETRY_DELAYS: delays } = await import('../src/composables/useBooking.js'))
  })
  afterEach(() => vi.useRealTimers())

  // One failed request during a cold start used to leave the page on the baked ladder
  // for as long as the tab stayed open.
  it('retries after a failure until the API answers', async () => {
    const live = { sold_out: false, sales_open: true, tiers: [{ from_seat: 1, to_seat: 60 }] }
    api.fetchAvailability
      .mockRejectedValueOnce(new ApiError('network', 'down'))
      .mockRejectedValueOnce(new ApiError('network', 'down'))
      .mockResolvedValue(live)

    await flow.loadAvailability()
    expect(flow.availability.value).toBeNull()
    await vi.advanceTimersByTimeAsync(delays[0] * 1000)
    await vi.advanceTimersByTimeAsync(delays[1] * 1000)

    expect(api.fetchAvailability).toHaveBeenCalledTimes(3)
    expect(flow.availability.value).toEqual(live)
    await vi.runAllTimersAsync()
    expect(api.fetchAvailability).toHaveBeenCalledTimes(3) // stops once it succeeds
  })

  it('gives up after the last delay', async () => {
    api.fetchAvailability.mockRejectedValue(new ApiError('network', 'down'))
    await flow.loadAvailability()
    await vi.runAllTimersAsync()
    expect(api.fetchAvailability).toHaveBeenCalledTimes(delays.length + 1)
    expect(flow.notice.value).toBeNull()
  })

  it('keeps the last good answer when a later refresh fails', async () => {
    const live = { sold_out: false, sales_open: true }
    await flow.loadAvailability()
    api.fetchAvailability.mockResolvedValue(live)
    await flow.loadAvailability()
    api.fetchAvailability.mockRejectedValue(new ApiError('network', 'down'))
    await flow.loadAvailability()
    expect(flow.availability.value).toEqual(live)
  })

  it('runs one retry loop however many times it is called', async () => {
    api.fetchAvailability.mockRejectedValue(new ApiError('network', 'down'))
    await flow.loadAvailability()
    await flow.loadAvailability()
    api.fetchAvailability.mockClear()
    await vi.advanceTimersByTimeAsync(delays[0] * 1000)
    expect(api.fetchAvailability).toHaveBeenCalledTimes(1)
  })
})

describe('submit', () => {
  it('trims every field and sends no quantity', async () => {
    api.createBooking.mockResolvedValue(BOOKING)
    await flow.submit(FORM)

    const payload = api.createBooking.mock.calls[0][0]
    expect(payload).toEqual({
      buyer_name: 'Ana López',
      buyer_whatsapp: '+54 9 11 5555 5555',
      buyer_email: '',
      sender_account_name: 'Juan López',
      guests: [{ full_name: 'Ana López' }, { full_name: 'Bea Núñez' }],
    })
    // Quantity is derived from len(guests) on the server, never sent by the client.
    expect(payload).not.toHaveProperty('quantity')
    expect(flow.step.value).toBe('pay')
    expect(flow.booking.value).toEqual(BOOKING)
  })

  it('sends one request for a double tap', async () => {
    let resolve
    api.createBooking.mockReturnValue(new Promise((r) => { resolve = r }))
    const first = flow.submit(FORM)
    const second = flow.submit(FORM)
    expect(flow.submitting.value).toBe(true)
    resolve(BOOKING)
    await Promise.all([first, second])
    expect(api.createBooking).toHaveBeenCalledTimes(1)
    expect(flow.submitting.value).toBe(false)
  })

  it('puts a 400 into fieldErrors and stays on the form', async () => {
    api.createBooking.mockRejectedValue(
      new ApiError('unknown', 'bad', { buyer_name: ['Required.'] }, 400),
    )
    await flow.submit(FORM)
    expect(flow.fieldErrors.value).toEqual({ buyer_name: ['Required.'] })
    expect(flow.notice.value).toBeNull()
    expect(flow.step.value).toBe('form')
  })

  it('shows sold_out as a notice and refreshes availability', async () => {
    api.createBooking.mockRejectedValue(
      new ApiError('sold_out', 'No spots left.', { remaining: 0 }, 409),
    )
    await flow.submit(FORM)
    expect(flow.notice.value).toMatchObject({ code: 'sold_out', message: 'No spots left.' })
    expect(api.fetchAvailability).toHaveBeenCalled()
    expect(flow.step.value).toBe('form')
  })

  it('shows a network failure as a notice, not field errors', async () => {
    api.createBooking.mockRejectedValue(new ApiError('network', 'Could not reach the server.'))
    await flow.submit(FORM)
    expect(flow.notice.value.code).toBe('network')
    expect(flow.fieldErrors.value).toEqual({})
  })

  it('clears the previous notice and errors on a new attempt', async () => {
    api.createBooking.mockRejectedValueOnce(new ApiError('network', 'down'))
    await flow.submit(FORM)
    api.createBooking.mockResolvedValue(BOOKING)
    await flow.submit(FORM)
    expect(flow.notice.value).toBeNull()
  })
})

describe('confirm', () => {
  beforeEach(async () => {
    api.createBooking.mockResolvedValue(BOOKING)
    await flow.submit(FORM)
  })

  it('moves to done with the server copy of the booking', async () => {
    api.confirmBooking.mockResolvedValue({ ...BOOKING, status: 'self_confirmed' })
    await flow.confirm()
    expect(api.confirmBooking).toHaveBeenCalledWith('REF123')
    expect(flow.step.value).toBe('done')
    expect(flow.booking.value.status).toBe('self_confirmed')
  })

  it('offers the organiser on booking_expired', async () => {
    api.confirmBooking.mockRejectedValue(
      new ApiError('booking_expired', 'Your hold expired.', { organiser_whatsapp: '+54 9 11 0000' }, 409),
    )
    await flow.confirm()
    expect(flow.notice.value).toEqual({
      code: 'booking_expired',
      message: 'Your hold expired.',
      whatsapp: '+54 9 11 0000',
    })
    expect(flow.step.value).toBe('pay')
  })

  it('does nothing without a booking', async () => {
    flow.startOver()
    await flow.confirm()
    expect(api.confirmBooking).not.toHaveBeenCalled()
  })
})

describe('songs', () => {
  beforeEach(async () => {
    api.createBooking.mockResolvedValue(BOOKING)
    await flow.submit(FORM)
    api.confirmBooking.mockResolvedValue({ ...BOOKING, status: 'self_confirmed' })
    await flow.confirm()
  })

  it('mirrors what the server kept, closing up a blank middle slot', async () => {
    flow.songs.value = ['Soda Stereo – Persiana americana', '', 'Bad Bunny – Tití me preguntó']
    api.saveSongRequests.mockResolvedValue({
      songs: [{ text: 'Soda Stereo – Persiana americana' }, { text: 'Bad Bunny – Tití me preguntó' }],
    })
    await flow.saveSongs()
    expect(flow.songs.value).toEqual([
      'Soda Stereo – Persiana americana',
      'Bad Bunny – Tití me preguntó',
      '',
    ])
    expect(flow.songsSaved.value).toBe(true)
  })

  it('shows a generic failure under the songs form', async () => {
    api.saveSongRequests.mockRejectedValue(new ApiError('network', 'Could not reach the server.'))
    await flow.saveSongs()
    expect(flow.songsError.value).toBe('Could not reach the server.')
    expect(flow.notice.value).toBeNull()
  })

  it('routes a cancelled booking to the page notice instead', async () => {
    api.saveSongRequests.mockRejectedValue(
      new ApiError('booking_cancelled', 'Cancelled.', { organiser_whatsapp: '+54' }, 409),
    )
    await flow.saveSongs()
    expect(flow.notice.value.code).toBe('booking_cancelled')
    expect(flow.songsError.value).toBe('')
  })
})

describe('startOver', () => {
  it('resets the flow to an empty form', async () => {
    api.createBooking.mockResolvedValue(BOOKING)
    await flow.submit(FORM)
    flow.songs.value = ['a', 'b', 'c']
    flow.startOver()
    expect(flow.step.value).toBe('form')
    expect(flow.booking.value).toBeNull()
    expect(flow.songs.value).toEqual(['', '', ''])
  })
})
