<script setup>
import { computed } from 'vue'
const props = defineProps({ values: { type: Array, required: true }, label: { type: String, required: true } })
const width = 120, height = 32
const points = computed(() => {
  const top = Math.max(...props.values, 1)
  const step = width / Math.max(props.values.length - 1, 1)
  return props.values.map((value, index) => [index * step, height - 2 - (value / top) * (height - 4)])
})
const line = computed(() => points.value.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' '))
const area = computed(() => `0,${height} ${line.value} ${width},${height}`)
</script>
<template>
  <svg class="sparkline" :viewBox="`0 0 ${width} ${height}`" preserveAspectRatio="none" role="img" :aria-label="label">
    <polygon :points="area" class="area" />
    <polyline :points="line" class="line" vector-effect="non-scaling-stroke" />
  </svg>
</template>
<style scoped>
.sparkline{display:block;width:100%;height:32px;overflow:visible}
.area{fill:var(--brand);opacity:.14}
.line{fill:none;stroke:var(--brand);stroke-width:1.5;stroke-linejoin:round;stroke-linecap:round}
</style>
