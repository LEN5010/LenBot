<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { api, sceneName } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import HostSkillSettings from '../components/HostSkillSettings.vue'

const route = useRoute(), router = useRouter()
const host = ref(null), snapshot = ref(null)
const loading = ref(false), hostLoading = ref(false), saving = ref(false)
const readError = ref(''), saveError = ref(''), savedNotice = ref('')
const skillDirty = ref(false)
const draftMode = ref('selected'), draftNames = ref([])
const scene = computed(() => route.query.scene)
const options = computed(() => host.value?.scenes.map(item => ({
  title: `${sceneName(item.scene)} · ${item.persona.name}`, value: item.scene
})) || [])
const knownNames = computed(() => snapshot.value?.tools.map(tool => tool.name) || [])
const otherNames = computed(() => draftNames.value.filter(name => !knownNames.value.includes(name)))
const toolDirty = computed(() => {
  if (!snapshot.value) return false
  const saved = snapshot.value.role_tools.saved
  if (draftMode.value === 'all') return saved !== 'all'
  return saved === 'all' || JSON.stringify([...draftNames.value].sort()) !== JSON.stringify([...saved].sort())
})
const dirty = computed(() => toolDirty.value || skillDirty.value)
useUnsavedChanges(dirty)
onBeforeRouteUpdate(() => !dirty.value || window.confirm('有尚未保存的工具或技能许可草稿。放弃并切换场景？'))

let request = 0
const beginRead = useRequestGuard(() => scene.value)
const beginHostRead = useRequestGuard()
const beginSave = useRequestGuard(() => `${scene.value}\u0000${request}`)
function adopt(value) {
  snapshot.value = value
  draftMode.value = value.role_tools.saved === 'all' ? 'all' : 'selected'
  draftNames.value = value.role_tools.saved === 'all' ? [] : [...value.role_tools.saved]
  savedNotice.value = ''
}
async function readScene(confirmDiscard = true) {
  if (confirmDiscard && toolDirty.value && !window.confirm('放弃未保存的工具许可草稿，重读工具保存值与运行状态？')) return
  const own = ++request
  const fresh = beginRead()
  if (typeof scene.value !== 'string') {
    snapshot.value = null
    readError.value = '场景地址必须只指定一个 scene 参数。'
    return
  }
  loading.value = true
  try {
    const result = await api(`/api/host/capabilities?scene=${encodeURIComponent(scene.value)}`)
    if (own !== request || !fresh()) return
    adopt(result)
    readError.value = ''
    saveError.value = ''
  } catch (error) {
    if (own === request && fresh()) readError.value = error.message
  } finally {
    if (own === request && fresh()) loading.value = false
  }
}
async function readHost() {
  const fresh = beginHostRead()
  hostLoading.value = true
  try {
    const value = await api('/api/host/state')
    if (!fresh()) return
    host.value = value
    if (scene.value == null) {
      await router.replace({ name: 'host-capabilities', query: { scene: host.value.scenes[0].scene } })
    } else {
      await readScene(false)
    }
  } catch (error) {
    if (fresh()) readError.value = error.message
  } finally {
    if (fresh()) hostLoading.value = false
  }
}
function selectScene(value) {
  router.push({ name: 'host-capabilities', query: { scene: value } })
}
function toggleName(name, enabled) {
  draftNames.value = enabled
    ? [...draftNames.value, name]
    : draftNames.value.filter(item => item !== name)
}
function serviceLabel(name, enabled) {
  return name === 'schedules' ? (enabled ? '当前已启用' : '当前未启用')
    : name === 'skills' ? (enabled ? '当前配置了技能目录' : '当前未配置技能目录')
    : (enabled ? '当前已配置' : '当前未配置')
}
async function save() {
  if (!snapshot.value || !toolDirty.value || saving.value) return
  const target = scene.value
  const fresh = beginSave()
  const tools = draftMode.value === 'all' ? 'all' : [...draftNames.value]
  saving.value = true
  saveError.value = ''
  savedNotice.value = ''
  try {
    const result = await api(`/api/host/scenes/${encodeURIComponent(target)}/role-tools`, {
      method: 'PUT', body: JSON.stringify({ tools })
    })
    if (!fresh()) return
    snapshot.value = { ...snapshot.value, role_tools: result }
    draftMode.value = result.saved === 'all' ? 'all' : 'selected'
    draftNames.value = result.saved === 'all' ? [] : [...result.saved]
    savedNotice.value = result.restart_required
      ? '角色工具许可已保存；当前运行工具不变，重启宿主后生效。'
      : '角色工具许可已保存；与当前运行值一致。'
  } catch (error) {
    if (fresh()) saveError.value = error.status >= 400 && error.status < 500
      ? `保存未被接受：${error.message}`
      : `保存结果未确认：${error.message} 草稿已保留；请重读保存值核对，不会自动重试。`
  } finally {
    if (fresh()) saving.value = false
  }
}
watch(scene, () => {
  ++request
  skillDirty.value = false
  saving.value = false
  loading.value = false
  snapshot.value = null
  readError.value = ''
  saveError.value = ''
  savedNotice.value = ''
  if (scene.value != null) readScene(false)
})
onMounted(readHost)
</script>

<template>
  <div class="page-stack host-capabilities">
    <header class="page-intro">
      <div><p class="eyebrow">独立多场景宿主</p><h1>工具能力</h1>
        <p class="muted">角色许可、当前注册和按需发现是不同事实；保存许可不会改变正在运行的工具。</p></div>
      <v-btn variant="outlined" :loading="loading || hostLoading" :disabled="saving" @click="readScene()">重读工具事实</v-btn>
    </header>
    <v-alert v-if="readError" type="error" variant="tonal" role="alert"
      :title="snapshot ? '读取失败 · 保留上次快照' : '读取能力失败'">{{ readError }}</v-alert>
    <v-alert v-if="saveError" type="error" variant="tonal" role="alert">{{ saveError }}</v-alert>
    <v-alert v-if="savedNotice && !toolDirty" type="success" variant="tonal" role="status">{{ savedNotice }}</v-alert>
    <section class="surface" aria-labelledby="cap-scene-title">
      <h2 id="cap-scene-title">选择配置场景</h2>
      <v-select :model-value="scene" :items="options" label="场景" hide-details
        :disabled="!host || saving" @update:model-value="selectScene" />
      <p v-if="(loading || hostLoading) && !snapshot" class="muted mt-4" role="status">正在读取实际许可和工具名单…</p>
    </section>
    <template v-if="snapshot">
      <section class="surface" aria-labelledby="cap-role-title">
        <div class="section-heading"><h2 id="cap-role-title">角色工具许可</h2>
          <v-chip variant="tonal" :color="snapshot.role_tools.restart_required?'warning':'info'">
            {{ snapshot.role_tools.restart_required?'保存值待重启':'保存值与运行值一致' }}
          </v-chip></div>
        <p>当前角色：<strong>{{ snapshot.persona.name }}</strong> <span class="muted">{{ snapshot.persona.id }}</span></p>
        <p>运行中许可：<strong>{{ snapshot.role_tools.running === 'all' ? '不逐项限制工具' : snapshot.role_tools.running.join('、') || '无' }}</strong></p>
        <p>角色包保存值：<strong>{{ snapshot.role_tools.saved === 'all' ? '不逐项限制工具' : snapshot.role_tools.saved.join('、') || '无' }}</strong></p>
        <p class="muted">此角色包还被这些配置场景使用：{{ snapshot.role_tools.affected_scenes.map(sceneName).join('、') }}。保存会影响它们下次启动的许可，不会立即注册新工具。</p>
        <p v-if="toolDirty" class="dirty-note" role="status">工具许可草稿尚未保存。</p>
        <form @submit.prevent="save">
          <v-radio-group v-model="draftMode" label="保存的工具许可范围" :disabled="saving || loading" hide-details>
            <v-radio label="不逐项限制工具（也允许后续已装配工具）" value="all" />
            <v-radio label="只允许勾选的工具" value="selected" />
          </v-radio-group>
          <div v-if="draftMode==='selected'" class="tool-choice">
            <v-checkbox v-for="tool in snapshot.tools" :key="tool.name"
              :model-value="draftNames.includes(tool.name)" :label="tool.name"
              :disabled="saving || loading" hide-details @update:model-value="value=>toggleName(tool.name,value)" />
            <p v-if="otherNames.length" class="muted">草稿中还有未列为当前实现的原声明：{{ otherNames.join('、') }}；不会静默删除。</p>
          </div>
          <div class="form-actions">
            <v-btn type="submit" color="primary" :loading="saving" :disabled="!toolDirty || loading">保存角色工具许可</v-btn>
            <span class="muted">写入角色包；当前运行值保持不变。</span>
          </div>
        </form>
      </section>
      <section class="surface" aria-labelledby="tools-title">
        <div class="section-heading"><h2 id="tools-title">本实例工具事实</h2><span class="muted">名单不代表已执行</span></div>
        <ul class="tool-list">
          <li v-for="tool in snapshot.tools" :key="tool.name" class="tool-item">
            <div class="tool-heading"><strong>{{ tool.name }}</strong><div class="tool-badges">
              <v-chip size="small" variant="tonal" :color="tool.registered?'success':'warning'">{{ tool.registered?'当前已注册':'当前未注册' }}</v-chip>
              <v-chip size="small" variant="tonal" :color="tool.allowed?'info':'default'">{{ tool.allowed?'当前许可':'当前未许可' }}</v-chip>
              <v-chip v-if="tool.deferred" size="small" variant="tonal">{{ tool.discovered?'按需已发现':'按需未发现' }}</v-chip>
            </div></div>
            <p class="muted">{{ tool.description }}</p>
            <ul v-if="tool.reasons.length" class="reason-list"><li v-for="reason in tool.reasons" :key="reason">{{ reason }}</li></ul>
          </li>
        </ul>
      </section>
      <section class="surface" aria-labelledby="services-title">
        <h2 id="services-title">配置的服务与尚未接入项</h2>
        <dl class="service-list">
          <div v-for="[name,enabled] in Object.entries(snapshot.services)" :key="name"><dt>{{ name }}</dt><dd>{{ serviceLabel(name,enabled) }}</dd></div>
        </dl>
        <p class="muted mt-4">技能的角色许可、保存目录与运行装配由下方独立读取；本服务列表不代表技能已执行。</p>
        <div v-if="snapshot.not_implemented.length" class="not-implemented">
          <h3>尚未接入执行</h3>
          <p v-for="item in snapshot.not_implemented" :key="item.name"><strong>{{ item.name }}</strong> · {{ item.description }}</p>
        </div>
      </section>
      <HostSkillSettings :key="scene" :scene="scene" @dirty="skillDirty=$event" />
    </template>
  </div>
</template>

<style scoped>
.host-capabilities{max-width:1200px;margin-inline:auto}
.page-intro,.section-heading,.tool-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
.page-intro>div,.section-heading>h2{min-width:0}
.page-intro h1{margin:0 0 10px}
.eyebrow{font-size:12px;letter-spacing:.08em;color:var(--primary);font-weight:700;margin:0 0 5px}
.surface{min-width:0}
.surface h2{font-size:18px;margin:0 0 14px}
.surface h3{font-size:15px;margin:16px 0 8px}
.tool-list,.reason-list{list-style:none;padding:0;margin:0}
.tool-list{display:grid;gap:12px}
.tool-item{border:1px solid var(--line);border-radius:10px;padding:14px;min-width:0;overflow-wrap:anywhere}
.tool-heading strong{font-size:16px}
.tool-badges{display:flex;gap:6px;flex-wrap:wrap}
.tool-item p{margin:8px 0}
.reason-list{color:var(--muted);font-size:13px}
.reason-list li{margin-top:4px}
.service-list{display:flex;gap:10px;flex-wrap:wrap;margin:0}
.service-list>div{border:1px solid var(--line);border-radius:8px;padding:8px 12px;min-width:120px}
.service-list dt{font-size:12px;color:var(--muted)}
.service-list dd{margin:0;font-weight:600}
.tool-choice{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:2px 12px}
.tool-choice p{grid-column:1/-1;overflow-wrap:anywhere}
.not-implemented p{overflow-wrap:anywhere}
.dirty-note{border-left:3px solid var(--primary);background:var(--selected-bg);padding:8px 12px}
.host-capabilities :deep(.v-btn){min-height:44px}
.host-capabilities :deep(.v-alert){overflow-wrap:anywhere}
@media(max-width:600px){.page-intro{display:grid}.page-intro>.v-btn{width:100%}.tool-item{padding:12px}}
</style>
