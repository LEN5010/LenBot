<script setup>
import { onBeforeUnmount, ref } from 'vue'
import { useAction } from '../../../composables/useResource.js'
import ErrorNote from '../../components/ErrorNote.vue'

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
  done.value = '已载入表单，未保存角色。可先试聊，再明确保存采用。'
  file.value = null
}
async function download() {
  done.value = ''
  const result = await action.run(() => props.preview(props.profile))
  if (!result) return
  clearDownload()
  downloadUrl.value = URL.createObjectURL(new Blob([JSON.stringify(result.profile, null, 2) + '\n'], { type: 'application/json' }))
  done.value = '草稿文件已准备好，点击下载链接保存。后续表单修改需重新生成；角色文件未改。'
}
</script>
<template>
  <details class="surface draft-file">
    <summary>导入／导出角色草稿</summary>
    <p class="muted">JSON 只包含本页的设定与样例，不改角色 ID、工具权限或素材。载入会替换当前表单，保存才采用；完整角色迁移使用角色包 ZIP。</p>
    <v-file-input v-model="file" label="角色表单草稿 JSON" accept="application/json,.json" prepend-icon="" :disabled="disabled || action.busy.value" />
    <div class="actions">
      <v-btn variant="tonal" :disabled="!file || disabled || action.busy.value" @click="load">载入草稿到表单</v-btn>
      <v-btn variant="text" :disabled="disabled || action.busy.value" @click="download">生成当前草稿文件</v-btn>
      <a v-if="downloadUrl" :href="downloadUrl" download="persona-profile-draft.json">下载刚生成的草稿 JSON</a>
    </div>
    <v-progress-linear v-if="action.busy.value" indeterminate />
    <ErrorNote v-if="action.error.value" title="草稿未完成" :error="action.error.value" />
    <p v-if="done" class="muted">{{ done }}</p>
  </details>
</template>
<style scoped>.draft-file[open]{display:grid;gap:12px}.draft-file summary{cursor:pointer}.draft-file p{margin:0}.actions{display:flex;gap:10px;flex-wrap:wrap}</style>
