<script setup>
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify, readHostState, readPendingRestart } from '../../store.js'
import { clone, numberOrBlank, numberOrNull, same } from '../../forms.js'
import ErrorNote from '../../components/ErrorNote.vue'
import AdvancedFields from '../../components/AdvancedFields.vue'
import QuietControl from './QuietControl.vue'
import SaveBar from '../../components/SaveBar.vue'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty'])
const router = useRouter()
const settings = useResource(() => api('/api/host/settings'))
const saved = computed(() => settings.data.value?.saved.scenes[props.scene] || null)
const group = computed(() => props.scene.startsWith('group:'))
const draft = ref(null), relationships = ref([])
const save = useAction(), binding = useAction()
const persona = ref('')

const timing = [
  ['direct_idle_seconds', '被叫到后等几秒再回', '这段时间里没有新消息就开始回复，方便把连发的几条一起看'],
  ['direct_max_seconds', '被叫到后最多等几秒', ''],
  ['named_idle_seconds', '被提到名字后等几秒再回', ''],
  ['named_max_seconds', '被提到名字后最多等几秒', ''],
  ['focus_seconds', '说完话后继续留意几秒', '这段时间里群友接话，Bot 更容易接着聊'],
  ['focus_idle_seconds', '留意期间等几秒再回', ''],
  ['focus_max_seconds', '留意期间最多等几秒', ''],
  ['keyword_cooldown_seconds', '关键词冷却（秒）', '同一个关键词两次叫醒 Bot 的最短间隔'],
  ['ambient_threshold', '插话门槛', '越高越不容易在没被叫到时插话'],
  ['ambient_min_interval_seconds', '两次插话最短间隔（秒）', ''],
  ['ambient_max_interval_seconds', '两次插话最长间隔（秒）', 'Bot 一直没插话时，间隔会逐渐拉长到这个值'],
  ['max_extensions', '最多延长等待次数', '等待期间又来新消息时，最多再等几次'],
]

function sceneBody(value, rows) {
  return {
    timezone: value.timezone || null, voice_mode: value.voice_mode, voice_context_tokens: value.voice_context_tokens,
    attention: value.attention,
    schedules: { enabled: value.schedules.enabled, max_pending: value.schedules.max_pending, autonomous: value.schedules.autonomous },
    proactive: value.proactive, transcribe_audio: value.transcribe_audio,
    persona_aliases: value.scene_persona.persona_aliases,
    relationships: Object.fromEntries(rows.map(row => [row.qq, row.text])),
    behavior_addendum: value.scene_persona.behavior_addendum || null,
  }
}
function taskBody(value) {
  const { enabled, max_running, max_daily_tasks, egress_max_task_bytes, egress_max_daily_bytes, egress_bytes_per_second } = value.tasks
  return { enabled, max_running, max_daily_tasks, egress_max_task_bytes, egress_max_daily_bytes, egress_bytes_per_second }
}
const savedRows = value => Object.entries(value.scene_persona.relationships).map(([qq, text]) => ({ qq, text }))
function adopt() {
  draft.value = clone(saved.value)
  relationships.value = savedRows(saved.value)
  persona.value = saved.value.persona
}
watch(saved, value => { if (value && !draft.value) adopt() }, { immediate: true })

const sceneDirty = computed(() => Boolean(draft.value && !same(sceneBody(draft.value, relationships.value), sceneBody(saved.value, savedRows(saved.value)))))
const tasksDirty = computed(() => Boolean(draft.value && !same(taskBody(draft.value), taskBody(saved.value))))
const dirty = computed(() => sceneDirty.value || tasksDirty.value)
watch(() => dirty.value || Boolean(saved.value && persona.value !== saved.value.persona),
  value => emit('dirty', value), { immediate: true })
const personaOptions = computed(() => [...new Set(Object.values(settings.data.value?.saved.scenes || {}).map(item => item.persona))])
const duplicateQQ = computed(() => new Set(relationships.value.map(row => row.qq)).size !== relationships.value.length)

function toggleQuiet(value) {
  draft.value.attention.quiet_hours = value ? { start: '23:00', end: '07:00', direct: 'defer', notice_text: null } : null
}
function toggleProactive(value) {
  draft.value.proactive = value ? { idle_seconds: 10800, start: '10:00', end: '22:00' } : null
}
async function submit() {
  const path = `/api/host/settings/scenes/${encodeURIComponent(props.scene)}`
  const sceneValue = sceneDirty.value ? sceneBody(draft.value, relationships.value) : null
  const tasksValue = tasksDirty.value ? { tasks: taskBody(draft.value) } : null
  // Two endpoints: whatever was saved stays saved, and an unsaved part keeps its draft.
  const done = await save.run(async () => {
    if (sceneValue) settings.data.value = await api(path, { method: 'PUT', body: JSON.stringify(sceneValue) })
    if (tasksValue) settings.data.value = await api(`${path}/tasks`, { method: 'PUT', body: JSON.stringify(tasksValue) })
    return true
  })
  readPendingRestart()
  if (done) {
    adopt()
    notify('已保存')
  }
}
async function rebind() {
  if (!window.confirm('换成这个角色？重启后生效。')) return
  const result = await binding.run(() => api(`/api/host/settings/scenes/${encodeURIComponent(props.scene)}/persona`, {
    method: 'PUT', body: JSON.stringify({ persona: persona.value }),
  }))
  if (result) { settings.data.value = result; readPendingRestart(); notify('已保存') }
}
async function removeScene() {
  if (!window.confirm('从配置里移除这个群？重启后 Bot 不再处理这个群。聊天记录、记忆和提醒都会保留。')) return
  const result = await binding.run(() => api(`/api/host/settings/scenes/${encodeURIComponent(props.scene)}`, { method: 'DELETE' }))
  if (result) { readPendingRestart(); readHostState(); notify('已移除，重启后生效'); router.push({ name: 'host-scenes' }) }
}
</script>

<template>
  <div class="page-stack">
    <ErrorNote v-if="settings.error.value" title="读取群设置失败" :error="settings.error.value" />
    <p v-if="settings.data.value && !saved" class="surface">这个群已经从配置里移除，重启后不再显示。</p>
    <template v-if="draft">
      <QuietControl :scene="scene" />

      <form class="page-stack" @submit.prevent="submit">
        <section class="surface">
          <h2>回复方式</h2>
          <div class="form-grid">
            <v-select v-model="draft.voice_mode" label="怎么组织回复" :items="[
              { title: '先想再说（表达器润色）', value: 'voice' }, { title: '直接说', value: 'direct' }]"
              hint="先想再说会多调用一次模型，口吻更稳定" persistent-hint />
            <v-text-field :model-value="draft.voice_context_tokens" type="number" label="表达器近期原话预算（token）"
              hint="完整保留引用及必要原话，再按预算补充近期记录" persistent-hint
              @update:model-value="value => draft.voice_context_tokens = numberOrBlank(value)" />
            <v-text-field :model-value="draft.timezone ?? ''" label="本群时区" placeholder="和全局一致"
              hint="留空使用全局时区，例如 Asia/Shanghai" persistent-hint @update:model-value="value => draft.timezone = value || null" />
          </div>
          <v-switch v-model="draft.transcribe_audio" label="自动转写语音消息" hint="需要先在模型页配置语音识别" persistent-hint />
        </section>

        <section class="surface">
          <h2>什么时候说话</h2>
          <v-switch v-model="draft.attention.only_direct" label="只在被 @、被回复或私聊时说话" />
          <template v-if="!draft.attention.only_direct">
            <div>
              <v-slider v-model="draft.attention.activity" :min="0" :max="1" :step="0.05" label="活跃度" thumb-label hide-details color="primary" />
              <p class="muted field-hint">活跃度越高，Bot 越常在没被叫到时插话</p>
            </div>
            <v-combobox v-model="draft.attention.keywords" label="关键词" multiple chips closable-chips
              hint="群里出现这些词时，Bot 会留意要不要接话" persistent-hint />
          </template>
          <v-combobox v-model="draft.attention.other_bot_qqs" label="群里其他 Bot 的 QQ" multiple chips closable-chips
            hint="这些账号的普通消息不会叫醒 Bot，避免两个 Bot 互相聊个没完" persistent-hint />
          <v-switch :model-value="draft.attention.quiet_hours !== null" label="每天的安静时段" @update:model-value="toggleQuiet" />
          <div v-if="draft.attention.quiet_hours" class="form-grid">
            <v-text-field v-model="draft.attention.quiet_hours.start" label="开始" type="time" />
            <v-text-field v-model="draft.attention.quiet_hours.end" label="结束" type="time" hint="早于开始表示跨过午夜" persistent-hint />
            <v-select v-model="draft.attention.quiet_hours.direct" label="安静时被 @" :items="[
              { title: '照常回复', value: 'allow' }, { title: '回一句固定的话', value: 'notice' }, { title: '等安静结束再回', value: 'defer' }]"
              @update:model-value="value => { if (value !== 'notice') draft.attention.quiet_hours.notice_text = null }" />
            <v-text-field v-if="draft.attention.quiet_hours.direct === 'notice'" v-model="draft.attention.quiet_hours.notice_text" label="固定回复的内容" />
          </div>
          <AdvancedFields label="等待与插话的细节">
            <v-text-field v-for="[key, label, hint] in timing" :key="key" :model-value="draft.attention[key]" type="number" :label="label"
              :hint="hint" :persistent-hint="Boolean(hint)" @update:model-value="value => draft.attention[key] = numberOrBlank(value)" />
          </AdvancedFields>
        </section>

        <section v-if="group" class="surface">
          <h2>主动开话题</h2>
          <v-switch :model-value="draft.proactive !== null" label="群里安静太久时主动开个话题"
            hint="每天最多一次，安静时段内不会；需要先在学习标签打开回复效果" persistent-hint @update:model-value="toggleProactive" />
          <div v-if="draft.proactive" class="form-grid">
            <v-text-field :model-value="draft.proactive.idle_seconds / 3600" type="number" label="安静多少小时后" hint="至少 10 分钟" persistent-hint
              @update:model-value="value => draft.proactive.idle_seconds = value === '' ? '' : Math.round(Number(value) * 3600)" />
            <v-text-field v-model="draft.proactive.start" label="每天从几点开始" type="time" />
            <v-text-field v-model="draft.proactive.end" label="到几点结束" type="time" />
          </div>
        </section>

        <section class="surface">
          <h2>在本群的称呼与关系</h2>
          <p class="muted">只对这个群生效，角色本身的设定在角色页修改。</p>
          <v-combobox v-model="draft.scene_persona.persona_aliases" label="群友对 Bot 的其他称呼" multiple chips closable-chips
            hint="群里这样叫 Bot 时，Bot 知道是在叫自己" persistent-hint />
          <div class="relations">
            <h3>和群友的关系</h3>
            <div v-for="(row, index) in relationships" :key="index" class="relation-row">
              <v-text-field v-model="row.qq" label="QQ" inputmode="numeric" />
              <v-textarea v-model="row.text" label="关系说明" rows="1" auto-grow placeholder="例如：群主，和 Bot 是老朋友" />
              <v-btn variant="text" @click="relationships.splice(index, 1)">删除</v-btn>
            </div>
            <p v-if="duplicateQQ" class="error-line">有重复的 QQ，请合并成一条</p>
            <v-btn variant="outlined" size="small" @click="relationships.push({ qq: '', text: '' })">添加关系</v-btn>
          </div>
          <v-textarea :model-value="draft.scene_persona.behavior_addendum ?? ''" label="本群的额外要求" rows="2" auto-grow
            hint="例如：这个群聊技术话题，回复可以长一点" persistent-hint
            @update:model-value="value => draft.scene_persona.behavior_addendum = value || null" />
        </section>

        <section class="surface">
          <h2>提醒与任务</h2>
          <p class="muted">谁可以用这些功能在设置页的权限里修改。</p>
          <div class="form-grid">
            <v-switch v-model="draft.schedules.enabled" label="允许定提醒" />
            <v-switch v-model="draft.schedules.autonomous" label="允许 Bot 自己定提醒" :disabled="!draft.schedules.enabled" />
            <v-text-field :model-value="draft.schedules.max_pending" type="number" label="最多同时等待的提醒" :disabled="!draft.schedules.enabled"
              @update:model-value="value => draft.schedules.max_pending = numberOrBlank(value)" />
          </div>
          <div class="form-grid">
            <v-switch v-model="draft.tasks.enabled" label="允许委托任务" hint="需要先在能力页配置任务执行环境" persistent-hint />
            <v-text-field :model-value="draft.tasks.max_running" type="number" label="同时进行的任务数" :disabled="!draft.tasks.enabled"
              @update:model-value="value => draft.tasks.max_running = numberOrBlank(value)" />
            <v-text-field :model-value="draft.tasks.max_daily_tasks" type="number" label="每人每天最多新任务" :disabled="!draft.tasks.enabled"
              @update:model-value="value => draft.tasks.max_daily_tasks = numberOrBlank(value)" />
          </div>
          <AdvancedFields v-if="draft.tasks.enabled" label="任务联网流量">
            <v-text-field v-for="[key, label] in [['egress_max_task_bytes', '每个任务流量上限（字节）'], ['egress_max_daily_bytes', '本群每天流量上限（字节）'], ['egress_bytes_per_second', '限速（字节／秒）']]"
              :key="key" :model-value="draft.tasks[key] ?? ''" type="number" :label="label" hint="留空使用全局设置" persistent-hint
              @update:model-value="value => draft.tasks[key] = numberOrNull(value)" />
          </AdvancedFields>
        </section>

        <SaveBar :on-save="submit" :dirty="dirty" :saving="save.busy.value" :error="save.error.value" label="保存本群设置"
          :problem="duplicateQQ ? '和群友的关系里有重复的 QQ' : ''" @discard="adopt" />
      </form>

      <section class="surface">
        <h2>角色</h2>
        <ErrorNote v-if="binding.error.value" title="没有保存成功" :error="binding.error.value" />
        <div class="bind-row">
          <v-combobox v-model="persona" :items="personaOptions" label="角色包目录" hint="选择已有角色包，或填写新的角色包目录" persistent-hint />
          <v-btn color="primary" variant="tonal" :loading="binding.busy.value" :disabled="!persona || persona === saved.persona" @click="rebind">换角色</v-btn>
        </div>
        <v-btn class="mt-6" color="error" variant="text" :loading="binding.busy.value" @click="removeScene">从配置移除这个群</v-btn>
      </section>
    </template>
  </div>
</template>

<style scoped>
section.surface{display:grid;gap:16px;align-content:start}
section.surface > h2{margin:0 !important}
section.surface > .muted{margin:-8px 0 0 !important}
h3{font-size:14px;margin:0}
.field-hint{font-size:12px;margin:0 16px}
.relations{display:grid;gap:8px;justify-items:start}
.relation-row{display:grid;grid-template-columns:160px minmax(0,1fr) auto;gap:12px;align-items:start;width:100%}
.error-line{color:var(--error-text);margin:0}
.bind-row{display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap}
.bind-row .v-input{flex:1 1 320px}
@media(max-width:600px){.relation-row{grid-template-columns:1fr auto}.relation-row .v-textarea{grid-column:1/-1;grid-row:2}}
</style>
