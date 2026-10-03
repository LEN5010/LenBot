<script setup>
import { ref, watch } from 'vue'
import { tasksApi } from '../../api/tasks.js'
import { useAction } from '../../../composables/useResource.js'
import { developerDetails } from '../../../composables/useDeveloperMode.js'
import { formatTime } from '../../time.js'
import ErrorNote from '../../components/ErrorNote.vue'
import { eventLabel } from './taskLabels.js'

const props = defineProps({
  scene: { type: String, required: true }, taskId: { type: Number, required: true },
  first: { type: Array, required: true }, firstNext: { type: Number, default: null }, timezone: { type: String, default: null },
})
const rows = ref([]), next = ref(null), full = ref({})
watch(() => props.first, value => { rows.value = value; next.value = value.length === 100 ? props.firstNext : null }, { immediate: true })
const more = useAction(), reading = useAction()
async function loadMore() {
  const page = await more.run(() => tasksApi.detail(props.scene, props.taskId, { after: next.value, limit: 100 }))
  if (!page) return
  rows.value = [...rows.value, ...page.events]
  next.value = page.events.length === 100 ? page.next_after : null
}
async function open(event) {
  if (full.value[event.id]) { const { [event.id]: _, ...rest } = full.value; full.value = rest; return }
  const record = await reading.run(() => tasksApi.event(props.scene, props.taskId, event.id))
  if (record) full.value = { ...full.value, [event.id]: record }
}
// Text and images a person can read; everything else is only in developer mode.
function parts(record) {
  const body = record.body
  const plain = ['text', 'title', 'message', 'value', 'note', 'summary'].filter(key => typeof body[key] === 'string').map(key => body[key])
  if (Array.isArray(body.options)) plain.push(`选项：${body.options.join('、')}`)
  if (typeof body.confirmed === 'boolean') plain.push(body.confirmed ? '同意' : '拒绝')
  const content = body.type === 'tool_execution_end' ? body.result?.content : body.type === 'message_end' ? body.message?.content : null
  return [...plain.map(text => ({ type: 'text', text })), ...(Array.isArray(content) ? content.filter(part => part.type === 'text'
    || (part.type === 'image' && ['image/png', 'image/jpeg', 'image/webp'].includes(part.mimeType))) : [])]
}
</script>

<template>
  <section class="surface events">
    <h2>过程</h2>
    <ErrorNote v-if="reading.error.value" title="读取这一步失败" :error="reading.error.value" />
    <p v-if="!rows.length" class="muted">还没有记录</p>
    <ol>
      <li v-for="event in rows" :key="event.id">
        <button type="button" class="line" @click="open(event)">
          <span class="muted">{{ formatTime(event.created, timezone) }}</span>
          <span>{{ eventLabel(event.event_type) }}{{ event.tool_name ? ` · ${event.tool_name}` : '' }}</span>
        </button>
        <div v-if="full[event.id]" class="body">
          <template v-for="(part, index) in parts(full[event.id])" :key="index">
            <pre v-if="part.type === 'text'">{{ part.text }}</pre>
            <img v-else :src="`data:${part.mimeType};base64,${part.data}`" alt="任务里的图片" loading="lazy" />
          </template>
          <ErrorNote v-if="full[event.id].body.error" title="这一步出错了" :error="String(full[event.id].body.error)" />
          <p v-if="!parts(full[event.id]).length && !full[event.id].body.error && !developerDetails" class="muted">这一步没有文字内容</p>
          <pre v-if="developerDetails" class="raw">{{ JSON.stringify(full[event.id], null, 2) }}</pre>
        </div>
      </li>
    </ol>
    <v-btn v-if="next !== null" size="small" variant="text" :loading="more.busy.value" @click="loadMore">显示更多</v-btn>
    <ErrorNote v-if="more.error.value" title="读取更多记录失败" :error="more.error.value" />
  </section>
</template>

<style scoped>
.events{display:grid;gap:8px}
ol{list-style:none;margin:0;padding:0;display:grid}
li{border-bottom:1px solid var(--line)}
li:last-child{border-bottom:0}
.line{display:grid;grid-template-columns:110px 1fr;gap:12px;width:100%;text-align:left;padding:8px 0;background:none;border:0;cursor:pointer;color:inherit;font:inherit}
.line:hover span:last-child{color:var(--primary)}
.body{display:grid;gap:8px;padding:0 0 10px 122px}
.body pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;font-size:13px}
.body img{max-width:100%;height:auto;border-radius:8px}
.raw{background:var(--code-bg);padding:8px;border-radius:8px}
@media(max-width:600px){.body{padding-left:0}}
</style>
