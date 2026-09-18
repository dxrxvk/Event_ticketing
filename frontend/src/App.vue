<script setup>
import { onMounted, ref } from 'vue'

import { warmUp } from './api.js'
import AvailabilityBadge from './components/AvailabilityBadge.vue'
import BookingForm from './components/BookingForm.vue'
import ConfirmedPanel from './components/ConfirmedPanel.vue'
import EventHero from './components/EventHero.vue'
import PayPanel from './components/PayPanel.vue'
import PriceTiers from './components/PriceTiers.vue'
import StatusNotice from './components/StatusNotice.vue'
import { useBooking } from './composables/useBooking.js'
import { event, whatsappLink } from './event.config.js'

const {
  step, booking, availability, notice, submitting, confirming, fieldErrors,
  songs, songsSaving, songsSaved, songsError,
  canBook, soldOut, salesClosed, loadAvailability, submit, confirm, saveSongs,
} = useBooking()

onMounted(() => {
  // First action on mount, per S10.2: wake Render (and Neon) while the visitor reads and
  // types, so the backend is up by the time they submit. Deliberately not awaited.
  warmUp()
  loadAvailability()
})

const soldOutLink = whatsappLink(`Hi! Is there any chance of a spot for ${event.name}?`)

// Presentation, not booking state: the page opens on the poster alone and a tap turns it
// over. Meanwhile warmUp() above has already started waking the backend.
const covered = ref(Boolean(event.posterUrl))
</script>

<template>
  <div class="app">
    <main class="page">
      <EventHero :covered="covered" :availability="availability" @open="covered = false" />

      <Transition name="fade">
        <div v-if="!covered">
          <div class="app__status">
            <AvailabilityBadge :availability="availability" />
          </div>

          <!-- Only while choosing. On the pay and confirmed screens the buyer already
               has a price, and the ladder would be noise next to the amount they owe. -->
          <PriceTiers
            v-if="step === 'form' && canBook"
            :availability="availability"
            class="app__tiers"
          />

          <StatusNotice v-if="notice" :notice="notice" class="app__notice" />

          <div class="app__body">
            <Transition name="fade" mode="out-in">
              <!-- Sold out and closed replace the form entirely: offering a form that
                   cannot succeed is worse than saying so plainly. -->
              <section v-if="step === 'form' && !canBook" key="closed" class="closed card">
                <h2 class="closed__heading">
                  {{ soldOut ? 'Sold out' : 'Bookings are closed' }}
                </h2>
                <p class="closed__body">
                  {{ soldOut
                    ? 'Every spot is taken. If someone drops out, the organiser will know first.'
                    : 'The guest list has gone to the venue, so no new bookings can be taken.' }}
                </p>
                <a v-if="soldOutLink" class="closed__link" :href="soldOutLink" target="_blank" rel="noopener">
                  Ask to be kept in mind
                </a>
              </section>

              <BookingForm
                v-else-if="step === 'form'"
                key="form"
                :submitting="submitting"
                :field-errors="fieldErrors"
                :availability="availability"
                @submit="submit"
              />

              <PayPanel
                v-else-if="step === 'pay'"
                key="pay"
                :booking="booking"
                :confirming="confirming"
                @confirm="confirm"
              />

              <ConfirmedPanel
                v-else
                key="done"
                :booking="booking"
                :availability="availability"
                :songs="songs"
                :songs-saving="songsSaving"
                :songs-saved="songsSaved"
                :songs-error="songsError"
                @update:songs="songs = $event"
                @save-songs="saveSongs"
              />
            </Transition>
          </div>

          <footer class="footer">
            <p>{{ event.name }}</p>
          </footer>
        </div>
      </Transition>
    </main>
  </div>
</template>

<style scoped>
.app { min-height: 100dvh; padding-bottom: var(--space-9); }

.app__status { margin-top: var(--space-5); }
.app__tiers { margin-top: var(--space-5); }
.app__notice { margin-top: var(--space-5); }
.app__body { margin-top: var(--space-6); }

.closed { text-align: center; }
.closed__heading { font-size: var(--text-xl); font-weight: 700; }
.closed__body { margin-top: var(--space-2); color: var(--ink-muted); }
.closed__link { display: inline-block; margin-top: var(--space-4); font-weight: 600; }

.footer {
  margin-top: var(--space-9);
  padding-top: var(--space-5);
  border-top: 1px solid var(--line);
  font-size: var(--text-sm);
  color: var(--ink-faint);
  text-align: center;
}
</style>
