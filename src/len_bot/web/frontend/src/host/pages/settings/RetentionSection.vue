<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify } from '../../store.js'
import { clone, numberOrBlank, same } from '../../forms.js'
import SettingSection from '../../components/SettingSection.vue'
import SceneRows from '../../components/SceneRows.vue'
import ErrorNote from '../../components/ErrorNote.vue'
import DevOnly from '../../components/DevOnly.vue'

const props = defineProps({ snapshot: { type: Object, required: true } })
const emit = defineEmits(['saved', 'dirty'])
const saved = computed(() => props.snapshot.saved.retention)
const scenes = computed(() => Object.keys(props.snapshot.saved.scenes))
const status = useResource(() => api('/api/host/retention'))
const draft = ref(null)
const save = useAction(), prune = useAction()
const counts = [['messages', '条聊天原文'], ['turns', '轮回复记录'], ['model_snapshots', '份模型请求记录'],
  ['auxiliary_snapshots', '份学习与媒体请求记录'], ['task_snapshots', '份任务请求记录'], ['memory_snapshots', '份记忆请求记录'],
  ['extraction_jobs', '份记忆抽取记录'], ['notices', '条平台通知']]
const summary = value => counts.filter(([key]) => value[key]).map(([key, unit]) => `${value[key]} ${unit}`).join('、')
watch(() => JSON.stringify(saved.value), () => { draft.value = saved.value === null ? null : clone(saved.value) }, { immediate: true })
const dirty = computed(() => !same(draft.value, saved.value))
watch(dirty, value => emit('dirty', value), { immediate: true })

function toggle(value) {
  draft.value = value ? { request_days: 7, timeline_days: 90, message_days: {} } : null
}
async function submit() {
  const result = await save.run(() => api('/api/host/settings/retention', { method: 'PUT', body: JSON.stringify({ retention: draft.value }) }))
  if (result) emit('saved', result)
}
async function runPrune() {
  if (!window.confirm('清理后无法恢复。确定现在清理一批？')) return
  const result = await prune.run(() => api('/api/host/retention?confirmed=true', { method: 'POST' }))
  if (result) notify(summary(result) ? `已清理 ${summary(result)}` : '没有需要清理的记录')
  status.reload()
}
</script>

<template>
  <div class="page-stack">
    <SettingSection title="数据保留" description="定期删除旧的聊天原文和请求记录，节省空间。角色、长期记忆和备份不受影响。"
      :dirty="dirty" :saving="save.busy.value" :error="save.error.value" @save="submit">
      <v-switch :model-value="draft !== null" label="自动清理旧记录" @update:model-value="toggle" />
      <template v-if="draft">
        <div class="form-grid">
          <v-text-field :model-value="draft.request_days" type="number" label="模型请求记录保留天数" hint="请求原文只用于排查问题，通常保留几天就够" persistent-hint
            @update:model-value="value => draft.request_days = numberOrBlank(value)" />
          <v-text-field :model-value="draft.timeline_days" type="number" label="回复记录保留天数" hint="至少 32 天" persistent-hint
            @update:model-value="value => draft.timeline_days = numberOrBlank(value)" />
        </div>
        <div>
          <h3>聊天原文保留天数</h3>
          <p class="muted">没有列出的群永久保留。</p>
          <SceneRows v-model="draft.message_days" :scenes="scenes" :empty-value="90" value-label="天数" />
        </div>
      </template>
    </SettingSection>
    <section v-if="status.data.value?.enabled" class="surface">
      <h2>立即清理</h2>
      <ErrorNote v-if="status.data.value.error" title="自动清理已停止" :error="status.data.value.error" />
      <ErrorNote v-if="status.error.value" title="读取清理预览失败" :error="status.error.value" />
      <ErrorNote v-if="prune.error.value" title="清理没有完成" :error="prune.error.value" />
      <p v-if="status.data.value.preview">{{ summary(status.data.value.preview) ? `下一批会清理 ${summary(status.data.value.preview)}。` : '目前没有需要清理的记录。' }}</p>
      <v-btn color="warning" variant="tonal" :loading="prune.busy.value" :disabled="status.data.value.busy || !summary(status.data.value.preview || {})"
        @click="runPrune">清理一批</v-btn>
      <DevOnly label="清理预览原始数据"><pre>{{ JSON.stringify(status.data.value, null, 2) }}</pre></DevOnly>
    </section>
  </div>
</template>

<style scoped>
h3{font-size:14px;margin:0}
h3 + .muted{margin:0 0 8px}
</style>
