<script setup>
import { computed } from 'vue'

const props = defineProps({
  availability: { type: Object, default: null },
})

// Loads asynchronously and renders nothing until it arrives -- S10.3 is explicit that
// availability must never block the page.
const tone = computed(() => {
  if (!props.availability) return 'idle'
  if (props.availability.sold_out) return 'gone'
  if (props.availability.remaining <= 10) return 'low'
  return 'open'
})

const label = computed(() => {
  const a = props.availability
  if (!a) return ''
  if (a.sales_open === false) return 'Bookings closed'
  if (a.sold_out) return 'Sold out'
  return `${a.remaining} of ${a.capacity} spots left`
})
</script>

<template>
  <p v-if="availability" :class="['badge', `badge--${tone}`]" role="status" aria-live="polite">
    <span class="badge__dot" aria-hidden="true" />
    {{ label }}
  </p>
</template>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  font-weight: 600;
  letter-spacing: var(--tracking-wide);
  border-radius: var(--radius-full);
  border: 1px solid var(--line);
  background: var(--surface);
}

.badge__dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }

.badge--open { color: var(--success); background: var(--success-soft); border-color: transparent; }
.badge--low { color: var(--accent); background: var(--accent-soft); border-color: transparent; }
.badge--gone { color: var(--danger); background: var(--danger-soft); border-color: transparent; }
</style>
