<script setup>
// Builds the schedule `when` text the backend accepts: an ISO time with offset,
// `every 30m/2h/1d`, or `cron:minute hour * * weekdays` in the scene timezone.
import { computed, ref, watch } from 'vue'

const props = defineProps({ timezone: { type: String, required: true } })
const emit = defineEmits(['update:modelValue'])
const mode = ref('once')
const today = new Date().toLocaleDateString('sv-SE', { timeZone: props.timezone })
const date = ref(today), time = ref('09:00')
const every = ref(1), unit = ref('h')
const days = ref([1, 2, 3, 4, 5, 6, 0])
const custom = ref('')
const weekdays = [[1, '一'], [2, '二'], [3, '三'], [4, '四'], [5, '五'], [6, '六'], [0, '日']]

// Offset of the scene timezone at a given instant, in minutes.
function offsetAt(instant) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', { timeZone: props.timezone, hourCycle: 'h23',
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
    .formatToParts(instant).map(part => [part.type, part.value]))
  const local = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute, +parts.second)
  return Math.round((local - instant) / 60000)
}
function isoWithOffset(day, clock) {
  const [y, m, d] = day.split('-').map(Number), [h, mi] = clock.split(':').map(Number)
  const guess = Date.UTC(y, m - 1, d, h, mi)
  const offset = offsetAt(guess - offsetAt(guess) * 60000)
  // A skipped local clock must not become a different time after conversion.
  if (offsetAt(guess - offset * 60000) !== offset) return ''
  const sign = offset < 0 ? '-' : '+', abs = Math.abs(offset)
  const pad = value => String(value).padStart(2, '0')
  return `${day}T${clock}:00${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`
}
const oneTime = computed(() => /^\d{4}-\d{2}-\d{2}$/.test(date.value) && /^\d{2}:\d{2}$/.test(time.value)
  ? isoWithOffset(date.value, time.value) : null)
const value = computed(() => {
  if (mode.value === 'custom') return custom.value.trim()
  if (mode.value === 'every') return every.value > 0 ? `every ${every.value}${unit.value}` : ''
  if (!/^\d{2}:\d{2}$/.test(time.value)) return ''
  if (mode.value === 'once') return oneTime.value || ''
  if (!days.value.length) return ''
  const [h, mi] = time.value.split(':').map(Number)
  const week = days.value.length === 7 ? '*' : [...days.value].sort().join(',')
  return `cron:${mi} ${h} * * ${week}`
})
watch(value, text => emit('update:modelValue', text), { immediate: true })
</script>

<template>
  <div class="picker">
    <v-btn-toggle v-model="mode" mandatory density="comfortable" color="primary" class="modes">
      <v-btn value="once">一次</v-btn><v-btn value="daily">每天/每周</v-btn><v-btn value="every">每隔</v-btn><v-btn value="custom">自己写</v-btn>
    </v-btn-toggle>
    <div v-if="mode === 'once'" class="row">
      <v-text-field v-model="date" type="date" label="日期" hide-details />
      <v-text-field v-model="time" type="time" label="时间" hide-details />
    </div>
    <template v-else-if="mode === 'daily'">
      <v-text-field v-model="time" type="time" label="时间" hide-details class="time" />
      <div class="days">
        <v-checkbox v-for="[day, label] in weekdays" :key="day" v-model="days" :value="day" :label="`周${label}`" hide-details density="compact" />
      </div>
    </template>
    <div v-else-if="mode === 'every'" class="row">
      <v-text-field v-model.number="every" type="number" min="1" label="每隔" hide-details />
      <v-select v-model="unit" :items="[{ title: '分钟', value: 'm' }, { title: '小时', value: 'h' }, { title: '天', value: 'd' }]" label="单位" hide-details />
    </div>
    <v-text-field v-else v-model="custom" label="时间写法" persistent-hint
      hint="例如 2026-10-02T09:00:00+08:00、every 2h，或 cron:30 9 * * 1-5（工作日 9:30）" />
    <p v-if="mode === 'once' && oneTime === ''" role="alert" class="time-error">{{ timezone }} 在这个日期跳过了所选时间，请选择实际存在的时间，或在“自己写”中填写带明确 UTC 偏移的时间。</p>
    <p class="muted">按 {{ timezone }} 的时间{{ value ? `，保存为 ${value}` : '' }}</p>
  </div>
</template>

<style scoped>
.picker{display:grid;gap:12px}
.modes{flex-wrap:wrap;height:auto}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.time{max-width:200px}
.days{display:flex;flex-wrap:wrap;gap:0 4px}
.picker p{margin:0;font-size:13px}
.picker .time-error{color:var(--error-text)}
</style>
