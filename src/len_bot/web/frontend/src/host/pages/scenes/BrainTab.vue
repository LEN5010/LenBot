<script setup>
import { ref } from 'vue'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify } from '../../store.js'
import { toolLabel } from '../../labels.js'
import { formatTime } from '../../time.js'
import ErrorNote from '../../components/ErrorNote.vue'
import DevOnly from '../../components/DevOnly.vue'

const props = defineProps({ scene: { type: String, required: true } })
const root = `/api/host/scenes/${encodeURIComponent(props.scene)}/history`
const everything = ref(false), entries = ref([])
const page = useResource(async (before = null) => {
  const query = new URLSearchParams({ limit: 50, active_only: !everything.value, ...(before ? { before } : {}) })
  const value = await api(`${root}?${query}`)
  entries.value = before ? [...value.entries, ...entries.value] : value.entries
  return value
})
const action = useAction()
const roleLabels = { user: '交给大脑的内容', assistant: 'Bot 的想法', tool: '工具结果', system: '系统设定' }
const text = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2)

function toggleEverything(value) {
  everything.value = value
  page.reload()
}
async function operate(kind) {
  const question = kind === 'compact'
    ? '把较早的对话整理成回想？会调用一次大脑模型。'
    : '开始新会话？Bot 会从这里重新开始，不再带着之前的对话。聊天记录、记忆和提醒都保留。'
  if (!window.confirm(question)) return
  const result = await action.run(() => api(`${root}/${kind}?confirmed=true`, { method: 'POST' }))
  if (result) notify(kind === 'compact' ? '已整理回想' : '已开始新会话')
  page.reload()
}
</script>

<template>
  <div class="page-stack">
    <div class="brain-actions">
      <v-btn variant="outlined" :loading="action.busy.value" @click="operate('compact')">整理回想</v-btn>
      <v-btn variant="outlined" :loading="action.busy.value" @click="operate('new-context')">开始新会话</v-btn>
      <v-switch :model-value="everything" label="显示之前的会话" class="ml-auto" @update:model-value="toggleEverything" />
    </div>
    <ErrorNote v-if="action.error.value" title="操作没有完成" :error="action.error.value" />
    <ErrorNote v-if="page.error.value" title="读取会话失败" :error="page.error.value" />
    <template v-if="page.data.value">
      <section class="surface">
        <h2>回想</h2>
        <p class="muted">Bot 对较早对话的概括，会和最近的对话一起交给大脑。</p>
        <p v-if="page.data.value.recap !== null" class="readable-copy">{{ page.data.value.recap }}</p>
        <p v-else class="muted">还没有回想。</p>
      </section>
      <section class="surface">
        <h2>最近的会话</h2>
        <p v-if="!entries.length" class="empty-state">这里还没有内容</p>
        <v-btn v-if="page.data.value.next_before !== null" variant="text" :loading="page.loading.value"
          @click="page.reload(page.data.value.next_before)">更早的内容</v-btn>
        <ol class="entries">
          <li v-for="entry in entries" :key="entry.seq" :class="[entry.message.role, { old: !entry.active }]">
            <div class="entry-head"><strong>{{ roleLabels[entry.message.role] || entry.message.role }}</strong>
              <span class="muted">{{ entry.created === null ? '' : formatTime(entry.created, page.data.value.timezone) }}<template v-if="!entry.active"> · 之前的会话</template></span></div>
            <p v-if="entry.message.content" class="readable-copy">{{ text(entry.message.content) }}</p>
            <div v-for="call in entry.message.tool_calls || []" :key="call.id" class="tool-call">
              <strong>{{ toolLabel(call.function.name) }}</strong>
              <pre>{{ text(call.function.arguments) }}</pre>
            </div>
            <DevOnly label="原始条目"><pre>{{ JSON.stringify(entry, null, 2) }}</pre></DevOnly>
          </li>
        </ol>
      </section>
    </template>
  </div>
</template>

<style scoped>
.brain-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.entries{list-style:none;margin:0;padding:0;display:grid;gap:10px}
.entries li{border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0}
.entries li.assistant{background:var(--selected-bg)}
.entries li.old{opacity:.65}
.entry-head{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.entries p{margin:6px 0 0}
.tool-call{border-left:3px solid var(--primary);padding:4px 10px;margin-top:8px}
.tool-call pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:4px 0 0;font-size:12px}
</style>
