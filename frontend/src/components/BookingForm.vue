<script setup>
import { computed, reactive } from 'vue'

import { event, formatPesos, ladderFrom, quoteFor } from '../event.config.js'
import AppButton from './AppButton.vue'
import GuestFields from './GuestFields.vue'

const props = defineProps({
  submitting: { type: Boolean, default: false },
  fieldErrors: { type: Object, default: () => ({}) },
  // Carries the live ladder and which seat is next. Null until the API answers, which
  // is why the figure below is labelled an estimate.
  availability: { type: Object, default: null },
})
const emit = defineEmits(['submit'])

const form = reactive({
  buyerName: '',
  buyerWhatsapp: '',
  buyerEmail: '',
  senderAccountName: '',
  guests: [''],
})

/**
 * What this party will owe, split across tier boundaries.
 *
 * Only ever an estimate: the price is decided by the server under a lock at the moment
 * Reserve is tapped, and seats can sell in between. Saying "estimated" is the honest
 * version of a number that can move, and the pay screen then shows the real one.
 */
const quote = computed(() =>
  quoteFor(
    form.guests.length,
    ladderFrom(props.availability),
    props.availability?.next_seat ?? 1,
  ),
)

const total = computed(() => quote.value.total)
const spansTiers = computed(() => quote.value.lines.length > 1)

const canSubmit = computed(
  () =>
    form.buyerName.trim() &&
    form.buyerWhatsapp.trim() &&
    form.guests.every((name) => name.trim()),
)

function errorFor(field) {
  const value = props.fieldErrors?.[field]
  return Array.isArray(value) ? value[0] : typeof value === 'string' ? value : null
}

/**
 * Every server-side validation message, flattened.
 *
 * DRF nests some errors -- guests comes back as { guests: { non_field_errors: [...] } } --
 * and anything this form does not render beside a specific input would otherwise vanish,
 * leaving the buyer tapping Reserve with no feedback at all. The summary guarantees
 * something is always shown, and doubles as the accessible error announcement.
 */
function flatten(value) {
  if (!value) return []
  if (typeof value === 'string') return [value]
  if (Array.isArray(value)) return value.flatMap(flatten)
  if (typeof value === 'object') return Object.values(value).flatMap(flatten)
  return []
}

const errorSummary = computed(() => flatten(props.fieldErrors))

function onSubmit() {
  if (!canSubmit.value) return
  emit('submit', form)
}
</script>

<template>
  <form class="form stack" novalidate @submit.prevent="onSubmit">
    <h2 class="form__heading">Reserve your spot</h2>

    <div v-if="errorSummary.length" class="summary-error" role="alert">
      <p class="summary-error__title">Please check the form</p>
      <ul class="summary-error__list">
        <li v-for="(message, i) in errorSummary" :key="i">{{ message }}</li>
      </ul>
    </div>

    <label class="field">
      <span class="field-label">Your name</span>
      <input v-model="form.buyerName" class="field-input" autocomplete="name" />
      <span v-if="errorFor('buyer_name')" class="field-error">{{ errorFor('buyer_name') }}</span>
    </label>

    <label class="field">
      <span class="field-label">WhatsApp number</span>
      <input
        v-model="form.buyerWhatsapp"
        class="field-input"
        type="tel"
        inputmode="tel"
        placeholder="+54 9 11 …"
        autocomplete="tel"
      />
      <span class="field-hint">So the organiser can reach you about your booking.</span>
      <span v-if="errorFor('buyer_whatsapp')" class="field-error">
        {{ errorFor('buyer_whatsapp') }}
      </span>
    </label>

    <label class="field">
      <span class="field-label">Email <span class="muted">(optional)</span></span>
      <input
        v-model="form.buyerEmail"
        class="field-input"
        type="email"
        inputmode="email"
        autocomplete="email"
      />
      <span v-if="errorFor('buyer_email')" class="field-error">{{ errorFor('buyer_email') }}</span>
    </label>

    <GuestFields
      :guests="form.guests"
      @update:guests="form.guests = $event"
    />

    <label class="field">
      <span class="field-label">
        What name will the transfer come from? <span class="muted">(optional)</span>
      </span>
      <input v-model="form.senderAccountName" class="field-input" autocomplete="off" />
      <!--
        The single most useful field on this form. Argentine transfers show the account
        HOLDER's name, which is frequently not the buyer -- a partner, a parent, a Mercado
        Pago handle. Without this, matching a deposit to a person is guesswork.
      -->
      <span class="field-hint">
        Only if the account isn't in your name. It's how the organiser matches your
        transfer to you.
      </span>
    </label>

    <div class="total">
      <span class="total__label">Estimated total</span>
      <span class="total__value">{{ formatPesos(total) }}</span>
      <span class="total__note">
        {{ form.guests.length }} {{ form.guests.length === 1 ? 'person' : 'people' }}
      </span>
      <!-- A party can straddle a price step, and "24.000" for four people reads as a
           mistake to someone who was told tickets cost 5.000. -->
      <p v-if="spansTiers" class="total__split">
        <span v-for="(line, i) in quote.lines" :key="i">
          {{ i > 0 ? ' + ' : '' }}{{ line.quantity }} &times; {{ formatPesos(line.price) }}
        </span>
      </p>
      <p class="total__note total__fixed">
        The price rises as spots sell. Yours is fixed the moment you tap Reserve.
      </p>
    </div>

    <AppButton type="submit" :loading="submitting" :disabled="!canSubmit">
      {{ submitting ? 'Reserving…' : 'Reserve' }}
    </AppButton>

    <p v-if="event.listDeadlineNote" class="field-hint form__deadline">
      {{ event.listDeadlineNote }}
    </p>
    <p class="field-hint">
      Names are shared with the venue for the door list only, and deleted after the event.
    </p>
  </form>
</template>

<style scoped>
.form__heading {
  font-size: var(--text-2xl);
  font-weight: 700;
  letter-spacing: var(--tracking-tight);
}

.total {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  padding: var(--space-4) 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  flex-wrap: wrap;
}

.total__label {
  font-size: var(--text-xs);
  font-weight: 600;
  letter-spacing: var(--tracking-caps);
  text-transform: uppercase;
  color: var(--ink-faint);
}

.total__value { margin-left: auto; font-size: var(--text-xl); font-weight: 700; }
.total__note { font-size: var(--text-sm); color: var(--ink-muted); }

/* Both sit on their own line under the figure, hence the full-width basis. */
.total__split,
.total__fixed {
  flex-basis: 100%;
  margin-top: var(--space-2);
  font-size: var(--text-sm);
  color: var(--ink-muted);
}

.total__split { font-weight: 600; color: var(--ink); }

.form__deadline { color: var(--accent-strong); }

.summary-error {
  padding: var(--space-4);
  background: var(--danger-soft);
  border: 1px solid var(--danger);
  border-radius: var(--radius-md);
}

.summary-error__title { font-weight: 700; color: var(--danger); }

.summary-error__list {
  margin-top: var(--space-2);
  padding-left: var(--space-4);
  list-style: disc;
  font-size: var(--text-base);
}
</style>
