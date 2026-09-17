<script setup>
import { ref } from 'vue'
import { mdiChevronRight } from '@mdi/js'

// Deliberately not a v-expansion-panel: those read as evidence viewers
// everywhere else in the panel, and an advanced setting is something you
// change, not something you inspect.
defineProps({
  title: { type: String, default: '高级参数' },
  note: String,
  open: { type: Boolean, default: false },
})
const expanded = ref(false)
</script>

<template>
  <section class="advanced" :class="{ 'advanced--open': expanded || open }">
    <button type="button" class="advanced__toggle" :aria-expanded="expanded || open"
            @click="expanded = !expanded">
      <v-icon :icon="mdiChevronRight" size="16" class="advanced__chevron" />
      <span class="advanced__title">{{ title }}</span>
      <span v-if="note" class="advanced__note">{{ note }}</span>
    </button>
    <div v-show="expanded || open" class="advanced__body"><slot /></div>
  </section>
</template>

<style scoped>
.advanced { border:1px dashed var(--line);border-radius:12px;background:transparent }
.advanced--open { background:var(--surface);border-style:solid }
.advanced__toggle {
  display:flex;align-items:center;gap:8px;width:100%;padding:12px 14px;
  border:0;background:none;cursor:pointer;text-align:left;font:inherit;color:var(--muted);
}
.advanced__toggle:hover { color:var(--ink) }
.advanced__chevron { transition:transform .15s ease;flex:none }
.advanced--open .advanced__chevron { transform:rotate(90deg) }
.advanced__title { font-weight:600;font-size:13px }
.advanced__note { color:var(--muted);font-size:12px;overflow-wrap:anywhere }
.advanced__body { padding:4px 14px 16px;display:grid;gap:16px }
@media(prefers-reduced-motion:reduce){.advanced__chevron{transition:none}}
</style>
