<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { mdiPlus } from '@mdi/js'
import { api, sceneName } from '../../../api.js'
import { confirm } from '../../../composables/useConfirm.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify, readPendingRestart } from '../../store.js'
import { formatTime } from '../../time.js'
import { numberOrBlank } from '../../forms.js'
import ResourceState from '../../ui/ResourceState.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import MasterDetail from '../../ui/MasterDetail.vue'
import Panel from '../../ui/Panel.vue'
import SettingSection from '../../ui/SettingSection.vue'
import ObjectList from '../../ui/ObjectList.vue'
import ObjectRow from '../../ui/ObjectRow.vue'
import StatusBadge from '../../ui/StatusBadge.vue'
import RowEditor from '../../ui/RowEditor.vue'
import Fold from '../../ui/Fold.vue'
import DevOnly from '../../ui/DevOnly.vue'

const emit = defineEmits(['dirty'])
const route = useRoute(), router = useRouter()
const mcp = useResource(() => api('/api/host/mcp'))
const save = useAction(), act = useAction()
const editing = ref(null), original = ref(null), actionError = ref(null)
const selected = computed(() => typeof route.query.item === 'string' ? route.query.item : null)
const select = item => router.push({ query: { ...route.query, item } })
const back = () => router.push({ query: { ...route.query, item: undefined } })

const running = computed(() => Object.fromEntries((mcp.data.value?.running || []).map(item => [item.name, item])))
const names = computed(() => mcp.data.value
  ? [...new Set([...Object.keys(mcp.data.value.saved), ...Object.keys(running.value)])].sort() : [])
const sceneOptions = computed(() => (mcp.data.value?.scenes || []).map(scene => ({ title: sceneName(scene), value: scene })))

const rows = values => Object.entries(values || {}).map(([key, value]) => ({ key, value: value ?? '', saved: value === null }))
function form(name, saved) {
  const transport = saved?.transport || { type: 'stdio', command: '', args: [], cwd: '.', env: {} }
  return {
    name, isNew: !saved, enabled: saved?.enabled ?? true, scenes: [...(saved?.scenes || [])],
    timeout_seconds: saved?.timeout_seconds ?? 30, max_response_bytes: saved?.max_response_bytes ?? 1048576,
    type: transport.type,
    command: transport.command || '', args: (transport.args || []).join('\n'), cwd: transport.cwd || '.',
    url: transport.url || '', env: rows(transport.env), headers: rows(transport.headers),
  }
}
function open(name) {
  editing.value = name === null || (name && !mcp.data.value.saved[name]) ? null : form(name, mcp.data.value.saved[name])
  original.value = JSON.stringify(editing.value)
  save.error.value = null
  actionError.value = null
}
watch(() => [selected.value, Boolean(mcp.data.value)], () => { if (mcp.data.value) open(selected.value) }, { immediate: true })
const dirty = computed(() => editing.value !== null && JSON.stringify(editing.value) !== original.value)
watch(dirty, value => emit('dirty', value), { immediate: true })

function pairs(list) {
  return Object.fromEntries(list.filter(row => row.key.trim()).map(row => [row.key.trim(), row.saved && row.value === '' ? null : row.value]))
}
function body() {
  const draft = editing.value
  const transport = draft.type === 'stdio'
    ? { type: 'stdio', command: draft.command, args: draft.args.split('\n').map(item => item.trim()).filter(Boolean), cwd: draft.cwd, env: pairs(draft.env) }
    : { type: 'http', url: draft.url, headers: pairs(draft.headers) }
  return { enabled: draft.enabled, scenes: draft.scenes, timeout_seconds: draft.timeout_seconds,
    max_response_bytes: draft.max_response_bytes, transport }
}
async function submit() {
  const name = editing.value.name.trim()
  const result = await save.run(() => api(`/api/host/mcp/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify({ settings: body() }) }))
  if (result) {
    mcp.data.value = result
    original.value = JSON.stringify(editing.value)
    readPendingRestart()
    notify('已保存，重启后生效')
    if (selected.value !== name) router.replace({ query: { ...route.query, item: name } })
  }
}
async function remove() {
  const name = editing.value.name
  if (!await confirm({ title: `删除 MCP 服务 ${name}？`, text: '重启后它的工具会消失。', confirmLabel: '删除', danger: true })) return
  const result = await save.run(() => api(`/api/host/mcp/${encodeURIComponent(name)}`, { method: 'DELETE' }))
  if (result) {
    mcp.data.value = result
    original.value = JSON.stringify(editing.value)
    readPendingRestart()
    notify('已删除，重启后生效')
    back()
  }
}
async function control(name, action) {
  if (action === 'connect' && !await confirm({ title: `重新连接 ${name}？`, text: '当前连接会先断开。', confirmLabel: '重新连接' })) return
  actionError.value = null
  const result = await act.run(() => api(`/api/host/mcp/${encodeURIComponent(name)}/${action}`, { method: 'POST' }))
  if (!result) return
  mcp.data.value = result
  if (result.result?.status === 'failed') actionError.value = { name, error: result.result.error }
  else notify(action === 'connect' ? '已连接' : '已断开')
}
const pairKey = computed(() => editing.value?.type === 'stdio' ? 'env' : 'headers')
const runtimeOf = name => running.value[name]
</script>

<template>
  <ResourceState :resource="mcp" error-title="读取 MCP 服务失败">
    <MasterDetail :selected="selected !== null" :empty="!names.length" @back="back">
      <template #list>
        <Panel title="MCP 服务" flush>
          <template #actions><v-btn variant="outlined" size="small" :prepend-icon="mdiPlus" @click="select('')">添加</v-btn></template>
          <ObjectList class="list">
            <ObjectRow v-for="name in names" :key="name" :title="name" clickable :active="selected === name" @click="select(name)"
              :subtitle="runtimeOf(name) ? `${runtimeOf(name).tools.length} 个工具 · ${runtimeOf(name).scenes.map(sceneName).join('、') || '没有群在用'}` : '重启后生效'">
              <template #meta><StatusBadge dot :kind="runtimeOf(name) ? 'mcp' : ''" :value="runtimeOf(name)?.status" :text="runtimeOf(name) ? '' : '待重启'" :tone="runtimeOf(name) ? '' : 'warning'" /></template>
            </ObjectRow>
            <li v-if="!names.length" class="muted empty">还没有 MCP 服务。接入后，Bot 需要时会自己找到并使用它提供的工具。</li>
          </ObjectList>
        </Panel>
      </template>
      <template #placeholder>接入外部工具服务，Bot 需要时会自己查找并使用这些工具。从左边选一个服务，或添加新的。</template>

      <Panel v-if="selected && runtimeOf(selected)" :title="selected">
        <template #actions>
          <StatusBadge kind="mcp" :value="runtimeOf(selected).status" />
          <v-btn size="small" variant="outlined" :disabled="act.busy.value || runtimeOf(selected).status === 'disabled'" @click="control(selected, 'connect')">重新连接</v-btn>
          <v-btn size="small" variant="text" :disabled="act.busy.value || !['running', 'connecting'].includes(runtimeOf(selected).status)" @click="control(selected, 'disconnect')">断开</v-btn>
        </template>
        <ErrorNote v-if="act.error.value" title="操作没有成功" :error="act.error.value" />
        <ErrorNote v-if="actionError?.name === selected" title="没有连上" :error="actionError.error" />
        <ErrorNote v-else-if="runtimeOf(selected).error" title="没有连上" :error="runtimeOf(selected).error" />
        <p class="muted small">{{ runtimeOf(selected).scenes.map(sceneName).join('、') || '没有群在用' }}</p>
        <Fold v-if="runtimeOf(selected).tools.length" :label="`工具 ${runtimeOf(selected).tools.length} 个`">
          <p v-for="tool in runtimeOf(selected).tools" :key="tool.name" class="tool"><code>{{ tool.name }}</code> {{ tool.description }}</p></Fold>
        <Fold v-if="runtimeOf(selected).errors.length" :label="`最近报错 ${runtimeOf(selected).errors.length} 次`">
          <div v-for="(item, index) in runtimeOf(selected).errors" :key="index"><span class="muted small">{{ formatTime(item.at) }}</span>
            <ErrorNote title="调用出错" :error="item.error" /></div></Fold>
        <DevOnly label="运行详情" :json="runtimeOf(selected)" />
      </Panel>

      <SettingSection v-if="editing" :title="editing.isNew ? '添加 MCP 服务' : '设置'" :dirty="dirty || (editing.isNew && Boolean(editing.name.trim()))"
        :problem="editing.name.trim() ? '' : '请填写服务名'" :saving="save.busy.value" :error="save.error.value" @save="submit">
        <template v-if="!editing.isNew" #actions><v-btn color="error" variant="text" size="small" :disabled="save.busy.value" @click="remove">删除服务</v-btn></template>
        <v-text-field v-if="editing.isNew" v-model="editing.name" label="服务名" placeholder="小写字母、数字和下划线，最多 24 位" />
        <v-switch v-model="editing.enabled" label="启动时连接" />
        <v-select v-model="editing.scenes" :items="sceneOptions" multiple chips closable-chips label="哪些群能用" />
        <v-btn-toggle v-model="editing.type" mandatory>
          <v-btn value="stdio">本机程序</v-btn><v-btn value="http">网络地址</v-btn></v-btn-toggle>
        <template v-if="editing.type === 'stdio'">
          <v-text-field v-model="editing.command" label="启动命令" placeholder="npx" />
          <v-textarea v-model="editing.args" rows="2" auto-grow label="命令参数" />
          <v-text-field v-model="editing.cwd" label="工作目录" />
        </template>
        <v-text-field v-else v-model="editing.url" label="服务地址" placeholder="http://127.0.0.1:8000/mcp" />
        <h3>{{ editing.type === 'stdio' ? '环境变量' : '请求头' }}</h3>
        <RowEditor :items="editing[pairKey]" :make="() => ({ key: '', value: '', saved: false })" add-label="添加一项">
          <template #default="{ item }">
            <v-text-field v-model="item.key" label="名称" :disabled="item.saved" />
            <v-text-field v-model="item.value" label="值" type="password" autocomplete="off"
              :placeholder="item.saved ? '已保存，留空不修改' : ''" persistent-placeholder />
          </template>
        </RowEditor>
        <div class="form-grid">
          <v-text-field :model-value="editing.timeout_seconds" type="number" label="单次调用超时（秒）"
            @update:model-value="value => editing.timeout_seconds = numberOrBlank(value)" />
          <v-text-field :model-value="editing.max_response_bytes" type="number" label="单次结果大小上限（字节）"
            @update:model-value="value => editing.max_response_bytes = numberOrBlank(value)" />
        </div>
      </SettingSection>
    </MasterDetail>
  </ResourceState>
</template>

<style scoped>
.list{padding:0 var(--sp-2) var(--sp-2)}
.empty{padding:var(--sp-3)}
.tool{margin:0;overflow-wrap:anywhere}
</style>
