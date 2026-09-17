<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtAgo } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import HelpHint from '../components/HelpHint.vue'
import SceneSettingsForm from '../components/SceneSettingsForm.vue'

const WORK_PLUGINS = ['workspace', 'python_workspace']
const PERMISSION_HELP = `聊天与工作是两种独立权限，不靠调参区分。

聊天：普通成员能否与 Bot 正常互动。关掉后闲聊仍会保存，QQ 白名单成员依然能提问。

工作：本群是否开放可跑代码、产出文件的工作空间插件。

旁听节奏（抽样概率、窗口、冷却）是全局统一的一份，新群自动继承，不在这里逐群设置。`

const route = useRoute()
const router = useRouter()
const sceneId = computed(() => route.params.sceneId || '')
const rows = ref([])
const loading = ref(false)
const error = ref('')
const message = ref('')
const busy = ref('')
const search = ref('')
const permission = ref('all')
const picked = ref([])
const guard = useRequestGuard()

function permissions(item) {
  const settings = item.settings
  if (!settings) return { chat: 'unconfigured', work: 'unconfigured', enabled: false }
  if (!settings.enabled) return { chat: 'disabled', work: 'off', enabled: false }
  const plugins = settings.plugins || {}
  const work = WORK_PLUGINS.some(name => plugins[name]?.enabled)
  return { chat: settings.chat ? 'on' : 'off', work: work ? 'on' : 'off', enabled: true }
}

const groups = computed(() => rows.value
  .filter(item => item.scene_id.startsWith('group:'))
  .map(item => ({ ...item, perm: permissions(item) })))

const shown = computed(() => {
  const text = search.value.trim()
  return groups.value.filter(item => {
    if (text && !`${item.display_name} ${item.scene_id}`.includes(text)) return false
    if (permission.value === 'chat') return item.perm.chat === 'on'
    if (permission.value === 'work') return item.perm.work === 'on'
    if (permission.value === 'unconfigured') return item.perm.chat === 'unconfigured'
    return true
  })
})

const counts = computed(() => ({
  total: groups.value.length,
  chat: groups.value.filter(item => item.perm.chat === 'on').length,
  work: groups.value.filter(item => item.perm.work === 'on').length,
  unconfigured: groups.value.filter(item => item.perm.chat === 'unconfigured').length,
}))

async function load() {
  const fresh = guard()
  loading.value = true
  error.value = ''
  try {
    const data = await api('/api/cockpit/scenes')
    if (fresh()) rows.value = data.scenes || []
  } catch (problem) {
    if (fresh()) error.value = problem.message
  } finally {
    if (fresh()) loading.value = false
  }
}

// One group at a time, each with its own baseline: a batch is a convenience
// over the same guarded save, never a way around the conflict check.
async function applyBatch(value) {
  if (!picked.value.length) return
  busy.value = 'batch'
  error.value = ''
  message.value = ''
  const done = [], failed = []
  for (const id of picked.value) {
    const path = `/api/cockpit/scenes/${encodeURIComponent(id)}/settings`
    try {
      const record = await api(path)
      if (!record.settings) { failed.push(`${id}：尚未配置，先单独打开配置一次`); continue }
      if (record.settings.chat === value) { done.push(id); continue }
      await api(path, { method: 'PUT', body: JSON.stringify({
        baseline: record.settings, values: { ...record.settings, chat: value } }) })
      done.push(id)
    } catch (problem) {
      failed.push(`${id}：${problem.message}`)
    }
  }
  busy.value = ''
  picked.value = []
  message.value = `${done.length} 个群已${value ? '开启' : '关闭'}聊天`
  if (failed.length) error.value = failed.join('；')
  await load()
}

watch(sceneId, () => { message.value = '' })
load()
</script>

<template>
  <div class="page-stack">
    <PageHeader title="群与权限" description="哪些群能聊天、哪些群能干活，以及每个群的单页配置。" />

    <template v-if="!sceneId">
      <v-alert v-if="error" type="error" variant="tonal">{{ error }}</v-alert>
      <v-alert v-if="message" type="success" variant="tonal">{{ message }}</v-alert>

      <div class="surface">
        <div class="filters">
          <v-text-field v-model="search" label="搜索群名或群号" clearable />
          <v-select v-model="permission" label="按权限筛选" :items="[
            {title:`全部（${counts.total}）`, value:'all'},
            {title:`可聊天（${counts.chat}）`, value:'chat'},
            {title:`可工作（${counts.work}）`, value:'work'},
            {title:`未配置（${counts.unconfigured}）`, value:'unconfigured'}]" />
          <HelpHint :text="PERMISSION_HELP" />
        </div>

        <div v-if="picked.length" class="batch-bar">
          <span>已选 {{ picked.length }} 个群</span>
          <v-btn size="small" color="primary" variant="tonal" :loading="busy==='batch'"
                 :disabled="!!busy" @click="applyBatch(true)">开启聊天</v-btn>
          <v-btn size="small" variant="outlined" :loading="busy==='batch'"
                 :disabled="!!busy" @click="applyBatch(false)">关闭聊天</v-btn>
          <v-btn size="small" variant="text" :disabled="!!busy" @click="picked=[]">取消选择</v-btn>
        </div>

        <v-skeleton-loader v-if="loading && !rows.length" type="list-item-two-line@3" />
        <p v-else-if="!shown.length" class="empty-state">没有符合条件的群。</p>
        <ul v-else class="group-list">
          <li v-for="item in shown" :key="item.scene_id" class="group-row">
            <v-checkbox-btn :model-value="picked.includes(item.scene_id)" :disabled="!!busy"
              @update:model-value="on => picked = on ? [...picked, item.scene_id]
                : picked.filter(id => id !== item.scene_id)" />
            <RouterLink class="group-main" :to="{name:'group', params:{sceneId:item.scene_id}}">
              <span class="group-name">{{ item.display_name }}</span>
              <span class="entity-id">{{ item.scene_id }}</span>
            </RouterLink>
            <div class="group-badges">
              <StatusBadge domain="scene_chat" :status="item.perm.chat" />
              <StatusBadge domain="scene_work" :status="item.perm.work" />
            </div>
            <span class="group-meta muted">{{ item.participant_count }} 人 · {{ fmtAgo(item.last_event_at) }}</span>
          </li>
        </ul>
      </div>
    </template>

    <template v-else>
      <v-btn variant="text" size="small" class="align-self-start"
             :to="{name:'groups'}">← 返回群列表</v-btn>
      <div class="surface"><SceneSettingsForm :scene-id="sceneId" @saved="load" /></div>
    </template>
  </div>
</template>

<style scoped>
.batch-bar { display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:16px;padding:12px 14px;border-radius:12px;background:rgb(var(--v-theme-surface-variant)) }
.group-list { list-style:none;margin:16px 0 0;padding:0;display:grid;gap:4px }
.group-row { display:flex;align-items:center;gap:12px;padding:10px 4px;border-bottom:1px solid var(--line);flex-wrap:wrap }
.group-row:last-child { border-bottom:0 }
.group-main { display:flex;flex-direction:column;gap:2px;min-width:0;flex:1 1 220px;text-decoration:none }
.group-main:hover .group-name { text-decoration:underline }
.group-name { font-weight:600;color:var(--ink);overflow-wrap:anywhere }
.group-badges { display:flex;gap:6px;flex:none }
.group-meta { font-size:13px;flex:none }
@media(max-width:600px){.group-meta{flex-basis:100%}}
</style>
