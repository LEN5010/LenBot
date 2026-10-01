<script setup>
// Per-reply styles: one is drawn for each reply by its share; the shares add up to 100%.
import { computed } from 'vue'
import { mdiClose, mdiPlus } from '@mdi/js'

const styles = defineModel({ type: Array, required: true })
const percent = weight => Math.round(weight * 10000) / 100
const total = computed(() => percent(styles.value.reduce((sum, style) => sum + style.weight, 0)))
const setPercent = (style, value) => { style.weight = value === '' ? 0 : Number(value) / 100 }
const add = () => styles.value.push({ name: '', weight: styles.value.length ? 0 : 1, note: null })
</script>

<template>
  <div class="styles">
    <div class="head">
      <h3>说话风格</h3>
      <span v-if="styles.length" :class="total === 100 ? 'muted' : 'off'">合计 {{ total }}%</span>
    </div>
    <p class="muted">可以不加。加了以后，每次回复按比例抽一种风格。</p>
    <div v-for="(style, index) in styles" :key="index" class="row">
      <v-text-field v-model="style.name" label="风格" density="compact" hide-details />
      <v-text-field :model-value="percent(style.weight)" label="比例" suffix="%" type="number" min="0" max="100"
        density="compact" hide-details @update:model-value="value => setPercent(style, value)" />
      <v-text-field :model-value="style.note || ''" label="说明（可不填）" density="compact" hide-details
        @update:model-value="value => style.note = value || null" />
      <v-btn :icon="mdiClose" variant="text" size="small" :aria-label="`删除风格 ${style.name}`" @click="styles.splice(index, 1)" />
    </div>
    <div><v-btn :prepend-icon="mdiPlus" variant="text" @click="add">加一种风格</v-btn></div>
  </div>
</template>

<style scoped>
.styles{display:grid;gap:10px}
.head{display:flex;justify-content:space-between;align-items:baseline}
h3{font-size:15px;margin:0}
.styles p{margin:0}
.off{color:var(--error-text)}
.row{display:grid;grid-template-columns:minmax(0,1fr) 120px minmax(0,2fr) auto;gap:8px;align-items:center}
@media(max-width:600px){.row{grid-template-columns:minmax(0,1fr) 110px auto}.row>:nth-child(3){grid-column:1/3;grid-row:2}}
</style>
