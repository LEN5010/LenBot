<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { mdiCheck, mdiDotsVertical, mdiPlus } from '@mdi/js'
import { api, sceneName } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { confirm } from '../../../composables/useConfirm.js'
import { notify, readPendingRestart } from '../../store.js'
import { formatTime } from '../../time.js'
import { same } from '../../forms.js'
import { initialField, configValue } from '../../pluginConfig.js'
import { pluginsApi } from '../../api/plugins.js'
import PluginField from '../../components/PluginField.vue'
import PageTabs from '../../ui/PageTabs.vue'
import ResourceState from '../../ui/ResourceState.vue'
import MasterDetail from '../../ui/MasterDetail.vue'
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

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty'])
const route = useRoute(), router = useRouter()
const plugins = useResource(() => pluginsApi.read())
const save = useAction()
const active = ref('')
const drafts = ref({}), paths = ref(null)
const installing = ref(false), repository = ref(''), repositoryRef = ref('')
const catalogDirty = ref(false), updating = ref(null), updateRef = ref('')
const recommended = ['rss_broadcast', 'group_digest']
const snapshot = computed(() => plugins.data.value)

// `?view=` picks installed or discover; `?item=` is the open plugin.
const view = computed(() => route.query.view === 'discover' ? 'discover' : 'installed')
const selected = computed(() => typeof route.query.item === 'string' ? route.query.item : null)
const open = name => router.push({ query: { ...route.query, view: undefined, item: name } })
const back = () => router.push({ query: { ...route.query, item: undefined } })

const running = computed(() => Object.fromEntries((snapshot.value?.running.plugins || []).map(item => [item.name, item])))
const names = computed(() => snapshot.value ? [...new Set([...Object.keys(snapshot.value.available),
  ...Object.keys(snapshot.value.saved.plugins), ...Object.keys(running.value)])].sort() : [])
const loaded = computed(() => Object.keys(snapshot.value?.saved.plugins || {}).sort())
const sceneSaved = computed(() => snapshot.value?.scenes[props.scene]?.saved || [])

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
// A fresh read resets every draft; a save only resets the part that was saved.
watch(() => plugins.data.value, (value, previous) => {
  if (!value) return
  for (const name of names.value) if (!drafts.value[name]) reset(name)
  if (!previous) reset('paths')
})

// Leaving a plugin (after the page asked) drops its unsaved parameters.
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
// Turning a plugin on or off for this scene applies at once (the plugin is reloaded, chat keeps running).
const toggleScene = (name, on) => send('scene', `/api/host/scenes/${encodeURIComponent(props.scene)}/plugins`,
  { plugins: on ? [...sceneSaved.value, name] : sceneSaved.value.filter(item => item !== name) })
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
      if (!keepPathsDraft) reset('paths')
      reset(result.operation.name)
      notify(result.operation.needs_config ? '已安装，请填写参数并启用' : '已安装，可为群聊启用')
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
const install = () => operate('install', () => pluginsApi.install(repository.value.trim(), repositoryRef.value.trim() || null))
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
    ['源码版本', manifest(name) ? `v${manifest(name).version}` : ''],
    ['运行版本', running.value[name]?.version ? `v${running.value[name].version}` : '未加载'],
    ['在用的群', running.value[name]?.scenes.map(sceneName).join('、') || '没有'],
    ['安装来源', source ? (source.ref || (source.branch ? `分支 ${source.branch}` : '固定提交')) : ''],
    ['依赖', manifest(name)?.dependencies.join('、') || ''],
  ]
}
</script>

<template>
  <PageTabs :tabs="[['installed', '已安装'], ['discover', '发现插件']]" :model-value="view" param="view" label="插件" />
  <ResourceState :resource="plugins" error-title="读取插件失败">
    <PluginCatalog v-if="view === 'discover'" :snapshot="snapshot" :busy="save.busy.value" @dirty="value => catalogDirty = value"
      @install="installEntry" @configure="open" @update="entry => chooseUpdate(entry.name, entry.ref)" />

    <MasterDetail v-else :selected="selected !== null" @back="back">
      <template #list>
        <div class="stack">
          <Panel title="已安装" flush>
            <template #actions><v-btn size="small" variant="tonal" color="primary" :prepend-icon="mdiPlus" @click="installing = true">从 Git 安装</v-btn></template>
            <ObjectList class="list">
              <ObjectRow v-for="name in names" :key="name" :title="name" clickable :active="selected === name" @click="open(name)"
                :subtitle="manifest(name)?.description || ''">
                <template #meta>
                  <v-icon v-if="sceneSaved.includes(name)" :icon="mdiCheck" size="16" color="primary" :aria-label="`在 ${sceneName(scene)} 使用`" />
                  <StatusBadge dot v-bind="status(name)" />
                </template>
              </ObjectRow>
              <li v-if="!names.length" class="muted empty">还没有插件，可以去发现插件里找找。</li>
            </ObjectList>
          </Panel>
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
              <v-textarea v-model="paths.paths" rows="2" auto-grow label="额外的插件目录" hint="每行一个，相对路径从 LenBot 目录算起" persistent-hint />
              <v-text-field v-model="paths.data_directory" label="插件数据目录" hint="插件保存自己数据的地方" persistent-hint />
              <ErrorNote v-if="errorOf('paths')" title="没有保存成功" :error="errorOf('paths')" />
              <v-btn type="submit" color="primary" class="justify-self-start" :loading="save.busy.value && active === 'paths'" :disabled="!pathsDirty">保存插件目录</v-btn>
            </AdvancedFields>
          </form>
        </div>
      </template>
      <template #placeholder>从左边选一个插件查看和配置。</template>

      <template v-if="selected && names.includes(selected)">
        <Panel :title="selected" :description="manifest(selected)?.description || ''">
          <template #actions>
            <StatusBadge v-bind="status(selected)" />
            <v-chip v-if="recommended.includes(selected)" variant="outlined">内置推荐</v-chip>
            <v-menu>
              <template #activator="{ props: menu }"><v-btn v-bind="menu" :icon="mdiDotsVertical" variant="text" size="small" aria-label="更多操作" /></template>
              <v-list density="compact">
                <v-list-item v-if="snapshot.saved.plugins[selected]" title="重载" :disabled="save.busy.value || pluginDirty(selected)" @click="manage(selected, 'reload')" />
                <v-list-item v-if="manifest(selected)?.managed" title="更新／选择版本" :disabled="save.busy.value || pluginDirty(selected)" @click="chooseUpdate(selected)" />
                <v-list-item v-if="manifest(selected)?.managed" title="卸载（保留数据）" base-color="error" :disabled="save.busy.value" @click="remove(selected, 'source')" />
                <v-list-item v-if="snapshot.saved.disabled.includes(selected)" title="删除插件数据" base-color="error" :disabled="save.busy.value" @click="remove(selected, 'data')" />
              </v-list>
            </v-menu>
          </template>
          <FactList :items="facts(selected)" />
          <div class="inline">
            <a v-if="manifest(selected)?.repository" :href="manifest(selected).repository" target="_blank" rel="noopener noreferrer">源码仓库</a>
            <a v-if="manifest(selected)?.homepage" :href="manifest(selected).homepage" target="_blank" rel="noopener noreferrer">使用说明</a>
          </div>
          <ErrorNote v-if="errorOf(selected)" title="操作没有完成" :error="errorOf(selected)" />
          <ErrorNote v-if="errorOf('scene')" title="本群设置没有保存" :error="errorOf('scene')" />
          <ErrorNote v-if="running[selected]?.error" title="插件没有启动成功" :error="running[selected].error" @retry="manage(selected, 'reload')" />
          <ErrorNote v-for="entry in (snapshot.available[selected] || []).filter(item => item.error)" :key="entry.directory"
            title="插件说明文件读不了" :error="entry.error" />
          <v-alert v-if="(snapshot.available[selected] || []).length > 1" type="warning">有多个目录提供了同名插件，需要删掉多余的一份才能加载。</v-alert>
          <v-switch v-if="loaded.includes(selected)" :model-value="sceneSaved.includes(selected)" :loading="save.busy.value && active === 'scene'"
            :disabled="save.busy.value" :label="`在 ${sceneName(scene)} 使用`" hint="立即生效，只重载这个插件" persistent-hint
            @update:model-value="value => toggleScene(selected, value)" />
        </Panel>

        <SettingSection v-if="manifest(selected) && drafts[selected]" title="参数" :restart="false" save-label="保存并应用"
          description="保存后只重载这个插件，聊天不用重启。" :dirty="pluginDirty(selected)" :saving="save.busy.value && active === selected"
          :error="errorOf(selected)" @save="savePlugin(selected)">
          <v-switch v-model="drafts[selected].enabled" label="启用这个插件" hint="停用后保留参数和数据" persistent-hint />
          <template v-if="drafts[selected].enabled">
            <PluginField v-for="field in manifest(selected).fields" :key="field.key" :field="field"
              v-model="drafts[selected].values[field.key]" :configured="snapshot.saved.plugins[selected]?.[field.key]?.configured" />
          </template>
        </SettingSection>

        <Panel v-if="running[selected]" title="运行情况">
          <p v-if="!running[selected].commands.length && !running[selected].rules.length && !running[selected].crons.length && !running[selected].errors.length && !running[selected].model_calls.length && !running[selected].skills.length && !running[selected].tools.length"
            class="muted">没有命令、规则或后台任务。</p>
          <div v-if="running[selected].commands.length || running[selected].rules.length" class="stack">
            <h3>命令与规则</h3>
            <ul class="plain-list commands">
              <li v-for="item in running[selected].commands" :key="item.name"><code>/{{ item.name }}</code> {{ item.description }}</li>
              <li v-for="item in running[selected].rules" :key="`${item.kind}:${item.pattern}`">
                {{ item.kind === 'fullmatch' ? '全文' : '正则' }} <code>{{ item.pattern }}</code> {{ item.description }}（直接处理，不调用模型）</li>
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
              <DevOnly label="用量与估算费用" :json="{ usage: call.usage, cost: call.cost }" />
            </div>
          </Fold>
        </Panel>
        <DevOnly label="插件详情" :json="{ available: snapshot.available[selected], running: running[selected] }" />
      </template>
    </MasterDetail>
  </ResourceState>

  <FormDialog v-model="installing" title="从 Git 安装插件" :busy="save.busy.value && active === 'install'">
    <p class="muted">安装会执行插件代码并安装它声明的依赖，和 LenBot 共用 Python 环境。</p>
    <v-text-field v-model="repository" label="插件仓库 URL" placeholder="https://example.com/author/plugin.git" />
    <v-text-field v-model="repositoryRef" label="版本（可选）" hint="标签、分支或提交；留空跟随仓库默认分支" persistent-hint />
    <ErrorNote v-if="errorOf('install')" title="插件安装没有完成" :error="errorOf('install')" />
    <template #actions><v-btn color="primary" :loading="save.busy.value && active === 'install'" :disabled="!repository.trim()" @click="install">安装</v-btn></template>
  </FormDialog>
  <FormDialog :model-value="updating !== null" :title="`更新 ${updating || ''}`" :busy="save.busy.value" @update:model-value="value => { if (!value) updating = null }">
    <v-text-field v-model="updateRef" label="标签、分支或提交" hint="留空沿用安装时的版本；没选过版本时更新到当前分支最新" persistent-hint />
    <p class="muted">更新源码和依赖，然后只重载这个插件。</p>
    <v-alert v-if="updating && pluginDirty(updating)" type="warning">这个插件的参数还没保存，请先保存或放弃修改。</v-alert>
    <ErrorNote v-if="updating && errorOf(updating)" title="更新没有完成" :error="errorOf(updating)" />
    <template #actions><v-btn color="primary" :loading="save.busy.value" :disabled="!updating || pluginDirty(updating)" @click="update">更新并重载</v-btn></template>
  </FormDialog>
</template>

<style scoped>
.list{padding:0 var(--sp-2) var(--sp-2)}
.empty{padding:var(--sp-3)}
.justify-self-start{justify-self:start}
.commands li{margin:var(--sp-1) 0;overflow-wrap:anywhere}
.entry{display:grid;gap:var(--sp-1)}
p{margin:0}
</style>
