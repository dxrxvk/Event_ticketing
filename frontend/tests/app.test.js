import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

// The whole page, mounted, with the network replaced. The composable is module-level
// state, so each test re-imports App after vi.resetModules() to get a fresh flow.
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

const OPEN = {
  capacity: 130, taken: 0, remaining: 130, next_seat: 1,
  tiers: [{ from_seat: 1, to_seat: 130, price_cents: 500000, price_display: '5.000' }],
  current_price_display: '5.000', sold_out: false, sales_open: true, venue: '', event_date: null,
}

let api

async function mountApp(availability = OPEN) {
  vi.resetModules()
  api = await import('../src/api.js')
  vi.mocked(api.fetchAvailability).mockReset().mockResolvedValue(availability)
  vi.mocked(api.warmUp).mockReset()
  vi.mocked(api.createBooking).mockReset()
  vi.mocked(api.confirmBooking).mockReset()
  const { default: App } = await import('../src/App.vue')
  const wrapper = mount(App)
  await flushPromises()
  return wrapper
}

async function uncover(wrapper) {
  await wrapper.get('.flip__tap').trigger('click')
  await flushPromises()
}

describe('App', () => {
  it('wakes the backend and loads availability on mount', async () => {
    await mountApp()
    expect(api.warmUp).toHaveBeenCalledTimes(1)
    expect(api.fetchAvailability).toHaveBeenCalledTimes(1)
  })

  it('opens on the poster alone, then shows the form after a tap', async () => {
    const wrapper = await mountApp()
    expect(wrapper.find('form').exists()).toBe(false)
    await uncover(wrapper)
    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.find('.tiers').exists()).toBe(true)
  })

  it('replaces the form with a sold-out card', async () => {
    const wrapper = await mountApp({ ...OPEN, remaining: 0, sold_out: true })
    await uncover(wrapper)
    expect(wrapper.find('form').exists()).toBe(false)
    expect(wrapper.find('.tiers').exists()).toBe(false)
    expect(wrapper.get('.closed__heading').text()).toBe('Sold out')
    expect(wrapper.get('.closed__link').attributes('href')).toMatch(/^https:\/\/wa\.me\//)
  })

  it('replaces the form with a closed card once sales close', async () => {
    const wrapper = await mountApp({ ...OPEN, sales_open: false })
    await uncover(wrapper)
    expect(wrapper.get('.closed__heading').text()).toBe('Bookings are closed')
  })

  it('walks form -> pay -> confirmed', async () => {
    const wrapper = await mountApp()
    await uncover(wrapper)

    const inputs = wrapper.findAll('form input')
    await inputs[0].setValue('Ana')
    await inputs[1].setValue('+54 9 11 5555')
    await wrapper.get('input[aria-label="Your full name"]').setValue('Ana López')

    api.createBooking.mockResolvedValue({
      reference: 'K7Q2XW9M', status: 'pending', quantity: 1, total_amount: 500000,
      amount_display: '5.000', price_breakdown: [], alias: 'fiesta.mixer.mp', cvu: '',
      account_holder_name: 'Dhruv Kumar', organiser_whatsapp: '', revolut: null,
    })
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('.pay').exists()).toBe(true)
    expect(wrapper.find('.tiers').exists()).toBe(false) // no ladder beside the amount owed
    expect(wrapper.get('.amount__value').text()).toBe('5.000')

    api.confirmBooking.mockResolvedValue({ reference: 'K7Q2XW9M', status: 'self_confirmed', quantity: 1 })
    await wrapper.get('.pay .btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.done').exists()).toBe(true)
    expect(wrapper.text()).toContain("You're on the list")
  })

  it('shows an expired hold as a notice with the organiser, staying on pay', async () => {
    const wrapper = await mountApp()
    await uncover(wrapper)
    const inputs = wrapper.findAll('form input')
    await inputs[0].setValue('Ana')
    await inputs[1].setValue('+54 9 11 5555')
    await wrapper.get('input[aria-label="Your full name"]').setValue('Ana López')
    api.createBooking.mockResolvedValue({
      reference: 'R', status: 'pending', quantity: 1, amount_display: '5.000',
      price_breakdown: [], alias: 'a', revolut: null,
    })
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const { ApiError } = api
    api.confirmBooking.mockRejectedValue(
      new ApiError('booking_expired', 'Your hold expired.', { organiser_whatsapp: '+5491100000000' }, 409),
    )
    await wrapper.get('.pay .btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.pay').exists()).toBe(true)
    expect(wrapper.get('.notice__title').text()).toBe('Your hold expired')
    expect(wrapper.get('.notice a').attributes('href')).toBe('https://wa.me/5491100000000')
  })
})
