import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  confirmBooking,
  createBooking,
  fetchAvailability,
  saveSongRequests,
  warmUp,
} from '../src/api.js'

/** A fetch Response stand-in: just the parts api.js reads. */
function reply(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: body === undefined ? () => Promise.reject(new SyntaxError('no body')) : async () => body,
  }
}

let fetchMock

beforeEach(() => {
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

async function caught(promise) {
  try {
    await promise
  } catch (error) {
    return error
  }
  throw new Error('expected the call to reject')
}

describe('requests', () => {
  it('GETs availability and returns the JSON body', async () => {
    fetchMock.mockResolvedValue(reply(200, { remaining: 12 }))
    expect(await fetchAvailability()).toEqual({ remaining: 12 })
    expect(fetchMock).toHaveBeenCalledWith('/api/availability/', {
      method: 'GET', headers: undefined, body: undefined,
    })
  })

  it('POSTs a booking as JSON', async () => {
    fetchMock.mockResolvedValue(reply(201, { reference: 'abc' }))
    await createBooking({ buyer_name: 'Ana' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/bookings/')
    expect(init.method).toBe('POST')
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' })
    expect(JSON.parse(init.body)).toEqual({ buyer_name: 'Ana' })
  })

  it('URL-encodes the reference in confirm and songs paths', async () => {
    fetchMock.mockResolvedValue(reply(200, { songs: [] }))
    await confirmBooking('a/b c')
    await saveSongRequests('a/b c', ['x'])
    expect(fetchMock.mock.calls[0][0]).toBe('/api/bookings/a%2Fb%20c/confirm/')
    expect(fetchMock.mock.calls[1][0]).toBe('/api/bookings/a%2Fb%20c/songs/')
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ songs: ['x'] })
  })
})

describe('error contract', () => {
  it('turns a failed fetch into a network ApiError, not a raw TypeError', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    const error = await caught(fetchAvailability())
    expect(error).toBeInstanceOf(ApiError)
    expect(error.code).toBe('network')
  })

  it('maps a 409 to its error code and keeps the payload', async () => {
    fetchMock.mockResolvedValue(
      reply(409, { error: 'sold_out', message: 'No spots left.', remaining: 1 }),
    )
    const error = await caught(createBooking({}))
    expect(error.code).toBe('sold_out')
    expect(error.message).toBe('No spots left.')
    expect(error.data.remaining).toBe(1)
    expect(error.status).toBe(409)
  })

  it('carries organiser_whatsapp on dead ends', async () => {
    fetchMock.mockResolvedValue(
      reply(409, { error: 'booking_expired', organiser_whatsapp: '+5491100000000' }),
    )
    const error = await caught(confirmBooking('ref'))
    expect(error.code).toBe('booking_expired')
    expect(error.data.organiser_whatsapp).toBe('+5491100000000')
  })

  it('maps a 400 to code unknown with the field errors as data', async () => {
    fetchMock.mockResolvedValue(reply(400, { buyer_name: ['This field is required.'] }))
    const error = await caught(createBooking({}))
    expect(error.code).toBe('unknown')
    expect(error.status).toBe(400)
    expect(error.data).toEqual({ buyer_name: ['This field is required.'] })
  })

  it('maps a 429 to throttled with a plain message, ignoring DRF detail', async () => {
    fetchMock.mockResolvedValue(reply(429, { detail: 'Request was throttled.' }))
    const error = await caught(createBooking({}))
    expect(error.code).toBe('throttled')
    expect(error.message).toMatch(/too many attempts from your network/i)
  })

  it('survives an error response with no JSON body', async () => {
    fetchMock.mockResolvedValue(reply(502))
    const error = await caught(fetchAvailability())
    expect(error.code).toBe('unknown')
    expect(error.status).toBe(502)
    expect(error.message).toBe('Something went wrong.')
  })
})

describe('warmUp', () => {
  it('pings health and swallows failure', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    expect(() => warmUp()).not.toThrow()
    expect(fetchMock).toHaveBeenCalledWith('/api/health/', expect.any(Object))
    // Let the rejected promise settle: an unhandled rejection would fail the run.
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
})
