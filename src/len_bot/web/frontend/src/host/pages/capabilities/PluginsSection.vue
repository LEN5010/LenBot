<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { mdiCheck, mdiDotsVertical, mdiPlus, mdiPuzzleOutline } from '@mdi/js'
import { api, sceneName } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { confirm } from '../../../composables/useConfirm.js'
import { notify, readPendingRestart } from '../../store.js'
import { formatTime } from '../../time.js'
import { same } from '../../forms.js'
import { initialField, configValue } from '../../pluginConfig.js'
import { pluginsApi } from '../../api/plugins.js'
import SchemaForm from '../../components/SchemaForm.vue'
import PageTabs from '../../ui/PageTabs.vue'
import ResourceState from '../../ui/ResourceState.vue'
import Panel from '../../ui/Panel.vue'
import SettingSection from '../../ui/SettingSection.vue'
import ObjectList from '../../ui/ObjectList.vue'
import ObjectRow from '../../ui/ObjectRow.vue'
import StatusBadge from '../../ui/StatusBadge.vue'
import FactList from '../../ui/FactList.vue'
import FormDialog from '../../ui/FormDialog.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import Fold from '../../ui/Fold.vue'
import AdvancedFields from '../../ui/AdvancedFields.vue'
import DevOnly from '../../ui/DevOnly.vue'
import PluginCatalog from './PluginCatalog.vue'

const emit = defineEmits(['dirty'])
const route = useRoute(), router = useRouter()
const plugins = useResource(() => pluginsApi.read())
const save = useAction()
const active = ref('')
const drafts = ref({}), paths = ref(null)
const zipImport = ref(false), zipFile = ref(null), switchSource = ref(false)
const installing = ref(false), repository = ref(''), repositoryRef = ref('')
const catalogDirty = ref(false), updating = ref(null), updateRef = ref('')
const snapshot = computed(() => plugins.data.value)

const view = computed(() => route.query.view === 'discover' ? 'discover' : 'installed')
const selected = computed(() => typeof route.query.item === 'string' ? route.query.item : null)
const open = name => router.push({ query: { ...route.query, view: undefined, item: name } })
const back = () => router.push({ query: { ...route.query, item: undefined } })

const running = computed(() => Object.fromEntries((snapshot.value?.running.plugins || []).map(item => [item.name, item])))
const names = computed(() => snapshot.value ? [...new Set([...Object.keys(snapshot.value.available),
  ...Object.keys(snapshot.value.saved.plugins), ...Object.keys(running.value)])].sort() : [])
const loaded = computed(() => Object.keys(snapshot.value?.saved.plugins || {}).sort())
const sceneList = computed(() => Object.keys(snapshot.value?.scenes || {}))
const sceneSaved = scene => snapshot.value?.scenes[scene]?.saved || []
const usedIn = name => sceneList.value.filter(scene => sceneSaved(scene).includes(name))
const version = name => running.value[name]?.version || manifest(name)?.source?.installed?.version || manifest(name)?.version || ''

function manifest(name) {
  const entries = snapshot.value.available[name] || []
  return entries.length === 1 && !entries[0].error ? entries[0] : null
}
function status(name) {
  if (snapshot.value.saved.disabled.includes(name)) return { text: '已停用', tone: 'neutral' }
  if (running.value[name]) return { kind: 'plugin', value: running.value[name].status }
  return { kind: 'plugin', value: snapshot.value.saved.plugins[name] ? 'pending' : 'unloaded' }
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
  if (part === 'paths') paths.value = initialPaths()
  else drafts.value[part] = initial(part)
}
watch(() => plugins.data.value, (value, previous) => {
  if (!value) return
  for (const name of names.value) if (!drafts.value[name]) reset(name)
  if (!previous) reset('paths')
})

watch(selected, (_, previous) => { if (previous && snapshot.value && names.value.includes(previous)) reset(previous) })
const pluginDirty = name => Boolean(drafts.value[name]) && !same(drafts.value[name], initial(name))
const pathsDirty = computed(() => paths.value !== null && !same(paths.value, initialPaths()))
const dirty = computed(() => Boolean(snapshot.value) && (names.value.some(pluginDirty) || pathsDirty.value || catalogDirty.value))
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
    if (part !== 'scene') reset(part)
    readPendingRestart()
    notify('已保存')
  } else await plugins.reload()
}
const savePlugin = name => send(name, `/api/host/plugins/${encodeURIComponent(name)}`,
  () => drafts.value[name].enabled ? { enabled: true, config: configBody(name) } : { enabled: false })
const switching = ref('')
async function toggleScene(scene, name, on) {
  switching.value = scene
  try {
    await send('scene', `/api/host/scenes/${encodeURIComponent(scene)}/plugins`,
      { plugins: on ? [...sceneSaved(scene), name] : sceneSaved(scene).filter(item => item !== name) })
  } finally { switching.value = '' }
}
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
      installing.value = false
      zipImport.value = false
      zipFile.value = null
      switchSource.value = false
      if (!keepPathsDraft) reset('paths')
      reset(result.operation.name)
      notify(result.operation.needs_config ? '候选已准备，请填写参数后应用' : '候选已准备，可以应用')
      open(result.operation.name)
    } else {
      if (names.value.includes(part)) reset(part)
      else delete drafts.value[part]
      notify(result.running.plugins.some(item => item.name === part && item.status === 'failed') ? '操作未完成，请看插件错误' : '操作完成')
    }
    readPendingRestart()
  } else await plugins.reload()
  return result
}
const install = () => operate('install', () => pluginsApi.install(repository.value.trim(), repositoryRef.value.trim() || null, switchSource.value))
const importZip = () => operate('install', () => pluginsApi.importZip(zipFile.value, switchSource.value))
const manage = (name, action) => operate(name, () => api(`/api/host/plugins/${encodeURIComponent(name)}/${action}`, { method: 'POST' }))
function installEntry(entry) {
  repository.value = entry.repository
  repositoryRef.value = entry.ref || ''
  installing.value = true
}
function chooseUpdate(name, selectedRef = null) {
  updating.value = name
  updateRef.value = selectedRef ?? manifest(name)?.source?.ref ?? ''
}
async function update() {
  const name = updating.value
  const result = await operate(name, () => pluginsApi.update(name, updateRef.value.trim() || null))
  if (result) updating.value = null
}
const removals = {
  source: { title: name => `卸载插件 ${name}？`, text: '停止它的处理器和后台，移除源码和启用配置；插件自己的数据保留。', confirmLabel: '卸载并保留数据' },
  data: { title: name => `删除插件 ${name} 的数据？`, text: '删除这个插件的数据目录，包括它保存的设置和素材，聊天记录不受影响。删除后不能恢复。', confirmLabel: '删除数据' },
}
async function remove(name, kind) {
  const question = removals[kind]
  if (!await confirm({ title: question.title(name), text: question.text, confirmLabel: question.confirmLabel, danger: true })) return
  const result = await operate(name, () => api(kind === 'source' ? `/api/host/plugins/${encodeURIComponent(name)}`
    : `/api/host/plugin-data/${encodeURIComponent(name)}`, { method: 'DELETE' }))
  if (result && kind === 'source') back()
}
const errorOf = part => active.value === part ? save.error.value : null
const facts = name => {
  const source = manifest(name)?.source
  return [
    ['源码版本', source?.installed ? `v${source.installed.version}` : source ? '尚未应用' : manifest(name) ? `v${manifest(name).version}` : ''],
    ['候选版本', source?.candidate ? `v${source.candidate.version}` : ''],
    ['生效方式', source?.candidate ? source.application === 'host' ? (source.requested ? '已选择，下次明确重启时应用' : '应用后需要重启') : '应用时重载这个插件' : ''],
    ['运行版本', running.value[name]?.version ? `v${running.value[name].version}` : '未加载'],
    ['在用的群', running.value[name]?.scenes.map(sceneName).join('、') || '没有'],
    ['安装来源', source?.installed ? `${source.installed.kind} · ${source.installed.location} · ${source.installed.ref || source.installed.branch || source.installed.revision}` : ''],
    ['候选来源', source?.candidate ? `${source.candidate.kind} · ${source.candidate.location} · ${source.candidate.revision}` : ''],
    ['兼容范围', manifest(name) ? `宿主 ${manifest(name).requires_lenbot} · Python ${manifest(name).requires_python} · ${manifest(name).platforms.join('、')}` : ''],
    ['依赖', manifest(name)?.dependencies.join('、') || ''],
  ]
}
</script>

<template>
  <PageTabs :tabs="[['installed', '已安装'], ['discover', '发现插件']]" :model-value="view" param="view" label="插件" />
  <ResourceState :resource="plugins" error-title="读取插件失败">
    <PluginCatalog v-if="view === 'discover'" :snapshot="snapshot" :busy="save.busy.value" @dirty="value => catalogDirty = value"
      @install="installEntry" @configure="open" @update="entry => chooseUpdate(entry.name, entry.ref)" />

    <div v-else class="stack">
      <div class="inline toolbar">
        <strong>{{ names.length }} 个插件</strong>
        <v-btn class="ml-auto" size="small" variant="outlined" :prepend-icon="mdiPlus" @click="installing = true">从 Git 安装</v-btn>
        <v-btn size="small" variant="outlined" :prepend-icon="mdiPlus" @click="zipImport = true">导入 ZIP</v-btn>
      </div>
      <div v-if="names.length" class="plugin-grid">
        <button v-for="name in names" :key="name" type="button" class="plugin-card" @click="open(name)">
          <span class="card-head">
            <span class="plugin-icon" :class="status(name).tone || status(name).value"><v-icon :icon="mdiPuzzleOutline" size="18" /></span>
            <span class="card-title"><strong>{{ name }}</strong><span v-if="version(name)" class="muted small">v{{ version(name) }}</span></span>
            <StatusBadge dot v-bind="status(name)" />
          </span>
          <span class="card-description muted">{{ manifest(name)?.description || '' }}</span>
          <span class="small muted">{{ usedIn(name).length ? `${usedIn(name).length} 个群` : '没有群在用' }}</span>
        </button>
      </div>
      <p v-else class="muted">还没有插件。<RouterLink :to="{ query: { ...route.query, view: 'discover' } }">去发现插件里看看</RouterLink></p>
      <ErrorNote v-for="error in [...snapshot.discovery_errors, ...snapshot.running.discovery_errors]" :key="error" title="有插件目录读不了" :error="error" />
      <Panel v-if="snapshot.retained_data.length" title="已卸载插件留下的数据" flush>
        <ObjectList class="list" divided>
          <ObjectRow v-for="item in snapshot.retained_data" :key="item.name" :title="item.name" :subtitle="item.directory">
            <template #actions><v-btn variant="text" size="small" color="error" :disabled="save.busy.value" @click="remove(item.name, 'data')">删除数据</v-btn></template>
          </ObjectRow>
        </ObjectList>
      </Panel>
      <form @submit.prevent="savePaths">
        <AdvancedFields label="插件目录">
          <v-textarea v-model="paths.paths" rows="2" auto-grow label="插件目录（每行一个）" placeholder="plugins" />
          <v-text-field v-model="paths.data_directory" label="插件数据目录" />
          <ErrorNote v-if="errorOf('paths')" title="没有保存成功" :error="errorOf('paths')" />
          <v-btn type="submit" color="primary" class="justify-self-start" :loading="save.busy.value && active === 'paths'" :disabled="!pathsDirty">保存插件目录</v-btn>
        </AdvancedFields>
      </form>
    </div>
  </ResourceState>

  <FormDialog v-if="snapshot" :model-value="Boolean(selected && names.includes(selected))" :title="selected || ''" size="lg" cancel-label="关闭"
    @update:model-value="value => { if (!value) back() }">
    <template v-if="selected && names.includes(selected)">
      <div class="inline">
        <StatusBadge v-bind="status(selected)" />
        <a v-if="manifest(selected)?.repository" :href="manifest(selected).repository" target="_blank" rel="noopener noreferrer">源码仓库</a>
        <a v-if="manifest(selected)?.homepage" :href="manifest(selected).homepage" target="_blank" rel="noopener noreferrer">使用说明</a>
        <v-menu>
          <template #activator="{ props: menu }"><v-btn v-bind="menu" class="ml-auto" :icon="mdiDotsVertical" variant="text" size="small" aria-label="更多操作" /></template>
          <v-list density="compact">
            <v-list-item v-if="snapshot.saved.plugins[selected]" title="重载" :disabled="save.busy.value || pluginDirty(selected)" @click="manage(selected, 'reload')" />
            <v-list-item v-if="manifest(selected)?.source?.installed?.kind === 'git'" title="更新／选择版本" :disabled="save.busy.value || pluginDirty(selected)" @click="chooseUpdate(selected)" />
            <v-list-item v-if="manifest(selected)?.managed" title="卸载（保留数据）" base-color="error" :disabled="save.busy.value" @click="remove(selected, 'source')" />
            <v-list-item v-if="snapshot.saved.disabled.includes(selected)" title="删除插件数据" base-color="error" :disabled="save.busy.value" @click="remove(selected, 'data')" />
          </v-list>
        </v-menu>
      </div>
      <p v-if="manifest(selected)?.description" class="muted">{{ manifest(selected).description }}</p>
      <div v-if="manifest(selected)?.source?.candidate" class="inline">
        <v-btn color="primary" :loading="save.busy.value" :disabled="pluginDirty(selected)" @click="manage(selected, 'apply')">{{ manifest(selected).source.application === 'host' ? '应用并等待重启' : '应用候选版本' }}</v-btn>
        <v-btn variant="text" :disabled="save.busy.value" @click="manage(selected, 'cancel')">取消候选</v-btn>
      </div>
      <div v-if="manifest(selected)?.source?.previous && !manifest(selected)?.source?.candidate" class="inline">
        <v-btn variant="text" :disabled="save.busy.value" @click="manage(selected, 'rollback')">回到上一版本 v{{ manifest(selected).source.previous.version }}</v-btn>
      </div>
      <ErrorNote v-if="manifest(selected)?.source?.error" title="版本应用失败" :error="manifest(selected).source.error" />
      <ErrorNote v-if="errorOf(selected)" title="操作没有完成" :error="errorOf(selected)" />
      <ErrorNote v-if="errorOf('scene')" title="群的启用设置没有保存" :error="errorOf('scene')" />
      <ErrorNote v-if="running[selected]?.error" title="插件没有启动成功" :error="running[selected].error" @retry="manage(selected, 'reload')" />
      <ErrorNote v-for="entry in (snapshot.available[selected] || []).filter(item => item.error)" :key="entry.directory"
        title="插件说明文件读不了" :error="entry.error" />
      <v-alert v-if="(snapshot.available[selected] || []).length > 1" type="warning">有多个目录提供了同名插件，需要删掉多余的一份才能加载。</v-alert>
      <div v-if="loaded.includes(selected)" class="scene-picks">
        <h3>在哪些群使用</h3>
        <div class="inline">
          <v-chip v-for="item in sceneList" :key="item" :disabled="save.busy.value || snapshot.scenes[item].saved === null"
            :color="sceneSaved(item).includes(selected) ? 'primary' : undefined" :variant="sceneSaved(item).includes(selected) ? 'tonal' : 'outlined'"
            :prepend-icon="sceneSaved(item).includes(selected) ? mdiCheck : undefined" :aria-pressed="sceneSaved(item).includes(selected)"
            @click="toggleScene(item, selected, !sceneSaved(item).includes(selected))">
            <v-progress-circular v-if="switching === item" indeterminate size="14" width="2" class="mr-1" />{{ sceneName(item) }}
          </v-chip>
          <span v-if="!sceneList.length" class="muted small">还没有配置群。</span>
        </div>
      </div>

      <SettingSection v-if="manifest(selected) && drafts[selected]" title="参数" :restart="false" save-label="保存并应用"
        :dirty="pluginDirty(selected)" :saving="save.busy.value && active === selected"
        :error="errorOf(selected)" @save="savePlugin(selected)">
        <v-switch v-model="drafts[selected].enabled" label="启用这个插件" />
        <template v-if="drafts[selected].enabled">
          <SchemaForm v-model="drafts[selected].values" :fields="manifest(selected).fields" :scene-choices="snapshot.scene_choices"
            :configured="Object.fromEntries(Object.entries(snapshot.saved.plugins[selected] || {}).map(([key, item]) => [key, Boolean(item.configured)]))" />
        </template>
      </SettingSection>

      <Fold label="版本与来源"><FactList :items="facts(selected)" /></Fold>
      <template v-if="running[selected]">
        <div v-if="running[selected].commands.length || running[selected].rules.length" class="stack">
          <h3>命令与规则</h3>
          <ul class="plain-list commands">
            <li v-for="item in running[selected].commands" :key="item.name"><code>/{{ item.name }}</code> {{ item.description }}</li>
            <li v-for="item in running[selected].rules" :key="`${item.kind}:${item.pattern}`">
              {{ item.kind === 'fullmatch' ? '全文' : '正则' }} <code>{{ item.pattern }}</code> {{ item.description }}</li>
          </ul>
        </div>
        <p v-if="running[selected].tools.length" class="small">模型工具：{{ running[selected].tools.map(item => item.name).join('、') }}</p>
        <p v-if="running[selected].skills.length" class="small">附带技能：{{ running[selected].skills.join('、') }}</p>
        <Fold v-if="running[selected].crons.length" :label="`定点播报 ${running[selected].crons.length} 项`">
          <div v-for="job in running[selected].crons" :key="`${job.scene}:${job.name}`" class="entry">
            <strong>{{ job.name }} · {{ sceneName(job.scene) }}</strong>
            <span class="muted small">{{ job.expression }} · {{ job.timezone }} · 下次 {{ formatTime(job.next_run) }}</span>
            <ErrorNote v-if="job.last_error" title="上次播报失败" :error="job.last_error" />
          </div>
        </Fold>
        <Fold v-if="running[selected].errors.length" :label="`最近报错 ${running[selected].errors.length} 次`">
          <div v-for="(item, index) in running[selected].errors" :key="index" class="entry">
            <span class="muted small">{{ formatTime(item.at) }}</span><ErrorNote title="插件报错" :error="item.error" /></div>
        </Fold>
        <Fold v-if="running[selected].model_calls.length" :label="`最近单次生成 ${running[selected].model_calls.length} 次`">
          <div v-for="call in running[selected].model_calls" :key="call.id" class="entry">
            <span class="small">{{ formatTime(call.started) }} · {{ sceneName(call.scene) }} · {{ call.role }} · {{ call.ended === null ? '进行中' : call.error ? '失败' : '已返回' }}</span>
            <ErrorNote v-if="call.error" title="生成失败" :error="call.error" />
            <DevOnly label="用量与 token" :json="{ usage: call.usage, tokens: call.tokens }" />
          </div>
        </Fold>
      </template>
      <DevOnly label="插件详情" :json="{ available: snapshot.available[selected], running: running[selected] }" />
    </template>
  </FormDialog>

  <FormDialog v-model="installing" title="从 Git 安装插件" :busy="save.busy.value && active === 'install'">
    <v-text-field v-model="repository" label="插件仓库 URL" placeholder="https://example.com/author/plugin.git" />
    <v-text-field v-model="repositoryRef" label="版本（可选）" placeholder="标签、分支或提交" />
    <v-switch v-model="switchSource" label="替换同名插件的安装来源" />
    <ErrorNote v-if="errorOf('install')" title="插件准备没有完成" :error="errorOf('install')" />
    <template #actions><v-btn color="primary" :loading="save.busy.value && active === 'install'" :disabled="!repository.trim()" @click="install">准备候选</v-btn></template>
  </FormDialog>
  <FormDialog v-model="zipImport" title="导入插件 ZIP" :busy="save.busy.value && active === 'install'">
    <v-file-input v-model="zipFile" accept=".zip,application/zip" label="插件 ZIP" />
    <p class="muted">ZIP 根目录或唯一顶层目录须包含 plugin.toml 和 __init__.py。</p>
    <v-switch v-model="switchSource" label="替换同名插件的安装来源" />
    <ErrorNote v-if="errorOf('install')" title="ZIP 准备没有完成" :error="errorOf('install')" />
    <template #actions><v-btn color="primary" :loading="save.busy.value" :disabled="!zipFile" @click="importZip">准备候选</v-btn></template>
  </FormDialog>
  <FormDialog :model-value="updating !== null" :title="`更新 ${updating || ''}`" :busy="save.busy.value" @update:model-value="value => { if (!value) updating = null }">
    <v-text-field v-model="updateRef" label="标签、分支或提交" />
    <v-alert v-if="updating && pluginDirty(updating)" type="warning">这个插件的参数还没保存，请先保存或放弃修改。</v-alert>
    <ErrorNote v-if="updating && errorOf(updating)" title="更新没有完成" :error="errorOf(updating)" />
    <template #actions><v-btn color="primary" :loading="save.busy.value" :disabled="!updating || pluginDirty(updating)" @click="update">准备候选</v-btn></template>
  </FormDialog>
</template>

<style scoped>
.plugin-icon{display:grid;place-items:center;width:34px;height:34px;border-radius:var(--radius-sm);background:var(--track);color:var(--muted)}
.plugin-icon.failed{background:var(--error-bg);color:var(--error)}
.plugin-icon.unloaded,.plugin-icon.neutral{background:var(--hover);color:var(--muted)}
.list{padding:0 var(--sp-2) var(--sp-2)}
.toolbar{align-items:center}
.plugin-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:var(--sp-3)}
.plugin-card{display:grid;gap:var(--sp-2);align-content:start;text-align:left;padding:var(--sp-4);border:1px solid var(--line);border-radius:var(--radius);background:var(--surface);color:inherit;font:inherit;cursor:pointer;transition:border-color .15s,box-shadow .15s}
.plugin-card:hover,.plugin-card:focus-visible{border-color:var(--primary);box-shadow:var(--shadow-hover);outline:none}
.card-head{display:flex;align-items:center;gap:var(--sp-2)}
.card-title{display:grid;flex:1;min-width:0}
.card-title strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card-description{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:2.6em}
.justify-self-start{justify-self:start}
.commands li{margin:var(--sp-1) 0;overflow-wrap:anywhere}
.entry{display:grid;gap:var(--sp-1)}
.scene-picks{display:grid;gap:var(--sp-2)}
.scene-picks h3{margin:0;font-size:var(--fs-md)}
p{margin:0}
</style>
