<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, sceneName } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const props = defineProps({
  scene: { type: String, required: true },
  source: { type: String, required: true },
  name: { type: String, required: true },
  taskId: { type: Number, default: null },
})
const emit = defineEmits(['changed'])
const listing = ref(null), selectedPath = ref(null), content = ref(''), nextOffset = ref(null)
const loading = ref(false), textLoading = ref(false), operating = ref(false)
const readError = ref(''), textError = ref(''), actionError = ref(''), actionResult = ref(null)
let textEpoch = 0
const locator = computed(() => `${props.scene}\u0000${props.source}\u0000${props.name}\u0000${props.taskId}`)
const beginFiles = useRequestGuard(() => locator.value)
const beginText = useRequestGuard(() => `${locator.value}\u0000${selectedPath.value}\u0000${textEpoch}`)
const beginAction = useRequestGuard(() => locator.value)
const canMove = computed(() => props.source === 'task' || props.source === 'scene')
const canDelete = computed(() => props.source === 'shared' || props.source === 'scene')
function sourceLabel(value) { return ({ builtin:'内置', shared:'共享', scene:'本场景', task:'本任务自写' })[value] }
function operationError(error) {
  return error.status >= 400 && error.status < 500
    ? `操作未被接受：${error.message}`
    : `操作结果未确认：${error.message} 请手动重读原目录与目标位置核对，不会自动重试。`
}
function location() {
  const values = { scene: props.scene, source: props.source, name: props.name }
  if (props.source === 'task') values.task_id = String(props.taskId)
  return new URLSearchParams(values)
}
async function readFiles() {
  if (loading.value || operating.value || actionResult.value) return
  const fresh = beginFiles()
  loading.value = true
  try {
    const value = await api(`/api/host/skills/files?${location()}`)
    if (!fresh()) return
    listing.value = value; readError.value = ''
    const first = value.files.find(file => file.path === 'SKILL.md')?.path ?? null
    selectFile(first)
  } catch (error) { if (fresh()) readError.value = error.message }
  finally { if (fresh()) loading.value = false }
}
function selectFile(path) {
  ++textEpoch
  selectedPath.value = path; content.value = ''; nextOffset.value = null
  textLoading.value = false; textError.value = ''
  if (path !== null) void readText(false)
}
async function readText(more) {
  if (selectedPath.value === null || textLoading.value || actionResult.value || (more && nextOffset.value === null)) return
  const path = selectedPath.value, offset = more ? nextOffset.value : 0, fresh = beginText()
  textLoading.value = true; textError.value = ''
  try {
    const value = await api(`/api/host/skills/text?${location()}&${new URLSearchParams({path,offset:String(offset)})}`)
    if (!fresh()) return
    content.value = more ? content.value + value.content : value.content
    nextOffset.value = value.next_offset
  } catch (error) { if (fresh()) textError.value = error.message }
  finally { if (fresh()) textLoading.value = false }
}
async function move(target) {
  const destination = target === 'scene' ? '本场景已采用技能' : '共享已采用技能'
  if (!window.confirm(`把“${props.name}”从${sourceLabel(props.source)}整个目录移动到${destination}？原位置会移走，不是复制，也不会覆盖同名目录。`)) return
  const fresh = beginAction()
  operating.value = true; actionError.value = ''; actionResult.value = null
  try {
    const result = await api(`/api/host/skills/move?${new URLSearchParams({scene:props.scene})}`, {
      method: 'POST', body: JSON.stringify({ source:props.source, name:props.name,
        task_id:props.source === 'task' ? props.taskId : null, target }),
    })
    if (!fresh()) return
    actionResult.value = result
    emit('changed', result)
  } catch (error) { if (fresh()) actionError.value = operationError(error) }
  finally { if (fresh()) operating.value = false }
}
async function remove() {
  if (!window.confirm(`删除${sourceLabel(props.source)}技能“${props.name}”的整个目录？原位置会移除；运行快照或保存白名单仍引用时，后端会拒绝。`)) return
  const fresh = beginAction()
  operating.value = true; actionError.value = ''; actionResult.value = null
  try {
    const result = await api(`/api/host/skills/${encodeURIComponent(props.source)}/${encodeURIComponent(props.name)}?${new URLSearchParams({scene:props.scene})}`, {
      method: 'DELETE',
    })
    if (!fresh()) return
    actionResult.value = result
    emit('changed', result)
  } catch (error) { if (fresh()) actionError.value = operationError(error) }
  finally { if (fresh()) operating.value = false }
}
onMounted(readFiles)
</script>

<template>
  <div class="skill-inspector">
    <div class="inspector-heading"><p class="muted">{{ sourceLabel(source) }}技能 · {{ name }}。以下是文件与许可事实，不表示已经执行。</p>
      <v-btn variant="outlined" :loading="loading" :disabled="operating || Boolean(actionResult)" @click="readFiles">重读文件清单</v-btn></div>
    <v-alert v-if="readError" type="error" variant="tonal" role="alert" :title="listing?'读取失败 · 保留上次文件清单':'读取技能失败'">{{ readError }}</v-alert>
    <p v-if="loading && !listing" class="muted" role="status">正在读取技能文件清单…</p>
    <template v-if="listing">
      <p><strong>{{ listing.skill.name }}</strong> · {{ listing.skill.description }}</p>
      <p class="muted">容器文件：{{ listing.skill.path }} · {{ listing.skill.model_invocation?'SKILL.md 声明可自动发现':'SKILL.md 声明仅显式调用' }}；这是文件元数据，不证明本次任务已装载。</p>
      <p v-if="listing.used_by_running.length" class="muted">当前宿主选中：{{ listing.used_by_running.map(sceneName).join('、') }}。<template v-if="canMove || canDelete">先取消对应角色许可并重启，再移除源目录。</template></p>
      <p v-if="listing.referenced_by_saved.length" class="muted">保存白名单仍引用：{{ listing.referenced_by_saved.map(sceneName).join('、') }}。删除前须解除这些引用；本场景同名提升为共享时可保留许可。</p>
      <h3>文件清单</h3>
      <p class="muted">只列实际相对路径与大小；二进制资源可列出但不能当文本预览。</p>
      <p v-if="actionResult" class="muted">下方是目录操作前读取的文件快照；原路径已失效。</p>
      <ul class="file-list"><li v-for="file in listing.files" :key="file.path">
        <v-btn variant="text" :disabled="Boolean(actionResult)" @click="selectFile(file.path)">{{ file.path }}</v-btn>
        <span class="muted">{{ file.size }} 字节</span></li></ul>
      <div v-if="selectedPath!==null" class="text-reader"><h3>文件正文 · {{ selectedPath }}</h3>
        <v-alert v-if="textError" type="error" variant="tonal" role="alert">{{ textError }}</v-alert>
        <p v-if="textLoading && !content" class="muted" role="status">正在读取 UTF-8 正文…</p>
        <pre v-if="content">{{ content }}</pre>
        <p v-else-if="!textLoading && !textError" class="muted">此文件没有可显示文字。</p>
        <v-btn v-if="nextOffset!==null" variant="outlined" :loading="textLoading" :disabled="textLoading || Boolean(actionResult)" @click="readText(true)">继续读取此文件</v-btn>
      </div>
      <div v-if="canMove || canDelete" class="actions"><h3>目录操作</h3>
        <p class="muted">移动整个目录后原位置不再存在；不会复制或覆盖。运行快照仍引用源目录时，移动或删除须先解除该项许可并重启；删除还须解除保存白名单。本场景同名提升为共享可保留许可，目标冲突仍由后端拒绝。</p>
        <v-btn v-if="source==='task'" variant="outlined" :disabled="operating || Boolean(actionResult)" :loading="operating" @click="move('scene')">移入本场景</v-btn>
        <v-btn v-if="canMove" variant="outlined" :disabled="operating || Boolean(actionResult)" :loading="operating" @click="move('shared')">{{ source==='scene'?'提升为共享':'移入共享' }}</v-btn>
        <v-btn v-if="canDelete" variant="outlined" color="error" :disabled="operating || Boolean(actionResult)" :loading="operating" @click="remove">删除此目录</v-btn>
      </div>
      <v-alert v-if="actionError" type="error" variant="tonal" role="alert">{{ actionError }}</v-alert>
      <v-alert v-if="actionResult" type="success" variant="tonal" role="status">后端已完成目录操作；原位置不再有效。受影响场景及重启要求以实际回执为准。</v-alert>
      <details v-if="actionResult"><summary>查看目录操作原始回执</summary><pre>{{ JSON.stringify(actionResult,null,2) }}</pre></details>
    </template>
  </div>
</template>

<style scoped>
.skill-inspector{min-width:0;overflow-wrap:anywhere}.inspector-heading{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}.inspector-heading p{min-width:0;flex:1 1 300px}.skill-inspector h3{font-size:15px;margin:18px 0 8px}.file-list{list-style:none;padding:0;margin:0}.file-list li{display:flex;align-items:center;gap:8px;flex-wrap:wrap;border-bottom:1px solid var(--line);min-width:0}.file-list :deep(.v-btn){height:auto;min-height:44px;white-space:normal;text-align:left;max-width:100%;overflow-wrap:anywhere}.text-reader{min-width:0}.skill-inspector pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:32rem;overflow:auto}.actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.actions h3,.actions p{flex-basis:100%;margin-bottom:0}.skill-inspector :deep(.v-btn){min-height:44px}.skill-inspector summary{min-height:44px;cursor:pointer}
</style>
