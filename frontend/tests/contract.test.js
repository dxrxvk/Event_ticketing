/* The frontend half of the contract in contract/frontend_contract.json.
 *
 * tickets/test_contract.py checks that the backend still says what the JSON says; this
 * file checks that the frontend still agrees. The page copies these facts by hand so it
 * can render before a sleeping backend answers, and until now nothing tied the copies
 * to the originals. */

import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import StatusNotice from '../src/components/StatusNotice.vue'
import { MAX_GUESTS } from '../src/constants.js'
import { bakedLadder, formatPesos, ladderFrom, quoteFor } from '../src/event.config.js'

const ROOT = join(import.meta.dirname, '..')
const contract = JSON.parse(readFileSync(join(ROOT, '..', 'contract', 'frontend_contract.json'), 'utf8'))

const seeded = ladderFrom({ tiers: contract.seeded_ladder })

describe('contract with the backend', () => {
  it('caps a booking at the same number of guests', () => {
    expect(MAX_GUESTS).toBe(contract.max_guests)
  })

  it('bakes in a ladder the API could have sent', () => {
    // The baked ladder follows the admin (flat or tiered), not the migrations, so it is
    // checked for shape only: contiguous from seat 1, whole positive prices. Keeping
    // its numbers current is a manual step -- see the comment on event.priceTiers.
    const bands = bakedLadder()
    expect(bands.length).toBeGreaterThan(0)
    bands.forEach((band, i) => {
      expect(band.fromSeat).toBe(i === 0 ? 1 : bands[i - 1].toSeat + 1)
      expect(band.toSeat).toBeGreaterThanOrEqual(band.fromSeat)
      expect(Number.isInteger(band.price) && band.price > 0).toBe(true)
    })
  })

  it('formats pesos the way format_ars() does', () => {
    for (const [cents, expected] of contract.format_ars) {
      expect(formatPesos(cents / 100), `${cents} cents`).toBe(expected)
    }
  })

  describe('quoteFor() agrees with price_seats()', () => {
    for (const quote of contract.quotes) {
      it(quote.name, () => {
        const result = quoteFor(quote.quantity, seeded, quote.next_seat)
        expect(result.lines.map((line) => [line.quantity, line.price * 100])).toEqual(quote.lines)
        expect(result.total * 100).toBe(quote.total_cents)
      })
    }
  })

  it('gives every backend error code its own title', () => {
    for (const code of contract.error_codes) {
      const wrapper = mount(StatusNotice, { props: { notice: { code, message: 'x' } } })
      expect(wrapper.get('.notice__title').text(), code).not.toBe('Something went wrong')
    }
  })
})

/**
 * Every API key the source reads, found by scanning for `availability.x`,
 * `booking.x` and `revolut.x` (optional chaining and `.value` included). Snake_case
 * only: the API speaks snake_case, and camelCase names are the app's own.
 *
 * A static scan rather than a runtime spy, because the point is to catch a component
 * that starts reading a key the backend has never sent -- which renders as a blank,
 * not an error, and so is invisible in every other test that feeds it a fixture.
 */
function keysRead(objectName) {
  const files = []
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name)
      if (entry.isDirectory()) walk(path)
      else if (/\.(vue|js)$/.test(entry.name)) files.push(path)
    }
  }
  walk(join(ROOT, 'src'))

  const pattern = new RegExp(`\\b${objectName}(?:\\.value)?\\??\\.([a-z_]+)\\b`, 'g')
  const keys = new Set()
  for (const file of files) {
    for (const match of readFileSync(file, 'utf8').matchAll(pattern)) {
      // `availability.value = ...` is the ref being assigned, not an API key.
      if (match[1] !== 'value') keys.add(match[1])
    }
  }
  return keys
}

describe('the frontend only reads keys the backend promises', () => {
  it.each([
    ['availability', 'availability_keys'],
    ['booking', 'booking_keys'],
    ['revolut', 'revolut_keys'],
  ])('%s', (objectName, contractKey) => {
    const read = keysRead(objectName)
    expect(read.size).toBeGreaterThan(0) // the scan found something, so it still works
    for (const key of read) {
      expect(contract[contractKey], `${objectName}.${key}`).toContain(key)
    }
  })
})
