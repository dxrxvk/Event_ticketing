import { computed, ref } from 'vue'

import * as api from '../api.js'

/**
 * The whole flow, in one place.
 *
 * event_ticketing.md S3 specifies Pinia, which is not carried here: four screens and a
 * single booking object do not need a store, and one composable avoids a dependency plus
 * its boilerplate. Revisit if the state ever outgrows one object.
 */

const step = ref('form') // 'form' | 'pay' | 'done'
const booking = ref(null)
const availability = ref(null) // null until loaded; never blocks the first render
const notice = ref(null) // { code, message, whatsapp } for a dead end
const submitting = ref(false)
const confirming = ref(false)
const fieldErrors = ref({})

const soldOut = computed(() => availability.value?.sold_out === true)
const salesClosed = computed(() => availability.value?.sales_open === false)
const canBook = computed(() => !soldOut.value && !salesClosed.value)

function noticeFrom(error) {
  return {
    code: error.code,
    message: error.message,
    whatsapp: error.data?.organiser_whatsapp || '',
  }
}

async function loadAvailability() {
  try {
    availability.value = await api.fetchAvailability()
  } catch {
    // Deliberately silent. Availability is an enhancement; the page is fully usable
    // without it and showing an error for a decorative counter is noise.
    availability.value = null
  }
}

async function submit(form) {
  // Client half of the double-tap guard. The server half -- returning the existing
  // booking for a repeat of the same buyer and party size -- is already in place.
  if (submitting.value) return
  submitting.value = true
  fieldErrors.value = {}
  notice.value = null

  try {
    booking.value = await api.createBooking({
      buyer_name: form.buyerName.trim(),
      buyer_whatsapp: form.buyerWhatsapp.trim(),
      buyer_email: form.buyerEmail.trim(),
      sender_account_name: form.senderAccountName.trim(),
      guests: form.guests.map((name) => ({ full_name: name.trim() })),
    })
    step.value = 'pay'
    loadAvailability()
  } catch (error) {
    if (error.code === 'unknown' && error.status === 400) {
      fieldErrors.value = error.data ?? {}
    } else {
      notice.value = noticeFrom(error)
      if (error.code === 'sold_out' || error.code === 'sales_closed') loadAvailability()
    }
  } finally {
    submitting.value = false
  }
}

async function confirm() {
  if (confirming.value || !booking.value) return
  confirming.value = true
  notice.value = null

  try {
    booking.value = await api.confirmBooking(booking.value.reference)
    step.value = 'done'
  } catch (error) {
    // booking_expired and booking_cancelled both strand someone who may be mid-transfer,
    // so the notice carries a way to reach a human rather than a dead end.
    notice.value = noticeFrom(error)
  } finally {
    confirming.value = false
  }
}

function startOver() {
  booking.value = null
  notice.value = null
  fieldErrors.value = {}
  step.value = 'form'
  loadAvailability()
}

export function useBooking() {
  return {
    step, booking, availability, notice, submitting, confirming, fieldErrors,
    soldOut, salesClosed, canBook,
    loadAvailability, submit, confirm, startOver,
    dismissNotice: () => { notice.value = null },
  }
}
