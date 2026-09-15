<script setup>
import { ref } from 'vue'

import { event, eventDateParts, formatPesos } from '../event.config.js'

const when = eventDateParts()
const title = ref(null)

defineExpose({
  focusTitle: () => title.value?.focus({ preventScroll: true }),
})
</script>

<template>
  <div class="details">
    <p class="eyebrow details__kicker">You're invited</p>
    <h1 ref="title" class="details__title" tabindex="-1">{{ event.name }}</h1>
    <p v-if="event.tagline" class="details__tagline">{{ event.tagline }}</p>

    <dl class="facts">
      <div class="facts__row">
        <dt class="eyebrow">When</dt>
        <dd>
          {{ when.day }}
          <span class="facts__sub">{{ when.time }}</span>
        </dd>
      </div>
      <div class="facts__row">
        <dt class="eyebrow">Where</dt>
        <dd>
          {{ event.venue }}
          <span v-if="event.venueArea" class="facts__sub">{{ event.venueArea }}</span>
        </dd>
      </div>
      <div class="facts__row">
        <dt class="eyebrow">Price</dt>
        <dd>
          {{ formatPesos(event.pricePerTicket) }}
          <span class="facts__sub">per person</span>
        </dd>
      </div>
    </dl>
  </div>
</template>

<style scoped>
.details__kicker { color: var(--accent-strong); }

.details__title {
  margin-top: var(--space-3);
  font-family: var(--font-display);
  font-stretch: 125%;   /* Archivo's width axis -- matches the poster's wide title */
  font-size: var(--text-display);
  line-height: var(--leading-tight);
  letter-spacing: var(--tracking-tight);
  font-weight: 700;
  text-wrap: balance;
  outline: none;        /* focused programmatically after the flip; no ring on a heading */
}

.details__tagline {
  margin-top: var(--space-3);
  font-size: var(--text-lg);
  color: var(--ink-muted);
  text-wrap: pretty;
}

.facts {
  margin-top: var(--space-6);
  border-top: 1px solid var(--line);
}

.facts__row {
  display: grid;
  grid-template-columns: 4.5rem 1fr;
  gap: var(--space-4);
  align-items: baseline;
  padding: var(--space-4) 0;
  border-bottom: 1px solid var(--line);
}

.facts__row dd { margin: 0; font-weight: 600; font-size: var(--text-md); }

.facts__sub {
  display: block;
  margin-top: 2px;
  font-weight: 400;
  font-size: var(--text-sm);
  color: var(--ink-muted);
}
</style>
