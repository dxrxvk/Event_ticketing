<script setup>
import { nextTick, ref, watch } from 'vue'

import { event } from '../event.config.js'
import EventDetails from './EventDetails.vue'

const props = defineProps({
  // True while the page shows only the poster. Meaningless without a poster.
  covered: { type: Boolean, default: false },
  // Passed straight through to EventDetails, which shows the live price from it.
  availability: { type: Object, default: null },
})
const emit = defineEmits(['open'])

const hasPoster = Boolean(event.posterUrl)
const details = ref(null)

watch(
  () => props.covered,
  async (covered, was) => {
    if (was && !covered) {
      await nextTick()
      details.value?.focusTitle()
    }
  },
)
</script>

<template>
  <header class="hero" :class="{ 'hero--covered': hasPoster && covered }">
    <!--
      The poster is an upgrade, not a dependency. Without one there is no cover and no
      card: the details stand alone and the page is designed to look finished that way.
    -->
    <EventDetails v-if="!hasPoster" :availability="availability" />

    <div v-else class="flip">
      <div class="flip__card" :class="{ 'is-open': !covered }">
        <div class="flip__face flip__front" :inert="!covered" :aria-hidden="!covered">
          <button
            type="button"
            class="flip__tap"
            :aria-label="`Open the booking page for ${event.name}`"
            @click="emit('open')"
          >
            <figure class="hero__poster">
              <img
                :src="event.posterUrl"
                :alt="`Poster for ${event.name}`"
                width="1000"
                height="1400"
              />
            </figure>
          </button>
        </div>

        <div class="flip__face flip__back" :inert="covered" :aria-hidden="covered">
          <EventDetails ref="details" :availability="availability" />
        </div>
      </div>

      <p v-if="covered" class="eyebrow hero__hint">Tap the poster to book</p>
    </div>
  </header>
</template>

<style scoped>
.hero { padding-top: var(--space-7); }

/* First screen: nothing but the poster and the hint, centred. */
.hero--covered {
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding-block: var(--space-6);
}

.flip { perspective: 1600px; }

/* Both faces share one grid cell, so the card is as tall as the poster and the details
   sit centred in that same box once it has turned. */
.flip__card {
  display: grid;
  transform-style: preserve-3d;
  transition: transform var(--duration-flip) var(--ease);
}
.flip__card.is-open { transform: rotateY(180deg); }

.flip__face {
  grid-area: 1 / 1;
  -webkit-backface-visibility: hidden;
  backface-visibility: hidden;
}

.flip__back {
  transform: rotateY(180deg);
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.flip__tap {
  display: block;
  width: 100%;
  padding: 0;
  border: 0;
  background: none;
  color: inherit;
  cursor: pointer;
  border-radius: var(--radius-lg);
}
.flip__tap:focus-visible { box-shadow: var(--focus-ring); }

.hero__poster {
  margin: 0;
  border-radius: var(--radius-lg);
  overflow: hidden;
  border: 1px solid var(--line);
  background: var(--surface);
}

/* The attributes give the browser the 5:7 ratio before the file arrives; this keeps
   the rendered box on that ratio at any width. */
.hero__poster img { width: 100%; height: auto; aspect-ratio: 1000 / 1400; }

.hero__hint {
  margin-top: var(--space-5);
  text-align: center;
  color: var(--ink-muted);
  animation: hint-pulse 2s var(--ease) infinite;
}

@keyframes hint-pulse {
  0%, 100% { opacity: 0.55; }
  50% { opacity: 1; }
}

/* No rotation for people who have asked for less motion: the faces crossfade. */
@media (prefers-reduced-motion: reduce) {
  .flip__card, .flip__card.is-open, .flip__back { transform: none; }
  .flip__face { transition: opacity 200ms var(--ease); }
  .flip__back { opacity: 0; }
  .flip__card.is-open .flip__front { opacity: 0; }
  .flip__card.is-open .flip__back { opacity: 1; }
  .hero__hint { animation: none; opacity: 1; }
}
</style>
