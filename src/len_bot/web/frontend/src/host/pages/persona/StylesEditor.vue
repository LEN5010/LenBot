<script setup>
import { computed } from 'vue'
import RowEditor from '../../ui/RowEditor.vue'

const styles = defineModel({ type: Array, required: true })
const percent = weight => Math.round(weight * 10000) / 100
const total = computed(() => percent(styles.value.reduce((sum, style) => sum + style.weight, 0)))
const setPercent = (style, value) => { style.weight = value === '' ? 0 : Number(value) / 100 }
const make = () => ({ name: '', weight: styles.value.length ? 0 : 1, note: null })
</script>

<template>
  <div class="stack styles">
    <div class="inline">
      <h3>说话风格</h3>
      <span v-if="styles.length" class="ml-auto small" :class="total === 100 ? 'muted' : 'problem'">合计 {{ total }}%</span>
    </div>
    <p class="muted small">可以不加。加了以后，每次回复按比例抽一种风格。</p>
    <RowEditor :items="styles" :make="make" add-label="加一种风格" columns="minmax(0,1fr) 120px minmax(0,2fr)">
      <template #default="{ item }">
        <v-text-field v-model="item.name" label="风格" />
        <v-text-field :model-value="percent(item.weight)" label="比例" suffix="%" type="number" min="0" max="100"
          @update:model-value="value => setPercent(item, value)" />
        <v-text-field :model-value="item.note || ''" label="说明（可不填）" @update:model-value="value => item.note = value || null" />
      </template>
    </RowEditor>
  </div>
</template>

<style scoped>
.styles{gap:var(--sp-2)}
.styles p{margin:0}
</style>
