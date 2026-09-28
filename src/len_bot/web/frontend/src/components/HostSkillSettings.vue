<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, sceneName } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty'])
const snapshot = ref(null), loading = ref(false), saving = ref(false)
const readError = ref(''), saveError = ref(''), savedNotice = ref('')
const draftMode = ref('selected'), draftNames = ref([])
const beginRead = useRequestGuard(() => props.scene)
const beginSave = useRequestGuard(() => props.scene)
const knownNames = computed(() => snapshot.value?.catalog.map(item => item.name) || [])
const otherNames = computed(() => draftNames.value.filter(name => !knownNames.value.includes(name)))
const dirty = computed(() => {
  if (!snapshot.value) return false
  const saved = snapshot.value.role_skills.saved
  if (draftMode.value === 'all') return saved !== 'all'
  return saved === 'all' || JSON.stringify([...draftNames.value].sort()) !== JSON.stringify([...saved].sort())
})
watch(dirty, value => emit('dirty', value), { immediate: true })
onBeforeUnmount(() => emit('dirty', false))

function adopt(value) {
  snapshot.value = value
  draftMode.value = value.role_skills.saved === 'all' ? 'all' : 'selected'
  draftNames.value = value.role_skills.saved === 'all'
    ? value.catalog.map(item => item.name) : [...value.role_skills.saved]
  savedNotice.value = ''
}
function sourceLabel(source) {
  return ({ builtin: '内置', shared: '已批准共享', scene: '本场景' })[source]
}
function toggleName(name, enabled) {
  draftNames.value = enabled
    ? draftNames.value.includes(name) ? draftNames.value : [...draftNames.value, name]
    : draftNames.value.filter(item => item !== name)
}
async function read(confirmDiscard = true) {
  if (confirmDiscard && dirty.value && !window.confirm('放弃当前技能许可草稿，重读保存值与运行技能？')) return
  const target = props.scene, fresh = beginRead()
  loading.value = true
  try {
    const value = await api(`/api/host/skills?scene=${encodeURIComponent(target)}`)
    if (!fresh()) return
    adopt(value); readError.value = ''; saveError.value = ''
  } catch (error) { if (fresh()) readError.value = error.message }
  finally { if (fresh()) loading.value = false }
}
async function save() {
  if (!snapshot.value || !dirty.value || loading.value || saving.value) return
  const target = props.scene, fresh = beginSave()
  const skills = draftMode.value === 'all' ? 'all' : [...draftNames.value]
  saving.value = true; saveError.value = ''; savedNotice.value = ''
  try {
    const result = await api(`/api/host/scenes/${encodeURIComponent(target)}/role-skills`, {
      method: 'PUT', body: JSON.stringify({ skills }),
    })
    if (!fresh()) return
    snapshot.value = { ...snapshot.value, role_skills: result,
      catalog: snapshot.value.catalog.map(item => ({ ...item,
        selected: result.saved === 'all' || result.saved.includes(item.name) })) }
    draftMode.value = result.saved === 'all' ? 'all' : 'selected'
    draftNames.value = result.saved === 'all' ? snapshot.value.catalog.map(item => item.name) : [...result.saved]
    savedNotice.value = result.restart_required
      ? '角色技能许可已保存；当前任务与运行技能不变，重启宿主后生效。'
      : '角色技能许可已保存；与当前运行值一致。'
  } catch (error) {
    if (fresh()) saveError.value = error.status >= 400 && error.status < 500
      ? `保存未被接受：${error.message}`
      : `保存结果未确认：${error.message} 草稿已保留；请重读核对，不会自动重试。`
  } finally { if (fresh()) saving.value = false }
}
onMounted(() => read(false))
</script>

<template>
  <section class="surface skill-settings" aria-labelledby="host-skills-title">
    <div class="section-heading"><div><h2 id="host-skills-title">Agent Skills</h2>
      <p class="muted">独立于工具许可：目录、角色允许范围、运行中已装配名单和实际执行是不同事实。</p></div>
      <v-btn variant="outlined" :loading="loading" :disabled="saving" @click="read()">重读技能事实</v-btn></div>
    <v-alert v-if="readError" type="error" variant="tonal" role="alert" :title="snapshot?'读取失败 · 保留上次快照':'读取技能失败'">{{ readError }}</v-alert>
    <v-alert v-if="saveError" type="error" variant="tonal" role="alert">{{ saveError }}</v-alert>
    <v-alert v-if="savedNotice && !dirty" type="success" variant="tonal" role="status">{{ savedNotice }}</v-alert>
    <p v-if="loading && !snapshot" class="muted" role="status">正在读取本场景技能目录与许可…</p>
    <template v-if="snapshot">
      <div class="directories"><p>根配置保存目录：<strong>{{ snapshot.directory ?? '未设置；不装载技能' }}</strong></p>
        <p>当前运行目录：<strong>{{ snapshot.running_directory ?? '未设置；未装载技能' }}</strong></p>
        <p v-if="snapshot.directory!==snapshot.running_directory" class="muted">目录保存值待重启；当前运行名单仍来自旧目录。</p></div>
      <div class="section-heading"><h3>角色技能许可</h3><v-chip variant="tonal" :color="snapshot.role_skills.restart_required?'warning':'info'">
        {{ snapshot.role_skills.restart_required?'保存值待重启':'保存值与运行值一致' }}</v-chip></div>
      <p>运行中许可：{{ snapshot.role_skills.running === 'all' ? '不逐项限制技能' : snapshot.role_skills.running.join('、') || '无' }}</p>
      <p>角色包保存值：{{ snapshot.role_skills.saved === 'all' ? '不逐项限制技能' : snapshot.role_skills.saved.join('、') || '无' }}</p>
      <p class="muted">此角色包还用于：{{ snapshot.role_skills.affected_scenes.map(sceneName).join('、') }}。保存会影响这些场景下次启动的许可，不会热加载任务。</p>
      <form @submit.prevent="save">
        <v-radio-group v-model="draftMode" label="保存的技能许可范围" :disabled="saving || loading" hide-details>
          <v-radio label="不逐项限制技能（也允许后续目录技能及本任务自写技能）" value="all" />
          <v-radio label="只允许勾选的技能" value="selected" />
        </v-radio-group>
        <div v-if="draftMode==='selected'" class="skill-choice">
          <v-checkbox v-for="item in snapshot.catalog" :key="item.name"
            :model-value="draftNames.includes(item.name)" :label="item.name" :disabled="saving || loading"
            hide-details @update:model-value="value=>toggleName(item.name,value)" />
          <p v-if="otherNames.length" class="muted">保存许可中还有当前目录未列出的名称：{{ otherNames.join('、') }}；不会自动删除或替换，若目录和许可冲突由后端返回错误原文。</p>
          <p v-if="!snapshot.catalog.length" class="muted">当前没有可选目录条目；未设置目录与目录为空不是同一情况。</p>
        </div>
        <p v-if="dirty" class="dirty-note" role="status">技能许可草稿尚未保存。</p>
        <div class="form-actions"><v-btn type="submit" color="primary" :loading="saving" :disabled="!dirty || loading">保存角色技能许可</v-btn>
          <span class="muted">写入共享角色包；不覆盖上方工具许可草稿。</span></div>
      </form>
      <h3>根配置目录中的技能</h3>
      <p class="muted">保存选择仅表示下次启动的允许范围；名称白名单与空列表不会自动扩大到任务自写技能。标成“仅显式调用”的技能不会自动被模型发现，也不表示已经使用。</p>
      <ul v-if="snapshot.catalog.length" class="skill-list"><li v-for="item in snapshot.catalog" :key="item.name">
        <strong>{{ item.name }}</strong> · {{ sourceLabel(item.source) }} · {{ item.selected?'保存值已选':'保存值未选' }} · {{ item.model_invocation?'可按描述自动发现':'仅显式调用' }}
        <p class="muted">{{ item.description }}</p><p class="path">容器文件：{{ item.path }}</p></li></ul>
      <p v-else class="muted">{{ snapshot.directory===null?'未设置技能目录；当前不装载内置、共享或场景技能。':'所选目录没有可用技能。' }}</p>
      <h3>当前运行已装配</h3>
      <ul v-if="snapshot.running.length" class="skill-list"><li v-for="item in snapshot.running" :key="item.name">
        <strong>{{ item.name }}</strong> · {{ sourceLabel(item.source) }} · {{ item.model_invocation?'可按描述自动发现':'仅显式调用' }}
        <p class="muted">{{ item.description }}</p><p class="path">容器文件：{{ item.path }}</p></li></ul>
      <p v-else class="muted">当前运行没有已装配的根／场景技能；本任务自己写的技能不在这份全局名单中。</p>
      <p class="muted">以上是装配与许可快照，不是技能已经被调用或任务完成的证明。</p>
    </template>
  </section>
</template>

<style scoped>
.skill-settings{min-width:0;overflow-wrap:anywhere}.section-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap}.section-heading>div{min-width:0;flex:1 1 360px}
.skill-settings h2{font-size:18px;margin:0 0 8px}.skill-settings h3{font-size:15px;margin:20px 0 8px}.section-heading h3{margin-top:0}
.directories{border-left:3px solid var(--line);padding-left:12px;margin:12px 0}.directories p{margin:4px 0}.skill-list{list-style:none;margin:0;padding:0;display:grid;gap:10px}.skill-list li{border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0;overflow-wrap:anywhere}.skill-list p{margin:6px 0}.path{font-size:13px;overflow-wrap:anywhere}
.skill-choice{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr));gap:2px 12px}.skill-choice p{grid-column:1/-1}.form-actions{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:14px 0}.dirty-note{border-left:3px solid var(--primary);background:var(--selected-bg);padding:8px 12px}.skill-settings :deep(.v-btn){min-height:44px}
@media(max-width:600px){.section-heading>.v-btn{width:100%}}
</style>
