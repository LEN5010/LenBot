<script setup>
import { computed, onScopeDispose, ref, watch } from 'vue'
import { api } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const props = defineProps({
  scene: { type: String, required: true }, taskId: { type: Number, required: true },
  status: { type: String, required: true }, container: { type: String, default: null },
  browserActive: { type: Boolean, required: true }, discarded: { type: Boolean, required: true },
  configured: { type: Boolean, required: true },
})
const emit = defineEmits(['dirty', 'busy', 'invalidated'])
const inspection = ref(null), loading = ref(false), removing = ref(false), errorText = ref(''), result = ref(null)
const requester = ref(''), confirmed = ref(false)
const eligible = computed(() => props.configured && ['done','failed','cancelled'].includes(props.status)
  && props.container === null && !props.browserActive)
const busy = computed(() => loading.value || removing.value)
const dirty = computed(() => requester.value !== '' || confirmed.value)
watch(busy, value => emit('busy', value), { immediate:true, flush:'sync' })
watch(dirty, value => emit('dirty', value), { immediate:true, flush:'sync' })
onScopeDispose(() => { emit('dirty',false); emit('busy',false) })
const begin = useRequestGuard(() => `${props.scene}\u0000${props.taskId}`)
watch(eligible, value => { if (!value) { inspection.value = null; confirmed.value = false } })
async function inspect() {
  if (!eligible.value || busy.value) return
  const fresh = begin()
  loading.value = true; confirmed.value = false
  try {
    const value = await api(`/api/host/tasks/${props.taskId}/storage?${new URLSearchParams({scene:props.scene})}`)
    if (!fresh()) return
    inspection.value = value; errorText.value = ''
  } catch (error) { if (fresh()) errorText.value = error.message }
  finally { if (fresh()) loading.value = false }
}
async function discard() {
  if (!eligible.value || busy.value || !inspection.value || !requester.value || !confirmed.value) return
  const workspace = inspection.value.roots.find(root=>root.kind==='workspace').path
  const runtime = inspection.value.roots.find(root=>root.kind==='runtime').path
  const body = { requester:requester.value, workspace, runtime, confirmed:confirmed.value }
  const fresh = begin()
  removing.value = true; errorText.value = ''
  try {
    const value = await api(`/api/host/tasks/${props.taskId}/workspace-discard?${new URLSearchParams({scene:props.scene})}`,
      { method:'POST', body:JSON.stringify(body) })
    if (!fresh()) return
    result.value = value; inspection.value = null; requester.value = ''; confirmed.value = false
    emit('invalidated')
  } catch (error) {
    if (fresh()) {
      errorText.value = `${error.message} 没有成功回执，不代表没有受理或没有删除；草稿保留。请先重读任务与目录核对剩余项，不会自动重试。`
      inspection.value = null; confirmed.value = false; emit('invalidated')
    }
  } finally { if (fresh()) removing.value = false }
}
</script>

<template>
  <section class="workspace-discard" aria-label="明确放弃停止任务的工作环境">
    <div class="section-heading"><h3>明确放弃任务环境</h3><v-btn variant="outlined" :loading="loading" :disabled="!eligible || busy" @click="inspect">读取当前目录以核对</v-btn></div>
    <p class="muted">只处理已结束且容器/账号会话已关闭的任务。受理后不再续接这个环境，即使文件清理失败；需要继续工作时明确新建任务。</p>
    <p class="muted">将删除工作区、原生会话、自写技能、家目录、输入快照及控制目录；已复制出的交付副本、共享采用原件、任务/费用/平台回执记录与主人浏览器资料保留。先自行保留需要的原件。</p>
    <p v-if="discarded" class="muted">任务记录已有明确放弃决定，不代表所有文件都已物理删除；若上次失败，只能人工核对后处理当前剩余项。</p>
    <p v-if="!eligible" class="muted">当前状态不满足清理条件，不能提交；宿主仍在收尾时后端也会拒绝。</p>
    <v-alert v-if="errorText" type="error" variant="tonal" role="alert">{{ errorText }}</v-alert>
    <template v-if="inspection">
      <ul><li v-for="root in inspection.roots" :key="root.kind" class="path"><strong>{{ root.kind==='deliveries'?'保留，不删除':'拟删除' }}</strong> · {{ root.path }} · {{ root.exists?'读取时存在':'读取时不存在' }}</li></ul>
      <form @submit.prevent="discard"><fieldset :disabled="!eligible || busy" class="discard-fields">
        <v-text-field v-model="requester" label="本次实际操作者 QQ" inputmode="numeric" hide-details="auto" />
        <v-checkbox v-model="confirmed" label="明确放弃此任务环境和原生会话，不再续接，并清理上列两棵任务目录；交付副本和记录保留" hide-details />
      </fieldset><v-btn type="submit" color="error" variant="outlined" :loading="removing" :disabled="!eligible || busy || !requester || !confirmed">明确放弃并清理</v-btn></form>
    </template>
    <template v-if="result"><p class="muted">上次清理回执：{{ result.notice }}</p>
      <p class="original-text">已移除目录：{{ result.removal.removed.join('、') || '没有' }}</p>
      <p class="original-text">读取时已不存在：{{ result.removal.absent.join('、') || '没有' }}</p>
      <p class="muted">目录操作已返回，不代表安全擦除、配额实际释放或客户端收到文件。</p>
    </template>
  </section>
</template>

<style scoped>
.workspace-discard{border-top:1px solid var(--line);margin-top:20px;padding-top:12px;min-width:0;overflow-wrap:anywhere}.section-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.section-heading h3{margin:0}.path{font-size:13px;overflow-wrap:anywhere}.discard-fields{border:0;padding:0;margin:12px 0;display:grid;gap:12px}.workspace-discard :deep(.v-btn){min-height:44px}
</style>
