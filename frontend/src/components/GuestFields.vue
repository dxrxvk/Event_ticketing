<script setup>
import { MAX_GUESTS } from '../constants.js'

const props = defineProps({
  guests: { type: Array, required: true },
})
const emit = defineEmits(['update:guests'])

function setGuest(index, value) {
  const next = [...props.guests]
  next[index] = value
  emit('update:guests', next)
}

function addGuest() {
  if (props.guests.length >= MAX_GUESTS) return
  emit('update:guests', [...props.guests, ''])
}

function removeGuest(index) {
  if (props.guests.length <= 1) return
  emit('update:guests', props.guests.filter((_, i) => i !== index))
}
</script>

<template>
  <fieldset class="guests">
    <legend class="field-label">Who's coming</legend>
    <p class="field-hint guests__hint">
      One full name per person, including you. This is exactly what goes on the venue's
      door list.
    </p>

    <div v-for="(guest, index) in guests" :key="index" class="guests__row">
      <input
        class="field-input"
        :value="guest"
        :placeholder="index === 0 ? 'Your full name' : `Guest ${index + 1} full name`"
        :aria-label="index === 0 ? 'Your full name' : `Guest ${index + 1} full name`"
        autocomplete="off"
        @input="setGuest(index, $event.target.value)"
      />
      <button
        v-if="guests.length > 1"
        class="guests__remove"
        type="button"
        :aria-label="`Remove guest ${index + 1}`"
        @click="removeGuest(index)"
      >
        &times;
      </button>
    </div>

    <button
      v-if="guests.length < MAX_GUESTS"
      class="guests__add"
      type="button"
      @click="addGuest"
    >
      + Add another person
    </button>
    <p v-else class="field-hint">
      {{ MAX_GUESTS }} is the most one booking can hold. Ask a friend to book separately.
    </p>
  </fieldset>
</template>

<style scoped>
.guests { margin: 0; padding: 0; border: 0; }

.guests__hint { margin-bottom: var(--space-3); }

.guests__row { display: flex; gap: var(--space-2); align-items: center; }
.guests__row + .guests__row { margin-top: var(--space-2); }

.guests__remove {
  flex-shrink: 0;
  width: 44px; height: 44px;     /* fingertip-sized */
  font-size: 1.25rem; line-height: 1;
  color: var(--ink-muted);
  background: transparent;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: color var(--duration) var(--ease), border-color var(--duration) var(--ease);
}
.guests__remove:hover { color: var(--danger); border-color: var(--danger); }

.guests__add {
  margin-top: var(--space-3);
  padding: var(--space-2) 0;
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--accent);
  background: none;
  border: 0;
  cursor: pointer;
}
.guests__add:hover { text-decoration: underline; text-underline-offset: 3px; }
</style>
