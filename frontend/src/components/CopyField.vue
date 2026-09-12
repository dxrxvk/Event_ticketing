<script setup>
import { ref } from 'vue'

const props = defineProps({
  label: { type: String, required: true },
  value: { type: String, required: true },
  hint: { type: String, default: '' },
  mono: { type: Boolean, default: false },
})

const copied = ref(false)
let timer

async function copy() {
  try {
    await navigator.clipboard.writeText(props.value)
  } catch {
    return // Clipboard blocked (insecure context, denied permission). Value is visible.
  }
  copied.value = true
  clearTimeout(timer)
  timer = setTimeout(() => { copied.value = false }, 2000)
}
</script>

<template>
  <div class="copy">
    <div class="copy__text">
      <span class="eyebrow">{{ label }}</span>
      <p :class="['copy__value', { 'copy__value--mono': mono }]">{{ value }}</p>
      <p v-if="hint" class="field-hint">{{ hint }}</p>
    </div>
    <button class="copy__btn" type="button" @click="copy">
      {{ copied ? 'Copied' : 'Copy' }}
    </button>
    <!-- Screen readers get the same confirmation the button shows visually. -->
    <span class="visually-hidden" role="status" aria-live="polite">
      {{ copied ? `${label} copied` : '' }}
    </span>
  </div>
</template>

<style scoped>
.copy {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-4) 0;
}

.copy + .copy { border-top: 1px solid var(--line); }

.copy__text { flex: 1; min-width: 0; }

.copy__value {
  margin-top: var(--space-1);
  font-size: var(--text-lg);
  font-weight: 600;
  /* A CVU is 22 digits and must be allowed to wrap rather than overflow. */
  overflow-wrap: anywhere;
}

.copy__value--mono {
  font-family: var(--font-mono);
  font-size: var(--text-base);
  letter-spacing: 0.01em;
}

.copy__btn {
  flex-shrink: 0;
  min-height: 40px;
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--ink);
  background: var(--surface-raised);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: background-color var(--duration) var(--ease);
}

.copy__btn:hover { background: var(--accent-soft); border-color: var(--accent); }
</style>
