<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import SkillInspector from './SkillInspector.vue'

const props = defineProps({
  scene: { type: String, required: true },
  taskId: { type: Number, required: true },
  status: { type: String, required: true },
  container: { type: String, default: null },
  configured: { type: Boolean, required: true },
})
const snapshot = ref(null), loading = ref(false), readError = ref(''), stale = ref(false)
const movedResult = ref(null), inspected = ref(null)
const eligible = computed(() => props.configured
  && ['done', 'failed', 'cancelled'].includes(props.status) && props.container === null)
const inspectorOpen = computed({ get: () => inspected.value !== null,
  set: value => { if (!value) inspected.value = null } })
const beginRead = useRequestGuard(() => `${props.scene}\u0000${props.taskId}\u0000${eligible.value}`)
watch(eligible, value => {
  if (!value) {
    beginRead()
    snapshot.value = null; inspected.value = null; stale.value = false
    loading.value = false; readError.value = ''
  }
})
async function read() {
  if (!eligible.value || loading.value) return
  const name = props.scene, id = props.taskId, fresh = beginRead()
  loading.value = true
  try {
    const value = await api(`/api/host/tasks/${encodeURIComponent(id)}/skills?${new URLSearchParams({scene:name})}`)
    if (!fresh()) return
    snapshot.value = value; readError.value = ''; stale.value = false
  } catch (error) { if (fresh()) readError.value = error.message }
  finally { if (fresh()) loading.value = false }
}
function moved(result) {
  movedResult.value = result
  inspected.value = null
  stale.value = true
  void read()
}
</script>

<template>
  <section class="task-skills" aria-labelledby="task-skills-title">
    <div class="section-heading"><h3 id="task-skills-title">任务自写技能</h3>
      <v-btn variant="outlined" :loading="loading" :disabled="!eligible" @click="read">读取候选</v-btn></div>
    <p class="muted">只在任务终态且容器已停止后按需读取实际工作目录；任务自写技能不自动成为本场景或共享技能。发现方式仅来自文件声明，不证明本次任务已装载或调用。</p>
    <p v-if="!eligible" class="muted">{{ !configured?'当前未配置 worker，无法定位任务工作区。':'运行中的任务不能读取候选；结束并停止容器后再查看。' }}</p>
    <v-alert v-if="readError" type="error" variant="tonal" role="alert" :title="snapshot?'读取失败 · 保留上次候选':'读取候选失败'">{{ readError }}</v-alert>
    <v-alert v-if="stale" type="warning" variant="tonal" role="status">目录已移动；候选名单正在重读，旧清单不可当作当前目录。</v-alert>
    <v-alert v-if="movedResult" type="success" variant="tonal" role="status">后端已移动整个技能目录，原任务位置不再存在；受影响场景和重启要求见实际回执。</v-alert>
    <details v-if="movedResult"><summary>查看目录移动原始回执</summary><pre>{{ JSON.stringify(movedResult,null,2) }}</pre></details>
    <template v-if="snapshot">
      <p class="muted">{{ snapshot.notice }}</p>
      <p v-if="!snapshot.items.length" class="muted">此任务工作区没有可列出的自写技能。</p>
      <ul v-else class="candidate-list"><li v-for="item in snapshot.items" :key="item.name">
        <strong>{{ item.name }}</strong> · {{ item.model_invocation?'声明可自动发现':'声明仅显式调用，不自动发现' }}
        <p class="muted">{{ item.description }}</p><p class="path">容器文件：{{ item.path }}</p>
        <v-btn variant="outlined" :disabled="stale" @click="inspected=item">查看文件与采用</v-btn></li></ul>
    </template>
    <v-dialog v-model="inspectorOpen" max-width="900" scrollable><v-card v-if="inspected" class="inspector-dialog">
      <v-card-title>任务技能 · {{ inspected.name }}</v-card-title>
      <v-card-text><SkillInspector :key="inspected.name" :scene="scene" source="task" :name="inspected.name" :task-id="taskId" @changed="moved" /></v-card-text>
      <v-card-actions><v-spacer /><v-btn variant="outlined" @click="inspected=null">关闭</v-btn></v-card-actions>
    </v-card></v-dialog>
  </section>
</template>

<style scoped>
.task-skills{border-top:1px solid var(--line);margin-top:20px;padding-top:4px;min-width:0;overflow-wrap:anywhere}.section-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.section-heading h3{margin:0}.candidate-list{list-style:none;padding:0;margin:0;display:grid;gap:10px}.candidate-list li{border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0;overflow-wrap:anywhere}.candidate-list p{margin:6px 0}.path{font-size:13px;overflow-wrap:anywhere}.task-skills pre{white-space:pre-wrap;overflow-wrap:anywhere}.task-skills summary{min-height:44px;cursor:pointer}.inspector-dialog{max-height:90vh}.task-skills :deep(.v-btn){min-height:44px}
</style>
