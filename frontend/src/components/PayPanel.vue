<script setup>
import { computed } from 'vue'

import AppButton from './AppButton.vue'
import CopyField from './CopyField.vue'

const props = defineProps({
  booking: { type: Object, required: true },
  confirming: { type: Boolean, default: false },
})
defineEmits(['confirm'])

const limitHelpLink = computed(() => {
  const number = props.booking.organiser_whatsapp?.replace(/\D/g, '')
  return number ? `https://wa.me/${number}` : null
})
</script>

<template>
  <section class="pay stack">
    <div>
      <p class="eyebrow">Almost there</p>
      <h2 class="pay__heading">Send the transfer</h2>
    </div>

    <div class="amount">
      <span class="eyebrow">Amount</span>
      <!--
        No copy button here, contra S10.5. Argentine banking apps require the amount to be
        typed into their own field -- there is nothing to paste into. S10.5 justified the
        button as "retyping is where people round it off", which was only ever a problem
        when each buyer owed a different number of centavos. A flat 5.000 is trivial to
        type correctly.
      -->
      <p class="amount__value">{{ booking.amount_display }}</p>
      <p class="amount__note">
        Send this as <strong>one single transfer</strong> for the full amount.
      </p>
    </div>

    <div class="card">
      <CopyField
        v-if="booking.alias"
        label="Alias"
        :value="booking.alias"
      />
      <CopyField
        v-if="booking.cvu"
        label="CVU"
        :value="booking.cvu"
        mono
        hint="Use this if your bank doesn't find the alias."
      />
      <p v-if="booking.account_holder_name" class="holder">
        Your banking app will show the destination as
        <strong>{{ booking.account_holder_name }}</strong>. That's right — it's the
        organiser's account.
      </p>
    </div>

    <div class="card">
      <CopyField
        label="Reference"
        :value="booking.reference"
        mono
        hint="If your banking app has a reference or note field, paste this in. It helps match your payment."
      />
    </div>

    <AppButton :loading="confirming" @click="$emit('confirm')">
      {{ confirming ? 'Confirming…' : "I've sent the transfer" }}
    </AppButton>

    <p class="field-hint">
      Tap that once the money is on its way. Your spot is held until then.
    </p>
    <p v-if="limitHelpLink" class="field-hint">
      Bank blocking it on a transfer limit?
      <a :href="limitHelpLink" target="_blank" rel="noopener">Message the organiser</a>.
    </p>
  </section>
</template>

<style scoped>
.pay__heading {
  margin-top: var(--space-2);
  font-size: var(--text-2xl);
  font-weight: 700;
  letter-spacing: var(--tracking-tight);
}

.amount {
  padding: var(--space-5);
  text-align: center;
  background: var(--accent-soft);
  border: 1px solid var(--accent);
  border-radius: var(--radius-lg);
}

.amount__value {
  margin-top: var(--space-2);
  font-family: var(--font-display);
  font-stretch: 125%;   /* Archivo's width axis -- matches the poster's wide title */
  font-size: var(--text-display);
  font-weight: 700;
  line-height: 1;
  letter-spacing: var(--tracking-tight);
  color: var(--accent-strong);
}

.amount__note {
  margin-top: var(--space-3);
  font-size: var(--text-base);
  color: var(--ink-muted);
}

.holder {
  padding-top: var(--space-4);
  border-top: 1px solid var(--line);
  font-size: var(--text-base);
  color: var(--ink-muted);
}
</style>
