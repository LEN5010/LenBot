<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, fmtTime, sceneName } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty', 'busy'])
const snapshot = ref(null), loading = ref(false), saving = ref('')
const readError = ref(''), saveError = ref(''), savedNotice = ref('')
const drafts = ref({}), sceneDraft = ref([]), pathsDraft = ref(''), dataDraft = ref('')
const beginRead = useRequestGuard()
const beginSave = useRequestGuard()

const STATUS = { running: '运行中', loaded: '已加载，尚未启动', failed: '加载失败', stopped: '已停止' }
const running = computed(() => Object.fromEntries((snapshot.value?.running.plugins || []).map(item => [item.name, item])))
const names = computed(() => {
  if (!snapshot.value) return []
  return [...new Set([...Object.keys(snapshot.value.available), ...Object.keys(snapshot.value.saved.plugins),
    ...Object.keys(running.value)])].sort()
})
const configured = computed(() => Object.keys(snapshot.value?.saved.plugins || {}).sort())
const sceneState = computed(() => snapshot.value?.scenes[props.scene] || null)

function manifest(name) {
  const entries = snapshot.value.available[name] || []
  return entries.length === 1 && !entries[0].error ? entries[0] : null
}
function initial(name) {
  const saved = snapshot.value.saved.plugins[name]
  const values = {}
  for (const field of manifest(name)?.fields || []) {
    const item = saved?.[field.key]
    values[field.key] = field.type === 'secret' ? ''
      : item && 'value' in item ? (field.type === 'string_list' ? item.value.join('\n') : item.value)
      : field.default == null ? (field.type === 'boolean' ? false : '')
      : (field.type === 'string_list' ? field.default.join('\n') : field.default)
  }
  return { enabled: Boolean(saved), values }
}
function adopt(value, savedPart = null) {
  const retained = savedPart === null ? {} : Object.fromEntries(names.value
    .filter(name => !(savedPart.kind === 'plugin' && name === savedPart.name) && pluginDirty(name)).map(name => [name, drafts.value[name]]))
  const keepScene = savedPart !== null && savedPart.kind !== 'scene' && sceneDirty.value
  const keepPaths = savedPart !== null && savedPart.kind !== 'paths' && pathsDirty.value
  snapshot.value = value
  drafts.value = Object.fromEntries(names.value.map(name => [name, retained[name] || initial(name)]))
  if (!keepScene) sceneDraft.value = [...(value.scenes[props.scene]?.saved || [])]
  if (!keepPaths) {
    pathsDraft.value = value.saved.paths.join('\n')
    dataDraft.value = value.saved.data_directory
  }
}
const pluginDirty = name => snapshot.value && JSON.stringify(drafts.value[name]) !== JSON.stringify(initial(name))
const sceneDirty = computed(() => sceneState.value && JSON.stringify(sceneDraft.value) !== JSON.stringify(sceneState.value.saved || []))
const pathsDirty = computed(() => snapshot.value && (pathsDraft.value !== snapshot.value.saved.paths.join('\n')
  || dataDraft.value !== snapshot.value.saved.data_directory))
const dirty = computed(() => Boolean(snapshot.value) && (names.value.some(pluginDirty) || sceneDirty.value || pathsDirty.value))
watch(dirty, value => emit('dirty', value), { immediate: true })
watch(saving, value => emit('busy', Boolean(value)), { immediate: true })
onBeforeUnmount(() => { emit('dirty', false); emit('busy', false) })
watch(() => props.scene, () => { if (snapshot.value) sceneDraft.value = [...(snapshot.value.scenes[props.scene]?.saved || [])] })

async function read(confirmDiscard = true) {
  if (confirmDiscard && dirty.value && !window.confirm('放弃未保存的插件草稿，重读保存值与运行状态？')) return
  const fresh = beginRead()
  loading.value = true
  try {
    const value = await api('/api/host/plugins')
    if (!fresh()) return
    adopt(value); readError.value = ''; saveError.value = ''
  } catch (error) { if (fresh()) readError.value = error.message }
  finally { if (fresh()) loading.value = false }
}
function configBody(name) {
  const draft = drafts.value[name], config = {}
  for (const field of manifest(name).fields) {
    const value = draft.values[field.key]
    if (field.type === 'secret') {
      if (value !== '') config[field.key] = value
      else if (snapshot.value.saved.plugins[name]?.[field.key]?.configured) config[field.key] = null
      continue
    }
    if (field.type === 'string_list') {
      const items = value.split('\n').map(item => item.trim()).filter(Boolean)
      config[field.key] = items
    } else if (field.type === 'integer' || field.type === 'number') {
      if (value === '' || value === null) { if (field.required) config[field.key] = value; continue }
      config[field.key] = Number(value)
    } else config[field.key] = value
  }
  return config
}
async function send(label, path, body, notice, part = { kind: 'plugin', name: label }) {
  if (saving.value) return
  const fresh = beginSave()
  beginRead() // An older full read cannot replace drafts after this save.
  loading.value = false
  saving.value = label; saveError.value = ''; savedNotice.value = ''
  try {
    const value = await api(path, { method: 'PUT', body: JSON.stringify(body) })
    if (!fresh()) return
    adopt(value, part)
    savedNotice.value = notice + (value.restart_required ? '当前运行的插件不变，重启宿主后生效。' : '与当前运行值一致。')
  } catch (error) {
    if (fresh()) saveError.value = error.status >= 400 && error.status < 500
      ? `保存未被接受：${error.message}`
      : `保存结果未确认：${error.message} 草稿已保留；请重读核对，不会自动重试。`
  } finally { if (fresh()) saving.value = '' }
}
function savePlugin(name) {
  const draft = drafts.value[name]
  const body = draft.enabled ? { enabled: true, config: configBody(name) } : { enabled: false }
  send(name, `/api/host/plugins/${encodeURIComponent(name)}`, body,
    draft.enabled ? `插件 ${name} 的配置已保存到根配置；` : `插件 ${name} 已从根配置移除；`)
}
function saveScene() {
  send('scene', `/api/host/scenes/${encodeURIComponent(props.scene)}/plugins`, { plugins: sceneDraft.value },
    `${sceneName(props.scene)} 的插件启用名单已保存；`, { kind: 'scene' })
}
function savePaths() {
  send('paths', '/api/host/plugin-paths', {
    paths: pathsDraft.value.split('\n').map(item => item.trim()).filter(Boolean), data_directory: dataDraft.value.trim()
  }, '插件目录已保存；', { kind: 'paths' })
}
function toggleScene(name, enabled) {
  sceneDraft.value = enabled ? [...sceneDraft.value, name] : sceneDraft.value.filter(item => item !== name)
}
function every(seconds) {
  return seconds % 3600 === 0 ? `${seconds / 3600} 小时` : seconds % 60 === 0 ? `${seconds / 60} 分钟` : `${seconds} 秒`
}
onMounted(() => read(false))
</script>

<template>
  <section class="surface plugins" aria-labelledby="plugins-title">
    <div class="section-heading">
      <h2 id="plugins-title">插件</h2>
      <div class="heading-actions">
        <v-chip v-if="snapshot" variant="tonal" :color="snapshot.restart_required ? 'warning' : 'info'">
          {{ snapshot.restart_required ? '保存值待重启' : '保存值与运行值一致' }}</v-chip>
        <v-btn variant="outlined" :loading="loading" :disabled="Boolean(saving)" @click="read()">重读插件</v-btn>
      </div>
    </div>
    <p class="muted">插件在宿主进程内运行，命令以 <code>/名称</code> 触发且不叫醒大脑；插件工具只能经 tool_search 发现。报错只影响该插件，宿主不重试、不自动停用。</p>
    <v-alert v-if="readError" type="error" variant="tonal" role="alert"
      :title="snapshot ? '读取失败 · 保留上次快照' : '读取插件失败'">{{ readError }}</v-alert>
    <v-alert v-if="saveError" type="error" variant="tonal" role="alert">{{ saveError }}</v-alert>
    <v-alert v-if="savedNotice && !dirty" type="success" variant="tonal" role="status">{{ savedNotice }}</v-alert>
    <p v-if="loading && !snapshot" class="muted" role="status">正在读取插件…</p>
    <template v-if="snapshot">
      <v-alert v-for="error in [...snapshot.discovery_errors, ...snapshot.running.discovery_errors]" :key="error"
        type="warning" variant="tonal" class="mb-3">{{ error }}</v-alert>

      <div v-if="sceneState" class="scene-box">
        <h3>{{ sceneName(scene) }} 启用的插件</h3>
        <p class="muted">运行中：{{ sceneState.running.join('、') || '无' }}；保存值：{{ (sceneState.saved || []).join('、') || '无' }}</p>
        <p v-if="!configured.length" class="muted">根配置还没有加载任何插件；先在下方为插件打开“加载”。</p>
        <div class="choice">
          <v-checkbox v-for="name in configured" :key="name" :label="name" hide-details
            :model-value="sceneDraft.includes(name)" :disabled="Boolean(saving)"
            @update:model-value="value => toggleScene(name, value)" />
        </div>
        <v-btn color="primary" :loading="saving === 'scene'" :disabled="!sceneDirty || Boolean(saving)" @click="saveScene">保存本场景启用名单</v-btn>
      </div>

      <ul class="plugin-list">
        <li v-for="name in names" :key="name" class="plugin-item">
          <div class="plugin-heading">
            <strong>{{ name }}</strong>
            <div class="badges">
              <v-chip size="small" variant="tonal" :color="running[name]?.status === 'running' ? 'success' : running[name]?.status === 'failed' ? 'error' : 'default'">
                {{ running[name] ? STATUS[running[name].status] : '当前未加载' }}</v-chip>
              <v-chip size="small" variant="tonal">{{ snapshot.saved.plugins[name] ? '根配置：加载' : '根配置：不加载' }}</v-chip>
              <v-chip v-if="running[name]?.errors.length" size="small" variant="tonal" color="error">最近错误 {{ running[name].errors.length }} 条</v-chip>
            </div>
          </div>
          <p v-if="manifest(name)">{{ manifest(name).description }} <span class="muted">· v{{ manifest(name).version }} · {{ manifest(name).license }}</span></p>
          <p class="muted path" v-for="entry in snapshot.available[name] || []" :key="entry.directory">目录：{{ entry.directory }}</p>
          <v-alert v-for="entry in (snapshot.available[name] || []).filter(item => item.error)" :key="'e' + entry.directory"
            type="warning" variant="tonal" density="compact">清单无法读取：{{ entry.error }}</v-alert>
          <v-alert v-if="(snapshot.available[name] || []).length > 1" type="warning" variant="tonal" density="compact">
            多个目录提供了同名插件，加载时会拒绝。</v-alert>
          <details v-if="running[name]?.error" open><summary>加载或启动失败原文</summary><pre>{{ running[name].error }}</pre></details>

          <dl v-if="running[name]" class="entries">
            <div v-if="running[name].commands.length"><dt>命令</dt>
              <dd v-for="item in running[name].commands" :key="item.name"><code>/{{ item.name }}</code> {{ item.description }}</dd></div>
            <div v-if="running[name].notices.length"><dt>平台通知</dt><dd>{{ running[name].notices.join('、') }}</dd></div>
            <div v-if="running[name].tools.length"><dt>低频工具</dt>
              <dd v-for="item in running[name].tools" :key="item.name"><code>{{ item.name }}</code> {{ item.description }}</dd></div>
            <div v-if="running[name].backgrounds.length"><dt>后台任务</dt>
              <dd v-for="item in running[name].backgrounds" :key="item.method">
                {{ item.method }} · 每 {{ every(item.every_seconds) }} ·
                最近开始 {{ item.last_started ? fmtTime(item.last_started) : '尚未运行' }}
                <span v-if="item.last_error" class="error-text"> · 上次出错：{{ item.last_error }}</span></dd></div>
            <div><dt>启用场景（运行中）</dt><dd>{{ running[name].scenes.map(sceneName).join('、') || '无' }}</dd></div>
          </dl>
          <details v-if="running[name]?.errors.length" class="errors">
            <summary>最近错误（本次启动内，最多 20 条）</summary>
            <ul><li v-for="(item, index) in running[name].errors" :key="index">
              <span class="muted">{{ fmtTime(item.at) }} · {{ item.where }}</span><pre>{{ item.error }}</pre></li></ul>
          </details>

          <form v-if="manifest(name) && drafts[name]" class="config" @submit.prevent="savePlugin(name)">
            <v-switch v-model="drafts[name].enabled" color="primary" hide-details :disabled="Boolean(saving)"
              label="加载这个插件（写入根配置 plugins.名称）" />
            <template v-if="drafts[name].enabled">
              <div v-for="field in manifest(name).fields" :key="field.key" class="field">
                <v-switch v-if="field.type === 'boolean'" v-model="drafts[name].values[field.key]" color="primary"
                  :label="field.key" :hint="field.description" persistent-hint :disabled="Boolean(saving)" />
                <v-textarea v-else-if="field.type === 'string_list'" v-model="drafts[name].values[field.key]" rows="2" auto-grow
                  :label="field.key + (field.required ? '（必填）' : '')" :hint="field.description + ' 每行一项。'" persistent-hint :disabled="Boolean(saving)" />
                <v-text-field v-else v-model="drafts[name].values[field.key]"
                  :type="field.type === 'secret' ? 'password' : field.type === 'integer' || field.type === 'number' ? 'number' : 'text'"
                  :label="field.key + (field.required ? '（必填）' : '')" :disabled="Boolean(saving)" persistent-hint
                  :hint="field.description + (field.type === 'secret' && snapshot.saved.plugins[name]?.[field.key]?.configured ? ' 已保存；留空保持原值。' : '')" />
              </div>
              <p v-if="!manifest(name).fields.length" class="muted">这个插件没有配置项。</p>
            </template>
            <div class="form-actions">
              <v-btn type="submit" color="primary" :loading="saving === name" :disabled="!pluginDirty(name) || Boolean(saving)">保存插件配置</v-btn>
              <span class="muted">写入根配置；取消加载前需先在各场景停用。</span>
            </div>
          </form>
        </li>
      </ul>
      <p v-if="!names.length" class="muted">没有发现可用插件。</p>

      <form class="paths" @submit.prevent="savePaths">
        <h3>插件目录</h3>
        <v-textarea v-model="pathsDraft" rows="2" auto-grow label="额外插件目录（plugins.paths）" :disabled="Boolean(saving)"
          hint="每行一个目录，相对路径按实例根目录解释；目录下每个子目录是一个插件，例如个人仓库的 plugins/。" persistent-hint />
        <v-text-field v-model="dataDraft" label="插件数据目录（plugins.data_directory）" :disabled="Boolean(saving)"
          hint="必须在实例根目录内；每个插件使用其中一个子目录。" persistent-hint />
        <v-btn type="submit" color="primary" :loading="saving === 'paths'" :disabled="!pathsDirty || Boolean(saving)">保存插件目录</v-btn>
      </form>
    </template>
  </section>
</template>

<style scoped>
.section-heading,.plugin-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.heading-actions,.badges{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.surface h2{font-size:18px;margin:0 0 14px}
.plugins h3{font-size:15px;margin:0 0 8px}
.scene-box,.paths{border:1px solid var(--line);border-radius:10px;padding:14px;margin:12px 0;display:grid;gap:8px}
.choice{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,200px),1fr));gap:2px 12px}
.plugin-list{list-style:none;padding:0;margin:12px 0;display:grid;gap:12px}
.plugin-item{border:1px solid var(--line);border-radius:10px;padding:14px;min-width:0;overflow-wrap:anywhere}
.plugin-item p{margin:6px 0}
.plugin-heading strong{font-size:16px}
.path{font-size:12px}
.entries{display:grid;gap:6px;margin:10px 0}
.entries dt{font-size:12px;color:var(--muted)}
.entries dd{margin:0}
.errors ul{list-style:none;padding:0;margin:6px 0}
pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:4px 0;font-size:13px}
.error-text{color:rgb(var(--v-theme-error))}
.config{display:grid;gap:10px;margin-top:10px}
.form-actions{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.plugins :deep(.v-btn){min-height:44px}
</style>
