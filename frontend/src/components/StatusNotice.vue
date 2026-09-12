<script setup>
import { computed } from 'vue'

import { whatsappLink } from '../event.config.js'

const props = defineProps({
  notice: { type: Object, required: true }, // { code, message, whatsapp }
})

const TITLES = {
  sold_out: 'Sold out',
  sales_closed: 'Bookings are closed',
  booking_expired: 'Your hold expired',
  booking_cancelled: 'This booking was cancelled',
  network: "Couldn't reach the server",
  not_found: 'Booking not found',
}

const title = computed(() => TITLES[props.notice.code] ?? 'Something went wrong')

// Every dead end offers a human. A buyer who hits booking_expired may already have sent
// the money, so leaving them with only an error message is the worst possible outcome.
const link = computed(() => {
  const number = props.notice.whatsapp?.replace(/\D/g, '')
  if (number) return `https://wa.me/${number}`
  return whatsappLink()
})

const isDeadEnd = computed(() =>
  ['booking_expired', 'booking_cancelled', 'sold_out', 'sales_closed'].includes(props.notice.code),
)
</script>

<template>
  <div class="notice" role="alert">
    <p class="notice__title">{{ title }}</p>
    <p class="notice__body">{{ notice.message }}</p>
    <a v-if="link && isDeadEnd" class="notice__link" :href="link" target="_blank" rel="noopener">
      Message the organiser on WhatsApp
    </a>
  </div>
</template>

<style scoped>
.notice {
  padding: var(--space-4) var(--space-5);
  background: var(--danger-soft);
  border: 1px solid var(--danger);
  border-radius: var(--radius-md);
}

.notice__title { font-weight: 700; color: var(--danger); }
.notice__body { margin-top: var(--space-1); font-size: var(--text-base); color: var(--ink); }

.notice__link {
  display: inline-block;
  margin-top: var(--space-3);
  font-weight: 600;
  font-size: var(--text-base);
}
</style>
