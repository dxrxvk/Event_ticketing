<script setup>
import { computed, ref } from 'vue'

import {
  bakedLadder, event, eventDateParts, formatPesos, ladderFrom,
} from '../event.config.js'

const props = defineProps({
  // Null until /api/availability/ answers. The baked ladder covers that gap so the
  // page never renders without a price.
  availability: { type: Object, default: null },
})

const when = eventDateParts()

const ladder = computed(() => ladderFrom(props.availability) ?? bakedLadder())

// "from 5.000" once there is more than one price, so the cheapest figure on the page
// is never mistaken for what everyone pays.
const leadPrice = computed(() => formatPesos(ladder.value[0].price))
const isTiered = computed(() => ladder.value.length > 1)

/** "first 50 at this price, then 7.000, then 9.000" -- the ladder in one line. */
const ladderNote = computed(() => {
  if (!isTiered.value) return 'per person'
  const [first, ...rest] = ladder.value
  const steps = rest.map((band) => `then ${formatPesos(band.price)}`).join(', ')
  return `per person for the first ${first.toSeat}, ${steps}`
})

// What the next ticket sold actually costs, once the server has said so. Shown only
// when it has moved past the first band -- before that it would just repeat the line
// above.
const currentPrice = computed(() => {
  const display = props.availability?.current_price_display
  if (!display || props.availability?.sold_out) return null
  return display === leadPrice.value ? null : display
})
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
          <span>{{ isTiered ? 'from ' : '' }}{{ leadPrice }}</span>
          <span class="facts__sub">{{ ladderNote }}</span>
          <span v-if="currentPrice" class="facts__now">
            Right now: {{ currentPrice }} per person
          </span>
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

.facts__now {
  display: block;
  margin-top: var(--space-2);
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--accent-strong);
}
</style>
