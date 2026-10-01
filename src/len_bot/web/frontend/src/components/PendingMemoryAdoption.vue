<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const props = defineProps({
  scene: { type: String, required: true },
  source: { type: String, required: true },
  original: { type: String, required: true },
  personaIds: { type: Array, required: true },
  blocked: { type: Boolean, required: true },
})
const emit = defineEmits(['dirty', 'busy', 'adopted'])
const target = ref(''), content = ref(''), reason = ref('')
const removeSource = ref(false), reviewed = ref(false), busy = ref(false)
const error = ref(''), result = ref(null)
const begin = useRequestGuard(() => `${props.scene}\u0000${props.source}\u0000${props.original}`)
const dirty = computed(() => result.value === null && (busy.value || target.value !== ''
  || content.value !== '' || reason.value !== '' || removeSource.value || reviewed.value))
watch(dirty, value => emit('dirty', value), { immediate: true })
watch(busy, value => emit('busy', value), { immediate: true })
const disabled = computed(() => busy.value || props.blocked || result.value !== null)
function discard() {
  if (busy.value || !window.confirm('放弃本次采用草稿？待确认原件不会改变。')) return
  target.value = ''; content.value = ''; reason.value = ''
  removeSource.value = false; reviewed.value = false; error.value = ''; result.value = null
}
async function adopt() {
  if (disabled.value || !reviewed.value || !target.value || !content.value.trim() || !reason.value.trim()) return
  const body = { scene: props.scene, source: props.source, original: props.original,
    target: target.value, content: content.value, reason: reason.value, remove_source: removeSource.value }
  const fresh = begin()
  busy.value = true; error.value = ''
  try {
    const value = await api('/api/host/memory/adopt-pending', { method: 'POST', body: JSON.stringify(body) })
    if (!fresh()) return
    result.value = value
    emit('adopted', value)
  } catch (failure) {
    if (fresh()) error.value = failure.status >= 400 && failure.status < 500
      ? `采用未被接受：${failure.message}`
      : `采用结果未确认：${failure.message} 草稿已保留；请手动核对目标与原件，不会自动重试。`
  } finally { busy.value = false }
}
</script>

<template>
  <section class="pending-adoption">
    <h3>明确采用待确认旧资料</h3>
    <p class="muted">当前原件：{{ source }}。下方正文从空白开始，请写入核对后应保留的完整事实，不自动把旧文件标题或混合角色资料当成事实。</p>
    <details><summary>查看本次读取的完整原件（非上方编辑草稿）</summary><pre>{{ original }}</pre></details>
    <p class="muted">只新建本场景正式 Markdown，不覆盖已有文件，不提升公共资料。Bot 资料须放在 bot/真实角色ID/文件.md；本场景已知实际角色：{{ personaIds.join('、') || '暂无' }}。</p>
    <form @submit.prevent="adopt">
      <v-text-field v-model="target" label="新的正式目标路径（例如 notes/topic.md）" :disabled="disabled" hide-details="auto" />
      <v-textarea v-model="content" label="重新核对后的完整正文" rows="8" auto-grow :disabled="disabled" hide-details="auto" />
      <v-textarea v-model="reason" label="本次采用原因" rows="2" auto-grow :disabled="disabled" hide-details="auto" />
      <v-checkbox v-model="removeSource" label="正式文件保存后，普通删除待确认原件（仍留历史，不是完整遗忘）" :disabled="disabled" hide-details="auto" />
      <v-checkbox v-model="reviewed" label="已核对原件、真实归属、目标及完整正文，确认按上述范围采用" :disabled="disabled" hide-details="auto" />
      <p v-if="blocked" class="muted">请先保存或放弃上方文件编辑草稿，并等待其他文件操作结束。</p>
      <div class="actions"><v-btn type="submit" color="primary" :loading="busy" :disabled="disabled || !reviewed || !target || !content.trim() || !reason.trim()">新建正式文件并按所选范围处理原件</v-btn>
        <v-btn v-if="dirty" variant="text" :disabled="busy" @click="discard">放弃采用草稿</v-btn></div>
    </form>
    <p class="muted">采用与源清理不是双文件事务；失败或断开后须核对两边。原聊天、抽取位置、排除和角色样例不改；保留的待确认原件仍不参与自动回想。</p>
    <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
    <v-alert v-if="result" type="success" variant="tonal" role="status">正式文件已保存：{{ result.target }}。{{ result.source_removed ? '待确认原件已普通删除。' : '待确认原件保留。' }} 本页不重复提交；逐字段结果见下方。</v-alert>
  </section>
</template>

<style scoped>
.pending-adoption{border-top:1px solid var(--line);margin-top:20px;padding-top:16px;overflow-wrap:anywhere}
form{display:grid;gap:12px}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-block:8px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}summary{cursor:pointer;min-height:44px}
</style>
