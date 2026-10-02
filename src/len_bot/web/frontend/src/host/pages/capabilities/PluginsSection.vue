<script setup>
import { computed, ref, watch } from 'vue'
import { api, sceneName } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify, readPendingRestart } from '../../store.js'
import { formatTime } from '../../time.js'
import { same } from '../../forms.js'
import { initialField, configValue } from '../../pluginConfig.js'
import PluginField from '../../components/PluginField.vue'
import SettingSection from '../../components/SettingSection.vue'
import ErrorNote from '../../components/ErrorNote.vue'
import AdvancedFields from '../../components/AdvancedFields.vue'
import DevOnly from '../../components/DevOnly.vue'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty'])
const plugins = useResource(() => api('/api/host/plugins'))
const save = useAction()
const active = ref('')
const drafts = ref({}), sceneDraft = ref(null), paths = ref(null)
const snapshot = computed(() => plugins.data.value)

const running = computed(() => Object.fromEntries((snapshot.value?.running.plugins || []).map(item => [item.name, item])))
const names = computed(() => snapshot.value ? [...new Set([...Object.keys(snapshot.value.available),
  ...Object.keys(snapshot.value.saved.plugins), ...Object.keys(running.value)])].sort() : [])
const loaded = computed(() => Object.keys(snapshot.value?.saved.plugins || {}).sort())
const sceneSaved = computed(() => snapshot.value?.scenes[props.scene]?.saved || [])

function manifest(name) {
  const entries = snapshot.value.available[name] || []
  return entries.length === 1 && !entries[0].error ? entries[0] : null
}
function initial(name) {
  const saved = snapshot.value.saved.plugins[name]
  const values = {}
  for (const field of manifest(name)?.fields || []) {
    const item = saved?.[field.key]
    values[field.key] = initialField(field, item && 'value' in item ? item.value : field.default)
  }
  return { enabled: Boolean(saved), values }
}
const initialPaths = () => ({ paths: snapshot.value.saved.paths.join('\n'), data_directory: snapshot.value.saved.data_directory })
function reset(part) {
  if (part === 'scene') sceneDraft.value = [...sceneSaved.value]
  else if (part === 'paths') paths.value = initialPaths()
  else drafts.value[part] = initial(part)
}
// A fresh read resets every draft; a save only resets the part that was saved.
watch(() => plugins.data.value, (value, previous) => {
  if (!value || previous) return
  for (const name of names.value) reset(name)
  reset('scene')
  reset('paths')
})

const pluginDirty = name => Boolean(drafts.value[name]) && !same(drafts.value[name], initial(name))
const sceneDirty = computed(() => sceneDraft.value !== null && !same([...sceneDraft.value].sort(), [...sceneSaved.value].sort()))
const pathsDirty = computed(() => paths.value !== null && !same(paths.value, initialPaths()))
const dirty = computed(() => Boolean(snapshot.value) && (names.value.some(pluginDirty) || sceneDirty.value || pathsDirty.value))
watch(dirty, value => emit('dirty', value), { immediate: true })

function configBody(name) {
  const draft = drafts.value[name], config = {}
  for (const field of manifest(name).fields) {
    const value = draft.values[field.key]
    if (field.type === 'secret') {
      if (value !== '') config[field.key] = value
      else if (snapshot.value.saved.plugins[name]?.[field.key]?.configured) config[field.key] = null
    } else {
      const parsed = configValue(field, value)
      if (parsed !== undefined) config[field.key] = parsed
    }
  }
  return config
}
async function send(part, path, body) {
  active.value = part
  const result = await save.run(async () => api(path, { method: 'PUT', body: JSON.stringify(typeof body === 'function' ? body() : body) }))
  if (result) {
    plugins.data.value = result
    reset(part)
    readPendingRestart()
    notify('已保存')
  }
}
const savePlugin = name => send(name, `/api/host/plugins/${encodeURIComponent(name)}`,
  () => drafts.value[name].enabled ? { enabled: true, config: configBody(name) } : { enabled: false })
const saveScene = () => send('scene', `/api/host/scenes/${encodeURIComponent(props.scene)}/plugins`, { plugins: sceneDraft.value })
const savePaths = () => send('paths', '/api/host/plugin-paths', {
  paths: paths.value.paths.split('\n').map(item => item.trim()).filter(Boolean), data_directory: paths.value.data_directory.trim(),
})
function toggleScene(name, on) {
  sceneDraft.value = on ? [...sceneDraft.value, name] : sceneDraft.value.filter(item => item !== name)
}
const statusLabel = { running: '运行中', loaded: '已加载', failed: '没有启动成功', stopped: '已停止' }
const statusColor = { running: 'success', failed: 'error' }
const errorOf = part => active.value === part ? save.error.value : null
</script>

<template>
  <ErrorNote v-if="plugins.error.value" title="读取插件失败" :error="plugins.error.value" />
  <template v-if="snapshot && sceneDraft !== null">
    <ErrorNote v-for="error in [...snapshot.discovery_errors, ...snapshot.running.discovery_errors]" :key="error" title="有插件目录读不了" :error="error" />

    <SettingSection :title="`${sceneName(scene)} 用哪些插件`" description="插件可提供命令、全文／正则匹配、自动播报和模型工具。先在下面加载插件，再在这里为本群打开。"
      :dirty="sceneDirty" :saving="save.busy.value && active === 'scene'" :error="errorOf('scene')" @save="saveScene">
      <p v-if="!loaded.length" class="muted">还没有加载任何插件。</p>
      <div class="choice">
        <v-checkbox v-for="name in loaded" :key="name" :label="name" hide-details
          :model-value="sceneDraft.includes(name)" @update:model-value="value => toggleScene(name, value)" />
      </div>
    </SettingSection>

    <SettingSection v-for="name in names" :key="name" :title="name" :description="manifest(name)?.description || ''"
      :dirty="pluginDirty(name)" :saving="save.busy.value && active === name" :error="errorOf(name)" @save="savePlugin(name)">
      <div class="plugin-status">
        <v-chip size="small" variant="tonal" :color="statusColor[running[name]?.status]">
          {{ running[name] ? statusLabel[running[name].status] || running[name].status : snapshot.saved.plugins[name] ? '重启后加载' : '未加载' }}</v-chip>
        <span v-if="running[name]?.scenes.length" class="muted">在 {{ running[name].scenes.map(sceneName).join('、') }} 使用</span>
        <a v-if="manifest(name)?.repository" :href="manifest(name).repository" target="_blank" rel="noopener noreferrer">源码仓库</a>
        <a v-if="manifest(name)?.homepage" :href="manifest(name).homepage" target="_blank" rel="noopener noreferrer">使用说明</a>
      </div>
      <ErrorNote v-if="running[name]?.error" title="插件没有启动成功" :error="running[name].error" />
      <ErrorNote v-for="entry in (snapshot.available[name] || []).filter(item => item.error)" :key="entry.directory"
        title="插件说明文件读不了" :error="entry.error" />
      <v-alert v-if="(snapshot.available[name] || []).length > 1" type="warning" variant="tonal" density="compact">
        有多个目录提供了同名插件，需要删掉多余的一份才能加载。</v-alert>
      <ul v-if="running[name]?.commands.length" class="commands">
        <li v-for="item in running[name].commands" :key="item.name"><code>/{{ item.name }}</code> {{ item.description }}</li>
      </ul>
      <ul v-if="running[name]?.rules.length" class="commands">
        <li v-for="item in running[name].rules" :key="`${item.kind}:${item.pattern}`">
          {{ item.kind === 'fullmatch' ? '全文' : '正则' }} <code>{{ item.pattern }}</code> {{ item.description }}（直接处理，不调用模型）
        </li>
      </ul>
      <details v-if="running[name]?.errors.length" class="recent-errors">
        <summary>最近报错 {{ running[name].errors.length }} 次</summary>
        <div v-for="(item, index) in running[name].errors" :key="index">
          <span class="muted">{{ formatTime(item.at) }}</span><pre>{{ item.error }}</pre></div>
      </details>
      <details v-if="running[name]?.crons.length" class="recent-errors">
        <summary>定点播报 {{ running[name].crons.length }} 项</summary>
        <div v-for="job in running[name].crons" :key="`${job.scene}:${job.name}`">
          <strong>{{ job.name }} · {{ sceneName(job.scene) }}</strong>
          <p>{{ job.expression }} · {{ job.timezone }} · 下次 {{ formatTime(job.next_run) }}</p>
          <ErrorNote v-if="job.last_error" title="本次播报失败" :error="job.last_error" />
        </div>
      </details>
      <details v-if="running[name]?.model_calls.length" class="recent-errors">
        <summary>最近单次生成 {{ running[name].model_calls.length }} 次</summary>
        <div v-for="call in running[name].model_calls" :key="call.id">
          <p>{{ formatTime(call.started) }} · {{ sceneName(call.scene) }} · {{ call.role }} · {{ call.ended === null ? '进行中' : call.error ? '失败' : '已返回' }}</p>
          <ErrorNote v-if="call.error" title="生成失败" :error="call.error" />
          <DevOnly label="用量与估算费用"><pre>{{ JSON.stringify({ usage: call.usage, cost: call.cost }, null, 2) }}</pre></DevOnly>
        </div>
      </details>
      <p v-if="running[name]?.skills.length" class="muted">附带只读技能：{{ running[name].skills.join('、') }}</p>

      <template v-if="manifest(name) && drafts[name]">
        <v-switch v-model="drafts[name].enabled" color="primary" hide-details label="加载这个插件" />
        <template v-if="drafts[name].enabled">
          <PluginField v-for="field in manifest(name).fields" :key="field.key" :field="field"
            v-model="drafts[name].values[field.key]" :configured="snapshot.saved.plugins[name]?.[field.key]?.configured" />
        </template>
      </template>
      <DevOnly label="插件详情"><pre>{{ JSON.stringify({ available: snapshot.available[name], running: running[name] }, null, 2) }}</pre></DevOnly>
    </SettingSection>
    <p v-if="!names.length" class="surface muted">没有找到插件。</p>

    <form @submit.prevent="savePaths">
      <AdvancedFields label="插件目录">
        <v-textarea v-model="paths.paths" rows="2" auto-grow label="额外的插件目录" hint="每行一个，相对路径从 LenBot 目录算起。" persistent-hint />
        <v-text-field v-model="paths.data_directory" label="插件数据目录" hint="插件保存自己数据的地方。" persistent-hint />
        <div>
          <ErrorNote v-if="errorOf('paths')" title="没有保存成功" :error="errorOf('paths')" />
          <v-btn type="submit" color="primary" :loading="save.busy.value && active === 'paths'" :disabled="!pathsDirty">保存插件目录</v-btn>
        </div>
      </AdvancedFields>
    </form>
  </template>
</template>

<style scoped>
.choice{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,200px),1fr));gap:0 12px}
.plugin-status{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.commands{margin:0;padding-left:18px}
.commands li{margin:4px 0}
.recent-errors summary{cursor:pointer;color:var(--muted)}
.recent-errors pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;margin:4px 0 8px}
.mono :deep(textarea){font-family:monospace}
</style>
