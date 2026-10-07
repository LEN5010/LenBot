<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../../../api.js'
import { useAction } from '../../../composables/useResource.js'
import { clone, numberOrBlank, same } from '../../forms.js'
import SettingSection from '../../ui/SettingSection.vue'

const props = defineProps({ snapshot: { type: Object, required: true } })
const emit = defineEmits(['saved', 'dirty'])
const saved = computed(() => props.snapshot.saved.processing)
const groups = [
  ['compaction', '上下文压缩', [
    ['input_tokens', '输入压缩阈值（token）'],
    ['keep_recent_tokens', '近期原文目标（token）'],
    ['max_output_tokens', '回想最大长度（token）']]],
  ['images', '图片', [
    ['max_bytes', '最大文件大小（字节）'], ['max_pixels', '最大像素数'],
    ['max_dimension', '缩放后最长边（像素）'], ['timeout_seconds', '下载超时（秒）']]],
  ['audio', '语音', [
    ['max_bytes', '最大文件大小（字节）'], ['max_seconds', '最长语音（秒）'],
    ['timeout_seconds', '转写超时（秒）'], ['wait_seconds', '回复前等转写（秒）']]],
]
const draft = ref(null)
const save = useAction()
watch(() => JSON.stringify(saved.value), () => { draft.value = clone(saved.value) }, { immediate: true })
const dirty = computed(() => !same(draft.value, saved.value))
watch(dirty, value => emit('dirty', value), { immediate: true })

async function submit() {
  const result = await save.run(() => api('/api/host/settings/processing', { method: 'PUT', body: JSON.stringify(draft.value) }))
  if (result) emit('saved', result)
}
</script>

<template>
  <SettingSection v-if="draft" title="上下文、媒体与日志" :dirty="dirty" :saving="save.busy.value" :error="save.error.value" @save="submit">
    <div v-for="[key, title, fields] in groups" :key="key" class="group">
      <h3>{{ title }}</h3>
      <div class="form-grid">
        <v-text-field v-for="[field, label] in fields" :key="field" :model-value="draft[key][field]" type="number" :label="label" @update:model-value="value => draft[key][field] = numberOrBlank(value)" />
      </div>
    </div>
    <div class="group">
      <h3>日志</h3>
      <div class="form-grid">
        <v-text-field v-model="draft.logging.directory" label="日志目录" placeholder="logs" />
        <v-text-field :model-value="draft.logging.retention_days" type="number" label="保留天数"
          @update:model-value="value => draft.logging.retention_days = numberOrBlank(value)" />
        <v-select v-model="draft.logging.level" :items="['INFO', 'WARNING', 'ERROR']" label="记录级别" />
      </div>
    </div>
  </SettingSection>
</template>

<style scoped>
.group{display:grid;gap:var(--sp-3)}
</style>
