<script setup>
import { computed, nextTick, ref, watch } from 'vue'
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
import PluginCatalog from './PluginCatalog.vue'
import { pluginsApi } from '../../api/plugins.js'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty'])
const plugins = useResource(() => pluginsApi.read())
const save = useAction()
const active = ref('')
const drafts = ref({}), sceneDraft = ref(null), paths = ref(null)
const repository = ref(''), repositoryRef = ref(''), removal = ref(null)
const view = ref('discover'), catalogDirty = ref(false), updating = ref(null), updateRef = ref('')
const recommended = ['rss_broadcast', 'group_digest']
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
  return { enabled: Boolean(saved) && !snapshot.value.saved.disabled.includes(name), values }
}
const initialPaths = () => ({ paths: snapshot.value.saved.paths.join('\n'), data_directory: snapshot.value.saved.data_directory })
function reset(part) {
  if (part === 'scene') sceneDraft.value = [...sceneSaved.value]
  else if (part === 'paths') paths.value = initialPaths()
  else drafts.value[part] = initial(part)
}
// A fresh read resets every draft; a save only resets the part that was saved.
watch(() => plugins.data.value, (value, previous) => {
  if (!value) return
  for (const name of names.value) if (!drafts.value[name]) reset(name)
  if (!previous) {
    reset('scene')
    reset('paths')
  }
})

const pluginDirty = name => Boolean(drafts.value[name]) && !same(drafts.value[name], initial(name))
const sceneDirty = computed(() => sceneDraft.value !== null && !same([...sceneDraft.value].sort(), [...sceneSaved.value].sort()))
const pathsDirty = computed(() => paths.value !== null && !same(paths.value, initialPaths()))
const dirty = computed(() => Boolean(snapshot.value) && (names.value.some(pluginDirty) || sceneDirty.value || pathsDirty.value || catalogDirty.value))
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
  } else await plugins.reload()
}
const savePlugin = name => send(name, `/api/host/plugins/${encodeURIComponent(name)}`,
  () => drafts.value[name].enabled ? { enabled: true, config: configBody(name) } : { enabled: false })
const saveScene = () => send('scene', `/api/host/scenes/${encodeURIComponent(props.scene)}/plugins`, { plugins: sceneDraft.value })
const savePaths = () => send('paths', '/api/host/plugin-paths', {
  paths: paths.value.paths.split('\n').map(item => item.trim()).filter(Boolean), data_directory: paths.value.data_directory.trim(),
})
async function operate(part, action) {
  active.value = part
  const result = await save.run(action)
  if (result) {
    const keepPathsDraft = pathsDirty.value
    plugins.data.value = result
    if (part === 'install') {
      repository.value = ''
      repositoryRef.value = ''
      if (!keepPathsDraft) reset('paths')
      reset(result.operation.name)
      notify(result.operation.needs_config ? '已安装，请填写参数并启用' : '已安装，可为群聊启用')
      configure(result.operation.name)
    } else {
      if (names.value.includes(part)) reset(part)
      else delete drafts.value[part]
      notify(result.running.plugins.some(item => item.name === part && item.status === 'failed') ? '操作未完成，请看插件错误' : '操作完成')
    }
    removal.value = null
    readPendingRestart()
  } else await plugins.reload()
  return result
}
const install = () => operate('install', () => pluginsApi.install(repository.value.trim(), repositoryRef.value.trim() || null))
const manage = (name, action) => operate(name, () => api(`/api/host/plugins/${encodeURIComponent(name)}/${action}`, { method: 'POST' }))
async function configure(name) {
  view.value = 'installed'
  await nextTick()
  document.getElementById(`plugin-${name}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
async function installEntry(entry) {
  repository.value = entry.repository; repositoryRef.value = entry.ref || ''; view.value = 'installed'
  await install()
}
function chooseUpdate(name, selectedRef = null) {
  view.value = 'installed'; updating.value = name
  updateRef.value = selectedRef ?? manifest(name)?.source?.ref ?? ''
}
async function update() {
  const name = updating.value
  const result = await operate(name, () => pluginsApi.update(name, updateRef.value.trim() || null))
  if (result) updating.value = null
}
async function remove() {
  const { name, kind } = removal.value
  const result = await operate(name, () => api(kind === 'source' ? `/api/host/plugins/${encodeURIComponent(name)}`
    : `/api/host/plugin-data/${encodeURIComponent(name)}`, { method: 'DELETE' }))
  if (result && kind === 'source') sceneDraft.value = sceneDraft.value.filter(item => item !== name)
}
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
    <nav class="plugin-actions" aria-label="插件视图">
      <v-btn :variant="view === 'discover' ? 'tonal' : 'text'" @click="view = 'discover'">发现</v-btn>
      <v-btn :variant="view === 'installed' ? 'tonal' : 'text'" @click="view = 'installed'">已安装与配置</v-btn>
    </nav>
    <PluginCatalog v-show="view === 'discover'" :snapshot="snapshot" :busy="save.busy.value" @dirty="value => catalogDirty = value"
      @install="installEntry" @configure="configure" @update="entry => chooseUpdate(entry.name, entry.ref)" />
    <div v-show="view === 'installed'" class="installed-plugins">
    <form class="surface install-plugin" @submit.prevent="install">
      <h2>安装插件</h2>
      <p class="muted">填写 Git 仓库 URL。安装会执行插件代码并安装清单声明的依赖；共享当前 Python 环境，依赖冲突直接报错。</p>
      <v-text-field v-model="repository" label="插件仓库 URL" placeholder="https://example.com/author/plugin.git"
        :disabled="save.busy.value && active === 'install'" hide-details />
      <v-text-field v-model="repositoryRef" label="发行 ref（可选）" hint="标签、分支或提交；留空跟随仓库默认分支。" persistent-hint :disabled="save.busy.value" />
      <ErrorNote v-if="errorOf('install')" title="插件安装未完成" :error="errorOf('install')" />
      <v-btn type="submit" color="primary" :loading="save.busy.value && active === 'install'"
        :disabled="!repository.trim() || save.busy.value">安装</v-btn>
    </form>
    <ErrorNote v-for="error in [...snapshot.discovery_errors, ...snapshot.running.discovery_errors]" :key="error" title="有插件目录读不了" :error="error" />

    <SettingSection :title="`${sceneName(scene)} 用哪些插件`" description="先安装并配置插件，再为本群打开。保存会重载涉及的插件，不重启聊天；全局停用的插件保留群配置但不执行。"
      :dirty="sceneDirty" :saving="save.busy.value && active === 'scene'" :error="errorOf('scene')" @save="saveScene">
      <p v-if="!loaded.length" class="muted">还没有加载任何插件。</p>
      <div class="choice">
        <v-checkbox v-for="name in loaded" :key="name" :label="name" hide-details
          :model-value="sceneDraft.includes(name)" @update:model-value="value => toggleScene(name, value)" />
      </div>
    </SettingSection>

    <SettingSection v-for="name in names" :id="`plugin-${name}`" :key="name" :title="name" :description="manifest(name)?.description || ''"
      :dirty="pluginDirty(name)" :saving="save.busy.value && active === name" :error="errorOf(name)" save-label="保存并应用" @save="savePlugin(name)">
      <div class="plugin-status">
        <span v-if="manifest(name)" class="muted">源码 v{{ manifest(name).version }} · 运行 {{ running[name]?.version ? `v${running[name].version}` : '未加载' }}</span>
        <v-chip size="small" variant="tonal" :color="statusColor[running[name]?.status]">
          {{ running[name] ? statusLabel[running[name].status] || running[name].status : snapshot.saved.plugins[name] ? '重启后加载' : '未加载' }}</v-chip>
        <span v-if="running[name]?.scenes.length" class="muted">在 {{ running[name].scenes.map(sceneName).join('、') }} 使用</span>
        <a v-if="manifest(name)?.repository" :href="manifest(name).repository" target="_blank" rel="noopener noreferrer">源码仓库</a>
        <a v-if="manifest(name)?.homepage" :href="manifest(name).homepage" target="_blank" rel="noopener noreferrer">使用说明</a>
        <v-chip v-if="recommended.includes(name)" size="small" variant="outlined">内置推荐</v-chip>
      </div>
      <div class="plugin-actions">
        <v-btn v-if="snapshot.saved.plugins[name]" type="button" variant="tonal" size="small"
          :disabled="save.busy.value || pluginDirty(name)" @click="manage(name, 'reload')">重载</v-btn>
        <v-btn v-if="manifest(name)?.managed" type="button" variant="tonal" size="small"
          :disabled="save.busy.value || pluginDirty(name)" @click="chooseUpdate(name)">更新／选择版本</v-btn>
        <v-btn v-if="manifest(name)?.managed" type="button" variant="text" size="small" color="error"
          :disabled="save.busy.value" @click="removal = { name, kind: 'source' }">卸载（保留数据）</v-btn>
        <v-btn v-if="snapshot.saved.disabled.includes(name)" type="button" variant="text" size="small" color="error"
          :disabled="save.busy.value" @click="removal = { name, kind: 'data' }">单独删除数据</v-btn>
      </div>
      <p v-if="manifest(name)?.source" class="muted">安装定位：{{ manifest(name).source.ref || (manifest(name).source.branch ? `分支 ${manifest(name).source.branch}` : '分离的 HEAD') }} · Git {{ manifest(name).source.revision }}</p>
      <p v-if="manifest(name)?.dependencies.length" class="muted">声明依赖：{{ manifest(name).dependencies.join('、') }}</p>
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
      <p v-if="running[name]?.tools.length" class="muted">模型工具：{{ running[name].tools.map(item => item.name).join('、') }}（仍按角色工具许可开放）</p>

      <template v-if="manifest(name) && drafts[name]">
        <v-switch v-model="drafts[name].enabled" color="primary" hide-details label="启用这个插件（停用保留参数和数据）" />
        <template v-if="drafts[name].enabled">
          <PluginField v-for="field in manifest(name).fields" :key="field.key" :field="field"
            v-model="drafts[name].values[field.key]" :configured="snapshot.saved.plugins[name]?.[field.key]?.configured" />
        </template>
      </template>
      <div v-if="loaded.includes(name)" class="plugin-actions">
        <v-checkbox :model-value="sceneDraft.includes(name)" :label="`在 ${sceneName(scene)} 使用`" hide-details
          :disabled="save.busy.value"
          @update:model-value="value => toggleScene(name, value)" />
        <v-btn type="button" size="small" variant="tonal" :disabled="!sceneDirty || save.busy.value" @click="saveScene">保存本群选择</v-btn>
      </div>
      <DevOnly label="插件详情"><pre>{{ JSON.stringify({ available: snapshot.available[name], running: running[name] }, null, 2) }}</pre></DevOnly>
    </SettingSection>
    <p v-if="!names.length" class="surface muted">没有找到插件。</p>

    <section v-if="snapshot.retained_data.length" class="surface">
      <h2>已保留的插件数据</h2>
      <div v-for="item in snapshot.retained_data" :key="item.name" class="plugin-status">
        <span>{{ item.name }} · {{ item.directory }}</span>
        <v-btn variant="text" size="small" color="error" @click="removal = { name: item.name, kind: 'data' }">单独删除数据</v-btn>
      </div>
    </section>

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
    </div>
  </template>
  <v-dialog :model-value="updating !== null" max-width="560" @update:model-value="value => { if (!value) updating = null }">
    <v-card v-if="updating" :title="`更新 ${updating}`">
      <v-card-text>
        <v-text-field v-model="updateRef" label="标签、分支或提交" hint="留空沿用安装定位；从未选择 ref 时快进当前分支。" persistent-hint />
        <p class="muted">更新源码和依赖，然后只重载这个插件。当前未保存的插件配置先保存。</p>
        <ErrorNote v-if="errorOf(updating)" title="更新未完成" :error="errorOf(updating)" />
      </v-card-text>
      <v-card-actions><v-btn :disabled="save.busy.value" @click="updating = null">取消</v-btn><v-spacer />
        <v-btn color="primary" :loading="save.busy.value" :disabled="pluginDirty(updating)" @click="update">更新并重载</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
  <v-dialog :model-value="removal !== null" max-width="560" @update:model-value="value => { if (!value) removal = null }">
    <v-card v-if="removal" :title="`${removal.kind === 'source' ? '卸载插件' : '删除插件数据'} ${removal.name}`">
      <v-card-text>{{ removal.kind === 'source' ? '停止本插件的处理器与后台，移除源码和启用配置；KV 与其他数据保留。' : '只删除此插件的数据目录，包括 KV 和素材，不删除聊天历史。此操作不可撤销。' }}</v-card-text>
      <v-card-text v-if="errorOf(removal.name)"><ErrorNote title="操作未完成" :error="errorOf(removal.name)" /></v-card-text>
      <v-card-actions><v-spacer /><v-btn @click="removal = null">取消</v-btn>
        <v-btn color="error" :loading="save.busy.value" @click="remove">{{ removal.kind === 'source' ? '卸载并保留数据' : '删除数据' }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.choice{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,200px),1fr));gap:0 12px}
.installed-plugins{display:grid;gap:16px}
.plugin-status{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.install-plugin{display:grid;gap:14px}
.install-plugin :deep(.v-btn){justify-self:start}
.plugin-actions{display:flex;gap:8px;flex-wrap:wrap}
.commands{margin:0;padding-left:18px}
.commands li{margin:4px 0}
.recent-errors summary{cursor:pointer;color:var(--muted)}
.recent-errors pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;margin:4px 0 8px}
.mono :deep(textarea){font-family:monospace}
</style>
