<script setup>
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, sceneName } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify, readHostState, readPendingRestart } from '../../store.js'
import { clone, numberOrBlank, numberOrNull, same } from '../../forms.js'
import { confirm } from '../../../composables/useConfirm.js'
import Panel from '../../ui/Panel.vue'
import ResourceState from '../../ui/ResourceState.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import AdvancedFields from '../../ui/AdvancedFields.vue'
import RowEditor from '../../ui/RowEditor.vue'
import SaveBar from '../../ui/SaveBar.vue'
import QuietControl from './QuietControl.vue'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty'])
const router = useRouter()
const settings = useResource(() => api('/api/host/settings'))
const saved = computed(() => settings.data.value?.saved.scenes[props.scene] || null)
const group = computed(() => props.scene.split(':', 3)[1] === 'group')
const draft = ref(null), relationships = ref([])
const save = useAction(), binding = useAction()
const persona = ref('')

const timing = [
  ['direct_idle_seconds', '被叫到后等几秒再回'],
  ['direct_max_seconds', '被叫到后最多等几秒'],
  ['named_idle_seconds', '被提到名字后等几秒再回'],
  ['named_max_seconds', '被提到名字后最多等几秒'],
  ['focus_seconds', '说完话后继续留意几秒'],
  ['focus_idle_seconds', '留意期间等几秒再回'],
  ['focus_max_seconds', '留意期间最多等几秒'],
  ['keyword_cooldown_seconds', '关键词冷却（秒）'],
  ['ambient_threshold', '插话门槛'],
  ['ambient_min_interval_seconds', '两次插话最短间隔（秒）'],
  ['ambient_max_interval_seconds', '两次插话最长间隔（秒）'],
  ['max_extensions', '最多延长等待次数'],
]

function sceneBody(value, rows) {
  return {
    timezone: value.timezone || null, voice_mode: value.voice_mode,
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
const duplicateAccount = computed(() => new Set(relationships.value.map(row => row.qq)).size !== relationships.value.length)

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
  if (!await confirm({ title: `把 ${sceneName(props.scene)} 换成角色 ${persona.value}？`, text: '重启后生效。', confirmLabel: '换角色' })) return
  const result = await binding.run(() => api(`/api/host/settings/scenes/${encodeURIComponent(props.scene)}/persona`, {
    method: 'PUT', body: JSON.stringify({ persona: persona.value }),
  }))
  if (result) { settings.data.value = result; readPendingRestart(); notify('已保存') }
}
async function removeScene() {
  if (!await confirm({ title: `移除 ${sceneName(props.scene)}？`, text: '重启后 Bot 不再处理这个群。聊天记录、记忆和提醒都会保留。', confirmLabel: '移除', danger: true })) return
  const result = await binding.run(() => api(`/api/host/settings/scenes/${encodeURIComponent(props.scene)}`, { method: 'DELETE' }))
  if (result) { readPendingRestart(); readHostState(); notify('已移除，重启后生效'); router.push({ name: 'host-overview' }) }
}
</script>

<template>
  <ResourceState :resource="settings" error-title="读取群设置失败">
    <p v-if="!saved" class="muted">这个群已经从配置里移除，重启后不再显示。</p>
    <template v-else-if="draft">
      <QuietControl :scene="scene" />

      <form class="stack" @submit.prevent="submit">
        <Panel title="回复方式">
          <div class="form-grid">
            <v-text-field :model-value="draft.timezone ?? ''" label="本群时区" placeholder="和全局一致"
              @update:model-value="value => draft.timezone = value || null" />
          </div>
          <v-switch v-model="draft.transcribe_audio" label="自动转写语音消息" />
        </Panel>

        <Panel title="什么时候说话">
          <v-switch v-model="draft.attention.only_direct" label="只在被 @、被回复或私聊时说话" />
          <template v-if="!draft.attention.only_direct">
            <v-slider v-model="draft.attention.activity" :min="0" :max="1" :step="0.05" label="活跃度" thumb-label color="primary"
              />
            <v-combobox v-model="draft.attention.keywords" label="关键词" multiple chips closable-chips
              />
          </template>
          <v-combobox v-model="draft.attention.other_bot_ids" label="群里其他 Bot 的账号" placeholder="onebot:QQ号" multiple chips closable-chips
            />
          <v-switch :model-value="draft.attention.quiet_hours !== null" label="每天的安静时段" @update:model-value="toggleQuiet" />
          <div v-if="draft.attention.quiet_hours" class="form-grid">
            <v-text-field v-model="draft.attention.quiet_hours.start" label="开始" type="time" />
            <v-text-field v-model="draft.attention.quiet_hours.end" label="结束" type="time" />
            <v-select v-model="draft.attention.quiet_hours.direct" label="安静时被 @" :items="[
              { title: '照常回复', value: 'allow' }, { title: '回一句固定的话', value: 'notice' }, { title: '等安静结束再回', value: 'defer' }]"
              @update:model-value="value => { if (value !== 'notice') draft.attention.quiet_hours.notice_text = null }" />
            <v-text-field v-if="draft.attention.quiet_hours.direct === 'notice'" v-model="draft.attention.quiet_hours.notice_text" label="固定回复的内容" />
          </div>
          <AdvancedFields label="等待与插话的细节">
            <v-text-field v-for="[key, label] in timing" :key="key" :model-value="draft.attention[key]" type="number" :label="label" @update:model-value="value => draft.attention[key] = numberOrBlank(value)" />
          </AdvancedFields>
        </Panel>

        <Panel v-if="group" title="主动开话题">
          <v-switch :model-value="draft.proactive !== null" label="群里安静太久时主动开个话题"
            @update:model-value="toggleProactive" />
          <div v-if="draft.proactive" class="form-grid">
            <v-text-field :model-value="draft.proactive.idle_seconds / 3600" type="number" label="安静多少小时后"
              @update:model-value="value => draft.proactive.idle_seconds = value === '' ? '' : Math.round(Number(value) * 3600)" />
            <v-text-field v-model="draft.proactive.start" label="每天从几点开始" type="time" />
            <v-text-field v-model="draft.proactive.end" label="到几点结束" type="time" />
          </div>
        </Panel>

        <Panel title="在本群的称呼与关系">
          <v-combobox v-model="draft.scene_persona.persona_aliases" label="群友对 Bot 的其他称呼" multiple chips closable-chips
            />
          <h3>和群友的关系</h3>
          <RowEditor :items="relationships" :make="() => ({ qq: '', text: '' })" add-label="添加关系" columns="160px minmax(0,1fr)">
            <template #default="{ item }">
              <v-text-field v-model="item.qq" label="账号" placeholder="onebot:QQ号" />
              <v-textarea v-model="item.text" label="关系说明" rows="1" auto-grow placeholder="例如：群主，和 Bot 是老朋友" />
            </template>
          </RowEditor>
          <p v-if="duplicateAccount" class="problem">有重复的账号，请合并成一条</p>
          <v-textarea :model-value="draft.scene_persona.behavior_addendum ?? ''" label="本群的额外要求" rows="2" auto-grow
            placeholder="这个群聊技术话题，回复可以长一点"
            @update:model-value="value => draft.scene_persona.behavior_addendum = value || null" />
        </Panel>

        <Panel title="提醒与任务">
          <div class="form-grid">
            <v-switch v-model="draft.schedules.enabled" label="允许定提醒" />
            <v-switch v-model="draft.schedules.autonomous" label="允许 Bot 自己定提醒" :disabled="!draft.schedules.enabled" />
            <v-text-field :model-value="draft.schedules.max_pending" type="number" label="最多同时等待的提醒" :disabled="!draft.schedules.enabled"
              @update:model-value="value => draft.schedules.max_pending = numberOrBlank(value)" />
          </div>
          <div class="form-grid">
            <v-switch v-model="draft.tasks.enabled" label="允许委托任务" />
            <v-text-field :model-value="draft.tasks.max_running" type="number" label="同时进行的任务数" :disabled="!draft.tasks.enabled"
              @update:model-value="value => draft.tasks.max_running = numberOrBlank(value)" />
            <v-text-field :model-value="draft.tasks.max_daily_tasks" type="number" label="每人每天最多新任务" :disabled="!draft.tasks.enabled"
              @update:model-value="value => draft.tasks.max_daily_tasks = numberOrBlank(value)" />
          </div>
          <AdvancedFields v-if="draft.tasks.enabled" label="任务联网流量">
            <v-text-field v-for="[key, label] in [['egress_max_task_bytes', '每个任务流量上限（字节）'], ['egress_max_daily_bytes', '本群每天流量上限（字节）'], ['egress_bytes_per_second', '限速（字节／秒）']]"
              :key="key" :model-value="draft.tasks[key] ?? ''" type="number" :label="label" placeholder="全局设置"
              @update:model-value="value => draft.tasks[key] = numberOrNull(value)" />
          </AdvancedFields>
        </Panel>

        <SaveBar :on-save="submit" :dirty="dirty" :saving="save.busy.value" :error="save.error.value" label="保存本群设置"
          :problem="duplicateAccount ? '和群友的关系里有重复的账号' : ''" @discard="adopt" />
      </form>

      <Panel title="角色与移除">
        <ErrorNote v-if="binding.error.value" title="没有保存成功" :error="binding.error.value" />
        <div class="bind-row">
          <v-combobox v-model="persona" :items="personaOptions" label="角色包目录" />
          <v-btn variant="outlined" :loading="binding.busy.value" :disabled="!persona || persona === saved.persona" @click="rebind">换角色</v-btn>
        </div>
        <template #footer>
          <span class="muted small grow">移除后聊天记录、记忆和提醒都保留。</span>
          <v-btn color="error" variant="text" :loading="binding.busy.value" @click="removeScene">从配置移除这个群</v-btn>
        </template>
      </Panel>
    </template>
  </ResourceState>
</template>

<style scoped>
.bind-row{display:flex;gap:var(--sp-3);align-items:flex-start;flex-wrap:wrap}
.bind-row .v-input{flex:1 1 320px}
.grow{flex:1}
</style>
