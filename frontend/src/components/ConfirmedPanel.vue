<script setup>
import { ref, watch } from 'vue'

import AppButton from './AppButton.vue'
import { event, eventDateParts } from '../event.config.js'

const props = defineProps({
  booking: { type: Object, required: true },
  songs: { type: Array, required: true },
  songsSaving: { type: Boolean, default: false },
  songsSaved: { type: Boolean, default: false },
  songsError: { type: String, default: '' },
})
const emit = defineEmits(['update:songs', 'save-songs'])

const when = eventDateParts()

// "Saved" only until the next keystroke, so the button always says what it will do.
const touched = ref(false)
watch(() => props.songsSaved, (saved) => { if (saved) touched.value = false })

function setSong(index, value) {
  touched.value = true
  emit('update:songs', props.songs.map((song, i) => (i === index ? value : song)))
}
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
      ticked off. That's it from you — unless you want a say in the music.
    </p>

    <form class="songs" @submit.prevent="emit('save-songs')">
      <div>
        <p class="eyebrow">Song requests <span class="muted">(optional)</span></p>
        <p class="songs__lead">Up to three. Artist and title works best.</p>
      </div>

      <label v-for="(song, index) in songs" :key="index" class="field">
        <span class="visually-hidden">Song {{ index + 1 }}</span>
        <input
          class="field-input"
          type="text"
          maxlength="120"
          autocomplete="off"
          :placeholder="`Song ${index + 1}: Artist – Title`"
          :value="song"
          @input="setSong(index, $event.target.value)"
        />
      </label>

      <p v-if="songsError" class="field-error" role="alert">{{ songsError }}</p>

      <AppButton type="submit" variant="ghost" :loading="songsSaving">
        {{ songsSaving ? 'Saving…' : songsSaved && !touched ? 'Saved' : 'Save songs' }}
      </AppButton>
    </form>
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

.songs {
  text-align: left;
  padding-top: var(--space-5);
  border-top: 1px solid var(--line);
}

.songs__lead {
  margin-top: var(--space-1);
  font-size: var(--text-sm);
  color: var(--ink-muted);
}

.songs > * + * { margin-top: var(--space-3); }
</style>
