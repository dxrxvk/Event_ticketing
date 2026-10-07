import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AvailabilityBadge from '../src/components/AvailabilityBadge.vue'
import BookingForm from '../src/components/BookingForm.vue'
import ConfirmedPanel from '../src/components/ConfirmedPanel.vue'
import CopyField from '../src/components/CopyField.vue'
import GuestFields from '../src/components/GuestFields.vue'
import PayPanel from '../src/components/PayPanel.vue'
import PriceTiers from '../src/components/PriceTiers.vue'
import StatusNotice from '../src/components/StatusNotice.vue'
import { MAX_GUESTS } from '../src/constants.js'
import { bakedLadder, event, formatPesos } from '../src/event.config.js'

/** The availability payload as the API sends it, with overrides. */
function availability(overrides = {}) {
  return {
    capacity: 130,
    taken: 0,
    remaining: 130,
    tiers: [
      { from_seat: 1, to_seat: 50, price_cents: 500000, price_display: '5.000' },
      { from_seat: 51, to_seat: 100, price_cents: 700000, price_display: '7.000' },
      { from_seat: 101, to_seat: 130, price_cents: 900000, price_display: '9.000' },
    ],
    next_seat: 1,
    current_price_display: '5.000',
    sold_out: false,
    sales_open: true,
    venue: '',
    event_date: null,
    ...overrides,
  }
}

/** The pay-screen payload, with overrides. */
function booking(overrides = {}) {
  return {
    reference: 'K7Q2XW9M',
    status: 'pending',
    quantity: 1,
    total_amount: 500000,
    amount_display: '5.000',
    price_breakdown: [
      { from_seat: 1, to_seat: 1, quantity: 1, unit_price_cents: 500000, unit_price_display: '5.000' },
    ],
    alias: 'fiesta.mixer.mp',
    cvu: '0000003100012345678901',
    account_holder_name: 'Dhruv Kumar',
    organiser_whatsapp: '+54 9 11 0000-0000',
    revolut: null,
    ...overrides,
  }
}

describe('GuestFields', () => {
  it('adds a blank guest', async () => {
    const wrapper = mount(GuestFields, { props: { guests: ['Ana'] } })
    await wrapper.get('.guests__add').trigger('click')
    expect(wrapper.emitted('update:guests')[0][0]).toEqual(['Ana', ''])
  })

  it('offers no remove button for the only guest', () => {
    const wrapper = mount(GuestFields, { props: { guests: ['Ana'] } })
    expect(wrapper.find('.guests__remove').exists()).toBe(false)
  })

  it('removes the chosen guest', async () => {
    const wrapper = mount(GuestFields, { props: { guests: ['Ana', 'Bea', 'Caro'] } })
    await wrapper.findAll('.guests__remove')[1].trigger('click')
    expect(wrapper.emitted('update:guests')[0][0]).toEqual(['Ana', 'Caro'])
  })

  it(`stops offering more at ${MAX_GUESTS} and says why`, () => {
    const wrapper = mount(GuestFields, { props: { guests: Array(MAX_GUESTS).fill('x') } })
    expect(wrapper.find('.guests__add').exists()).toBe(false)
    expect(wrapper.text()).toContain(`${MAX_GUESTS} is the most one booking can hold`)
  })
})

describe('BookingForm', () => {
  async function fill(wrapper, { name = 'Ana', whatsapp = '+54 9 11 5555' } = {}) {
    const inputs = wrapper.findAll('input')
    await inputs[0].setValue(name)
    await inputs[1].setValue(whatsapp)
    await wrapper.get('input[aria-label="Your full name"]').setValue(name)
  }

  it('keeps Reserve disabled until name, WhatsApp and every guest are filled', async () => {
    const wrapper = mount(BookingForm)
    const button = () => wrapper.get('button[type="submit"]')
    expect(button().attributes('disabled')).toBeDefined()
    await fill(wrapper)
    expect(button().attributes('disabled')).toBeUndefined()
  })

  it('emits the form on submit', async () => {
    const wrapper = mount(BookingForm)
    await fill(wrapper)
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('submit')[0][0]).toMatchObject({
      buyerName: 'Ana',
      guests: ['Ana'],
    })
  })

  it('does not emit when the form is incomplete', async () => {
    const wrapper = mount(BookingForm)
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('labels the total an estimate, from the baked ladder before the API answers', () => {
    const wrapper = mount(BookingForm)
    expect(wrapper.get('.total__label').text()).toBe('Estimated total')
    expect(wrapper.get('.total__value').text()).toBe(formatPesos(bakedLadder()[0].price))
  })

  it('prices from the live ladder and next seat once availability arrives', () => {
    const wrapper = mount(BookingForm, { props: { availability: availability({ next_seat: 60 }) } })
    expect(wrapper.get('.total__value').text()).toBe('7.000')
  })

  it('shows the split when the party straddles a step', async () => {
    const wrapper = mount(BookingForm, { props: { availability: availability({ next_seat: 50 }) } })
    await wrapper.get('.guests__add').trigger('click')
    expect(wrapper.get('.total__value').text()).toBe('12.000')
    expect(wrapper.get('.total__split').text().replace(/\s+/g, ' ')).toBe('1 × 5.000 + 1 × 7.000')
  })

  it('shows a field error beside its input and in the summary', () => {
    const wrapper = mount(BookingForm, {
      props: { fieldErrors: { buyer_whatsapp: ['Enter a valid number.'] } },
    })
    expect(wrapper.get('.field-error').text()).toBe('Enter a valid number.')
    expect(wrapper.get('[role="alert"]').text()).toContain('Enter a valid number.')
  })
})

describe('PriceTiers', () => {
  it('renders the baked ladder before availability arrives', () => {
    const wrapper = mount(PriceTiers)
    const boxes = wrapper.findAll('.tier')
    expect(boxes.map((box) => box.get('.tier__price').text()))
      .toEqual(bakedLadder().map((band) => formatPesos(band.price)))
    expect(boxes[0].classes()).toContain('tier--open')
  })

  it('says how many are left in the open band and when the next one opens', () => {
    const wrapper = mount(PriceTiers, { props: { availability: availability({ next_seat: 41 }) } })
    const boxes = wrapper.findAll('.tier')
    expect(boxes[0].get('.tier__note').text()).toBe('10 left at this price')
    expect(boxes[1].get('.tier__note').text()).toBe('Opens in 10 spots')
    // The third box counts every cheaper seat still to go, not just the next band's.
    expect(boxes[2].get('.tier__note').text()).toBe('Opens in 60 spots')
  })

  it('names every state in text, not only by colour', () => {
    const wrapper = mount(PriceTiers, { props: { availability: availability({ next_seat: 51 }) } })
    const labels = wrapper.findAll('.tier__state').map((el) => el.text())
    expect(labels).toEqual(['Gone', 'On sale now', expect.stringContaining('Locked')])
  })

  it('exposes progress to screen readers on the open band only', () => {
    const wrapper = mount(PriceTiers, { props: { availability: availability({ next_seat: 26 }) } })
    const bars = wrapper.findAll('[role="progressbar"]')
    expect(bars).toHaveLength(1)
    expect(bars[0].attributes('aria-valuenow')).toBe('25')
    expect(bars[0].attributes('aria-valuemax')).toBe('50')
  })
})

describe('AvailabilityBadge', () => {
  it('renders nothing until availability arrives', () => {
    expect(mount(AvailabilityBadge).html()).toBe('<!--v-if-->')
  })

  it('shows the current price without a seat count', () => {
    const wrapper = mount(AvailabilityBadge, {
      props: { availability: availability({ remaining: 3, current_price_display: '7.000' }) },
    })
    expect(wrapper.text()).toBe('Tickets available · now 7.000')
    expect(wrapper.text()).not.toMatch(/\d+ of \d+|left/)
    expect(wrapper.classes()).toContain('badge--open')
  })

  it('says sold out and closed plainly', () => {
    const sold = mount(AvailabilityBadge, { props: { availability: availability({ sold_out: true, remaining: 0 }) } })
    expect(sold.text()).toBe('Sold out')
    const closed = mount(AvailabilityBadge, { props: { availability: availability({ sales_open: false }) } })
    expect(closed.text()).toBe('Bookings closed')
  })
})

describe('StatusNotice', () => {
  it('offers the organiser on a dead end, using the number the API sent', () => {
    const wrapper = mount(StatusNotice, {
      props: { notice: { code: 'booking_expired', message: 'Expired.', whatsapp: '+54 9 11 1234-5678' } },
    })
    expect(wrapper.get('.notice__title').text()).toBe('Your hold expired')
    expect(wrapper.get('a').attributes('href')).toBe('https://wa.me/5491112345678')
  })

  it('falls back to the baked number when the API sent none', () => {
    const wrapper = mount(StatusNotice, {
      props: { notice: { code: 'sales_closed', message: 'Closed.', whatsapp: '' } },
    })
    expect(wrapper.get('a').attributes('href')).toBe(`https://wa.me/${event.organiserWhatsapp}`)
  })

  it('shows no WhatsApp link for a network error', () => {
    const wrapper = mount(StatusNotice, {
      props: { notice: { code: 'network', message: 'Could not reach the server.', whatsapp: '' } },
    })
    expect(wrapper.get('.notice__title').text()).toBe("Couldn't reach the server")
    expect(wrapper.find('a').exists()).toBe(false)
  })

  it('has a generic title for unknown codes', () => {
    const wrapper = mount(StatusNotice, { props: { notice: { code: 'throttled', message: 'Wait.' } } })
    expect(wrapper.get('.notice__title').text()).toBe('Something went wrong')
  })
})

describe('CopyField', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('copies the value and announces it', async () => {
    const writeText = vi.fn().mockResolvedValue()
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    const wrapper = mount(CopyField, { props: { label: 'Alias', value: 'fiesta.mixer.mp' } })
    await wrapper.get('button').trigger('click')
    await Promise.resolve()
    expect(writeText).toHaveBeenCalledWith('fiesta.mixer.mp')
    expect(wrapper.get('button').text()).toBe('Copied')
    expect(wrapper.get('[role="status"]').text()).toBe('Alias copied')
  })

  it('stays quiet when the clipboard is blocked', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
    const wrapper = mount(CopyField, { props: { label: 'CVU', value: '0000' } })
    await wrapper.get('button').trigger('click')
    await Promise.resolve()
    expect(wrapper.get('button').text()).toBe('Copy')
  })
})

describe('PayPanel', () => {
  it('shows the server amount and the payment destination', () => {
    const wrapper = mount(PayPanel, { props: { booking: booking() } })
    expect(wrapper.get('.amount__value').text()).toBe('5.000')
    expect(wrapper.text()).toContain('fiesta.mixer.mp')
    expect(wrapper.text()).toContain('0000003100012345678901')
    expect(wrapper.text()).toContain('K7Q2XW9M')
    expect(wrapper.text()).toContain('Dhruv Kumar')
  })

  it('puts copy buttons on alias, CVU and reference only, never the amount', () => {
    const wrapper = mount(PayPanel, { props: { booking: booking() } })
    const labels = wrapper.findAllComponents(CopyField).map((field) => field.props('label'))
    expect(labels).toEqual(['Alias', 'CVU', 'Reference'])
    expect(wrapper.get('.amount').find('button').exists()).toBe(false)
  })

  it('shows the split only when the party crossed a step', () => {
    const single = mount(PayPanel, { props: { booking: booking() } })
    expect(single.find('.amount__split').exists()).toBe(false)

    const split = mount(PayPanel, {
      props: {
        booking: booking({
          quantity: 4,
          amount_display: '24.000',
          price_breakdown: [
            { from_seat: 49, to_seat: 50, quantity: 2, unit_price_cents: 500000, unit_price_display: '5.000' },
            { from_seat: 51, to_seat: 52, quantity: 2, unit_price_cents: 700000, unit_price_display: '7.000' },
          ],
        }),
      },
    })
    expect(split.get('.amount__split').text()).toMatch(/2 × 5.000\s*\+ 2 × 7.000/)
  })

  it('hides the Revolut block when the API sends none', () => {
    const wrapper = mount(PayPanel, { props: { booking: booking() } })
    expect(wrapper.find('details').exists()).toBe(false)
  })

  it('shows a Revolut note without a price, collapsed', () => {
    const wrapper = mount(PayPanel, {
      props: {
        booking: booking({
          revolut: {
            tag: 'dhruvk', link: 'https://revolut.me/dhruvk',
            note: 'Send the equivalent of 5.000 ARS in USD.',
            currency: '', amount_cents: null, amount_display: '',
          },
        }),
      },
    })
    const details = wrapper.get('details')
    expect(details.attributes('open')).toBeUndefined()
    expect(details.find('.revolut__lead').exists()).toBe(false)
    expect(details.get('.revolut__note').text()).toBe('Send the equivalent of 5.000 ARS in USD.')
    expect(details.text()).toContain('@dhruvk')
  })

  it('emits confirm and shows progress while confirming', async () => {
    const wrapper = mount(PayPanel, { props: { booking: booking() } })
    await wrapper.get('.btn').trigger('click')
    expect(wrapper.emitted('confirm')).toHaveLength(1)

    await wrapper.setProps({ confirming: true })
    expect(wrapper.get('.btn').text()).toBe('Confirming…')
    expect(wrapper.get('.btn').attributes('disabled')).toBeDefined()
  })
})

describe('ConfirmedPanel', () => {
  const props = (overrides = {}) => ({
    booking: booking({ quantity: 2, status: 'self_confirmed' }),
    songs: ['', '', ''],
    ...overrides,
  })

  it('says TBD for the time and venue until the admin sets them', () => {
    const wrapper = mount(ConfirmedPanel, { props: props() })
    expect(wrapper.text()).toContain('2 spots are reserved')
    expect(wrapper.text()).toContain('Friday 9 October, time TBD')
    expect(wrapper.text()).toContain(event.venue)
  })

  it('shows the admin venue and time once availability carries them', () => {
    const wrapper = mount(ConfirmedPanel, {
      props: props({ availability: availability({ venue: 'Niceto Club', event_date: '2026-10-10T01:00:00Z' }) }),
    })
    expect(wrapper.text()).toContain('Friday 9 October, 22:00')
    expect(wrapper.text()).toContain('Niceto Club')
  })

  it('emits song edits and save', async () => {
    const wrapper = mount(ConfirmedPanel, { props: props() })
    await wrapper.findAll('input')[1].setValue('Charly García – Demoliendo hoteles')
    expect(wrapper.emitted('update:songs')[0][0]).toEqual(['', 'Charly García – Demoliendo hoteles', ''])
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('save-songs')).toHaveLength(1)
  })

  it('says Saved until the next keystroke', async () => {
    const wrapper = mount(ConfirmedPanel, { props: props() })
    await wrapper.setProps({ songsSaved: true })
    expect(wrapper.get('.btn').text()).toBe('Saved')
    await wrapper.findAll('input')[0].setValue('x')
    expect(wrapper.get('.btn').text()).toBe('Save songs')
  })
})
