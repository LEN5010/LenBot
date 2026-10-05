<script setup>
import { onBeforeUnmount, ref } from 'vue'
import { useAction } from '../../../composables/useResource.js'
import ErrorNote from '../../ui/ErrorNote.vue'
import AdvancedFields from '../../ui/AdvancedFields.vue'

const props = defineProps({ profile: { type: Object, required: true }, preview: { type: Function, required: true }, disabled: Boolean })
const emit = defineEmits(['imported'])
const file = ref(null), action = useAction(), done = ref(''), downloadUrl = ref('')
function clearDownload() {
  if (downloadUrl.value) URL.revokeObjectURL(downloadUrl.value)
  downloadUrl.value = ''
}
onBeforeUnmount(clearDownload)
async function load() {
  done.value = ''
  const result = await action.run(async () => {
    const text = await file.value.text()
    let profile
    try { profile = JSON.parse(text) }
    catch (error) { throw new Error(`草稿 JSON 解析失败：${error.message}；原文开头：${text.slice(0, 300)}`) }
    return props.preview(profile)
  })
  if (!result) return
  emit('imported', result.profile)
  done.value = '已载入到表单，还没保存。可以先试聊，满意再保存。'
  file.value = null
}
async function download() {
  done.value = ''
  const result = await action.run(() => props.preview(props.profile))
  if (!result) return
  clearDownload()
  downloadUrl.value = URL.createObjectURL(new Blob([JSON.stringify(result.profile, null, 2) + '\n'], { type: 'application/json' }))
  done.value = '草稿文件准备好了，点下载链接保存。之后再改表单需要重新生成。'
}
</script>

<template>
  <AdvancedFields label="导入／导出角色草稿" class="draft-file">
    <div class="body">
      <p class="muted small">草稿只包含这一页的设定和样例。载入会替换当前表单，保存后才生效；整个角色搬家请用导入导出里的 ZIP。</p>
      <v-file-input v-model="file" label="角色草稿 JSON" accept="application/json,.json" prepend-icon="" :disabled="disabled || action.busy.value" />
      <div class="inline">
        <v-btn variant="tonal" :disabled="!file || disabled || action.busy.value" @click="load">载入到表单</v-btn>
        <v-btn variant="text" :disabled="disabled || action.busy.value" @click="download">生成当前草稿文件</v-btn>
        <a v-if="downloadUrl" :href="downloadUrl" download="persona-profile-draft.json">下载草稿 JSON</a>
      </div>
      <v-progress-linear v-if="action.busy.value" indeterminate color="primary" />
      <ErrorNote v-if="action.error.value" title="草稿没有处理成功" :error="action.error.value" />
      <p v-if="done" class="muted small">{{ done }}</p>
    </div>
  </AdvancedFields>
</template>
<style scoped>
.draft-file :deep(.advanced-grid){display:block}
.body{display:grid;gap:var(--sp-3)}
.body p{margin:0}
</style>
