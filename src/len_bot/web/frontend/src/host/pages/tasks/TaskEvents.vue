<script setup>
import { ref, watch } from 'vue'
import { tasksApi } from '../../api/tasks.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { developerDetails } from '../../../composables/useDeveloperMode.js'
import { formatTime } from '../../time.js'
import Panel from '../../ui/Panel.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import LoadMore from '../../ui/LoadMore.vue'
import CodeBlock from '../../ui/CodeBlock.vue'
import { eventLabel } from './taskLabels.js'

const props = defineProps({
  scene: { type: String, required: true }, taskId: { type: Number, required: true },
  first: { type: Array, required: true }, firstNext: { type: Number, default: null }, timezone: { type: String, default: null },
})
const rows = ref([]), next = ref(null), full = ref({}), wanted = ref(100)
const more = useResource(async () => {
  const events = [...props.first]
  let cursor = events.at(-1)?.id ?? 0, count = events.length
  while (count === 100 && events.length < wanted.value) {
    const page = await tasksApi.detail(props.scene, props.taskId, { after: cursor, limit: 100 })
    events.push(...page.events)
    count = page.events.length
    cursor = page.next_after
  }
  return { events, next: count === 100 ? cursor : null }
}, { immediate: false })
watch(() => props.first, () => more.reload(), { immediate: true })
watch(() => more.data.value, value => { if (value) { rows.value = value.events; next.value = value.next } })
const reading = useAction()
async function loadMore() {
  wanted.value += 100
  await more.reload()
}
async function open(event) {
  if (full.value[event.id]) { const { [event.id]: _, ...rest } = full.value; full.value = rest; return }
  const record = await reading.run(() => tasksApi.event(props.scene, props.taskId, event.id))
  if (record) full.value = { ...full.value, [event.id]: record }
}
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
  <Panel title="过程">
    <ErrorNote v-if="reading.error.value" title="读取这一步失败" :error="reading.error.value" />
    <p v-if="!rows.length" class="muted">还没有记录。</p>
    <ol class="events">
      <li v-for="event in rows" :key="event.id">
        <button type="button" class="line" :aria-expanded="Boolean(full[event.id])" @click="open(event)">
          <span class="muted small">{{ formatTime(event.created, timezone) }}</span>
          <span>{{ eventLabel(event.event_type) }}{{ event.tool_name ? ` · ${event.tool_name}` : '' }}</span>
        </button>
        <div v-if="full[event.id]" class="body">
          <template v-for="(part, index) in parts(full[event.id])" :key="index">
            <CodeBlock v-if="part.type === 'text'" :text="part.text" />
            <img v-else :src="`data:${part.mimeType};base64,${part.data}`" alt="任务里的图片" loading="lazy" />
          </template>
          <ErrorNote v-if="full[event.id].body.error" title="这一步出错了" :error="String(full[event.id].body.error)" />
          <p v-if="!parts(full[event.id]).length && !full[event.id].body.error && !developerDetails" class="muted small">这一步没有文字内容</p>
          <CodeBlock v-if="developerDetails" :text="JSON.stringify(full[event.id], null, 2)" />
        </div>
      </li>
    </ol>
    <LoadMore v-if="next !== null" :loading="more.loading.value" @more="loadMore" />
    <ErrorNote v-if="more.error.value" title="读取更多记录失败" :error="more.error.value" />
  </Panel>
</template>

<style scoped>
.events{list-style:none;margin:0;padding:0;display:grid}
.events li + li{border-top:1px solid var(--line)}
.line{display:grid;grid-template-columns:110px 1fr;gap:var(--sp-3);width:100%;text-align:left;padding:var(--sp-2) var(--sp-1);background:none;border:0;border-radius:var(--radius-sm);cursor:pointer;color:inherit;font:inherit}
.line:hover{background:var(--hover)}
.body{display:grid;gap:var(--sp-2);padding:0 0 var(--sp-3) 122px}
.body p{margin:0}
.body img{max-width:100%;height:auto;border-radius:var(--radius)}
@media(max-width:600px){.body{padding-left:0}.line{grid-template-columns:1fr}}
</style>
