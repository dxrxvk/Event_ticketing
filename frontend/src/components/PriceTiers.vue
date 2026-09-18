<script setup>
import { computed } from 'vue'

import { bakedLadder, formatPesos, ladderFrom, tierStates } from '../event.config.js'

/**
 * The price ladder as three boxes: one on sale, the dearer ones locked behind it.
 *
 * Honest because the ladder really is public and really does only climb -- the server
 * prices every booking off a high-water mark, so a locked band is a thing that will
 * happen, not a thing being dangled. Nothing here is a countdown timer: the only
 * pressure shown is the seat count, which is a fact.
 *
 * Renders from the baked ladder before /api/availability/ answers, so the boxes are
 * there on first paint rather than popping in a second later.
 */

const props = defineProps({
  availability: { type: Object, default: null },
})

const ladder = computed(() => ladderFrom(props.availability) ?? bakedLadder())

// 1-based, and the server's own answer wherever possible: this is the seat the next
// ticket will be priced as, which after abandoned bookings is NOT the same as how
// full the room is. Quoting occupancy here would contradict the booking endpoint.
const nextSeat = computed(() => props.availability?.next_seat ?? 1)
const soldOut = computed(() => props.availability?.sold_out === true)

// The banding rule itself lives in event.config.js, beside the ladder helpers and the
// quote function, so it can be tested without mounting a component.
const tiers = computed(() =>
  tierStates(ladder.value, nextSeat.value, soldOut.value),
)

const STATE_LABEL = { open: 'On sale now', locked: 'Locked', gone: 'Gone' }

/** What a locked box promises, in seats rather than in vague urgency. */
function unlockNote(tier) {
  const previousLeft = tiers.value
    .filter((t) => t.toSeat < tier.fromSeat && t.state !== 'gone')
    .reduce((sum, t) => sum + t.left, 0)
  if (!previousLeft) return `Opens at spot ${tier.fromSeat}`
  return `Opens in ${previousLeft} ${previousLeft === 1 ? 'spot' : 'spots'}`
}
</script>

<template>
  <section class="tiers" aria-labelledby="tiers-heading">
    <h2 id="tiers-heading" class="tiers__heading">Ticket price</h2>

    <ol class="tiers__list">
      <li
        v-for="tier in tiers"
        :key="tier.fromSeat"
        :class="['tier', `tier--${tier.state}`]"
      >
        <!-- Every state is named in text as well as in colour and opacity: the
             difference between locked and open must survive a greyscale screen. -->
        <p class="tier__state">
          <span v-if="tier.state === 'locked'" class="tier__lock" aria-hidden="true">🔒</span>
          {{ STATE_LABEL[tier.state] }}
        </p>

        <p class="tier__price">{{ formatPesos(tier.price) }}</p>

        <p class="tier__range">Spots {{ tier.fromSeat }}&ndash;{{ tier.toSeat }}</p>

        <p v-if="tier.state === 'open'" class="tier__note">
          {{ tier.left }} left at this price
        </p>
        <p v-else-if="tier.state === 'locked'" class="tier__note">
          {{ unlockNote(tier) }}
        </p>
        <p v-else class="tier__note">All {{ tier.size }} sold</p>

        <div
          v-if="tier.state === 'open'"
          class="tier__bar"
          role="progressbar"
          :aria-valuenow="tier.sold"
          aria-valuemin="0"
          :aria-valuemax="tier.size"
          :aria-label="`${tier.sold} of ${tier.size} spots sold at ${formatPesos(tier.price)}`"
        >
          <span class="tier__bar-fill" :style="{ width: `${tier.percent}%` }" />
        </div>
      </li>
    </ol>

    <p class="tiers__footnote">
      The price only goes up. Yours is fixed the moment you reserve.
    </p>
  </section>
</template>

<style scoped>
.tiers__heading {
  font-size: var(--text-xs);
  font-weight: 600;
  letter-spacing: var(--tracking-caps);
  text-transform: uppercase;
  color: var(--ink-faint);
}

.tiers__list {
  display: grid;
  /* Always three across, so the ladder reads as a ladder. minmax(0, 1fr) rather than
     1fr: without it a long word floors the column and the row overflows a 320px
     phone, which is most of this event's traffic. */
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-2);
  margin-top: var(--space-3);
  padding: 0;
  list-style: none;
}

.tier {
  padding: var(--space-3) var(--space-2);
  text-align: center;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}

.tier__state {
  font-size: var(--text-xs);
  font-weight: 600;
  letter-spacing: var(--tracking-wide);
  color: var(--ink-faint);
}

.tier__lock { margin-right: 2px; font-size: 0.7em; }

.tier__price {
  margin-top: var(--space-1);
  font-family: var(--font-display);
  font-size: var(--text-xl);
  font-weight: 700;
  line-height: 1.1;
  letter-spacing: var(--tracking-tight);
}

.tier__range,
.tier__note {
  margin-top: 2px;
  font-size: var(--text-xs);
  color: var(--ink-muted);
  text-wrap: balance;
}

.tier__note { color: var(--ink-faint); }

/* The band you can actually buy: the only one carrying the accent, so the eye lands
   on the price being charged rather than on the cheapest number present. */
.tier--open {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.tier--open .tier__state { color: var(--accent-strong); }
.tier--open .tier__price { color: var(--accent-strong); }
.tier--open .tier__note { color: var(--ink-muted); }

.tier--locked {
  border-style: dashed;
  background: transparent;
}

.tier--locked .tier__price { color: var(--ink-muted); }

.tier--gone {
  background: transparent;
  border-color: var(--line);
}

.tier--gone .tier__price {
  color: var(--ink-faint);
  text-decoration: line-through;
}

.tier__bar {
  height: 3px;
  margin-top: var(--space-2);
  border-radius: var(--radius-full);
  background: var(--line-strong);
  overflow: hidden;
}

.tier__bar-fill {
  display: block;
  height: 100%;
  background: var(--accent);
  transition: width 240ms ease;
}

@media (prefers-reduced-motion: reduce) {
  .tier__bar-fill { transition: none; }
}

.tiers__footnote {
  margin-top: var(--space-3);
  font-size: var(--text-sm);
  color: var(--ink-muted);
}
</style>
