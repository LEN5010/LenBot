<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api.js'
import PageHeader from '../components/PageHeader.vue'
import ScopeSelect from '../components/ScopeSelect.vue'

const route = useRoute(), router = useRouter()
const wizard = computed(() => ['python', 'research', 'broadcast'].includes(route.query.wizard) ? route.query.wizard : 'python')
const options = ref(null), preview = ref(null), error = ref(''), message = ref(''), loading = ref(false), busy = ref(false)
const pythonScene = ref(''), researchTopics = ref(''), shareScenes = ref([]), dailyLimit = ref(2), cooldown = ref(3600)
const broadcastScene = ref(''), broadcastPlugin = ref('asoul_calendar'), liveNames = ref([]), mentionAll = ref(false), announce = ref(true)
watch(() => route.query.scene, value => { if (value) { pythonScene.value = value; broadcastScene.value = value } }, { immediate: true })

async function loadOptions() {
  loading.value = true; error.value = ''
  try { options.value = await api('/api/setup/options'); if (options.value.heartbeat_topics?.length && !researchTopics.value) researchTopics.value = options.value.heartbeat_topics.join('\n') }
  catch (e) { error.value = e.message }
  finally { loading.value = false }
}
function values() {
  if (wizard.value === 'python') return { scene_id: pythonScene.value }
  if (wizard.value === 'research') return {
    topics: researchTopics.value.split('\n').map(item => item.trim()).filter(Boolean),
    share_scenes: shareScenes.value, daily_limit: Number(dailyLimit.value), cooldown_seconds: Number(cooldown.value),
  }
  return { scene_id: broadcastScene.value, plugin_id: broadcastPlugin.value, live_subscriptions: liveNames.value,
           mention_all: mentionAll.value, announce: announce.value, commands: ['calendar_today', 'calendar_tomorrow', 'calendar_week'] }
}
async function loadPreview() {
  error.value = ''; message.value = ''; preview.value = null; busy.value = true
  try { preview.value = await api('/api/setup/preview', { method: 'POST', body: JSON.stringify({ wizard: wizard.value, values: values() }) }) }
  catch (e) { error.value = e.message }
  finally { busy.value = false }
}
async function apply() {
  if (!preview.value || preview.value.blocked) return
  busy.value = true; error.value = ''; message.value = ''
  try {
    const result = await api('/api/setup/apply', { method: 'POST', body: JSON.stringify({ wizard: wizard.value, values: values() }) })
    message.value = result.message + (result.requires_restart ? '；研究开关需手动重启后生效。' : '')
    preview.value = result.preview
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
function selectWizard(value) { router.replace({ name: 'setup', query: { wizard: value, scene: route.query.scene } }) }
watch(wizard, () => { preview.value = null; message.value = '' })
loadOptions()
</script>
<template>
  <div class="page-stack">
    <PageHeader title="能力启用向导" description="按任务预览并保存根配置改动；不填写 grant_id 或内部主体，也不用 raw JSON。">
      <v-btn variant="outlined" :to="{ name: 'capabilities' }">返回能力页</v-btn>
    </PageHeader>
    <v-btn-toggle :model-value="wizard" mandatory @update:model-value="selectWizard">
      <v-btn value="python">本群使用 Python</v-btn>
      <v-btn value="research">公开研究与指定群分享</v-btn>
      <v-btn value="broadcast">增加群播报来源</v-btn>
    </v-btn-toggle>
    <v-alert v-if="error" type="error" variant="tonal">{{ error }}</v-alert>
    <v-alert v-if="message" type="success" variant="tonal">{{ message }}</v-alert>
    <v-progress-linear v-if="loading" indeterminate />
    <v-card v-if="wizard==='python'" class="pa-5 form-card">
      <h2>向导 A · 本群使用 Python</h2>
      <p class="muted">展示已有后端并只开放本群。不配置出网，不授予文件发布。</p>
      <ScopeSelect :model-value="pythonScene" label="选择群" @update:model-value="pythonScene=$event" />
    </v-card>
    <v-card v-else-if="wizard==='research'" class="pa-5 form-card">
      <h2>向导 B · 公开研究与指定群分享</h2>
      <p class="muted">系统研究与分享群分开。没有分享群也可以只做研究；研究成功不授予发布。</p>
      <v-textarea v-model="researchTopics" label="研究主题（每行一项）" auto-grow />
      <v-select v-model="shareScenes" :items="(options?.scenes||[]).map(item=>({title:item.scene_id,value:item.scene_id}))" label="允许分享的群（可空）" multiple chips />
      <v-text-field v-model.number="dailyLimit" type="number" label="每群每日分享上限" />
      <v-text-field v-model.number="cooldown" type="number" label="冷却秒数" />
    </v-card>
    <v-card v-else class="pa-5 form-card">
      <h2>向导 C · 增加一个群播报来源</h2>
      <p class="muted">核对全局来源后写入本群播报。仅关闭普通聊天不叫播报配置完成。</p>
      <ScopeSelect :model-value="broadcastScene" label="选择群" @update:model-value="broadcastScene=$event" />
      <v-select v-model="broadcastPlugin" :items="[{title:'A-SOUL 日程',value:'asoul_calendar'},{title:'A-SOUL 动态',value:'asoul_dynamics'},{title:'直播监测',value:'bilibili_live_sensor'}]" label="来源服务" />
      <v-select v-if="broadcastPlugin==='bilibili_live_sensor'" v-model="liveNames" :items="(options?.members||[]).map(item=>({title:item.name,value:item.name}))" label="已登记主播" multiple chips />
      <v-switch v-if="broadcastPlugin==='bilibili_live_sensor'" v-model="announce" label="开播邀请" />
      <v-switch v-if="broadcastPlugin==='bilibili_live_sensor'" v-model="mentionAll" label="本群播报可 @全体" />
    </v-card>
    <div class="actions"><v-btn variant="tonal" :loading="busy" @click="loadPreview">预览改动</v-btn><v-btn color="primary" :disabled="!preview || preview.blocked" :loading="busy" @click="apply">保存预览中的改动</v-btn></div>
    <v-card v-if="preview" class="pa-5 form-card">
      <h2>将要写入的改动</h2>
      <v-alert v-if="preview.blocked" type="warning" variant="tonal">{{ preview.blocked }}</v-alert>
      <p v-if="preview.backend" class="muted">当前后端：{{ preview.backend }}</p>
      <p v-if="preview.note" class="muted">{{ preview.note }}</p>
      <article v-for="(change,index) in preview.changes||[]" :key="index" class="change">
        <strong>{{ change.path }}</strong>
        <p>{{ change.note }}</p>
        <pre>{{ JSON.stringify({ from: change.from, to: change.to }, null, 2) }}</pre>
      </article>
      <p v-if="preview && !preview.blocked && !preview.changes?.length" class="muted">没有需要保存的改动。</p>
    </v-card>
  </div>
</template>
<style scoped>
.form-card{display:grid;gap:16px}.actions{display:flex;gap:12px;flex-wrap:wrap}.change{padding:12px 0;border-top:1px solid var(--line)}.change pre{font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere}
</style>
