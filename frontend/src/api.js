/* Thin fetch wrapper. Turns the backend's error contract into typed errors the UI can
   switch on, rather than leaving components to parse messages. */

// Empty in development: the Vite dev proxy forwards /api to Django on the same origin,
// so CORS never enters the picture locally.
const BASE = import.meta.env.VITE_API_BASE ?? ''

export class ApiError extends Error {
  constructor(code, message, data = {}, status = 0) {
    super(message)
    this.code = code
    this.data = data
    this.status = status
  }
}

async function request(path, { method = 'GET', body } = {}) {
  let response
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch {
    // A sleeping dyno, flaky mobile data, or the backend being down. A handled state,
    // not an unhandled rejection.
    throw new ApiError('network', 'Could not reach the server. Check your connection.')
  }

  const payload = await response.json().catch(() => ({}))

  if (!response.ok) {
    throw new ApiError(
      payload.error ?? 'unknown',
      payload.message ?? 'Something went wrong.',
      payload,
      response.status,
    )
  }
  return payload
}

/**
 * Fire-and-forget wake-up call, per S10.2. The visitor reads and types for 30-60s
 * while Render cold-starts, so the backend is awake by the time they submit. It hits
 * the database too, because Neon sleeps independently of the web service.
 */
export function warmUp() {
  request('/api/health/').catch(() => {})
}

export function fetchAvailability() {
  return request('/api/availability/')
}

export function createBooking(payload) {
  return request('/api/bookings/', { method: 'POST', body: payload })
}

export function confirmBooking(reference) {
  return request(`/api/bookings/${encodeURIComponent(reference)}/confirm/`, {
    method: 'POST',
  })
}
