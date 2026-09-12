<script setup>
import { event, eventDateParts } from '../event.config.js'

defineProps({
  booking: { type: Object, required: true },
})

const when = eventDateParts()
</script>

<template>
  <section class="done stack">
    <div class="done__mark" aria-hidden="true">✓</div>

    <div>
      <h2 class="done__heading">You're on the list</h2>
      <p class="done__body">
        {{ booking.quantity }}
        {{ booking.quantity === 1 ? 'spot is' : 'spots are' }}
        reserved for {{ event.name }}.
      </p>
    </div>

    <dl class="summary">
      <div class="summary__row">
        <dt class="eyebrow">When</dt>
        <dd>{{ when.day }}, {{ when.time }}</dd>
      </div>
      <div class="summary__row">
        <dt class="eyebrow">Where</dt>
        <dd>{{ event.venue }}</dd>
      </div>
      <div class="summary__row">
        <dt class="eyebrow">Reference</dt>
        <dd class="summary__mono">{{ booking.reference }}</dd>
      </div>
    </dl>

    <p class="field-hint">
      The organiser checks payments by hand, so it may be a day or two before yours is
      ticked off. Nothing more is needed from you — just turn up.
    </p>
  </section>
</template>

<style scoped>
.done { text-align: center; }

.done__mark {
  width: 56px; height: 56px;
  margin: 0 auto;
  display: grid; place-items: center;
  font-size: 1.5rem;
  color: var(--success);
  background: var(--success-soft);
  border-radius: 50%;
}

.done__heading {
  font-size: var(--text-2xl);
  font-weight: 700;
  letter-spacing: var(--tracking-tight);
}

.done__body { margin-top: var(--space-2); color: var(--ink-muted); }

.summary { text-align: left; border-top: 1px solid var(--line); }

.summary__row {
  display: grid;
  grid-template-columns: 6rem 1fr;
  gap: var(--space-3);
  align-items: baseline;
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--line);
}

.summary__row dd { margin: 0; font-weight: 600; }
.summary__mono { font-family: var(--font-mono); font-size: var(--text-base); }
</style>
