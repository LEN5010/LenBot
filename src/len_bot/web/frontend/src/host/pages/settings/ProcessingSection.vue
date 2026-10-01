<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../../../api.js'
import { useAction } from '../../../composables/useResource.js'
import { clone, numberOrBlank, same } from '../../forms.js'
import SettingSection from '../../components/SettingSection.vue'

const props = defineProps({ snapshot: { type: Object, required: true } })
const emit = defineEmits(['saved', 'dirty'])
const saved = computed(() => props.snapshot.saved.processing)
const groups = [
  ['compaction', '上下文压缩', [
    ['trigger_ratio', '压缩时机（0–1）', '对话占到模型上下文的这个比例时，把较早的内容压缩成回想'],
    ['keep_recent_entries', '保留最近条数', '压缩时原样保留的最近对话条数'],
    ['max_output_tokens', '回想最大长度（token）', '']]],
  ['images', '图片', [
    ['max_bytes', '最大文件大小（字节）', '超过的图片不交给模型看'], ['max_pixels', '最大像素数', ''],
    ['max_dimension', '缩放后最长边（像素）', '图片交给模型前会缩小到这个尺寸'], ['timeout_seconds', '下载超时（秒）', '']]],
  ['audio', '语音', [
    ['max_bytes', '最大文件大小（字节）', ''], ['max_seconds', '最长语音（秒）', '超过的语音不转写'],
    ['timeout_seconds', '转写超时（秒）', ''], ['wait_seconds', '回复前等转写（秒）', 'Bot 回复前最多等语音转写这么久']]],
]
const draft = ref(null)
const save = useAction()
watch(() => JSON.stringify(saved.value), () => { draft.value = clone(saved.value) }, { immediate: true })
const dirty = computed(() => !same(draft.value, saved.value))
watch(dirty, value => emit('dirty', value), { immediate: true })

function toggleLogging(value) {
  draft.value.logging = value ? { directory: 'data/logs', retention_days: 14, level: 'INFO' } : null
}
async function submit() {
  const result = await save.run(() => api('/api/host/settings/processing', { method: 'PUT', body: JSON.stringify(draft.value) }))
  if (result) emit('saved', result)
}
</script>

<template>
  <SettingSection v-if="draft" title="上下文、媒体与日志" :dirty="dirty" :saving="save.busy.value" :error="save.error.value" @save="submit">
    <div v-for="[key, title, fields] in groups" :key="key">
      <h3>{{ title }}</h3>
      <div class="form-grid">
        <v-text-field v-for="[field, label, hint] in fields" :key="field" :model-value="draft[key][field]" type="number" :label="label"
          :hint="hint" :persistent-hint="Boolean(hint)" @update:model-value="value => draft[key][field] = numberOrBlank(value)" />
      </div>
    </div>
    <div>
      <h3>日志</h3>
      <v-switch :model-value="draft.logging !== null" label="把运行日志保存到文件" hint="排查问题时有用，每天一个文件" persistent-hint
        @update:model-value="toggleLogging" />
      <div v-if="draft.logging" class="form-grid mt-4">
        <v-text-field v-model="draft.logging.directory" label="日志目录" hint="相对于 LenBot 实例目录" persistent-hint />
        <v-text-field :model-value="draft.logging.retention_days" type="number" label="保留天数"
          @update:model-value="value => draft.logging.retention_days = numberOrBlank(value)" />
        <v-select v-model="draft.logging.level" :items="['INFO', 'WARNING', 'ERROR']" label="记录级别" />
      </div>
    </div>
  </SettingSection>
</template>

<style scoped>
h3{font-size:14px;margin:0 0 10px}
</style>
