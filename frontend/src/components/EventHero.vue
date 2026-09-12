<script setup>
import { event, eventDateParts, formatPesos } from '../event.config.js'

const when = eventDateParts()
</script>

<template>
  <header class="hero">
    <!--
      The poster is an upgrade, not a dependency. Until posterUrl is set this block does
      not render at all -- no placeholder box, no empty frame. The page is designed to
      look finished without it.
    -->
    <figure v-if="event.posterUrl" class="hero__poster">
      <img :src="event.posterUrl" :alt="`Poster for ${event.name}`" />
    </figure>

    <p class="eyebrow hero__kicker">You're invited</p>
    <h1 class="hero__title">{{ event.name }}</h1>
    <p v-if="event.tagline" class="hero__tagline">{{ event.tagline }}</p>

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
  </header>
</template>

<style scoped>
.hero { padding-top: var(--space-7); }

.hero__poster {
  margin: 0 0 var(--space-6);
  border-radius: var(--radius-lg);
  overflow: hidden;
  border: 1px solid var(--line);
  background: var(--surface);
}

.hero__kicker { color: var(--accent); }

.hero__title {
  margin-top: var(--space-3);
  font-family: var(--font-display);
  font-size: var(--text-display);
  line-height: var(--leading-tight);
  letter-spacing: var(--tracking-tight);
  font-weight: 700;
  text-wrap: balance;
}

.hero__tagline {
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
