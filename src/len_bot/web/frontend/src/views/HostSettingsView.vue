<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api, sceneName } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import HostWorkerSettings from '../components/HostWorkerSettings.vue'

const route = useRoute(), router = useRouter()
let sceneEpoch = 0
const selection = () => `${scene.value}\u0000${sceneEpoch}`
const beginRead = useRequestGuard(selection)
const beginPersona = useRequestGuard(selection)
const beginSceneSave = useRequestGuard(selection)
const beginServiceSave = useRequestGuard()
const snapshot = ref(null), personaSnapshot = ref(null), scene = ref('')
const workerDirty = ref(false), workerSceneDirty = ref(false), workerSaving = ref(false), workerPanelKey = ref(0)
const loading = ref(false), personaLoading = ref(false), saving = ref('')
const readError = ref(''), personaError = ref(''), saveError = ref(''), localError = ref(''), savedNotice = ref('')
const draft = ref(null), webRead = ref(null), webSearch = ref(null)
const aliases = ref([]), relationships = ref([])
const keywords = ref([]), otherBots = ref([]), admins = ref([]), whitelist = ref([])
const roleOptions = ['owner', 'admin', 'group_manager', 'whitelist', 'member']
const roleLabels = { owner: '主人', admin: '管理员', group_manager: '群管理员', whitelist: '白名单', member: '成员' }
const attentionNumbers = [
  ['direct_idle_seconds','直接呼唤空闲秒数'], ['direct_max_seconds','直接呼唤最长秒数'],
  ['named_idle_seconds','名字线索空闲秒数'], ['named_max_seconds','名字线索最长秒数'],
  ['keyword_cooldown_seconds','关键词冷却秒数'], ['focus_seconds','持续关注秒数'],
  ['focus_idle_seconds','关注空闲秒数'], ['focus_max_seconds','关注最长秒数'],
  ['activity','场景活跃度（0–1）'], ['ambient_threshold','旁听触发阈值'],
  ['ambient_min_interval_seconds','旁听最短间隔秒数'], ['ambient_max_interval_seconds','旁听最长间隔秒数'],
  ['max_extensions','最多延长次数'],
]
const sceneOptions = computed(() => Object.keys(snapshot.value?.saved.scenes || {}).map(value => ({ title: sceneName(value), value })))
const savedScene = computed(() => snapshot.value?.saved.scenes[scene.value] || null)
const runningScene = computed(() => snapshot.value?.running.scenes[scene.value] || null)
function copy(value) { return JSON.parse(JSON.stringify(value)) }
function numeric(value) { return value === '' ? '' : Number(value) }
function listRows(values) { return values.map(value => ({ value })) }
function cleanList(rows) { return rows.map(row => row.value) }
function displayValue(value) {
  if (value === null) return '未设置'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) return value.join('、') || '无'
  if (typeof value === 'object') return Object.entries(value).map(([key, item]) => `${key}: ${displayValue(item)}`).join('；') || '无'
  return String(value)
}
function sceneBody() {
  if (!draft.value) return null
  return {
    voice_mode: draft.value.voice_mode,
    attention: { ...draft.value.attention, keywords: cleanList(keywords.value), other_bot_qqs: cleanList(otherBots.value) },
    schedules: { ...draft.value.schedules, admins: cleanList(admins.value), whitelist: cleanList(whitelist.value) },
    persona_aliases: cleanList(aliases.value),
    relationships: Object.fromEntries(relationships.value.map(row => [row.qq, row.text])),
    behavior_addendum: draft.value.behavior_addendum === '' ? null : draft.value.behavior_addendum,
  }
}
function savedBody(value) {
  return { voice_mode: value.voice_mode, attention: value.attention, schedules: value.schedules,
    persona_aliases: value.scene_persona.persona_aliases, relationships: value.scene_persona.relationships,
    behavior_addendum: value.scene_persona.behavior_addendum }
}
const sceneDirty = computed(() => {
  if (!savedScene.value || !draft.value) return false
  const duplicate = relationships.value.length !== new Set(relationships.value.map(row => row.qq)).size
  return duplicate || JSON.stringify(sceneBody()) !== JSON.stringify(savedBody(savedScene.value))
})
const readDirty = computed(() => snapshot.value && JSON.stringify(webRead.value) !== JSON.stringify(snapshot.value.saved.web_read))
const searchDirty = computed(() => snapshot.value && JSON.stringify(webSearch.value) !== JSON.stringify(snapshot.value.saved.web_search))
const dirty = computed(() => Boolean(sceneDirty.value || readDirty.value || searchDirty.value || workerDirty.value))
useUnsavedChanges(dirty)
onBeforeRouteUpdate(() => !workerSaving.value && (!sceneDirty.value && !workerSceneDirty.value ||
  window.confirm('有尚未保存的场景草稿。放弃并打开另一场景？')))
function adoptScene(value) {
  const record = value.saved.scenes[scene.value]
  draft.value = { voice_mode: record.voice_mode, attention: copy(record.attention), schedules: copy(record.schedules),
    behavior_addendum: record.scene_persona.behavior_addendum ?? '' }
  aliases.value = listRows(record.scene_persona.persona_aliases)
  relationships.value = Object.entries(record.scene_persona.relationships).map(([qq,text]) => ({ qq, text }))
  keywords.value = listRows(record.attention.keywords)
  otherBots.value = listRows(record.attention.other_bot_qqs)
  admins.value = listRows(record.schedules.admins)
  whitelist.value = listRows(record.schedules.whitelist)
}
function adoptAll(value) {
  const previousScene = scene.value
  snapshot.value = value
  if (!value.saved.scenes[scene.value]) scene.value = Object.keys(value.saved.scenes)[0] || ''
  if (scene.value !== previousScene) personaSnapshot.value = null
  if (scene.value) adoptScene(value)
  if (scene.value !== route.query.scene) router.replace({ name: 'host-settings', query: { scene: scene.value } })
  webRead.value = copy(value.saved.web_read)
  webSearch.value = copy(value.saved.web_search)
  savedNotice.value = ''
  ++workerPanelKey.value
}
async function readPersona() {
  if (!scene.value) return
  const fresh = beginPersona()
  personaLoading.value = true
  personaError.value = ''
  try {
    const value = await api(`/api/host/scenes/${encodeURIComponent(scene.value)}/persona`)
    if (fresh()) personaSnapshot.value = value
  } catch (error) {
    if (fresh()) personaError.value = error.message
  } finally {
    if (fresh()) personaLoading.value = false
  }
}
async function read(confirmDiscard = true) {
  if (workerSaving.value) return
  if (confirmDiscard && dirty.value &&
      !window.confirm('放弃全部未保存草稿，重新读取根配置？')) return
  const fresh = beginRead()
  loading.value = true
  try {
    const value = await api('/api/host/settings')
    if (!fresh()) return
    adoptAll(value)
    readError.value = ''
    saveError.value = ''
    localError.value = ''
    await readPersona()
  } catch (error) {
    if (fresh()) readError.value = error.message
  } finally {
    if (fresh()) loading.value = false
  }
}
function changeScene(next, fromRoute = false) {
  if (next === scene.value) return
  if (workerSaving.value) return
  if (!fromRoute && (sceneDirty.value || workerSceneDirty.value) &&
      !window.confirm('放弃当前场景尚未保存的修改并切换？')) return
  if (!snapshot.value.saved.scenes[next]) return
  scene.value = next
  ++sceneEpoch
  loading.value = false
  if (!fromRoute) router.replace({ name: 'host-settings', query: { scene: next } })
  personaSnapshot.value = null
  personaError.value = ''
  adoptScene(snapshot.value)
  saveError.value = ''
  localError.value = ''
  savedNotice.value = ''
  readPersona()
}
function toggleQuiet(value) {
  draft.value.attention.quiet_hours = value
    ? { start: '23:00', end: '07:00', direct: 'defer', notice_text: null } : null
}
function toggleWebRead(value) { webRead.value = value ? { timeout_seconds: 20 } : null }
function toggleWebSearch(value) { webSearch.value = value ? { provider: 'bing_rss', timeout_seconds: 15, max_results: 5 } : null }
function errorMessage(error) {
  return error.status >= 400 && error.status < 500
    ? `保存未被接受：${error.message}`
    : `保存结果未确认：${error.message} 草稿已保留；请重读根配置核对，不会自动重试。`
}
async function saveScene() {
  if (!sceneDirty.value || loading.value || saving.value) return
  const qqs = relationships.value.map(row => row.qq)
  localError.value = qqs.length !== new Set(qqs).size ? '关系说明中的 QQ 重复；保存前请删掉重复行。' : ''
  if (localError.value) return
  const target = scene.value
  const fresh = beginSceneSave()
  const payload = sceneBody()
  saving.value = 'scene'; saveError.value = ''; savedNotice.value = ''
  try {
    const value = await api(`/api/host/settings/scenes/${encodeURIComponent(target)}`, {
      method: 'PUT', body: JSON.stringify(payload),
    })
    if (!fresh()) return
    snapshot.value = value
    adoptScene(value)
    savedNotice.value = value.restart_required.scenes[target]
      ? '场景设置已保存到根文件；当前运行值不变，重启宿主后生效。'
      : '场景设置已保存到根文件；与当前运行值一致。'
  } catch (error) { if (fresh()) saveError.value = errorMessage(error) }
  finally { if (saving.value === 'scene') saving.value = '' }
}
async function saveService(which) {
  if (loading.value || saving.value || !(which === 'web-read' ? readDirty.value : searchDirty.value)) return
  const fresh = beginServiceSave()
  saving.value = which; saveError.value = ''; savedNotice.value = ''
  const payload = which === 'web-read' ? { web_read: webRead.value } : { web_search: webSearch.value }
  try {
    const value = await api(`/api/host/settings/${which}`, { method: 'PUT', body: JSON.stringify(payload) })
    if (!fresh()) return
    snapshot.value = value
    if (which === 'web-read') webRead.value = copy(value.saved.web_read)
    else webSearch.value = copy(value.saved.web_search)
    savedNotice.value = value.restart_required[which === 'web-read' ? 'web_read' : 'web_search']
      ? '服务配置已保存到根文件；当前运行值不变，重启宿主后生效。'
      : '服务配置已保存到根文件；与当前运行值一致。'
  } catch (error) { if (fresh()) saveError.value = errorMessage(error) }
  finally { if (fresh()) saving.value = '' }
}
onMounted(() => {
  if (typeof route.query.scene === 'string') scene.value = route.query.scene
  read(false)
})
watch(() => route.query.scene, value => {
  if (typeof value !== 'string' || value === scene.value) return
  if (snapshot.value?.saved.scenes[value]) changeScene(value, true)
  else if (!snapshot.value) {
    scene.value = value
    ++sceneEpoch
    read(false)
  }
})
</script>

<template>
  <div class="page-stack host-settings">
    <header class="page-intro"><div><p class="eyebrow">独立多场景宿主</p><h1>群聊设置与角色</h1>
      <p class="muted">分别编辑场景和外部服务的根配置保存值。运行中的设置与角色文件保持原样，重启宿主后才生效；不在这里启动、重载或发送消息。</p></div>
      <v-btn variant="outlined" :loading="loading" :disabled="Boolean(saving) || workerSaving" @click="read()">重读根配置</v-btn></header>
    <v-alert v-if="readError" type="error" variant="tonal" role="alert" :title="snapshot?'读取失败 · 保留上次草稿':'读取设置失败'">{{ readError }}</v-alert>
    <v-alert v-if="saveError" type="error" variant="tonal" role="alert">{{ saveError }}</v-alert>
    <v-alert v-if="localError" type="warning" variant="tonal" role="alert">{{ localError }}</v-alert>
    <v-alert v-if="savedNotice" type="success" variant="tonal" role="status">{{ savedNotice }}</v-alert>
    <div v-if="loading && !snapshot" class="surface empty-state" role="status">正在读取场景与服务的运行值、保存值…</div>
    <template v-if="snapshot && draft">
      <section class="surface"><div class="section-heading"><h2>当前配置场景</h2>
        <v-chip variant="tonal" :color="snapshot.restart_required.scenes[scene]?'warning':'info'">{{ snapshot.restart_required.scenes[scene]?'场景保存值待重启':'场景保存值与运行值一致' }}</v-chip></div>
        <v-select :model-value="scene" :items="sceneOptions" label="选择场景" hide-details="auto" :disabled="Boolean(saving) || loading || workerSaving" @update:model-value="changeScene" />
        <p class="muted mt-4">运行值：{{ runningScene?.voice_mode === 'voice' ? '表达器发言' : '大脑直接发言' }}；保存值：{{ savedScene?.voice_mode === 'voice' ? '表达器发言' : '大脑直接发言' }}。场景与角色包的绑定路径不在此页修改。</p>
        <details v-if="runningScene"><summary>查看当前运行的场景设置</summary>
          <h3>参与与安静时段</h3><dl class="role-facts"><div v-for="(value,key) in runningScene.attention" :key="key"><dt>{{ key }}</dt><dd>{{ displayValue(value) }}</dd></div></dl>
          <h3>提醒权限</h3><dl class="role-facts"><div v-for="(value,key) in runningScene.schedules" :key="key"><dt>{{ key }}</dt><dd>{{ displayValue(value) }}</dd></div></dl>
          <h3>角色补充</h3><dl class="role-facts"><div><dt>补充称呼</dt><dd>{{ displayValue(runningScene.scene_persona.persona_aliases) }}</dd></div>
            <div><dt>关系说明</dt><dd>{{ displayValue(runningScene.scene_persona.relationships) }}</dd></div>
            <div><dt>行为风格补充</dt><dd>{{ displayValue(runningScene.scene_persona.behavior_addendum) }}</dd></div></dl>
        </details>
        <p v-if="sceneDirty" class="dirty-note" role="status">当前场景有未保存修改。</p>
      </section>
      <form class="page-stack" @submit.prevent="saveScene">
        <fieldset class="surface editor-section" :disabled="Boolean(saving) || loading"><legend>场景参与与安静时段</legend>
          <v-select v-model="draft.voice_mode" label="表达方式" :items="[{title:'表达器组织台词',value:'voice'},{title:'大脑直接表达',value:'direct'}]" hide-details="auto" />
          <v-switch v-model="draft.attention.only_direct" label="只处理直接呼唤" hide-details />
          <div class="list-block"><h3>关键词</h3><p class="muted">逐项编辑；合法关键词会由配置入口校验，不按行拆分。</p>
            <div v-for="(row,index) in keywords" :key="index" class="list-row"><v-text-field v-model="row.value" :label="`关键词 ${index+1}`" hide-details="auto" /><v-btn variant="outlined" @click="keywords.splice(index,1)">删除</v-btn></div>
            <v-btn variant="outlined" @click="keywords.push({value:''})">添加关键词</v-btn></div>
          <div class="list-block"><h3>其他 Bot QQ</h3><div v-for="(row,index) in otherBots" :key="index" class="list-row"><v-text-field v-model="row.value" :label="`Bot QQ ${index+1}`" inputmode="numeric" hide-details="auto" /><v-btn variant="outlined" @click="otherBots.splice(index,1)">删除</v-btn></div><v-btn variant="outlined" @click="otherBots.push({value:''})">添加 Bot QQ</v-btn></div>
          <div class="form-grid"><v-text-field v-for="[key,label] in attentionNumbers" :key="key" :model-value="draft.attention[key]" type="number" :step="key==='max_extensions'?'1':'any'" :label="label" hide-details="auto" @update:model-value="value=>draft.attention[key]=numeric(value)" /></div>
          <v-switch :model-value="draft.attention.quiet_hours!==null" label="启用本地安静时段" hide-details @update:model-value="toggleQuiet" />
          <div v-if="draft.attention.quiet_hours" class="entry-card form-grid">
            <v-text-field v-model="draft.attention.quiet_hours.start" label="开始钟面时间 HH:MM" hide-details="auto" />
            <v-text-field v-model="draft.attention.quiet_hours.end" label="结束钟面时间 HH:MM" hide-details="auto" />
            <v-select v-model="draft.attention.quiet_hours.direct" label="直接呼唤处理" :items="[{title:'允许',value:'allow'},{title:'告知安静',value:'notice'},{title:'延后',value:'defer'}]" hide-details="auto" @update:model-value="value=>{if(value!=='notice') draft.attention.quiet_hours.notice_text=null}" />
            <v-textarea v-if="draft.attention.quiet_hours.direct==='notice'" v-model="draft.attention.quiet_hours.notice_text" label="安静提示原文" rows="3" auto-grow hide-details="auto" />
          </div>
        </fieldset>
        <fieldset class="surface editor-section" :disabled="Boolean(saving) || loading"><legend>一次性提醒权限</legend>
          <p class="muted">权限身份可组合命中；列表只影响此场景的安排能力。</p>
          <div class="form-grid"><v-switch v-model="draft.schedules.enabled" label="启用提醒" hide-details />
            <v-switch v-model="draft.schedules.autonomous" label="允许 Bot 自主安排" hide-details />
            <v-text-field :model-value="draft.schedules.max_pending" type="number" step="1" label="最多待执行安排" hide-details="auto" @update:model-value="value=>draft.schedules.max_pending=numeric(value)" />
            <v-text-field :model-value="draft.schedules.owner ?? ''" label="主人 QQ（留空表示无主人）" inputmode="numeric" hide-details="auto" @update:model-value="value=>draft.schedules.owner=value===''?null:value" /></div>
          <div class="list-block"><h3>管理员 QQ</h3><div v-for="(row,index) in admins" :key="index" class="list-row"><v-text-field v-model="row.value" :label="`管理员 QQ ${index+1}`" inputmode="numeric" hide-details="auto" /><v-btn variant="outlined" @click="admins.splice(index,1)">删除</v-btn></div><v-btn variant="outlined" @click="admins.push({value:''})">添加管理员</v-btn></div>
          <div class="list-block"><h3>白名单 QQ</h3><div v-for="(row,index) in whitelist" :key="index" class="list-row"><v-text-field v-model="row.value" :label="`白名单 QQ ${index+1}`" inputmode="numeric" hide-details="auto" /><v-btn variant="outlined" @click="whitelist.splice(index,1)">删除</v-btn></div><v-btn variant="outlined" @click="whitelist.push({value:''})">添加白名单</v-btn></div>
          <div class="form-grid"><v-select v-for="[key,label] in [['own','安排自己的提醒'],['others','安排他人的提醒'],['manage','管理提醒']]" :key="key" v-model="draft.schedules[key]" :label="label" :items="roleOptions.map(value=>({title:roleLabels[value],value}))" multiple chips closable-chips hide-details="auto" /></div>
        </fieldset>
        <fieldset class="surface editor-section" :disabled="Boolean(saving) || loading"><legend>此场景的角色补充</legend>
          <p class="muted">只改场景附加文字，不编辑角色文件或人工样例；原文可保留换行。</p>
          <div class="list-block"><h3>补充称呼</h3><div v-for="(row,index) in aliases" :key="index" class="list-row"><v-textarea v-model="row.value" :label="`称呼 ${index+1}`" rows="2" auto-grow hide-details="auto" /><v-btn variant="outlined" @click="aliases.splice(index,1)">删除</v-btn></div><v-btn variant="outlined" @click="aliases.push({value:''})">添加称呼</v-btn></div>
          <div class="list-block"><h3>关系说明</h3><div v-for="(row,index) in relationships" :key="index" class="list-row"><v-text-field v-model="row.qq" :label="`对象 QQ ${index+1}`" inputmode="numeric" hide-details="auto" /><v-textarea v-model="row.text" :label="`关系原文 ${index+1}`" rows="2" auto-grow hide-details="auto" /><v-btn variant="outlined" @click="relationships.splice(index,1)">删除</v-btn></div><v-btn variant="outlined" @click="relationships.push({qq:'',text:''})">添加关系</v-btn></div>
          <v-textarea v-model="draft.behavior_addendum" label="行为风格补充（清空则不设置）" rows="4" auto-grow hide-details="auto" />
        </fieldset>
        <div class="form-actions"><v-btn type="submit" color="primary" :loading="saving==='scene'" :disabled="!sceneDirty || Boolean(saving) || loading">保存此场景</v-btn><span class="muted">完整场景设置一次提交；后端校验失败时保留草稿与原始错误。</span></div>
      </form>
      <HostWorkerSettings :key="workerPanelKey" :scene="scene" @dirty="workerDirty=$event"
        @scene-dirty="workerSceneDirty=$event" @saving="workerSaving=$event" />
      <section class="surface" aria-labelledby="persona-title"><div class="section-heading"><h2 id="persona-title">当前运行的角色资料</h2><span class="muted">只读 · 角色文件改动需重启</span></div>
        <p v-if="personaLoading" role="status">正在读取当前角色…</p>
        <v-alert v-if="personaError" type="error" variant="tonal" role="alert">{{ personaError }}<span v-if="personaSnapshot"> 下方保留上次读取值。</span></v-alert>
        <template v-if="personaSnapshot"><p><strong>{{ personaSnapshot.persona.name }}</strong> <span class="muted">{{ personaSnapshot.persona.id }}</span></p>
          <dl class="role-facts"><div><dt>身份简述</dt><dd>{{ personaSnapshot.persona.brief }}</dd></div><div><dt>行为风格</dt><dd>{{ personaSnapshot.persona.behavior }}</dd></div><div><dt>说话风格</dt><dd>{{ personaSnapshot.persona.voice }}</dd></div><div><dt>身份边界</dt><dd>{{ personaSnapshot.persona.boundaries }}</dd></div></dl>
          <details><summary>查看自称、称呼、风格、样例及资料目录</summary>
            <p>自称：{{ personaSnapshot.persona.self_reference.join('、') || '无' }}</p><p>角色称呼：{{ personaSnapshot.persona.aliases.join('、') || '无' }}</p>
            <h3>说话风格变体</h3><ul><li v-for="(style,index) in personaSnapshot.persona.styles" :key="index">{{ style.name }} · {{ style.weight }}<p>{{ style.note }}</p></li></ul>
            <p>工具许可声明：{{ personaSnapshot.persona.tools==='all'?'all':personaSnapshot.persona.tools.join('、')||'无' }}；技能声明：{{ personaSnapshot.persona.skills==='all'?'all':personaSnapshot.persona.skills.join('、')||'无' }}（技能执行尚未接入）。</p>
            <p>样例选集标签：{{ personaSnapshot.persona.example_tags.join('、') || '未设置' }}。</p>
            <h3>本次选中的人工样例</h3><ol><li v-for="(example,index) in personaSnapshot.selected_examples" :key="index"><p>{{ example.context }}</p><p>{{ example.line }}</p><p>标签：{{ example.tags.join('、') || '无' }}</p></li></ol>
            <h3>角色包的全部人工样例</h3><ol><li v-for="(example,index) in personaSnapshot.persona.examples" :key="index"><p>{{ example.context }}</p><p>{{ example.line }}</p><p>标签：{{ example.tags.join('、') || '无' }}</p></li></ol>
            <p class="muted">全部 {{ personaSnapshot.persona.examples.length }} 条；本次选中 {{ personaSnapshot.selected_examples.length }} 条。人工样例不是聊天历史。</p>
            <h3>角色资料目录</h3><ul><li v-for="item in personaSnapshot.knowledge" :key="item.filename">{{ item.filename }} · {{ item.characters }} 字符 · 标签：{{ item.tags.join('、') || '无' }}</li></ul>
          </details>
          <p class="muted">角色原始文件可在 <RouterLink :to="{name:'host-persona',query:{scene}}">角色文件</RouterLink> 明确编辑并保存；工具许可与实际注册状态请到 <RouterLink :to="{name:'host-capabilities',query:{scene}}">工具能力</RouterLink> 查看；<RouterLink :to="{name:'host-history',query:{scene}}">大脑会话</RouterLink> 可只读查看已保存的回想与原生条目。名单不代表已执行。</p>
        </template>
      </section>
      <section class="surface service-section"><div class="section-heading"><h2>网页读取</h2><v-chip variant="tonal" :color="snapshot.restart_required.web_read?'warning':'info'">{{ snapshot.restart_required.web_read?'保存值待重启':'保存值与运行值一致' }}</v-chip></div>
        <p class="muted">运行值：{{ snapshot.running.web_read===null?'未配置':`超时 ${snapshot.running.web_read.timeout_seconds} 秒` }}</p>
        <v-switch :model-value="webRead!==null" label="在保存值中启用网页读取" :disabled="Boolean(saving)||loading" hide-details @update:model-value="toggleWebRead" />
        <v-text-field v-if="webRead" :model-value="webRead.timeout_seconds" type="number" label="读取超时（秒）" :disabled="Boolean(saving)||loading" hide-details="auto" @update:model-value="value=>webRead.timeout_seconds=numeric(value)" />
        <v-btn color="primary" :loading="saving==='web-read'" :disabled="!readDirty||Boolean(saving)||loading" @click="saveService('web-read')">保存网页读取配置</v-btn></section>
      <section class="surface service-section"><div class="section-heading"><h2>网页搜索</h2><v-chip variant="tonal" :color="snapshot.restart_required.web_search?'warning':'info'">{{ snapshot.restart_required.web_search?'保存值待重启':'保存值与运行值一致' }}</v-chip></div>
        <p class="muted">运行值：{{ snapshot.running.web_search===null?'未配置':`${snapshot.running.web_search.provider} · 最多 ${snapshot.running.web_search.max_results} 条` }}</p>
        <v-switch :model-value="webSearch!==null" label="在保存值中启用网页搜索" :disabled="Boolean(saving)||loading" hide-details @update:model-value="toggleWebSearch" />
        <div v-if="webSearch" class="form-grid"><v-text-field :model-value="webSearch.provider" label="服务" readonly hide-details="auto" />
          <v-text-field :model-value="webSearch.timeout_seconds" type="number" label="搜索超时（秒）" :disabled="Boolean(saving)||loading" hide-details="auto" @update:model-value="value=>webSearch.timeout_seconds=numeric(value)" />
          <v-text-field :model-value="webSearch.max_results" type="number" step="1" label="最多返回条数" :disabled="Boolean(saving)||loading" hide-details="auto" @update:model-value="value=>webSearch.max_results=numeric(value)" /></div>
        <v-btn color="primary" :loading="saving==='web-search'" :disabled="!searchDirty||Boolean(saving)||loading" @click="saveService('web-search')">保存网页搜索配置</v-btn></section>
    </template>
  </div>
</template>

<style scoped>
.host-settings{max-width:1280px;margin-inline:auto}
.page-intro,.section-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap}
.page-intro>div{min-width:0;flex:1 1 460px}
.page-intro h1{margin:0 0 10px}
.eyebrow{font-size:12px;letter-spacing:.08em;color:var(--primary);font-weight:700;margin:0 0 5px}
.surface,.entry-card{min-width:0;overflow-wrap:anywhere}
.surface h2{font-size:18px;margin:0 0 14px}
.editor-section{border:1px solid var(--line)}
.editor-section legend{font-size:18px;font-weight:650;padding:0 6px}
.editor-section h3{font-size:15px;margin:0 0 8px}
.list-block{border-top:1px solid var(--line);padding:14px 0;margin:10px 0}
.list-row{display:flex;align-items:flex-start;gap:10px;margin:10px 0;min-width:0}
.list-row>:first-child{flex:1;min-width:0}
.entry-card{border:1px solid var(--line);border-radius:10px;padding:14px;margin:12px 0}
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:12px;margin:12px 0}
.role-facts{display:grid;gap:12px}.role-facts>div{border-top:1px solid var(--line);padding-top:10px}.role-facts dt{font-weight:700}.role-facts dd{margin:6px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}
.host-settings details{border-top:1px solid var(--line);padding:14px 0}.host-settings details p,.host-settings details li{white-space:pre-wrap;overflow-wrap:anywhere}
.host-settings summary{cursor:pointer;min-height:44px;font-weight:700}.service-section>:last-child{margin-top:12px}
.dirty-note{border-left:3px solid var(--primary);background:var(--selected-bg);padding:8px 12px}
.host-settings :deep(.v-btn){min-height:44px}.host-settings :deep(.v-alert),.host-settings .muted{overflow-wrap:anywhere}
@media(max-width:600px){.page-intro{display:grid}.page-intro>.v-btn{width:100%}.list-row{flex-wrap:wrap}.list-row>.v-btn{width:100%}.surface{padding:16px}}
</style>
