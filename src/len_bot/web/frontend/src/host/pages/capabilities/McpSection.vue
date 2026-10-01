<script setup>
import { computed, ref, watch } from 'vue'
import { mdiClose, mdiPlus } from '@mdi/js'
import { api, sceneName } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify, readPendingRestart } from '../../store.js'
import { formatTime } from '../../time.js'
import { numberOrBlank } from '../../forms.js'
import ErrorNote from '../../components/ErrorNote.vue'
import DevOnly from '../../components/DevOnly.vue'

const emit = defineEmits(['dirty'])
const mcp = useResource(() => api('/api/host/mcp'))
const save = useAction(), act = useAction()
const editing = ref(null), original = ref(null), actionError = ref(null)

const running = computed(() => Object.fromEntries((mcp.data.value?.running || []).map(item => [item.name, item])))
const names = computed(() => mcp.data.value
  ? [...new Set([...Object.keys(mcp.data.value.saved), ...Object.keys(running.value)])].sort() : [])
const sceneOptions = computed(() => (mcp.data.value?.scenes || []).map(scene => ({ title: sceneName(scene), value: scene })))
const statusLabel = { disabled: '未启用', connecting: '连接中', running: '已连接', failed: '连接失败', stopped: '已断开' }
const statusColor = { running: 'success', failed: 'error', connecting: 'info' }

// Saved env and header values come back as null; a row left blank keeps the saved value.
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
  editing.value = form(name, mcp.data.value.saved[name])
  original.value = JSON.stringify(editing.value)
}
const dirty = computed(() => editing.value !== null && JSON.stringify(editing.value) !== original.value)
watch(dirty, value => emit('dirty', value), { immediate: true })
function close() {
  if (dirty.value && !window.confirm('放弃这次修改？')) return
  editing.value = null
  save.error.value = null
}

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
    editing.value = null
    readPendingRestart()
    notify('已保存，重启后生效')
  }
}
async function remove() {
  const name = editing.value.name
  if (!window.confirm(`删除 MCP 服务 ${name}？重启后它的工具会消失。`)) return
  const result = await save.run(() => api(`/api/host/mcp/${encodeURIComponent(name)}`, { method: 'DELETE' }))
  if (result) {
    mcp.data.value = result
    editing.value = null
    readPendingRestart()
    notify('已删除，重启后生效')
  }
}
async function control(name, action) {
  if (action === 'connect' && !window.confirm(`重新连接 ${name}？当前连接会先断开。`)) return
  actionError.value = null
  const result = await act.run(() => api(`/api/host/mcp/${encodeURIComponent(name)}/${action}`, { method: 'POST' }))
  if (!result) return
  mcp.data.value = result
  if (result.result?.status === 'failed') actionError.value = { name, error: result.result.error }
  else notify(action === 'connect' ? '已连接' : '已断开')
}
</script>

<template>
  <ErrorNote v-if="mcp.error.value" title="读取 MCP 服务失败" :error="mcp.error.value" />
  <section v-if="mcp.data.value" class="surface">
    <div class="heading">
      <div><h2>MCP 服务</h2><p class="muted">接入外部工具服务，Bot 需要时会自己查找并使用这些工具。</p></div>
      <v-btn color="primary" variant="tonal" @click="open('')">添加服务</v-btn>
    </div>
    <ErrorNote v-if="act.error.value" title="操作没有成功" :error="act.error.value" />
    <p v-if="!names.length" class="muted">还没有 MCP 服务。</p>
    <ul class="services">
      <li v-for="name in names" :key="name">
        <div class="service-head">
          <strong>{{ name }}</strong>
          <v-chip size="small" variant="tonal" :color="statusColor[running[name]?.status]">
            {{ running[name] ? statusLabel[running[name].status] : '重启后生效' }}</v-chip>
          <span v-if="running[name]" class="muted">
            {{ running[name].tools.length }} 个工具 · {{ running[name].scenes.map(sceneName).join('、') || '没有群在用' }}</span>
          <v-spacer />
          <v-btn v-if="running[name]" size="small" variant="text" :disabled="act.busy.value || running[name].status === 'disabled'"
            @click="control(name, 'connect')">重新连接</v-btn>
          <v-btn v-if="running[name]" size="small" variant="text" :disabled="act.busy.value || !['running', 'connecting'].includes(running[name].status)"
            @click="control(name, 'disconnect')">断开</v-btn>
          <v-btn v-if="mcp.data.value.saved[name]" size="small" variant="outlined" @click="open(name)">编辑</v-btn>
        </div>
        <ErrorNote v-if="actionError?.name === name" title="没有连上" :error="actionError.error" />
        <ErrorNote v-else-if="running[name]?.error" title="没有连上" :error="running[name].error" />
        <details v-if="running[name]?.tools.length" class="fold"><summary>工具列表</summary>
          <p v-for="tool in running[name].tools" :key="tool.name"><code>{{ tool.name }}</code> {{ tool.description }}</p></details>
        <details v-if="running[name]?.errors.length" class="fold"><summary>最近报错 {{ running[name].errors.length }} 次</summary>
          <div v-for="(item, index) in running[name].errors" :key="index"><span class="muted">{{ formatTime(item.at) }}</span><pre>{{ item.error }}</pre></div></details>
        <DevOnly label="运行详情"><pre>{{ JSON.stringify(running[name] || null, null, 2) }}</pre></DevOnly>
      </li>
    </ul>
  </section>

  <v-dialog :model-value="editing !== null" max-width="640" scrollable persistent>
    <v-card v-if="editing" :title="editing.isNew ? '添加 MCP 服务' : `编辑 ${editing.name}`">
      <v-card-text class="mcp-form">
        <v-text-field v-if="editing.isNew" v-model="editing.name" label="服务名" hint="小写字母开头，可用小写字母、数字和下划线，最多 24 位。" persistent-hint />
        <v-switch v-model="editing.enabled" color="primary" label="启动时连接" hide-details />
        <v-select v-model="editing.scenes" :items="sceneOptions" multiple chips closable-chips label="哪些群能用" />
        <v-btn-toggle v-model="editing.type" mandatory density="comfortable" color="primary">
          <v-btn value="stdio">本机程序</v-btn><v-btn value="http">网络地址</v-btn></v-btn-toggle>
        <template v-if="editing.type === 'stdio'">
          <v-text-field v-model="editing.command" label="启动命令" hint="例如 npx 或程序的完整路径" persistent-hint />
          <v-textarea v-model="editing.args" rows="2" auto-grow label="命令参数" hint="每行一个" persistent-hint />
          <v-text-field v-model="editing.cwd" label="工作目录" />
        </template>
        <v-text-field v-else v-model="editing.url" label="服务地址" hint="例如 http://127.0.0.1:8000/mcp" persistent-hint />
        <div class="pairs">
          <span>{{ editing.type === 'stdio' ? '环境变量' : '请求头' }}</span>
          <div v-for="(row, index) in editing[editing.type === 'stdio' ? 'env' : 'headers']" :key="index" class="pair">
            <v-text-field v-model="row.key" label="名称" density="compact" hide-details :disabled="row.saved" />
            <v-text-field v-model="row.value" label="值" type="password" autocomplete="off" density="compact" hide-details
              :placeholder="row.saved ? '已保存，留空不修改' : ''" persistent-placeholder />
            <v-btn :icon="mdiClose" variant="text" size="small" aria-label="删除这一项"
              @click="editing[editing.type === 'stdio' ? 'env' : 'headers'].splice(index, 1)" />
          </div>
          <v-btn size="small" variant="text" :prepend-icon="mdiPlus"
            @click="editing[editing.type === 'stdio' ? 'env' : 'headers'].push({ key: '', value: '', saved: false })">添加一项</v-btn>
        </div>
        <div class="form-grid">
          <v-text-field :model-value="editing.timeout_seconds" type="number" label="单次调用超时（秒）"
            @update:model-value="value => editing.timeout_seconds = numberOrBlank(value)" />
          <v-text-field :model-value="editing.max_response_bytes" type="number" label="单次结果大小上限（字节）"
            @update:model-value="value => editing.max_response_bytes = numberOrBlank(value)" />
        </div>
        <ErrorNote v-if="save.error.value" title="没有保存成功" :error="save.error.value" />
      </v-card-text>
      <v-card-actions>
        <v-btn v-if="!editing.isNew" color="error" variant="text" :disabled="save.busy.value" @click="remove">删除</v-btn>
        <v-spacer />
        <v-btn :disabled="save.busy.value" @click="close">取消</v-btn>
        <v-btn color="primary" :loading="save.busy.value" :disabled="!dirty || !editing.name.trim()" @click="submit">保存</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.heading{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.services{list-style:none;margin:8px 0 0;padding:0;display:grid}
.services li{padding:12px 0;border-bottom:1px solid var(--line);display:grid;gap:8px;min-width:0}
.services li:last-child{border-bottom:0}
.service-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.fold summary{cursor:pointer;color:var(--muted);font-size:14px}
.fold p{margin:6px 0;overflow-wrap:anywhere}
.fold pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;margin:4px 0 8px}
.mcp-form{display:grid;gap:14px}
.pairs{display:grid;gap:8px}
.pairs>span{font-size:14px;color:var(--muted)}
.pair{display:grid;grid-template-columns:1fr 1fr auto;gap:8px;align-items:center}
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,200px),1fr));gap:12px}
</style>
