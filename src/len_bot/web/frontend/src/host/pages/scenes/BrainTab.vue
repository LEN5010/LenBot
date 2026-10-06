<script setup>
import { ref } from 'vue'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { confirm } from '../../../composables/useConfirm.js'
import { notify } from '../../store.js'
import { toolLabel } from '../../labels.js'
import { formatTime } from '../../time.js'
import Panel from '../../ui/Panel.vue'
import ResourceState from '../../ui/ResourceState.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import LoadMore from '../../ui/LoadMore.vue'
import CodeBlock from '../../ui/CodeBlock.vue'
import DevOnly from '../../ui/DevOnly.vue'

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
const questions = {
  compact: { title: '整理回想？', text: '把较早的对话整理成回想，会调用一次大脑模型。', confirmLabel: '整理' },
  'new-context': { title: '开始新会话？', text: 'Bot 会从这里重新开始，不再带着之前的对话。聊天记录、记忆和提醒都保留。', confirmLabel: '开始新会话' },
}

function toggleEverything(value) {
  everything.value = value
  page.reload()
}
async function operate(kind) {
  if (!await confirm(questions[kind])) return
  const result = await action.run(() => api(`${root}/${kind}?confirmed=true`, { method: 'POST' }))
  if (result) notify(kind === 'compact' ? '已整理回想' : '已开始新会话')
  page.reload()
}
</script>

<template>
  <ErrorNote v-if="action.error.value" title="操作没有完成" :error="action.error.value" />
  <ResourceState :resource="page" error-title="读取会话失败" v-slot="{ data }">
    <Panel title="回想" description="Bot 对较早对话的概括，会和最近的对话一起交给大脑。">
      <template #actions><v-btn variant="outlined" :loading="action.busy.value" @click="operate('compact')">整理回想</v-btn></template>
      <p v-if="data.recap !== null" class="readable-copy recap">{{ data.recap }}</p>
      <p v-else class="muted recap">还没有回想。</p>
    </Panel>
    <Panel title="最近的会话">
      <template #actions>
        <v-switch :model-value="everything" label="显示之前的会话" @update:model-value="toggleEverything" />
        <v-btn variant="outlined" :loading="action.busy.value" @click="operate('new-context')">开始新会话</v-btn>
      </template>
      <p v-if="!entries.length" class="muted">这里还没有内容。</p>
      <LoadMore v-if="data.next_before !== null" label="更早的内容" :loading="page.loading.value" @more="page.reload(data.next_before)" />
      <ol class="entries">
        <li v-for="entry in entries" :key="entry.seq" :class="[entry.message.role, { old: !entry.active }]">
          <div class="inline"><strong>{{ roleLabels[entry.message.role] || entry.message.role }}</strong>
            <span class="muted small">{{ entry.created === null ? '' : formatTime(entry.created, data.timezone) }}<template v-if="!entry.active"> · 之前的会话</template></span></div>
          <p v-if="entry.message.content" class="readable-copy">{{ text(entry.message.content) }}</p>
          <div v-for="call in entry.message.tool_calls || []" :key="call.id" class="tool-call">
            <strong>{{ toolLabel(call.function.name) }}</strong>
            <CodeBlock :text="text(call.function.arguments)" />
          </div>
          <DevOnly label="原始条目" :json="entry" />
        </li>
      </ol>
    </Panel>
  </ResourceState>
</template>

<style scoped>
.recap{margin:0}
.entries{list-style:none;margin:0;padding:0;display:grid;gap:var(--sp-2)}
.entries li{border:1px solid var(--line);border-radius:var(--radius);padding:var(--sp-3);min-width:0;display:grid;gap:var(--sp-2)}
.entries li.assistant{background:var(--hover)}
.entries li.old{opacity:.65}
.entries p{margin:0}
.tool-call{border-left:2px solid var(--primary);padding-left:var(--sp-3);display:grid;gap:var(--sp-1)}
</style>
