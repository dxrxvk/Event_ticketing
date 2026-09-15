<script setup>
defineProps({
  variant: { type: String, default: 'primary' }, // 'primary' | 'ghost'
  loading: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  type: { type: String, default: 'button' },
})
</script>

<template>
  <button
    :type="type"
    :class="['btn', `btn--${variant}`]"
    :disabled="disabled || loading"
    :aria-busy="loading"
  >
    <span v-if="loading" class="btn__spinner" aria-hidden="true" />
    <span :class="{ 'btn__label--loading': loading }"><slot /></span>
  </button>
</template>

<style scoped>
.btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  width: 100%;
  min-height: 52px;
  padding: var(--space-3) var(--space-5);
  font-size: var(--text-md);
  font-weight: 650;
  letter-spacing: var(--tracking-wide);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: transform var(--duration) var(--ease),
              background-color var(--duration) var(--ease),
              opacity var(--duration) var(--ease);
}

.btn:active:not(:disabled) { transform: scale(0.985); }

.btn:disabled { cursor: not-allowed; opacity: 0.55; }

/* The outline is not decoration: --accent is only 2.69:1 against the yellow page,
   under the 3:1 WCAG floor for a component boundary. Near-black ink carries it. */
.btn--primary {
  background: var(--accent);
  color: var(--accent-ink);
  border-color: var(--ink);
}
.btn--primary:hover:not(:disabled) { filter: brightness(1.07); }

.btn--ghost {
  background: transparent;
  color: var(--ink);
  border-color: var(--line-strong);
}
.btn--ghost:hover:not(:disabled) { background: var(--surface-raised); }

.btn__label--loading { opacity: 0.75; }

.btn__spinner {
  width: 15px; height: 15px;
  border: 2px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

@media (prefers-reduced-motion: reduce) {
  .btn__spinner { animation-duration: 2s; }
}
</style>
